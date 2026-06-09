"""P2-13(A)（#176）— routers/tak.py `POST /api/tak/downlink` 下行指令端點。

驗 RBAC（COMMAND_ROLES）+ **強制 audit（audit-first）** + send_cot 配線。
send_cot 走真 :8089 連線 → 一律 monkeypatch 攔截（不碰網路），驗「建了什麼 CoT、有沒有送」。
"""

from __future__ import annotations

import pytest

from auth.role_enum import ROLE_OBSERVER_ZH, ROLE_OPERATOR_ZH
from core.database import get_conn
from repositories.account_repo import create_account
from services import tak_downlink


def _cmd(**over):
    body = {"type": "a-f-G", "lat": 25.03, "lon": 121.56, "callsign": "CMD-1", "remarks": "推進"}
    body.update(over)
    return body


def _login(client, username, pin="1234"):
    r = client.post("/api/auth/login", json={"username": username, "pin": pin})
    assert r.status_code == 200, r.text
    return {"X-Session-Token": r.json()["session_id"]}


@pytest.fixture
def captured_cot(monkeypatch):
    """攔 send_cot → 記下 CoT 字串，不碰網路。"""
    sent: list[str] = []

    async def _fake_send(cot_xml: str) -> None:
        sent.append(cot_xml)

    monkeypatch.setattr(tak_downlink, "send_cot", _fake_send)
    return sent


def _audit_rows(uid):
    with get_conn() as conn:
        return conn.execute(
            "SELECT operator, detail FROM audit_log WHERE action_type='TAK_DOWNLINK' AND target_id=?",
            (uid,),
        ).fetchall()


def test_downlink_sends_and_audits(client, auth, captured_cot):
    r = client.post("/api/tak/downlink", json=_cmd(uid="ICS-CMD-T1"), headers=auth)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "sent" and data["uid"] == "ICS-CMD-T1"
    # 真的送出一筆 CoT，內容對
    assert len(captured_cot) == 1
    assert "ICS-CMD-T1" in captured_cot[0] and "type='a-f-G'" in captured_cot[0].replace('"', "'")
    # 強制 audit 落地
    rows = _audit_rows("ICS-CMD-T1")
    assert len(rows) == 1 and rows[0][0] == "admin"


def test_downlink_autogenerates_uid(client, auth, captured_cot):
    r = client.post("/api/tak/downlink", json=_cmd(), headers=auth)
    assert r.status_code == 200
    assert r.json()["uid"].startswith("ICS-CMD-")
    assert len(captured_cot) == 1


def test_downlink_requires_auth(client, captured_cot):
    r = client.post("/api/tak/downlink", json=_cmd())
    assert r.status_code == 401
    assert captured_cot == []  # 未送


def test_observer_cannot_downlink(client, captured_cot):
    create_account("obs_dl", "1234", ROLE_OBSERVER_ZH, "Obs DL", "observer")
    r = client.post("/api/tak/downlink", json=_cmd(uid="ICS-CMD-OBS"), headers=_login(client, "obs_dl"))
    assert r.status_code == 403
    assert captured_cot == []
    assert _audit_rows("ICS-CMD-OBS") == []  # 被擋 → 無稽核、無送出


def test_operator_cannot_downlink(client, captured_cot):
    # 下達指令是指揮層動作（COMMAND_ROLES），operator（WRITE_ROLES）不可。
    create_account("op_dl", "1234", ROLE_OPERATOR_ZH, "Op DL", "operator")
    r = client.post("/api/tak/downlink", json=_cmd(uid="ICS-CMD-OP"), headers=_login(client, "op_dl"))
    assert r.status_code == 403
    assert captured_cot == []


def test_downlink_invalid_type_422(client, auth, captured_cot):
    r = client.post("/api/tak/downlink", json=_cmd(type="<script>"), headers=auth)
    assert r.status_code == 422
    assert captured_cot == []


def test_send_failure_503_but_still_audited(client, auth, monkeypatch):
    """audit-first 紀律：送出失敗 → 503，但稽核已記下達意圖（無未稽核之下達；失敗有跡可循）。"""

    async def _boom(cot_xml: str) -> None:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(tak_downlink, "send_cot", _boom)
    r = client.post("/api/tak/downlink", json=_cmd(uid="ICS-CMD-FAIL"), headers=auth)
    assert r.status_code == 503
    # 送出失敗，但稽核已落（audit-first）
    assert len(_audit_rows("ICS-CMD-FAIL")) == 1
