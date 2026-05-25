"""
tests/security/test_session_edge.py — Session 超時邊界測試

涵蓋：
  - 超時判斷邊界（just under / just over SESSION_TIMEOUT）
  - heartbeat 延長 session
  - 過期 session 從 DB 清除
  - 登出後 token 失效
  - 同帳號多 session 並存

實作備忘：
  check_and_touch 判斷條件是 now - last_active > SESSION_TIMEOUT（嚴格大於）
  → 恰好等於 timeout 的 session 仍算有效
"""

import pytest
from unittest.mock import patch
import time


# ─────────────────────────────────────────────────────────────────
# 超時邊界
# ─────────────────────────────────────────────────────────────────

class TestSessionTimeout:
    def test_session_valid_just_before_timeout(self, tmp_db, monkeypatch):
        """SESSION_TIMEOUT - 1 秒：session 仍有效"""
        from auth import service as svc
        from core.config import IDLE_TIMEOUT
        fake_account = {"username": "u1", "role": "operator", "display_name": "U1"}
        token = svc.create_session(fake_account)

        # 偽造 last_active 為「差 1 秒就到 timeout」
        offset = IDLE_TIMEOUT - 1
        _patch_last_active(tmp_db, token, offset)

        result = svc.check_and_touch(token)
        assert result is not None, "差 1 秒前 session 應仍有效"

    def test_session_expired_just_over_timeout(self, tmp_db):
        """SESSION_TIMEOUT + 1 秒：session 應過期"""
        from auth import service as svc
        from core.config import IDLE_TIMEOUT
        fake_account = {"username": "u2", "role": "operator", "display_name": "U2"}
        token = svc.create_session(fake_account)

        _patch_last_active(tmp_db, token, IDLE_TIMEOUT + 1)

        result = svc.check_and_touch(token)
        assert result is None, "超過 timeout 1 秒應回傳 None"

    def test_expired_session_deleted_from_db(self, tmp_db):
        """過期的 session 在 check_and_touch 時從 DB 刪除"""
        from auth import service as svc
        from core.config import IDLE_TIMEOUT
        from core.database import get_conn
        fake_account = {"username": "u3", "role": "operator", "display_name": "U3"}
        token = svc.create_session(fake_account)
        _patch_last_active(tmp_db, token, IDLE_TIMEOUT + 10)

        svc.check_and_touch(token)  # 觸發過期刪除

        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE token=?", (token,)
            ).fetchone()
        assert row is None, "過期 session 應從 DB 移除"

    def test_session_remaining_zero_after_expiry(self, tmp_db):
        """過期後 session_remaining 回傳 0"""
        from auth import service as svc
        from core.config import IDLE_TIMEOUT
        token = svc.create_session({"username": "u4", "role": "operator", "display_name": "U4"})
        _patch_last_active(tmp_db, token, IDLE_TIMEOUT + 60)

        remaining = svc.session_remaining(token)
        assert remaining == 0


# ─────────────────────────────────────────────────────────────────
# Heartbeat 延長 Session
# ─────────────────────────────────────────────────────────────────

class TestSessionHeartbeat:
    def test_heartbeat_via_api_extends_session(self, client):
        """GET /api/auth/heartbeat 刷新 last_active → session 延長"""
        from core.database import get_conn
        # 登入拿 token
        r = client.post("/api/auth/login", json={"username": "admin", "pin": "1234"})
        token = r.json()["session_id"]

        # 讀目前 last_active
        with get_conn() as conn:
            before = conn.execute(
                "SELECT last_active FROM sessions WHERE token=?", (token,)
            ).fetchone()["last_active"]

        time.sleep(1.1)  # 等 1 秒讓時間戳有差

        # 打 heartbeat
        client.get("/api/auth/heartbeat", headers={"X-Session-Token": token})

        with get_conn() as conn:
            after = conn.execute(
                "SELECT last_active FROM sessions WHERE token=?", (token,)
            ).fetchone()["last_active"]

        assert after > before, "heartbeat 應更新 last_active"


