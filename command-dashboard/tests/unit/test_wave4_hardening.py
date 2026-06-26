# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
unit/test_wave4_hardening.py — #275 wave 4：§8.6 #2 機制面硬化

涵蓋：PBKDF2 100k→600k（含舊 hash 相容 + 透明 rehash）、XFF 信任修正、鎖定-DoS 緩解
（持綁定裝置證者不被鎖死）。
"""

from types import SimpleNamespace

import pytest
from starlette.datastructures import Headers

pytestmark = pytest.mark.unit


def _req(headers=None, host="9.9.9.9"):
    return SimpleNamespace(headers=Headers(headers or {}), client=SimpleNamespace(host=host))


class TestPbkdf2Upgrade:
    def test_new_hash_encodes_600k_iterations(self):
        from repositories._helpers import hash_pin

        h, _ = hash_pin("123456")
        assert h.startswith("600000$")

    def test_new_hash_verifies(self):
        from repositories._helpers import hash_pin, verify_pin

        h, s = hash_pin("123456")
        assert verify_pin("123456", h, s) is True
        assert verify_pin("000000", h, s) is False

    def test_legacy_100k_bare_hex_still_verifies(self):
        """舊資料：裸 hex（無 $ 前綴）= legacy 100k，須仍可驗。"""
        import hashlib

        from repositories._helpers import verify_pin

        salt = "00112233445566778899aabbccddeeff"
        digest = hashlib.pbkdf2_hmac("sha256", b"123456", bytes.fromhex(salt), 100_000).hex()
        assert verify_pin("123456", digest, salt) is True  # bare hex
        assert verify_pin("999999", digest, salt) is False

    def test_pin_needs_rehash(self):
        from repositories._helpers import hash_pin, pin_needs_rehash

        new_h, _ = hash_pin("123456")
        assert pin_needs_rehash(new_h) is False
        assert pin_needs_rehash("deadbeef") is True  # legacy bare = 100k
        assert pin_needs_rehash("100000$deadbeef") is True

    def test_login_transparently_rehashes_legacy(self, tmp_db):
        """舊 100k hash 帳號登入成功後 → 透明升級為 600k。"""
        import hashlib

        from core.database import get_conn
        from repositories.account_repo import create_account, verify_login

        create_account("legacy", "123456", operator="seed")
        salt = "00112233445566778899aabbccddeeff"
        legacy = hashlib.pbkdf2_hmac("sha256", b"123456", bytes.fromhex(salt), 100_000).hex()
        with get_conn() as c:
            c.execute("UPDATE accounts SET pin_hash=?, pin_salt=? WHERE username='legacy'", (legacy, salt))
        acct, reason = verify_login("legacy", "123456")
        assert reason == "ok" and acct
        with get_conn() as c:
            after = c.execute("SELECT pin_hash FROM accounts WHERE username='legacy'").fetchone()[0]
        assert after.startswith("600000$")  # 已升級


class TestXffTrust:
    def test_ignores_xff_when_not_behind_proxy(self, monkeypatch):
        import core.config as config
        from auth.service import _client_ip

        monkeypatch.setattr(config, "ICS_BEHIND_PROXY", False)
        r = _req({"x-forwarded-for": "1.2.3.4", "x-real-ip": "5.6.7.8"}, host="10.0.0.1")
        assert _client_ip(r) == "10.0.0.1"  # 用實際 peer，不信偽造 header

    def test_trusts_x_real_ip_when_behind_proxy(self, monkeypatch):
        import core.config as config
        from auth.service import _client_ip

        monkeypatch.setattr(config, "ICS_BEHIND_PROXY", True)
        r = _req({"x-forwarded-for": "1.2.3.4", "x-real-ip": "5.6.7.8"}, host="172.20.0.2")
        assert _client_ip(r) == "5.6.7.8"  # nginx 設的真實 client

    def test_rate_limit_client_ip_same_policy(self, monkeypatch):
        import core.config as config
        from auth.rate_limit import _client_ip

        monkeypatch.setattr(config, "ICS_BEHIND_PROXY", False)
        assert _client_ip(_req({"x-forwarded-for": "1.2.3.4"}, host="10.0.0.9")) == "10.0.0.9"
        monkeypatch.setattr(config, "ICS_BEHIND_PROXY", True)
        assert _client_ip(_req({"x-real-ip": "5.6.7.8"}, host="172.20.0.2")) == "5.6.7.8"


class TestLockoutDosMitigation:
    def _lock(self, username):
        from datetime import UTC, datetime, timedelta

        from core.database import get_conn

        until = (datetime.now(UTC) + timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with get_conn() as c:
            c.execute("UPDATE accounts SET failed_login_count=5, locked_until=? WHERE username=?", (until, username))

    def test_locked_without_cert_stays_locked(self, tmp_db):
        from repositories.account_repo import create_account, verify_login

        create_account("opx", "123456", operator="seed")
        self._lock("opx")
        acct, reason = verify_login("opx", "123456", bypass_lockout=False)
        assert acct is None and reason == "locked"

    def test_locked_with_bound_cert_can_login(self, tmp_db):
        """持綁定裝置證者（bypass_lockout=True）即使被鎖也能登入 → 不被攻擊者鎖死。"""
        from repositories.account_repo import create_account, verify_login

        create_account("cmdr", "123456", operator="seed")
        self._lock("cmdr")
        acct, reason = verify_login("cmdr", "123456", bypass_lockout=True)
        assert reason == "ok" and acct

    def test_bypass_wrong_pin_does_not_relock(self, tmp_db):
        from core.database import get_conn
        from repositories.account_repo import create_account, verify_login

        create_account("cmdr2", "123456", operator="seed")
        self._lock("cmdr2")
        # 持證但打錯 PIN：不再上鎖（locked_until 不被續設）
        verify_login("cmdr2", "000000", bypass_lockout=True)
        with get_conn() as c:
            lu = c.execute("SELECT locked_until FROM accounts WHERE username='cmdr2'").fetchone()[0]
        assert lu is None
