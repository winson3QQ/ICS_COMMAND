from __future__ import annotations

from auth.role_enum import ROLE_OPERATOR_ZH
from repositories.account_repo import create_account
from repositories.config_repo import set_admin_pin


def _login(client, username: str = "admin", pin: str = "1234") -> dict[str, str]:
    r = client.post("/api/auth/login", json={"username": username, "pin": pin})
    assert r.status_code == 200, r.text
    return {"X-Session-Token": r.json()["session_id"]}


class TestSysadminSessionGate:
    def test_sysadmin_session_without_admin_pin_passes(self, client):
        set_admin_pin("1234", "test")
        r = client.get("/api/admin/accounts", headers=_login(client))
        assert r.status_code == 200

    def test_admin_pin_without_session_is_rejected(self, client):
        set_admin_pin("1234", "test")
        r = client.get("/api/admin/accounts", headers={"X-Admin-PIN": "1234"})
        assert r.status_code == 401

    def test_wrong_admin_pin_does_not_override_sysadmin_session(self, client):
        set_admin_pin("1234", "test")
        headers = _login(client)
        headers["X-Admin-PIN"] = "000000"
        r = client.get("/api/admin/accounts", headers=headers)
        assert r.status_code == 200


class TestRoleGate:
    def _operator_headers(self, client):
        create_account("op_user", "5678", ROLE_OPERATOR_ZH, "Operator", "operator")
        return _login(client, "op_user", "5678")

    def test_operator_without_admin_pin_gets_403(self, client):
        r = client.get("/api/admin/accounts", headers=self._operator_headers(client))
        assert r.status_code == 403

    def test_operator_with_admin_pin_still_gets_403(self, client):
        headers = self._operator_headers(client)
        headers["X-Admin-PIN"] = "1234"
        r = client.get("/api/admin/accounts", headers=headers)
        assert r.status_code == 403

    def test_operator_cannot_escalate_own_role(self, client):
        headers = self._operator_headers(client)
        r = client.put(
            "/api/admin/accounts/op_user/role",
            headers=headers,
            json={"role": "系統管理員", "role_detail": "sysadmin"},
        )
        assert r.status_code == 403


class TestAdminBoundary:
    def test_admin_pin_change_requires_sysadmin_session(self, client):
        r = client.put("/api/admin/pin", headers=_login(client), json={"new_pin": "5678"})
        assert r.status_code == 200

    def test_delete_nonexistent_account_returns_404(self, client):
        r = client.delete("/api/admin/accounts/ghost_user", headers=_login(client))
        assert r.status_code == 404
