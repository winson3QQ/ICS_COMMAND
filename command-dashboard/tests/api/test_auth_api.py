# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
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
        r = client.get("/api/exercises", headers={"X-Session-Token": "not-a-real-token"})
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
        r = client.post("/api/auth/logout", headers={"X-Session-Token": session_token})
        assert r.status_code == 200
        # logout 後同一 token 應無效
        r2 = client.get("/api/exercises", headers=auth)
        assert r2.status_code == 401


class TestSessionCookie:
    """#293：session token 改放 httpOnly cookie（JS 讀不到 → XSS 竊不走）。階段1 相容期 header/cookie 雙認。"""

    def test_login_sets_httponly_cookie(self, client):
        r = client.post("/api/auth/login", json={"username": "admin", "pin": "1234"})
        assert r.status_code == 200
        raw = r.headers.get("set-cookie", "").lower()
        assert "cmd_session=" in raw
        assert "httponly" in raw  # JS document.cookie 讀不到 → XSS 竊不走
        assert "samesite=strict" in raw  # 擋跨站自動帶 cookie

    def test_login_cookie_is_session_scoped(self, client):
        # #293 階段2：session cookie（無 max-age/expires）→ 關瀏覽器即失效，維持「關分頁＝登出」語意
        r = client.post("/api/auth/login", json={"username": "admin", "pin": "1234"})
        raw = r.headers.get("set-cookie", "").lower()
        assert "max-age" not in raw and "expires" not in raw

    def test_heartbeat_returns_display_name(self, client, auth):
        # #293 階段2：新分頁/reload 靠 heartbeat（cookie 認）還原顯示態 → 須帶 display_name 補徽章
        r = client.get("/api/auth/heartbeat", headers=auth)
        assert r.status_code == 200
        assert r.json().get("display_name")

    def test_cookie_only_auth_passes(self, client, session_token):
        # 只帶 cookie、不帶 X-Session-Token header → 仍認證通過（cookie fallback，階段2 前端不帶 header）
        client.cookies.set("cmd_session", session_token)  # 覆蓋 jar（login 的 Secure cookie 不隨 http 送）
        r = client.get("/api/exercises")
        assert r.status_code == 200

    def test_header_still_works_compat(self, client, auth):
        # 相容期：header 路徑不變（既有前端零改照跑）
        r = client.get("/api/exercises", headers=auth)
        assert r.status_code == 200

    def test_bogus_cookie_rejected(self, client):
        client.cookies.set("cmd_session", "not-a-real-token")
        r = client.get("/api/exercises")
        assert r.status_code == 401

    def test_logout_clears_cookie(self, client, session_token):
        r = client.post("/api/auth/logout", headers={"X-Session-Token": session_token})
        assert r.status_code == 200
        raw = r.headers.get("set-cookie", "").lower()
        assert "cmd_session=" in raw  # delete_cookie 覆蓋一個過期/空的 cmd_session