# ─────────────────────────────────────────────────────────────────
# 登出後 Token 失效
# ─────────────────────────────────────────────────────────────────

class TestSessionDestroy:
    def test_logout_invalidates_token(self, client):
        """登出後原 token 打 /api/auth/me 應得 401"""
        r = client.post("/api/auth/login", json={"username": "admin", "pin": "1234"})
        token = r.json()["session_id"]

        client.post("/api/auth/logout", headers={"X-Session-Token": token})

        r2 = client.get("/api/auth/me", headers={"X-Session-Token": token})
        assert r2.status_code == 401

    def test_same_user_can_have_multiple_sessions(self, client):
        """同一帳號可同時持有多個 session（多裝置場景）"""
        r1 = client.post("/api/auth/login", json={"username": "admin", "pin": "1234"})
        r2 = client.post("/api/auth/login", json={"username": "admin", "pin": "1234"})
        t1 = r1.json()["session_id"]
        t2 = r2.json()["session_id"]
        assert t1 != t2, "兩次登入應產生不同 token"

        # 兩個 token 都有效
        assert client.get("/api/auth/me", headers={"X-Session-Token": t1}).status_code == 200
        assert client.get("/api/auth/me", headers={"X-Session-Token": t2}).status_code == 200

    def test_logout_one_session_does_not_affect_other(self, client):
        """登出其中一個 session 不影響另一個"""
        r1 = client.post("/api/auth/login", json={"username": "admin", "pin": "1234"})
        r2 = client.post("/api/auth/login", json={"username": "admin", "pin": "1234"})
        t1 = r1.json()["session_id"]
        t2 = r2.json()["session_id"]

        client.post("/api/auth/logout", headers={"X-Session-Token": t1})

        # t1 失效，t2 仍有效
        assert client.get("/api/auth/me", headers={"X-Session-Token": t1}).status_code == 401
        assert client.get("/api/auth/me", headers={"X-Session-Token": t2}).status_code == 200


# ─────────────────────────────────────────────────────────────────
# 工具函式
# ─────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────
# 嚴格邊界（Strict Boundary）
# ─────────────────────────────────────────────────────────────────

class TestSessionStrictBoundary:
    def test_heartbeat_api_remaining_refreshes(self, client):
        """heartbeat 後 remaining 不繼續遞減（應 ≥ 第一次呼叫的值 - 1s 容差）"""
        r = client.post("/api/auth/login", json={"username": "admin", "pin": "1234"})
        token = r.json()["session_id"]

        first = client.get("/api/auth/heartbeat", headers={"X-Session-Token": token})
        assert first.status_code == 200
        rem1 = first.json()["remaining"]

        import time
        time.sleep(1.1)

        second = client.get("/api/auth/heartbeat", headers={"X-Session-Token": token})
        assert second.status_code == 200
        rem2 = second.json()["remaining"]

        # heartbeat 刷新 last_active → remaining 不應低於 rem1 - 1（正常遞減容差）
        assert rem2 >= rem1 - 1, "heartbeat 應刷新 session，remaining 不應大幅下降"

    def test_expired_session_heartbeat_returns_401(self, tmp_db, client):
        """已過期的 session 呼叫 heartbeat API → 401（HTTP 層驗證）"""
        from auth import service as svc
        from core.config import IDLE_TIMEOUT
        token = svc.create_session({"username": "expired_hb", "role": "operator",
                                    "display_name": "Expired"})
        _patch_last_active(tmp_db, token, IDLE_TIMEOUT + 2)

        r = client.get("/api/auth/heartbeat", headers={"X-Session-Token": token})
        assert r.status_code == 401


def _patch_last_active(tmp_db, token: str, seconds_ago: int):
    """直接修改 DB，讓 last_active = 現在 - seconds_ago 秒"""
    from datetime import datetime, timezone, timedelta
    from core.database import get_conn
    old_time = (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
                ).strftime('%Y-%m-%dT%H:%M:%SZ')
    with get_conn() as conn:
        conn.execute(
            "UPDATE sessions SET last_active=? WHERE token=?",
            (old_time, token)
        )
        conn.commit()
