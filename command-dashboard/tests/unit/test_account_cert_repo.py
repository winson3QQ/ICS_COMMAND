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
