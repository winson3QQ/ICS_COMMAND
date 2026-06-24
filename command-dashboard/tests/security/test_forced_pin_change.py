"""tests/security/test_forced_pin_change.py — #348-F5 P2a 新帳號首登強制改初始 PIN

驗證：
- is_first_run_required 收斂為 bootstrap-only（第 2+ 帳號 default_pin 不觸發全系統 423）
- account_needs_pin_change helper
- admin 建帳號(API) → is_default_pin=1 → 登入 must_change_pin=true → server-side 閘擋非改 PIN API
  (423 PIN_CHANGE_REQUIRED) → change-initial-pin 走得通 → 改完即解
"""


def _login(client, username="admin", pin="1234"):
    r = client.post("/api/auth/login", json={"username": username, "pin": pin})
    assert r.status_code == 200, r.text
    return {"X-Session-Token": r.json()["session_id"]}


class TestIsFirstRunDecouple:
    def test_bootstrap_only_single_admin(self, tmp_db):
        from repositories.account_repo import create_account, is_first_run_required

        create_account("admin", "739104", "系統管理員", "", "sysadmin", require_pin_change=True)
        assert is_first_run_required() is True  # 唯一 admin 未改 → bootstrap

    def test_second_account_does_not_trigger_systemgate(self, tmp_db):
        from repositories.account_repo import create_account, is_first_run_required

        create_account("admin", "739104", "系統管理員", "", "sysadmin", require_pin_change=True)
        create_account("op", "739104", "操作員", "", "operator", require_pin_change=True)
        assert is_first_run_required() is False  # 第 2 帳號 → 不再全系統 first-run（解耦）

    def test_false_after_admin_changed(self, tmp_db):
        from repositories.account_repo import clear_default_pin_flag, create_account, is_first_run_required

        create_account("admin", "739104", "系統管理員", "", "sysadmin", require_pin_change=True)
        clear_default_pin_flag("admin")
        assert is_first_run_required() is False


class TestAccountNeedsPinChange:
    def test_flag_lifecycle(self, tmp_db):
        from repositories.account_repo import account_needs_pin_change, clear_default_pin_flag, create_account

        create_account("u", "739104", "操作員", "", "operator", require_pin_change=True)
        assert account_needs_pin_change("u") is True
        clear_default_pin_flag("u")
        assert account_needs_pin_change("u") is False
        assert account_needs_pin_change("ghost") is False  # 不存在 → False


class TestForcedChangeFlow:
    def test_new_account_gated_until_changed(self, client):
        auth = _login(client)  # 預設 admin（fixture 已清 default_pin）
        # admin 建帳號（API）→ require_pin_change → is_default_pin=1
        r = client.post(
            "/api/admin/accounts",
            json={"username": "newop", "pin": "739104", "role": "操作員"},
            headers=auth,
        )
        assert r.status_code == 200, r.text

        # 新帳號登入 → must_change_pin=true
        lr = client.post("/api/auth/login", json={"username": "newop", "pin": "739104"})
        assert lr.status_code == 200
        assert lr.json()["must_change_pin"] is True
        nt = {"X-Session-Token": lr.json()["session_id"]}

        # 未改前：非白名單 API → 423 PIN_CHANGE_REQUIRED（server-side 真強制）
        g = client.get("/api/dashboard", headers=nt)
        assert g.status_code == 423 and g.json().get("code") == "PIN_CHANGE_REQUIRED", g.text
        # me/heartbeat 等白名單仍通（前端改 PIN 畫面需要）
        assert client.get("/api/auth/me", headers=nt).status_code == 200

        # change-initial-pin 走得通（解耦：認 account default_pin、非 is_first_run_required）
        c = client.post(
            "/api/auth/change-initial-pin",
            json={"current_pin": "739104", "new_pin": "820471"},
            headers=nt,
        )
        assert c.status_code == 200, c.text

        # 改完 → 同 API 不再 423
        assert client.get("/api/dashboard", headers=nt).status_code != 423

    def test_creating_account_does_not_systemlock_admin(self, client):
        auth = _login(client)
        client.post(
            "/api/admin/accounts",
            json={"username": "op2", "pin": "739104", "role": "操作員"},
            headers=auth,
        )
        # is_first_run_required 收斂 → 建第 2 帳號不鎖全系統，admin 仍能用 admin API
        assert client.get("/api/admin/accounts", headers=auth).status_code == 200
