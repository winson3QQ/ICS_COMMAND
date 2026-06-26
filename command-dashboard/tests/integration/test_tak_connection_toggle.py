"""integration/test_tak_connection_toggle.py — P2-24（#164）runtime TAK 連線開關

驗證：
- `POST /api/tak/connection {enabled}` → 持久化 config（重啟後維持）+ `TAK_CONNECTION_TOGGLE` audit-first
- `/api/tak/status` 的 enabled 跟著 runtime 開關走（非只反映啟動時 env）
- RBAC = SYSADMIN_ONLY（gate 直測 + operator → 403）
- 無真 TAK config → start() 內部失敗回 false，endpoint 仍 200（running=false，不 raise）
"""

import pytest

from auth.role_enum import ROLE_OPERATOR_ZH, SYSADMIN_ONLY, allowed_roles_for
from core.database import get_conn
from repositories.account_repo import create_account
from repositories.config_repo import get_config

pytestmark = pytest.mark.integration


def _login(client, username="admin", pin="1234"):
    r = client.post("/api/auth/login", json={"username": username, "pin": pin})
    assert r.status_code == 200, r.text
    return {"X-Session-Token": r.json()["session_id"]}


class TestToggleGate:
    def test_path_maps_to_sysadmin_only(self):
        assert allowed_roles_for("POST", "/api/tak/connection") == SYSADMIN_ONLY

    def test_operator_gets_403(self, client):
        create_account("op_user", "5678", ROLE_OPERATOR_ZH, "Operator", "operator")
        r = client.post(
            "/api/tak/connection",
            json={"enabled": True},
            headers=_login(client, "op_user", "5678"),
        )
        assert r.status_code == 403


class TestTogglePersistAndAudit:
    def test_enable_persists_audits_and_status(self, client, auth):
        r = client.post("/api/tak/connection", json={"enabled": True}, headers=auth)
        assert r.status_code == 200
        assert r.json()["enabled"] is True
        # 持久化（重啟後維持）
        assert get_config("tak.connection_enabled") == "true"
        # status 跟著走
        s = client.get("/api/tak/status", headers=auth).json()
        assert s["enabled"] is True
        # audit-first 留痕
        with get_conn() as conn:
            n = conn.execute("SELECT COUNT(*) FROM audit_log WHERE action_type='TAK_CONNECTION_TOGGLE'").fetchone()[0]
        assert n >= 1

    def test_disable_persists_and_status_off(self, client, auth):
        client.post("/api/tak/connection", json={"enabled": True}, headers=auth)
        r = client.post("/api/tak/connection", json={"enabled": False}, headers=auth)
        assert r.status_code == 200
        assert r.json()["enabled"] is False
        assert r.json()["running"] is False  # 無真 TAK config，停掉後不在跑
        assert get_config("tak.connection_enabled") == "false"
        s = client.get("/api/tak/status", headers=auth).json()
        assert s["enabled"] is False


class TestResyncRespectsToggle:
    def test_resync_gated_by_runtime_toggle(self, tmp_db, monkeypatch):
        """P2-24 review 修正：_resync_tak_if_shared 改讀 effective_enabled()——
        runtime 關掉 TAK 後 move 已分享標記不再推；toggle 開啟才推（與 :8089 訂閱同源）。"""
        import asyncio

        from routers import cop
        from services import tak_downlink, tak_runtime

        calls = []
        monkeypatch.setattr(tak_downlink, "entity_to_cot", lambda e: "<cot/>")

        async def _fake_send(cot):
            calls.append(cot)

        monkeypatch.setattr(tak_downlink, "send_cot", _fake_send)
        shared = {"attributes": {"shared_tak": True}}

        tak_runtime.set_persisted_enabled(False)  # toggle OFF
        asyncio.run(cop._resync_tak_if_shared(shared))
        assert calls == []  # 關掉 → 不推

        tak_runtime.set_persisted_enabled(True)  # toggle ON
        asyncio.run(cop._resync_tak_if_shared(shared))
        assert len(calls) == 1  # 開啟 → 推
