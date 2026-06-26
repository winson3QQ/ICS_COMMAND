# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
unit/test_account_cert_repo.py — #275 wave 3：per-device 裝置憑證綁定 + App 層撤銷

涵蓋：bind / list / revoke、唯一 active CN、cert_active_for_account（login 第二因子）、
is_cert_active（check_session 撤銷即時失效）、check_session 撤銷後 session 失效。
"""

from types import SimpleNamespace

import pytest
from starlette.datastructures import Headers

pytestmark = pytest.mark.unit


def _req(cert_cn=None, cert_verify=None, ip="10.0.0.5", ua="pytest"):
    h = {}
    if cert_cn is not None:
        h["x-client-cert-cn"] = cert_cn
    if cert_verify is not None:
        h["x-client-cert-verify"] = cert_verify
    if ua is not None:
        h["user-agent"] = ua
    return SimpleNamespace(headers=Headers(h), client=SimpleNamespace(host=ip))


def _mk_account(username="alice", role="操作員"):
    from repositories.account_cert_repo import account_id_for_username
    from repositories.account_repo import create_account

    create_account(username, "123456", role=role, operator="system")
    return {"username": username, "id": account_id_for_username(username)}


class TestBindRepo:
    def test_bind_then_list(self, tmp_db):
        from repositories.account_cert_repo import bind_cert, list_certs

        acct = _mk_account()
        rec = bind_cert(acct["id"], "alice-phone", "私人手機", "system")
        assert rec["cert_cn"] == "alice-phone" and rec["status"] == "active"
        rows = list_certs(acct["id"])
        assert len(rows) == 1 and rows[0]["label"] == "私人手機"

    def test_bind_multiple_devices_per_account(self, tmp_db):
        from repositories.account_cert_repo import bind_cert, list_certs

        acct = _mk_account()
        bind_cert(acct["id"], "alice-phone", None, "system")
        bind_cert(acct["id"], "alice-tablet", None, "system")
        assert len(list_certs(acct["id"])) == 2  # per-device：一帳號多裝置

    def test_duplicate_active_cn_rejected(self, tmp_db):
        from repositories.account_cert_repo import bind_cert

        acct = _mk_account()
        bind_cert(acct["id"], "dup-cn", None, "system")
        with pytest.raises(ValueError):
            bind_cert(acct["id"], "dup-cn", None, "system")

    def test_empty_cn_rejected(self, tmp_db):
        from repositories.account_cert_repo import bind_cert

        acct = _mk_account()
        with pytest.raises(ValueError):
            bind_cert(acct["id"], "   ", None, "system")


class TestRevoke:
    def test_revoke_flips_status_and_frees_cn(self, tmp_db):
        from repositories.account_cert_repo import bind_cert, revoke_cert

        acct = _mk_account()
        rec = bind_cert(acct["id"], "gone-device", None, "system")
        out = revoke_cert(rec["id"], "system")
        assert out["status"] == "revoked" and out["revoked_at"]
        # 撤銷後同 CN 可重簽（唯一索引只擋 active）
        again = bind_cert(acct["id"], "gone-device", None, "system")
        assert again["status"] == "active"

    def test_revoke_unknown_returns_none(self, tmp_db):
        from repositories.account_cert_repo import revoke_cert

        assert revoke_cert(9999, "system") is None

    def test_revoke_scoped_to_account(self, tmp_db):
        from repositories.account_cert_repo import bind_cert, revoke_cert

        a = _mk_account("alice")
        b = _mk_account("bob")
        rec = bind_cert(a["id"], "alice-dev", None, "system")
        # 以 bob 的 account_id 撤 alice 的證 → 撈不到，None（防越權）
        assert revoke_cert(rec["id"], "system", account_id=b["id"]) is None
        assert revoke_cert(rec["id"], "system", account_id=a["id"]) is not None


class TestPurgeRevoked:
    def test_purge_removes_only_revoked(self, tmp_db):
        from repositories.account_cert_repo import (
            bind_cert,
            list_certs,
            purge_revoked_certs,
            revoke_cert,
        )

        acct = _mk_account()
        keep = bind_cert(acct["id"], "alice-phone", None, "system")  # 保持 active
        gone = bind_cert(acct["id"], "alice-tablet", None, "system")
        revoke_cert(gone["id"], "system")
        assert len(list_certs(acct["id"])) == 2
        n = purge_revoked_certs(acct["id"], "system")
        assert n == 1
        rows = list_certs(acct["id"])
        assert len(rows) == 1 and rows[0]["id"] == keep["id"] and rows[0]["status"] == "active"

    def test_purge_empty_returns_zero(self, tmp_db):
        from repositories.account_cert_repo import bind_cert, purge_revoked_certs

        acct = _mk_account()
        bind_cert(acct["id"], "alice-phone", None, "system")  # 只有 active
        assert purge_revoked_certs(acct["id"], "system") == 0

    def test_purge_scoped_to_account(self, tmp_db):
        from repositories.account_cert_repo import bind_cert, list_certs, purge_revoked_certs, revoke_cert

        a = _mk_account("alice")
        b = _mk_account("bob")
        ra = bind_cert(a["id"], "alice-dev", None, "system")
        rb = bind_cert(b["id"], "bob-dev", None, "system")
        revoke_cert(ra["id"], "system")
        revoke_cert(rb["id"], "system")
        # 清 alice 的不動 bob 的
        assert purge_revoked_certs(a["id"], "system") == 1
        assert len(list_certs(a["id"])) == 0
        assert len(list_certs(b["id"])) == 1  # bob 的 revoked 列還在


class TestMtlsBootstrapWindow:
    """#306：bootstrap 窗口 = 唯一帳號 + 零 active 綁定。"""

    def test_true_single_account_no_certs(self, tmp_db):
        from repositories.account_cert_repo import is_mtls_bootstrap

        _mk_account("admin")
        assert is_mtls_bootstrap() is True

    def test_false_no_accounts(self, tmp_db):
        from repositories.account_cert_repo import is_mtls_bootstrap

        assert is_mtls_bootstrap() is False

    def test_false_after_first_cert_bound(self, tmp_db):
        from repositories.account_cert_repo import bind_cert, is_mtls_bootstrap

        acct = _mk_account("admin")
        bind_cert(acct["id"], "dev-1", None, "system")
        assert is_mtls_bootstrap() is False  # 綁定後關窗

    def test_false_latched_after_revoke_all(self, tmp_db):
        """單向閂：綁過第一張後即使撤光證，窗口不重開（撤銷不可被 bootstrap 繞過）。"""
        from repositories.account_cert_repo import bind_cert, is_mtls_bootstrap, revoke_cert

        acct = _mk_account("admin")
        rec = bind_cert(acct["id"], "dev-1", None, "system")
        revoke_cert(rec["id"], "system")  # 撤光 → 0 active，但 row 仍在
        assert is_mtls_bootstrap() is False

    def test_false_latched_after_revoke_and_purge(self, tmp_db):
        """security-review #306 V1：bind→revoke→purge 把 account_certs 清空，但持久旗標
        mtls_bootstrap_done 仍在 → 窗口不重開（purge 清不掉旗標）。"""
        from repositories.account_cert_repo import (
            bind_cert,
            is_mtls_bootstrap,
            list_certs,
            purge_revoked_certs,
            revoke_cert,
        )

        acct = _mk_account("admin")
        rec = bind_cert(acct["id"], "dev-1", None, "system")
        revoke_cert(rec["id"], "system")
        purge_revoked_certs(acct["id"], "system")
        assert len(list_certs(acct["id"])) == 0  # account_certs 已清空
        assert is_mtls_bootstrap() is False  # 但旗標擋住，窗口不重開

    def test_false_multiple_accounts(self, tmp_db):
        from repositories.account_cert_repo import is_mtls_bootstrap

        _mk_account("admin")
        _mk_account("op")
        assert is_mtls_bootstrap() is False  # 多帳號永久關窗（撤光證也無法重開）


