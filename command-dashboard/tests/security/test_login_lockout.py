"""
tests/security/test_login_lockout.py — C1-A 登入鎖定 + IP rate limit 測試

對應規格：commercialization §C1-A
- 5 次失敗 → 鎖定 15 分鐘
- 10 req/IP/min → 429
"""

import pytest
from datetime import datetime, timezone


# ─────────────────────────────────────────────────────────────────
# Account-level lockout（5 fails / 15 min）
# ─────────────────────────────────────────────────────────────────

class TestAccountLockout:
    def test_fifth_wrong_pin_locks_account(self, tmp_db):
        from repositories.account_repo import (
            create_account, verify_login, LOCKOUT_THRESHOLD
        )
        create_account("alice", "right", "操作員", "", "operator")
        # 前 4 次：bad_pin 但不鎖
        for i in range(LOCKOUT_THRESHOLD - 1):
            acc, reason = verify_login("alice", "wrong")
            assert acc is None and reason == "bad_pin", f"iter {i}"
        # 第 5 次：bad_pin 觸發鎖定，回傳 reason="locked"
        acc, reason = verify_login("alice", "wrong")
        assert acc is None and reason == "locked"

    def test_locked_account_rejects_correct_pin(self, tmp_db):
        """鎖定中即使輸入正確 PIN 也拒絕（防止鎖定中暴力）。"""
        from repositories.account_repo import create_account, verify_login
        create_account("bob", "right", "操作員", "", "operator")
        for _ in range(5):
            verify_login("bob", "wrong")
        acc, reason = verify_login("bob", "right")  # 正確 PIN
        assert acc is None and reason == "locked"

    def test_successful_login_resets_failed_count(self, tmp_db):
        """成功登入清空失敗計數。"""
        from repositories.account_repo import create_account, verify_login
        from core.database import get_conn
        create_account("carol", "right", "操作員", "", "operator")
        # 累積 3 次失敗
        for _ in range(3):
            verify_login("carol", "wrong")
        # 成功一次
        acc, reason = verify_login("carol", "right")
        assert reason == "ok"
        # DB 確認失敗計數歸零
        with get_conn() as conn:
            row = conn.execute(
                "SELECT failed_login_count, locked_until FROM accounts WHERE username='carol'"
            ).fetchone()
        assert row["failed_login_count"] == 0
        assert row["locked_until"] is None

    def test_unlock_account_clears_lock(self, tmp_db):
        """unlock_account 管理員手動解鎖。"""
        from repositories.account_repo import (
            create_account, verify_login, unlock_account
        )
        create_account("dan", "right", "操作員", "", "operator")
        for _ in range(5):
            verify_login("dan", "wrong")
        # 確認鎖定
        acc, reason = verify_login("dan", "right")
        assert reason == "locked"
        # 解鎖
        assert unlock_account("dan", operator="admin") is True
        # 解鎖後可正常登入
        acc, reason = verify_login("dan", "right")
        assert acc is not None and reason == "ok"

    def test_lockout_expires_after_duration(self, tmp_db, monkeypatch):
        """鎖定 15 分鐘後自動解鎖（透過 monkeypatch 過去時間模擬）。"""
        from repositories import account_repo
        from repositories.account_repo import create_account, verify_login
        from core.database import get_conn
        create_account("eve", "right", "操作員", "", "operator")
        for _ in range(5):
            verify_login("eve", "wrong")
        # 直接改 DB 的 locked_until 為過去時間（模擬 15 分鐘已過）
        with get_conn() as conn:
            conn.execute(
                "UPDATE accounts SET locked_until=? WHERE username='eve'",
                ("2020-01-01T00:00:00Z",))
            conn.commit()
        # 應可正常登入
        acc, reason = verify_login("eve", "right")
        assert acc is not None and reason == "ok"


# ─────────────────────────────────────────────────────────────────
# Login API 行為（透過 TestClient 驗證 HTTP 層）
# ─────────────────────────────────────────────────────────────────

class TestLoginAPILockout:
    def test_locked_returns_423(self, client):
        """鎖定後 API 回 423 Locked。"""
        # 預設 admin/1234 已存在
        for _ in range(5):
            client.post("/api/auth/login", json={"username": "admin", "pin": "wrong"})
        r = client.post("/api/auth/login", json={"username": "admin", "pin": "1234"})
        assert r.status_code == 423

    def test_no_user_returns_401_not_404(self, client):
        """不存在的帳號回 401（不洩漏帳號是否存在）。"""
        r = client.post("/api/auth/login", json={"username": "ghost", "pin": "1234"})
        assert r.status_code == 401


# ─────────────────────────────────────────────────────────────────
# IP rate limit（10 req / IP / min）
# ─────────────────────────────────────────────────────────────────

class TestIPRateLimit:
    def test_eleventh_request_returns_429(self, client):
        """同一 IP 第 11 次 login 應回 429。"""
        # 前 10 次（無論成敗）都不該被 rate limit
        for i in range(10):
            r = client.post("/api/auth/login", json={"username": "x", "pin": "y"})
            assert r.status_code != 429, f"iter {i} 不應被 rate limit"
        # 第 11 次：429
        r = client.post("/api/auth/login", json={"username": "x", "pin": "y"})
        assert r.status_code == 429
        assert "Retry-After" in r.headers

    def test_rate_limit_only_applies_to_login(self, client):
        """非 /api/auth/login 不受 rate limit（用 /api/health 驗證）。"""
        for _ in range(20):
            r = client.get("/api/health")
            assert r.status_code != 429
