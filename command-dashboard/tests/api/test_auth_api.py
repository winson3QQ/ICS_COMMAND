"""
api/test_auth_api.py — 認證流程 HTTP 端對端測試

使用 FastAPI TestClient，測試完整的 HTTP 請求/回應。
"""

import pytest

pytestmark = pytest.mark.api


class TestLogin:
    def test_success(self, client):
        r = client.post("/api/auth/login", json={"username": "admin", "pin": "1234"})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert "session_id" in body
        assert body["username"] == "admin"

    def test_wrong_pin(self, client):
        r = client.post("/api/auth/login", json={"username": "admin", "pin": "0000"})
        assert r.status_code == 401

    def test_nonexistent_user(self, client):
        r = client.post("/api/auth/login", json={"username": "ghost", "pin": "1234"})
        assert r.status_code == 401

    def test_missing_fields_422(self, client):
        r = client.post("/api/auth/login", json={"username": "admin"})
        assert r.status_code == 422


class TestProtectedEndpoints:
    def test_no_token_returns_401(self, client):
        r = client.get("/api/exercises")
        assert r.status_code == 401

    def test_invalid_token_returns_401(self, client):
        r = client.get("/api/exercises",
                        headers={"X-Session-Token": "not-a-real-token"})
        assert r.status_code == 401

    def test_valid_token_passes(self, client, auth):
        r = client.get("/api/exercises", headers=auth)
        assert r.status_code == 200


class TestHeartbeat:
    def test_returns_remaining(self, client, auth):
        r = client.get("/api/auth/heartbeat", headers=auth)
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["remaining"] > 0
        assert body["username"] == "admin"

    def test_without_token_401(self, client):
        r = client.get("/api/auth/heartbeat")
        assert r.status_code == 401


class TestMe:
    def test_returns_user_info(self, client, auth):
        r = client.get("/api/auth/me", headers=auth)
        assert r.status_code == 200
        body = r.json()
        assert body["username"] == "admin"
        assert "role" in body
        assert "pin_hash" not in body
        assert "pin_salt" not in body


class TestLogout:
    def test_logout_invalidates_session(self, client, session_token, auth):
        r = client.post("/api/auth/logout",
                        headers={"X-Session-Token": session_token})
        assert r.status_code == 200
        # logout 後同一 token 應無效
        r2 = client.get("/api/exercises", headers=auth)
        assert r2.status_code == 401
