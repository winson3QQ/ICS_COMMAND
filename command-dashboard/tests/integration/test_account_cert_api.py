"""
integration/test_account_cert_api.py — #275 wave 3：裝置憑證綁定管理 API

/api/admin/accounts/{username}/certs（GET/POST/DELETE，sysadmin only）。
涵蓋：綁定→列出→撤銷生命週期、重複 active CN 409、撤銷越權隔離、未授權 401、找不到帳號 404。
"""
import pytest

pytestmark = pytest.mark.integration


def _mk_account(client, auth, username="alice", role="操作員"):
    r = client.post("/api/admin/accounts",
                    json={"username": username, "pin": "123456", "role": role},
                    headers=auth)
    assert r.status_code in (200, 201), r.text
    return username


class TestCertLifecycle:
    def test_bind_list_revoke(self, client, auth):
        _mk_account(client, auth, "alice")
        # 綁定
        r = client.post("/api/admin/accounts/alice/certs",
                        json={"cert_cn": "alice-phone", "label": "私人手機"}, headers=auth)
        assert r.status_code == 200, r.text
        cert_id = r.json()["id"]
        assert r.json()["status"] == "active"
        # 列出
        r = client.get("/api/admin/accounts/alice/certs", headers=auth)
        assert r.status_code == 200 and len(r.json()) == 1
        # 撤銷
        r = client.delete(f"/api/admin/accounts/alice/certs/{cert_id}", headers=auth)
        assert r.status_code == 200 and r.json()["status"] == "revoked"
        # 撤銷後仍在列表（稽核留痕），但 status=revoked
        r = client.get("/api/admin/accounts/alice/certs", headers=auth)
        assert r.json()[0]["status"] == "revoked"

    def test_multiple_devices(self, client, auth):
        _mk_account(client, auth, "bob")
        for dev in ("bob-phone", "bob-tablet"):
            r = client.post("/api/admin/accounts/bob/certs",
                            json={"cert_cn": dev}, headers=auth)
            assert r.status_code == 200
        assert len(client.get("/api/admin/accounts/bob/certs", headers=auth).json()) == 2

    def test_duplicate_active_cn_conflict(self, client, auth):
        _mk_account(client, auth, "carol")
        client.post("/api/admin/accounts/carol/certs", json={"cert_cn": "dup"}, headers=auth)
        r = client.post("/api/admin/accounts/carol/certs", json={"cert_cn": "dup"}, headers=auth)
        assert r.status_code == 409

    def test_revoke_cross_account_isolated(self, client, auth):
        _mk_account(client, auth, "dan")
        _mk_account(client, auth, "erin")
        cid = client.post("/api/admin/accounts/dan/certs",
                          json={"cert_cn": "dan-dev"}, headers=auth).json()["id"]
        # 以 erin 的路徑撤 dan 的 cert_id → 404（帳號隔離）
        r = client.delete(f"/api/admin/accounts/erin/certs/{cid}", headers=auth)
        assert r.status_code == 404

    def test_unknown_account_404(self, client, auth):
        r = client.get("/api/admin/accounts/ghost/certs", headers=auth)
        assert r.status_code == 404


class TestOnlineIssue:
    def test_issue_503_when_step_ca_not_configured(self, client, auth):
        """#275 wave B-2：未配置 step-ca → 線上發證回 503（改走離線簽 + 僅綁定）。"""
        _mk_account(client, auth, "frank")
        r = client.post("/api/admin/accounts/frank/certs/issue",
                        json={"cert_cn": "frank-pc"}, headers=auth)
        assert r.status_code == 503

    def test_issue_success_returns_p12_and_binds(self, client, auth, monkeypatch):
        """配置齊備 + daemon 簽成功 → 回 p12（x-pkcs12）+ 自動綁定 CN。"""
        import core.config as config
        import services.cert_issuance as ci
        monkeypatch.setattr(config, "step_ca_configured", lambda: True)
        monkeypatch.setattr(ci, "issue_p12", lambda cn: b"PKCS12-FAKE-BYTES")
        _mk_account(client, auth, "grace")
        r = client.post("/api/admin/accounts/grace/certs/issue",
                        json={"cert_cn": "grace-laptop", "label": "工作機"}, headers=auth)
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "application/x-pkcs12"
        assert "grace-laptop.p12" in r.headers.get("content-disposition", "")
        assert r.content == b"PKCS12-FAKE-BYTES"
        # 已自動綁定
        certs = client.get("/api/admin/accounts/grace/certs", headers=auth).json()
        assert any(c["cert_cn"] == "grace-laptop" and c["status"] == "active" for c in certs)

    def test_issue_409_when_cn_already_active(self, client, auth, monkeypatch):
        import core.config as config
        import services.cert_issuance as ci
        monkeypatch.setattr(config, "step_ca_configured", lambda: True)
        monkeypatch.setattr(ci, "issue_p12", lambda cn: b"X")
        _mk_account(client, auth, "heidi")
        client.post("/api/admin/accounts/heidi/certs", json={"cert_cn": "dupe-cn"}, headers=auth)
        r = client.post("/api/admin/accounts/heidi/certs/issue",
                        json={"cert_cn": "dupe-cn"}, headers=auth)
        assert r.status_code == 409

    def test_issue_502_on_issuance_error(self, client, auth, monkeypatch):
        import core.config as config
        import services.cert_issuance as ci
        monkeypatch.setattr(config, "step_ca_configured", lambda: True)
        def _boom(cn): raise ci.CertIssuanceError("daemon 不可達")
        monkeypatch.setattr(ci, "issue_p12", _boom)
        _mk_account(client, auth, "ivan")
        r = client.post("/api/admin/accounts/ivan/certs/issue",
                        json={"cert_cn": "ivan-pc"}, headers=auth)
        assert r.status_code == 502


class TestAuthz:
    def test_requires_auth(self, client):
        assert client.get("/api/admin/accounts/alice/certs").status_code == 401
        assert client.post("/api/admin/accounts/alice/certs",
                           json={"cert_cn": "x"}).status_code == 401
        assert client.post("/api/admin/accounts/alice/certs/issue",
                           json={"cert_cn": "x"}).status_code == 401