class TestActiveLookups:
    def test_cert_active_for_account(self, tmp_db):
        from repositories.account_cert_repo import bind_cert, cert_active_for_account

        acct = _mk_account()
        bind_cert(acct["id"], "alice-phone", None, "system")
        assert cert_active_for_account(acct["id"], "alice-phone") is True
        assert cert_active_for_account(acct["id"], "other") is False
        assert cert_active_for_account(acct["id"] + 999, "alice-phone") is False

    def test_is_cert_active_after_revoke(self, tmp_db):
        from repositories.account_cert_repo import bind_cert, is_cert_active, revoke_cert

        acct = _mk_account()
        rec = bind_cert(acct["id"], "alice-phone", None, "system")
        assert is_cert_active("alice-phone") is True
        revoke_cert(rec["id"], "system")
        assert is_cert_active("alice-phone") is False


class TestCertCnValidation:
    def test_valid_cns(self, tmp_db):
        from repositories.account_cert_repo import is_valid_cert_cn

        for cn in ("my-phone", "commander-phone-01", "王小明-iPhone", "a.b_c@d", "夜鷹 平板"):
            assert is_valid_cert_cn(cn) is True, cn

    def test_invalid_cns(self, tmp_db):
        from repositories.account_cert_repo import is_valid_cert_cn

        # 空、逗號（破壞 nginx [^,]+ CN 抽取）、前導 dash（step CLI flag injection）、控制/引號
        for cn in ("", "   ", "a,b", "-flag", "--not-after=8760h", 'a"b', "a\nb", "x" * 65):
            assert is_valid_cert_cn(cn) is False, repr(cn)


class TestRevokeKillsLiveSession:
    def test_revoked_cert_invalidates_active_session(self, tmp_db, monkeypatch):
        """撤銷後活躍 session 下一個 request 即失效（不必等 token 過期）。"""
        import core.config as config

        monkeypatch.setattr(config, "ICS_MTLS_REQUIRED", True)
        from auth.service import EVENT_BINDING_MISMATCH_CERT, check_session, create_session
        from repositories.account_cert_repo import bind_cert, revoke_cert

        acct = _mk_account()
        rec = bind_cert(acct["id"], "alice-phone", None, "system")
        token = create_session({"username": acct["username"], "role": "operator"}, cert_cn="alice-phone")
        # 撤銷前：出示對應 cert → 通
        sess, failure = check_session(token, request=_req(cert_cn="alice-phone", cert_verify="SUCCESS"))
        assert failure is None and sess is not None
        # 撤銷後：同 token 同 cert → 失效
        revoke_cert(rec["id"], "system")
        sess, failure = check_session(token, request=_req(cert_cn="alice-phone", cert_verify="SUCCESS"))
        assert sess is None
        assert failure and failure["event"] == EVENT_BINDING_MISMATCH_CERT
