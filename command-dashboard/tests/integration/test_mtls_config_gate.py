"""#290 M1：prod mTLS 組態 fail-fast 安全閘測試。

ICS_MTLS_REQUIRED=true 但 ICS_PROXY_SHARED_SECRET 未設時，_proxy_trusted() 會 fail-open
無條件信任 X-Client-Cert-* header（內網可偽造繞 mTLS）。此危險組合須啟動即拒。
測試直接驗 main._assert_safe_mtls_config（lifespan 啟動時呼叫）。
"""

import main
import pytest


def _set(monkeypatch, prod, mtls, secret):
    monkeypatch.setattr(main, "IS_PROD", prod)
    monkeypatch.setattr(main, "ICS_MTLS_REQUIRED", mtls)
    monkeypatch.setattr(main, "ICS_PROXY_SHARED_SECRET", secret)


def test_refuses_prod_mtls_without_proxy_secret(monkeypatch):
    _set(monkeypatch, True, True, "")
    with pytest.raises(RuntimeError, match="ICS_PROXY_SHARED_SECRET"):
        main._assert_safe_mtls_config()


def test_allows_prod_mtls_with_secret(monkeypatch):
    _set(monkeypatch, True, True, "x" * 32)
    main._assert_safe_mtls_config()  # 不應 raise


def test_allows_dev_without_secret(monkeypatch):
    # dev 不強制（mTLS 多在 prod 才開）
    _set(monkeypatch, False, True, "")
    main._assert_safe_mtls_config()


def test_allows_prod_without_mtls(monkeypatch):
    # 未開 mTLS 時 cert header 不被消費，無此風險
    _set(monkeypatch, True, False, "")
    main._assert_safe_mtls_config()
