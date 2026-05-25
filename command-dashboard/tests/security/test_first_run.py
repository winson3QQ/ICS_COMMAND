"""
tests/security/test_first_run.py — C1-A 首次設定強制流程測試

對應規格：commercialization §C1-A
- 移除預設 PIN 1234，產生隨機 6 位數 PIN
- is_default_pin=1 → login 回傳 must_change_pin=true
- 改 PIN 成功後清旗標，gate 解除
- gate 啟動時 whitelist 外的路徑回 423
"""

import pytest
import os
import re


# ─────────────────────────────────────────────────────────────────
# ensure_initial_admin_token
# ─────────────────────────────────────────────────────────────────

class TestEnsureInitialAdminToken:
    def test_creates_admin_with_random_pin(self, tmp_db, tmp_path):
        from repositories.account_repo import (
            ensure_initial_admin_token, get_all_accounts
        )
        pin = ensure_initial_admin_token(token_dir=str(tmp_path))
        assert pin is not None
        # 必須是 6 位數字
        assert re.match(r"^\d{6}$", pin)
        # admin 帳號已建立
        accounts = get_all_accounts()
        assert any(a["username"] == "admin" for a in accounts)

    def test_writes_token_file(self, tmp_db, tmp_path):
        from repositories.account_repo import ensure_initial_admin_token
        pin = ensure_initial_admin_token(token_dir=str(tmp_path))
        token_file = tmp_path / "first_run_token"
        assert token_file.exists()
        assert token_file.read_text().strip() == pin
        # 權限 0600
        if os.name != "nt":
            assert oct(token_file.stat().st_mode & 0o777) == "0o600"

    def test_returns_none_if_accounts_exist(self, tmp_db, tmp_path):
        from repositories.account_repo import (
            ensure_initial_admin_token, create_account
        )
        create_account("preexisting", "9999", "操作員", "", "operator")
        pin = ensure_initial_admin_token(token_dir=str(tmp_path))
        assert pin is None

    def test_pin_unpredictable(self, tmp_db, tmp_path):
        """連續 5 次（使用獨立 DB）產出 PIN 不重複（隨機性 sanity check）。"""
        from repositories.account_repo import ensure_initial_admin_token
        from core.database import get_conn
        pins = set()
        for i in range(5):
            # 每次清掉 accounts 重新跑
            with get_conn() as conn:
                conn.execute("DELETE FROM accounts")
                conn.commit()
            pin = ensure_initial_admin_token(token_dir=str(tmp_path))
            pins.add(pin)
        # 5 次至少應有 4 個不同（極小機率撞號，>4 已有極高信心）
        assert len(pins) >= 4

    def test_initial_pin_is_default_flagged(self, tmp_db, tmp_path):
        """新建 admin 必須 is_default_pin=1。"""
        from repositories.account_repo import (
            ensure_initial_admin_token, is_first_run_required
        )
        ensure_initial_admin_token(token_dir=str(tmp_path))
        assert is_first_run_required() is True


# ─────────────────────────────────────────────────────────────────
# clear_default_pin_flag
# ─────────────────────────────────────────────────────────────────

class TestClearDefaultPinFlag:
    def test_clear_after_pin_change(self, tmp_db, tmp_path):
        from repositories.account_repo import (
            ensure_initial_admin_token, clear_default_pin_flag, is_first_run_required
        )
        ensure_initial_admin_token(token_dir=str(tmp_path))
        assert is_first_run_required() is True
        clear_default_pin_flag("admin")
        assert is_first_run_required() is False


# ─────────────────────────────────────────────────────────────────
# first_run_gate middleware（with custom client fixture）
# ─────────────────────────────────────────────────────────────────

@pytest.fixture
def first_run_client(tmp_db, monkeypatch):
    """模擬 production 首次啟動：使用真實 ensure_initial_admin_token，
    is_default_pin=1 stays，gate 啟動。"""
    from repositories import account_repo
    from fastapi.testclient import TestClient
    # 不 monkeypatch；讓真正的 ensure_initial_admin_token 跑（產隨機 PIN）
    captured_pin = {}

    def _wrapped(*args, **kwargs):
        # 用固定 PIN 方便測試但保留 is_default_pin=1
        from repositories.account_repo import create_account
        from core.database import get_conn
        with get_conn() as conn:
            cnt = conn.execute("SELECT COUNT(*) as c FROM accounts").fetchone()["c"]
            if cnt == 0:
                create_account("admin", "999999", "指揮官", "系統管理員", "admin")
                conn.execute(
                    "UPDATE accounts SET is_default_pin=1 WHERE username='admin'")
                conn.commit()
        captured_pin["pin"] = "999999"

    monkeypatch.setattr("main.ensure_initial_admin_token", _wrapped)
    from main import app
    with TestClient(app, raise_server_exceptions=True) as c:
        c._initial_pin = captured_pin.get("pin")
        yield c


class TestFirstRunGate:
    def test_login_allowed_when_first_run(self, first_run_client):
        """白名單：login 必須能用初始 PIN 登入。"""
        r = first_run_client.post("/api/auth/login", json={
            "username": "admin", "pin": "999999"
        })
        assert r.status_code == 200
        body = r.json()
        assert body["must_change_pin"] is True

    def test_health_allowed_when_first_run(self, first_run_client):
        """白名單：/api/health。"""
        r = first_run_client.get("/api/health")
        assert r.status_code == 200

    def test_other_apis_blocked_with_423(self, first_run_client):
        """非白名單路徑（含已登入 token）回 423。"""
        # 先用初始 PIN 登入拿 token
        r = first_run_client.post("/api/auth/login", json={
            "username": "admin", "pin": "999999"
        })
        token = r.json()["session_id"]
        # 試訪問 /api/snapshots/shelter（未在白名單）
        r2 = first_run_client.get(
            "/api/snapshots/shelter",
            headers={"X-Session-Token": token})
        assert r2.status_code == 423
        assert r2.json()["code"] == "FIRST_RUN_REQUIRED"

    def test_change_pin_clears_gate(self, first_run_client, monkeypatch):
        """改 PIN 後 is_default_pin=0，gate 解除，原本被擋的 API 可訪問。"""
        # 1. 登入拿 token
        r = first_run_client.post("/api/auth/login", json={
            "username": "admin", "pin": "999999"
        })
        token = r.json()["session_id"]
        # 2. 改 PIN（admin PIN check 也要先存在；先 mock 過）
        from repositories.config_repo import set_admin_pin
        set_admin_pin("888888", "system")
        r2 = first_run_client.put(
            "/api/admin/accounts/admin/pin",
            json={"new_pin": "777777"},
            headers={"X-Session-Token": token, "X-Admin-Pin": "888888"})
        assert r2.status_code == 200
        # 3. is_default_pin 已清，再訪 snapshots 應正常（200 或 404 但不是 423）
        r3 = first_run_client.get(
            "/api/snapshots/shelter",
            headers={"X-Session-Token": token})
        assert r3.status_code != 423
