# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""unit/test_tak_enrollment.py — #429 ICS 代理 TAK enrollment 發證（純邏輯 + mock orchestrator）。

只測密碼複雜度 / PEM 抽取 / is_configured / orchestrator 串接（mock 掉 REST/signClient/openssl）；
真打 :8446/8443 + openssl 簽走 dogfood/integration。
"""

import asyncio

import pytest

pytestmark = pytest.mark.unit

_SPECIAL = set("-_!@#$%^&*")


class TestPassword:
    def test_meets_usermod_complexity(self):
        """每次產的密碼須 ≥15 + 含大寫/小寫/數字/特殊符（UserManager 規則，否則靜默不生效）。"""
        from services.tak_enrollment import _gen_password

        for _ in range(50):
            pw = _gen_password()
            assert len(pw) >= 15
            assert any(c.isupper() for c in pw)
            assert any(c.islower() for c in pw)
            assert any(c.isdigit() for c in pw)
            assert any(c in _SPECIAL for c in pw)

    def test_random(self):
        from services.tak_enrollment import _gen_password

        assert len({_gen_password() for _ in range(20)}) == 20


class TestParseSignedCert:
    def test_json_signedcert_base64_der(self):
        """v2 回 JSON {signedCert: base64 DER} → 包成 PEM（dogfood 實證格式）。"""
        from services.tak_enrollment import _parse_signed_cert

        out = _parse_signed_cert('{"signedCert": "MIIEHzCC\\nVzEMabc"}')
        assert out.startswith("-----BEGIN CERTIFICATE-----\n")
        assert out.endswith("-----END CERTIFICATE-----\n")
        assert "MIIEHzCC\nVzEMabc" in out

    def test_bare_pem_fallback(self):
        from services.tak_enrollment import _parse_signed_cert

        body = "junk\n-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\ntail"
        out = _parse_signed_cert(body)
        assert out.startswith("-----BEGIN CERTIFICATE-----")
        assert "AAAA" in out

    def test_json_without_signedcert_raises(self):
        from services.cert_issuance import CertIssuanceError
        from services.tak_enrollment import _parse_signed_cert

        with pytest.raises(CertIssuanceError):
            _parse_signed_cert('{"other": "x"}')

    def test_no_cert_raises(self):
        from services.cert_issuance import CertIssuanceError
        from services.tak_enrollment import _parse_signed_cert

        with pytest.raises(CertIssuanceError):
            _parse_signed_cert("no pem here")


class TestIsConfigured:
    def test_needs_admin_cert_and_enroll_url(self, monkeypatch):
        from core import config
        from services import tak_enrollment

        monkeypatch.setattr(config, "TAK_MARTI_URL", "https://takserver:8443")
        monkeypatch.setattr(config, "TAK_MARTI_ADMIN_CERT", "/c.pem")
        monkeypatch.setattr(config, "TAK_MARTI_ADMIN_KEY", "/k.pem")
        monkeypatch.setattr(config, "TAK_ENROLL_URL", "")
        assert tak_enrollment.is_configured() is False  # 缺 enroll URL
        monkeypatch.setattr(config, "TAK_ENROLL_URL", "https://takserver:8446")
        assert tak_enrollment.is_configured() is True


class TestOrchestrator:
    def test_issue_via_enrollment_wires_steps(self, monkeypatch):
        """建帳號 → CSR → signClient → serial/fp，回傳形狀對齊 _sign_with_tak_ca。"""
        from core import config
        from services import tak_enrollment

        monkeypatch.setattr(config, "TAK_MARTI_URL", "https://takserver:8443")
        monkeypatch.setattr(config, "TAK_MARTI_ADMIN_CERT", "/c.pem")
        monkeypatch.setattr(config, "TAK_MARTI_ADMIN_KEY", "/k.pem")
        monkeypatch.setattr(config, "TAK_ENROLL_URL", "https://takserver:8446")
        monkeypatch.setattr(config, "TAK_ENROLL_DEFAULT_GROUP", "neutral")

        created = {}

        async def fake_create(username, password, group):
            created["args"] = (username, password, group)

        async def fake_sign(username, password, csr_pem):
            created["sign"] = (username, "CSR" in csr_pem.upper() or "REQUEST" in csr_pem.upper())
            return "-----BEGIN CERTIFICATE-----\nLEAF\n-----END CERTIFICATE-----\n"

        monkeypatch.setattr(tak_enrollment, "create_managed_user", fake_create)
        monkeypatch.setattr(tak_enrollment, "sign_client_csr", fake_sign)
        monkeypatch.setattr(tak_enrollment, "_gen_csr", lambda cs: ("CSR-PEM", "KEY-PEM"))
        monkeypatch.setattr(tak_enrollment, "_cert_serial_fingerprint", lambda pem: ("AB12", "FP:00"))

        cert, key, serial, fp = asyncio.run(tak_enrollment.issue_via_enrollment("blue-99"))

        assert created["args"][0] == "blue-99"  # username = callsign
        assert created["args"][2] == "neutral"  # 初始群 fail-closed
        assert created["sign"][0] == "blue-99"
        assert "LEAF" in cert and key == "KEY-PEM"
        assert serial == "AB12" and fp == "FP:00"

    def test_unconfigured_raises(self, monkeypatch):
        from core import config
        from services import tak_enrollment
        from services.cert_issuance import CertIssuanceError

        monkeypatch.setattr(config, "TAK_ENROLL_URL", "")
        with pytest.raises(CertIssuanceError):
            asyncio.run(tak_enrollment.issue_via_enrollment("x"))
