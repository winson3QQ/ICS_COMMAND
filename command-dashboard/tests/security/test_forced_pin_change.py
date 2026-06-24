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


class TestGatedManagerCannotSelfUnlock:
    """review 修正：待改帳號不得用 reset_pin（不驗目前 PIN）自清 default 解閘（繞 change-initial-pin）。"""

    def test_reset_pin_self_blocked_when_not_first_run(self, client):
        auth = _login(client)
        # 建 gated sysadmin（具 account-manager 權限可呼叫 reset_pin）
        r = client.post(
            "/api/admin/accounts",
            json={"username": "mgr", "pin": "739104", "role": "系統管理員"},
            headers=auth,
        )
        assert r.status_code == 200, r.text
        tok = client.post("/api/auth/login", json={"username": "mgr", "pin": "739104"}).json()["session_id"]
        h = {"X-Session-Token": tok}
        # 非 first-run（已 2 帳號）→ reset_pin 自清 default 被閘擋（須改走 change-initial-pin 驗舊 PIN）
        rp = client.put("/api/admin/accounts/mgr/pin", json={"new_pin": "820471"}, headers=h)
        assert rp.status_code == 423 and rp.json().get("code") == "PIN_CHANGE_REQUIRED", rp.text
        from repositories.account_repo import account_needs_pin_change

        assert account_needs_pin_change("mgr") is True  # 仍待改，未被繞過


class TestGatedAccountWebSocket:
    """review 修正：HTTP 閘不跑 WS scope → cop /ws/updates 須自查 account_needs_pin_change。"""

    def test_cop_ws_blocked_then_allowed(self, client):
        import pytest
        from starlette.websockets import WebSocketDisconnect

        auth = _login(client)
        # gated sysadmin（READ_ROLES 內 → 排除「因 role 被擋」干擾，close 確定來自 PIN 閘）
        client.post(
            "/api/admin/accounts",
            json={"username": "wsadmin", "pin": "739104", "role": "系統管理員"},
            headers=auth,
        )
        tok = client.post("/api/auth/login", json={"username": "wsadmin", "pin": "739104"}).json()["session_id"]
        subs = ["ics-cop-v1", f"ics.session.{tok}"]
        # 待改初始 PIN → WS 拒（4401）
        with (
            pytest.raises(WebSocketDisconnect) as ei,
            client.websocket_connect("/api/cop/ws/updates", subprotocols=subs),
        ):
            pass
        assert ei.value.code == 4401
        # 清旗標 → 同帳號 WS 通（收 hello）
        from repositories.account_repo import clear_default_pin_flag

        clear_default_pin_flag("wsadmin")
        with client.websocket_connect("/api/cop/ws/updates", subprotocols=subs) as ws:
            assert ws.receive_json()["op"] == "hello"
