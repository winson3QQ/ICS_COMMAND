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


def _create(client, auth, username, role="操作員"):
    # #348-F5 P2b：admin 不再自設 PIN → 後端產隨機臨時 PIN，一次性回傳。回傳 temp_pin 供登入。
    r = client.post("/api/admin/accounts", json={"username": username, "role": role}, headers=auth)
    assert r.status_code == 200, r.text
    return r.json()["temp_pin"]


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
        # admin 建帳號（API）→ 系統產臨時 PIN（temp_pin）→ require_pin_change → is_default_pin=1
        temp = _create(client, auth, "newop")

        # 新帳號用臨時 PIN 登入 → must_change_pin=true
        lr = client.post("/api/auth/login", json={"username": "newop", "pin": temp})
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
            json={"current_pin": temp, "new_pin": "820471"},
            headers=nt,
        )
        assert c.status_code == 200, c.text

        # 改完 → 同 API 不再 423
        assert client.get("/api/dashboard", headers=nt).status_code != 423

    def test_creating_account_does_not_systemlock_admin(self, client):
        auth = _login(client)
        _create(client, auth, "op2")
        # is_first_run_required 收斂 → 建第 2 帳號不鎖全系統，admin 仍能用 admin API
        assert client.get("/api/admin/accounts", headers=auth).status_code == 200


class TestGatedManagerCannotSelfUnlock:
    """review 修正：待改帳號不得用 reset_pin（不驗目前 PIN）自清 default 解閘（繞 change-initial-pin）。"""

    def test_reset_pin_self_blocked_when_not_first_run(self, client):
        auth = _login(client)
        # 建 gated sysadmin（具 account-manager 權限可呼叫 reset_pin）
        temp = _create(client, auth, "mgr", "系統管理員")
        tok = client.post("/api/auth/login", json={"username": "mgr", "pin": temp}).json()["session_id"]
        h = {"X-Session-Token": tok}
        # 非 first-run（已 2 帳號）→ reset_pin 被閘擋（待改帳號只准走 change-initial-pin 驗舊 PIN）
        rp = client.put("/api/admin/accounts/mgr/pin", headers=h)
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
        temp = _create(client, auth, "wsadmin", "系統管理員")
        tok = client.post("/api/auth/login", json={"username": "wsadmin", "pin": temp}).json()["session_id"]
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


class TestP2bSystemTempPin:
    """#348-F5 P2b：admin 不再自設 PIN → 系統產隨機臨時 PIN（一次性回傳），reset 同模型。"""

    def test_create_returns_policy_valid_temp_pin(self, client):
        from core.pin_policy import MIN_LEN, validate_pin_strength

        auth = _login(client)
        r = client.post("/api/admin/accounts", json={"username": "p2bu", "role": "操作員"}, headers=auth)
        assert r.status_code == 200, r.text
        temp = r.json()["temp_pin"]
        assert isinstance(temp, str) and len(temp) >= MIN_LEN
        validate_pin_strength(temp, "p2bu")  # 產生值必過強度策略（否則 raise）
        lr = client.post("/api/auth/login", json={"username": "p2bu", "pin": temp})
        assert lr.status_code == 200 and lr.json()["must_change_pin"] is True

    def test_admin_supplied_pin_is_ignored(self, client):
        # 安全核心：admin 帶 pin 欄也不算數 → admin 無法得知/設定使用者初始 PIN
        auth = _login(client)
        r = client.post(
            "/api/admin/accounts",
            json={"username": "p2bx", "pin": "attacker-knows-this", "role": "操作員"},
            headers=auth,
        )
        assert r.status_code == 200, r.text
        temp = r.json()["temp_pin"]

        def _login_status(pin):
            return client.post("/api/auth/login", json={"username": "p2bx", "pin": pin}).status_code

        assert _login_status("attacker-knows-this") == 401  # admin 帶的 pin 無效
        assert _login_status(temp) == 200

    def test_reset_generates_temp_and_reforces_change(self, client):
        from repositories.account_repo import account_needs_pin_change

        auth = _login(client)
        temp = _create(client, auth, "p2br")
        # 使用者改掉初始 PIN → 清旗標
        tok = client.post("/api/auth/login", json={"username": "p2br", "pin": temp}).json()["session_id"]
        client.post(
            "/api/auth/change-initial-pin",
            json={"current_pin": temp, "new_pin": "820471"},
            headers={"X-Session-Token": tok},
        )
        assert account_needs_pin_change("p2br") is False
        # admin reset → 新隨機 temp + 重新強制改（不收 body）
        rp = client.put("/api/admin/accounts/p2br/pin", headers=auth)
        assert rp.status_code == 200, rp.text
        new_temp = rp.json()["temp_pin"]
        assert new_temp != "820471"
        assert account_needs_pin_change("p2br") is True
        # 舊使用者 PIN 失效、新 temp 可登入且 must_change_pin
        assert client.post("/api/auth/login", json={"username": "p2br", "pin": "820471"}).status_code == 401
        lr = client.post("/api/auth/login", json={"username": "p2br", "pin": new_temp})
        assert lr.status_code == 200 and lr.json()["must_change_pin"] is True
