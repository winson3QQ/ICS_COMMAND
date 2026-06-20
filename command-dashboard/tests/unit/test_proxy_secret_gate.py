"""
unit/test_proxy_secret_gate.py — #280 紅隊修補：nginx↔後端共享密鑰

紅隊實證：內網直打後端 :8000 偽造 X-Client-Cert-* 可繞 mTLS 拿 sysadmin。修法＝後端
只在請求帶相符 X-Proxy-Auth（只有 nginx 知道）時才採信 cert header。

涵蓋：未配置 secret = back-compat（信任）；配置後缺/錯 secret → cert header 不採信；
相符 → 採信；等時比較。
"""
from types import SimpleNamespace

import pytest
from starlette.datastructures import Headers

pytestmark = pytest.mark.unit


def _req(verify=None, cn=None, proxy_auth=None):
    h = {}
    if verify is not None:
        h["x-client-cert-verify"] = verify
    if cn is not None:
        h["x-client-cert-cn"] = cn
    if proxy_auth is not None:
        h["x-proxy-auth"] = proxy_auth
    return SimpleNamespace(headers=Headers(h), client=SimpleNamespace(host="1.2.3.4"))


class TestBackCompat:
    def test_no_secret_configured_trusts_headers(self, monkeypatch):
        import core.config as config
        from auth.service import client_cert_cn, client_cert_verified
        monkeypatch.setattr(config, "ICS_PROXY_SHARED_SECRET", "")
        r = _req(verify="SUCCESS", cn="dev-1")  # 無 X-Proxy-Auth
        assert client_cert_verified(r) is True
        assert client_cert_cn(r) == "dev-1"


class TestSecretGate:
    def test_configured_but_missing_proxy_auth_untrusted(self, monkeypatch):
        """紅隊攻擊 5：偽造 cert header 但沒 secret → 不採信。"""
        import core.config as config
        from auth.service import client_cert_cn, client_cert_verified
        monkeypatch.setattr(config, "ICS_PROXY_SHARED_SECRET", "s3cr3t")
        r = _req(verify="SUCCESS", cn="cmd-tablet")  # 偽造但無 X-Proxy-Auth
        assert client_cert_verified(r) is False
        assert client_cert_cn(r) is None

    def test_wrong_secret_untrusted(self, monkeypatch):
        import core.config as config
        from auth.service import client_cert_cn, client_cert_verified
        monkeypatch.setattr(config, "ICS_PROXY_SHARED_SECRET", "s3cr3t")
        r = _req(verify="SUCCESS", cn="cmd-tablet", proxy_auth="guess")
        assert client_cert_verified(r) is False
        assert client_cert_cn(r) is None

    def test_matching_secret_trusted(self, monkeypatch):
        """合法經 nginx：nginx 注入相符 secret → 採信。"""
        import core.config as config
        from auth.service import client_cert_cn, client_cert_verified
        monkeypatch.setattr(config, "ICS_PROXY_SHARED_SECRET", "s3cr3t")
        r = _req(verify="SUCCESS", cn="cmd-tablet", proxy_auth="s3cr3t")
        assert client_cert_verified(r) is True
        assert client_cert_cn(r) == "cmd-tablet"

    def test_none_request_untrusted(self, monkeypatch):
        import core.config as config
        from auth.service import client_cert_cn, client_cert_verified
        monkeypatch.setattr(config, "ICS_PROXY_SHARED_SECRET", "s3cr3t")
        assert client_cert_verified(None) is False
        assert client_cert_cn(None) is None
