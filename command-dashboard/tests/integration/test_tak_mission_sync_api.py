# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""#506 M1：routers/tak.py `POST /api/tak/mission-sync` 端點。

驗 RBAC（COMMAND_ROLES）+ audit-first（TAK_MISSION_SYNC）+ run_mission_sync 配線 +
未配置 422。run_mission_sync 走真 :8443 → monkeypatch 攔（不碰網路）。
"""

from __future__ import annotations

from auth.role_enum import ROLE_OPERATOR_ZH
from core.database import get_conn
from repositories.account_repo import create_account
from services import tak_missions


def _operator_auth(client):
    create_account("op1", "1234", role=ROLE_OPERATOR_ZH)
    r = client.post("/api/auth/login", json={"username": "op1", "pin": "1234"})
    assert r.status_code == 200, r.text
    return {"X-Session-Token": r.json()["session_id"]}


def test_mission_sync_disabled_returns_422(client, auth, monkeypatch):
    monkeypatch.setattr(tak_missions, "mission_sync_enabled", lambda: False)
    r = client.post("/api/tak/mission-sync", headers=auth)
    assert r.status_code == 422


def test_mission_sync_runs_and_audits(client, auth, monkeypatch):
    monkeypatch.setattr(tak_missions, "mission_sync_enabled", lambda: True)
    monkeypatch.setattr(tak_missions, "configured_mission_names", lambda: ["ICS", "OpX"])

    async def _fake_run():
        return {
            "enabled": True,
            "missions": {"ICS": {"fetched": 2, "ingested": 2, "skipped": 0, "errors": 0}},
            "fetched": 2,
            "ingested": 2,
            "skipped": 0,
            "errors": 0,
        }

    monkeypatch.setattr(tak_missions, "run_mission_sync", _fake_run)
    r = client.post("/api/tak/mission-sync", headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["ingested"] == 2 and body["enabled"] is True
    # audit-first：TAK_MISSION_SYNC 有留痕（含 feeds）
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT detail FROM audit_log WHERE action_type='TAK_MISSION_SYNC'",
        ).fetchall()
    assert len(rows) == 1
    assert "ICS" in rows[0]["detail"]


def test_mission_sync_rbac_operator_forbidden(client, monkeypatch):
    # COMMAND_ROLES 限定 → operator（WRITE）403，且不觸發 run（gate 在中央 middleware）
    called = {"n": 0}

    def _boom():
        called["n"] += 1
        return True

    monkeypatch.setattr(tak_missions, "mission_sync_enabled", _boom)
    auth = _operator_auth(client)
    r = client.post("/api/tak/mission-sync", headers=auth)
    assert r.status_code == 403
    assert called["n"] == 0  # 中央 gate 先擋，沒進 handler


def test_mission_sync_requires_auth(client):
    assert client.post("/api/tak/mission-sync").status_code != 200
