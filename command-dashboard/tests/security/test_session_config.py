"""
tests/security/test_session_config.py — Session 設定邊界測試

補充 test_session_edge.py（已覆蓋 timeout 邊界 / heartbeat / logout）。
本檔聚焦：
  - SESSION_TIMEOUT env 覆寫行為
  - cleanup_expired_sessions() 批量清除
  - 極短 timeout 場景
  - session 並發安全：同一 token 同時送多個請求
"""

import pytest
import threading
import time


# ─────────────────────────────────────────────────────────────────
# SESSION_TIMEOUT 環境變數覆寫
# ─────────────────────────────────────────────────────────────────

class TestSessionTimeoutConfig:
    def test_custom_short_timeout_expires_session(self, tmp_db, monkeypatch):
        """SESSION_TIMEOUT=2 秒：2 秒後 session 過期"""
        import core.config as cfg
        monkeypatch.setattr(cfg, "SESSION_TIMEOUT", 2)

        import auth.service as svc
        # monkeypatch 讓 service 使用新 timeout
        monkeypatch.setattr(svc, "SESSION_TIMEOUT", 2)

        token = svc.create_session({"username": "u1", "role": "op", "display_name": "U1"})
        assert svc.check_and_touch(token) is not None  # 建立後立刻有效

        # 直接改 DB last_active 為 3 秒前（超過 timeout=2）
        from datetime import datetime, timezone, timedelta
        from core.database import get_conn
        old = (datetime.now(timezone.utc) - timedelta(seconds=3)).strftime('%Y-%m-%dT%H:%M:%SZ')
        with get_conn() as conn:
            conn.execute("UPDATE sessions SET last_active=? WHERE token=?", (old, token))
            conn.commit()

        result = svc.check_and_touch(token)
        assert result is None, "SESSION_TIMEOUT=2s，3 秒後應過期"

    def test_zero_timeout_immediately_expires(self, tmp_db, monkeypatch):
        """SESSION_TIMEOUT=0：任何 session 立即過期（邊界值）"""
        import auth.service as svc
        monkeypatch.setattr(svc, "SESSION_TIMEOUT", 0)

        token = svc.create_session({"username": "u2", "role": "op", "display_name": "U2"})
        # 讓 DB 的 last_active 稍舊（1 秒前）
        from datetime import datetime, timezone, timedelta
        from core.database import get_conn
        old = (datetime.now(timezone.utc) - timedelta(seconds=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        with get_conn() as conn:
            conn.execute("UPDATE sessions SET last_active=? WHERE token=?", (old, token))
            conn.commit()

        result = svc.check_and_touch(token)
        assert result is None, "timeout=0 時 session 應立即過期"


# ─────────────────────────────────────────────────────────────────
# cleanup_expired_sessions()
# ─────────────────────────────────────────────────────────────────

class TestCleanupExpiredSessions:
    def test_cleanup_removes_expired_sessions(self, tmp_db):
        """cleanup_expired_sessions() 移除所有過期 session"""
        from auth.service import create_session, cleanup_expired_sessions
        from core.config import SESSION_TIMEOUT
        from datetime import datetime, timezone, timedelta
        from core.database import get_conn

        # 建立 3 個 session
        tokens = [
            create_session({"username": f"u{i}", "role": "op", "display_name": f"U{i}"})
            for i in range(3)
        ]

        # 把前 2 個設為過期
        old = (datetime.now(timezone.utc) - timedelta(seconds=SESSION_TIMEOUT + 60)
               ).strftime('%Y-%m-%dT%H:%M:%SZ')
        with get_conn() as conn:
            conn.execute("UPDATE sessions SET last_active=? WHERE token IN (?, ?)",
                         (old, tokens[0], tokens[1]))
            conn.commit()

        deleted = cleanup_expired_sessions()
        assert deleted == 2

        # 第 3 個仍存在
        with get_conn() as conn:
            remaining = conn.execute("SELECT COUNT(*) as c FROM sessions").fetchone()["c"]
        assert remaining == 1

    def test_cleanup_returns_zero_when_nothing_expired(self, tmp_db):
        """沒有過期 session 時 cleanup 回傳 0"""
        from auth.service import create_session, cleanup_expired_sessions
        create_session({"username": "fresh", "role": "op", "display_name": "Fresh"})
        deleted = cleanup_expired_sessions()
        assert deleted == 0


# ─────────────────────────────────────────────────────────────────
# 並發 Session 安全
# ─────────────────────────────────────────────────────────────────

class TestConcurrentSession:
    def test_concurrent_requests_with_same_token(self, client):
        """
        同一 token 同時發出多個請求（模擬前端 tab 並發）。
        所有請求應都通過（check_and_touch 不應競爭損壞 session）。
        """
        r = client.post("/api/auth/login", json={"username": "admin", "pin": "1234"})
        token = r.json()["session_id"]
        headers = {"X-Session-Token": token}

        results = []

        def call_me():
            resp = client.get("/api/auth/me", headers=headers)
            results.append(resp.status_code)

        threads = [threading.Thread(target=call_me) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 全部應回 200（不應因並發 UPDATE 而損壞 session）
        assert all(s == 200 for s in results), f"部分請求失敗：{results}"

    def test_session_touch_updates_last_active(self, client, tmp_db):
        """每次 check_and_touch 都更新 last_active（確認非 read-only）"""
        from core.database import get_conn
        from auth.service import create_session, check_and_touch

        token = create_session({"username": "u", "role": "op", "display_name": "U"})

        with get_conn() as conn:
            before = conn.execute(
                "SELECT last_active FROM sessions WHERE token=?", (token,)
            ).fetchone()["last_active"]

        time.sleep(1.1)
        check_and_touch(token)

        with get_conn() as conn:
            after = conn.execute(
                "SELECT last_active FROM sessions WHERE token=?", (token,)
            ).fetchone()["last_active"]

        assert after > before
