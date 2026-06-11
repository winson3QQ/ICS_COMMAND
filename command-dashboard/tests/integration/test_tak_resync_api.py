"""P2-14 (C)（#194/#173）— routers/tak.py `POST /api/tak/resync` 權威 resync 端點。

驗 RBAC（COMMAND_ROLES）+ audit-first + 未配置 → 422 + 拉取失敗 → 503 + 配線。
run_resync 走真 Marti 連線 → 一律 monkeypatch 攔截（不碰網路）。
"""

from __future__ import annotations

from auth.role_enum import ROLE_OBSERVER_ZH, ROLE_OPERATOR_ZH
from core.database import get_conn
from repositories.account_repo import create_account
from services import tak_resync
from services.tak_rest_client import TakRestError


def _login(client, username, pin="1234"):
    r = client.post("/api/auth/login", json={"username": username, "pin": pin})
    assert r.status_code == 200, r.text
    return {"X-Session-Token": r.json()["session_id"]}


def _audit_rows():
    with get_conn() as conn:
        return conn.execute("SELECT operator, detail FROM audit_log WHERE action_type='TAK_RESYNC'").fetchall()


def test_resync_runs_and_audits(client, auth, monkeypatch):
    monkeypatch.setattr(tak_resync, "resync_enabled", lambda: True)

    async def _fake_run(lookback_s=None):
        return {"enabled": True, "fetched": 3, "ingested": 2, "skipped": 1, "errors": 0}

    monkeypatch.setattr(tak_resync, "run_resync", _fake_run)

    r = client.post("/api/tak/resync", headers=auth)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] and data["fetched"] == 3 and data["ingested"] == 2
    # audit-first 落地（admin = COMMAND_ROLES）
    rows = _audit_rows()
    assert len(rows) == 1 and rows[0][0] == "admin"


def test_resync_disabled_returns_422(client, auth, monkeypatch):
    monkeypatch.setattr(tak_resync, "resync_enabled", lambda: False)
    r = client.post("/api/tak/resync", headers=auth)
    assert r.status_code == 422
    assert _audit_rows() == []  # 未配置即擋 → 無稽核


def test_resync_fetch_failure_returns_503(client, auth, monkeypatch):
    monkeypatch.setattr(tak_resync, "resync_enabled", lambda: True)

    async def _boom(lookback_s=None):
        raise TakRestError("Marti 400 BAD_REQUEST")

    monkeypatch.setattr(tak_resync, "run_resync", _boom)
    r = client.post("/api/tak/resync", headers=auth)
    assert r.status_code == 503
    # audit-first：意圖已先稽核（即使拉取失敗，留痕下達 resync 的意圖）
    assert len(_audit_rows()) == 1


def test_observer_cannot_resync(client, monkeypatch):
    monkeypatch.setattr(tak_resync, "resync_enabled", lambda: True)
    create_account("obs_rs", "1234", ROLE_OBSERVER_ZH, "Obs RS", "observer")
    r = client.post("/api/tak/resync", headers=_login(client, "obs_rs"))
    assert r.status_code == 403
    assert _audit_rows() == []


def test_operator_cannot_resync(client, monkeypatch):
    # resync 改寫整 COP 態勢，比照其餘 /api/tak/* POST 限 COMMAND_ROLES（operator 不可）。
    monkeypatch.setattr(tak_resync, "resync_enabled", lambda: True)
    create_account("op_rs", "1234", ROLE_OPERATOR_ZH, "Op RS", "operator")
    r = client.post("/api/tak/resync", headers=_login(client, "op_rs"))
    assert r.status_code == 403
