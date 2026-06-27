# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
unit/test_mtls_cert_binding.py — #275 mTLS cert-bound session（後端 wave 1）

涵蓋：client cert header helpers、create_session 存 cert_cn、check_session 的
cert binding（旗標開/關、符合/不符/legacy 無 cert）。
"""

from types import SimpleNamespace

import pytest
from starlette.datastructures import Headers

pytestmark = pytest.mark.unit


def _req(cert_cn=None, cert_verify=None, ip="10.0.0.5", ua="pytest"):
    """造一個帶（或不帶）mTLS 反代 header 的假 Request。"""
    h = {}
    if cert_cn is not None:
        h["x-client-cert-cn"] = cert_cn
    if cert_verify is not None:
        h["x-client-cert-verify"] = cert_verify
    if ua is not None:
        h["user-agent"] = ua
    return SimpleNamespace(headers=Headers(h), client=SimpleNamespace(host=ip))


class TestCertHelpers:
    def test_cn_extracted_and_trimmed(self):
        from auth.service import client_cert_cn

        assert client_cert_cn(_req(cert_cn=" alice-tablet ")) == "alice-tablet"

    def test_cn_none_when_absent_or_no_request(self):
        from auth.service import client_cert_cn

        assert client_cert_cn(_req()) is None
        assert client_cert_cn(None) is None

    def test_verified_only_on_success(self):
        from auth.service import client_cert_verified

        assert client_cert_verified(_req(cert_verify="SUCCESS")) is True
        assert client_cert_verified(_req(cert_verify="success")) is True
        assert client_cert_verified(_req(cert_verify="FAILED")) is False
        assert client_cert_verified(_req()) is False
        assert client_cert_verified(None) is False


class TestCreateSessionStoresCertCn:
    def test_cert_cn_persisted(self, tmp_db):
        from auth.service import create_session
        from core.database import get_conn

        token = create_session({"username": "a", "role": "operator"}, cert_cn="dev-1")
        conn = get_conn()
        row = conn.execute("SELECT cert_cn FROM sessions WHERE token=?", (token,)).fetchone()
        conn.close()
        assert row[0] == "dev-1"

    def test_cert_cn_null_by_default(self, tmp_db):
        from auth.service import create_session
        from core.database import get_conn

        token = create_session({"username": "a", "role": "operator"})
        conn = get_conn()
        row = conn.execute("SELECT cert_cn FROM sessions WHERE token=?", (token,)).fetchone()
        conn.close()
        assert row[0] is None


class TestCheckSessionCertBinding:
    def test_flag_off_ignores_cert(self, tmp_db, monkeypatch):
        import core.config as config

        monkeypatch.setattr(config, "ICS_MTLS_REQUIRED", False)
        from auth.service import check_session, create_session

        token = create_session({"username": "a", "role": "operator"})
        sess, failure = check_session(token, request=_req())  # 無 cert 也通
        assert failure is None and sess is not None

    def test_flag_on_matching_cert_ok(self, tmp_db, monkeypatch):
        import core.config as config

        monkeypatch.setattr(config, "ICS_MTLS_REQUIRED", True)
        from auth.service import check_session, create_session
        from repositories.account_cert_repo import account_id_for_username, bind_cert
        from repositories.account_repo import create_account

        # wave 3：per-device 以 account_certs 為 SoT；session 的 cert_cn 須有 active 綁定
        create_account("a", "123456", operator="system")
        bind_cert(account_id_for_username("a"), "dev-1", None, "system")
        token = create_session({"username": "a", "role": "operator"}, cert_cn="dev-1")
        sess, failure = check_session(token, request=_req(cert_cn="dev-1", cert_verify="SUCCESS"))
        assert failure is None and sess is not None

    def test_flag_on_cert_mismatch_rejected(self, tmp_db, monkeypatch):
        import core.config as config
        from auth.service import EVENT_BINDING_MISMATCH_CERT, check_session, create_session

        monkeypatch.setattr(config, "ICS_MTLS_REQUIRED", True)
        token = create_session({"username": "a", "role": "operator"}, cert_cn="dev-1")
        sess, failure = check_session(token, request=_req(cert_cn="evil-device", cert_verify="SUCCESS"))
        assert sess is None
        assert failure and failure["event"] == EVENT_BINDING_MISMATCH_CERT

    def test_flag_on_request_none_skips_cert_check(self, tmp_db, monkeypatch):
        """request-less（內部可信呼叫，無 cert 可出示）不套 per-request cert 防護，
        避免背景/狀態驗證被誤殺（code-review finding）。"""
        import core.config as config

        monkeypatch.setattr(config, "ICS_MTLS_REQUIRED", True)
        from auth.service import check_session, create_session

        token = create_session({"username": "a", "role": "operator"}, cert_cn="dev-1")
        sess, failure = check_session(token, request=None)
        assert failure is None and sess is not None

    def test_flag_on_legacy_session_without_cert_rejected(self, tmp_db, monkeypatch):
        """旗標開啟前建立（cert_cn 為空）的 session → 強制帶憑證重新登入。"""
        import core.config as config
        from auth.service import EVENT_BINDING_MISMATCH_CERT, check_session, create_session

        monkeypatch.setattr(config, "ICS_MTLS_REQUIRED", True)
        token = create_session({"username": "a", "role": "operator"})  # 無 cert_cn
        sess, failure = check_session(token, request=_req(cert_cn="dev-1", cert_verify="SUCCESS"))
        assert sess is None
        assert failure and failure["event"] == EVENT_BINDING_MISMATCH_CERT


class TestBootstrapRelax:
    """#306：全新部署 bootstrap 窗口放行未綁定 session（mTLS=true 下解雞生蛋）。"""

    def _single_admin(self):
        from repositories.account_repo import create_account

        create_account("admin", "123456", operator="system")  # 唯一帳號、零綁定

    def test_bootstrap_allows_unbound_session_with_verified_cert(self, tmp_db, monkeypatch):
        import core.config as config

        monkeypatch.setattr(config, "ICS_MTLS_REQUIRED", True)
        from auth.service import check_session, create_session

        self._single_admin()
        # session 綁了出示的證（login 暫綁），但該證尚未在 account_certs 綁定
        token = create_session({"username": "admin", "role": "sysadmin"}, cert_cn="bootstrap-dev")
        sess, failure = check_session(token, request=_req(cert_cn="bootstrap-dev", cert_verify="SUCCESS"))
        assert failure is None and sess is not None  # bootstrap 放行

    def test_bootstrap_needs_verified_cert(self, tmp_db, monkeypatch):
        """放行仍要求 nginx CA 已驗（X-Client-Cert-Verify=SUCCESS）；缺 → 拒。"""
        import core.config as config
        from auth.service import EVENT_BINDING_MISMATCH_CERT, check_session, create_session

        monkeypatch.setattr(config, "ICS_MTLS_REQUIRED", True)
        self._single_admin()
        token = create_session({"username": "admin", "role": "sysadmin"}, cert_cn="bootstrap-dev")
        sess, failure = check_session(token, request=_req(cert_cn="bootstrap-dev"))  # 無 verify
        assert sess is None and failure["event"] == EVENT_BINDING_MISMATCH_CERT

    def test_window_closed_after_first_bind_rejects_unbound(self, tmp_db, monkeypatch):
        """已有 active 綁定（窗口關）→ 未綁定的 session 即使出示 CA 證也拒。"""
        import core.config as config
        from auth.service import EVENT_BINDING_MISMATCH_CERT, check_session, create_session
        from repositories.account_cert_repo import account_id_for_username, bind_cert

        monkeypatch.setattr(config, "ICS_MTLS_REQUIRED", True)
        self._single_admin()
        bind_cert(account_id_for_username("admin"), "other-dev", None, "system")  # active>0 → 關窗
        token = create_session({"username": "admin", "role": "sysadmin"}, cert_cn="bootstrap-dev")
        sess, failure = check_session(token, request=_req(cert_cn="bootstrap-dev", cert_verify="SUCCESS"))
        assert sess is None and failure["event"] == EVENT_BINDING_MISMATCH_CERT
