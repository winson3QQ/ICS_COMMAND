"""
unit/test_auth_service.py — auth/service.py session 管理測試

Session 現在以 SQLite 持久化，每個測試透過 tmp_db 取得獨立 DB。
"""

import pytest

pytestmark = pytest.mark.unit


class TestCreateSession:
    def test_returns_uuid_string(self, tmp_db):
        from auth.service import create_session

        token = create_session({"username": "alice", "role": "operator"})
        assert isinstance(token, str)
        assert len(token) == 43  # urlsafe 32-byte session token

    def test_session_stored_in_db(self, tmp_db):
        from auth.service import create_session, get_session

        token = create_session({"username": "alice", "role": "operator"})
        sess = get_session(token)
        assert sess is not None
        assert sess["username"] == "alice"

    def test_unique_tokens(self, tmp_db):
        from auth.service import create_session

        t1 = create_session({"username": "u1", "role": "operator"})
        t2 = create_session({"username": "u2", "role": "operator"})
        assert t1 != t2


class TestGetSession:
    def test_valid_token(self, tmp_db):
        from auth.service import create_session, get_session

        token = create_session({"username": "bob", "role": "commander"})
        sess = get_session(token)
        assert sess is not None
        assert sess["username"] == "bob"

    def test_invalid_token(self, tmp_db):
        from auth.service import get_session

        assert get_session("nonexistent-token") is None

    def test_expired_session(self, tmp_db):
        from auth.service import create_session, get_session
        from core.database import get_conn

        token = create_session({"username": "x", "role": "operator"})
        # 直接把 last_active 設到很久以前，模擬過期
        conn = get_conn()
        conn.execute("UPDATE sessions SET last_active = '2000-01-01T00:00:00Z' WHERE token = ?", (token,))
        conn.commit()
        conn.close()
        assert get_session(token) is None
        # 過期 session 應被清除
        conn = get_conn()
        row = conn.execute("SELECT 1 FROM sessions WHERE token = ?", (token,)).fetchone()
        conn.close()
        assert row is None


class TestDestroySession:
    def test_removes_session(self, tmp_db):
        from auth.service import create_session, destroy_session, get_session

        token = create_session({"username": "eve", "role": "operator"})
        result = destroy_session(token)
        assert result is not None
        assert get_session(token) is None

    def test_nonexistent_returns_none(self, tmp_db):
        from auth.service import destroy_session

        assert destroy_session("ghost-token") is None


class TestSessionRemaining:
    def test_fresh_session_has_remaining(self, tmp_db):
        from auth.service import create_session, session_remaining

        token = create_session({"username": "u", "role": "operator"})
        rem = session_remaining(token)
        assert rem > 0

    def test_nonexistent_returns_zero(self, tmp_db):
        from auth.service import session_remaining

        assert session_remaining("ghost") == 0
