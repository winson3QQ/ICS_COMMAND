from __future__ import annotations

from repositories.config_repo import set_admin_pin


class TestAdminEndpointProtection:
    def test_list_accounts_without_session_returns_401(self, client):
        set_admin_pin("999999", "test")
        r = client.get("/api/admin/accounts")
        assert r.status_code == 401

    def test_list_accounts_wrong_admin_pin_without_session_returns_401(self, client):
        set_admin_pin("999999", "test")
        r = client.get("/api/admin/accounts", headers={"X-Admin-PIN": "000000"})
        assert r.status_code == 401

    def test_list_accounts_empty_admin_pin_without_session_returns_401(self, client):
        set_admin_pin("999999", "test")
        r = client.get("/api/admin/accounts", headers={"X-Admin-PIN": ""})
        assert r.status_code == 401

    def test_create_account_without_session_returns_401(self, client):
        set_admin_pin("999999", "test")
        r = client.post(
            "/api/admin/accounts",
            json={
                "username": "hacker",
                "pin": "1234",
                "role": "操作員",
                "display_name": "",
                "role_detail": "operator",
            },
        )
        assert r.status_code == 401

    def test_delete_account_without_session_returns_401(self, client):
        set_admin_pin("999999", "test")
        r = client.delete("/api/admin/accounts/admin")
        assert r.status_code == 401

    def test_admin_pin_not_configured_still_requires_session(self, client):
        r = client.get("/api/admin/accounts")
        assert r.status_code == 401


class TestPiPushProtection:
    def test_pi_push_without_bearer_returns_401(self, client):
        r = client.post("/api/pi-push/shelter", json={"records": []})
        assert r.status_code == 401

    def test_pi_push_wrong_token_returns_403(self, hmac_client):
        c, sign = hmac_client
        body_bytes, hdrs = sign("POST", "/api/pi-push/shelter", {"records": []})
        hdrs["Authorization"] = "Bearer INVALID_TOKEN"
        r = c.post("/api/pi-push/shelter", content=body_bytes, headers=hdrs)
        assert r.status_code == 403


class TestTokenForging:
    def test_nonexistent_token_returns_401(self, client):
        r = client.get("/api/auth/me", headers={"X-Session-Token": "missing-token"})
        assert r.status_code == 401

    def test_sql_injection_in_token_returns_401(self, client):
        malicious = "' OR '1'='1'; DROP TABLE sessions; --"
        r = client.get("/api/auth/me", headers={"X-Session-Token": malicious})
        assert r.status_code == 401

    def test_very_long_token_returns_401(self, client):
        r = client.get("/api/auth/me", headers={"X-Session-Token": "A" * 10_000})
        assert r.status_code == 401

    def test_empty_token_returns_401(self, client):
        r = client.get("/api/auth/me", headers={"X-Session-Token": ""})
        assert r.status_code == 401

    def test_no_token_returns_401(self, client):
        r = client.get("/api/dashboard")
        assert r.status_code == 401


class TestOpenEndpointsByDesign:
    def test_snapshot_post_requires_hmac_not_session(self, client):
        r = client.post(
            "/api/snapshots",
            json={
                "v": 3,
                "type": "shelter",
                "snapshot_id": "bypass-check-001",
                "t": "2026-04-24T10:00:00Z",
                "src": "test",
            },
        )
        assert r.status_code == 401
        assert r.json().get("detail", {}).get("reason") == "no_sig"

    def test_snapshot_get_is_open_by_design(self, client):
        r = client.get("/api/snapshots/shelter")
        assert r.status_code == 200

    def test_health_is_open(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
