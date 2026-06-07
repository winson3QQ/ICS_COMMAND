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


# ── RT-L4（#153）：audit-log limit 服務端 clamp ──────────────────────────────


def _audit_status(client):
    return client.get("/api/admin/audit-log?limit=999999", headers=_login(client))


class TestAuditLogLimitClamp:
    def test_huge_limit_clamped_not_error(self, client):
        # ?limit=999999 不該全控 → 服務端 clamp 1000；端點正常回 list（非 500/慢查詢爆）。
        r = _audit_status(client)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_negative_limit_clamped_to_zero(self, client):
        # 負 limit 在 SQLite `LIMIT -1` 會變「無上限」破口；clamp max(0,..) → 回空 list。
        r = client.get("/api/admin/audit-log?limit=-1", headers=_login(client))
        assert r.status_code == 200
        assert r.json() == []


# ── OP-1（#153）：suspend-all 不自鎖 + 強制確認字串 ─────────────────────────


def _status(username: str):
    from core.database import get_conn

    with get_conn() as conn:
        row = conn.execute("SELECT status FROM accounts WHERE username=?", (username,)).fetchone()
    return row["status"] if row else None


class TestSuspendAllSelfLock:
    def test_suspend_all_requires_confirm_string(self, client):
        # 後端強制 confirm（不依賴前端 dialog）；缺 / 錯 → 422，且不執行停權。
        h = _login(client)
        assert client.post("/api/admin/suspend-all", json={}, headers=h).status_code == 422
        assert client.post("/api/admin/suspend-all", json={"confirm": "x"}, headers=h).status_code == 422
        assert _status("admin") == "active"  # 沒被執行

    def test_suspend_all_excludes_operator_self(self, client):
        create_account("victim_op", "1234", ROLE_OPERATOR_ZH, "Victim", "operator")
        h = _login(client)  # admin = 發起者
        r = client.post("/api/admin/suspend-all", json={"confirm": "SUSPEND_ALL"}, headers=h)
        assert r.status_code == 200, r.text
        assert _status("admin") == "active"  # 發起者沒被自鎖
        assert _status("victim_op") == "suspended"  # 其他人被停權
        # 發起者 session 仍可操作 admin API（沒進「需主機 shell 救」狀態）
        assert client.get("/api/admin/accounts", headers=h).status_code == 200
