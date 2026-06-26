# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
unit/test_cert_issuance.py — #275 wave B-2：線上發證服務守門（純邏輯分支）

subprocess 與 step-ca daemon 的 happy path 由 mTLS 驗證棧實測涵蓋；此處測 guard：
未配置 / 空 CN / fingerprint 檔讀取。
"""

import pytest

pytestmark = pytest.mark.unit


class TestGuards:
    def test_not_configured_raises(self, monkeypatch):
        import core.config as config
        from services.cert_issuance import CertIssuanceError, issue_p12

        monkeypatch.setattr(config, "step_ca_configured", lambda: False)
        with pytest.raises(CertIssuanceError):
            issue_p12("any-cn")

    def test_empty_cn_raises(self, monkeypatch):
        import core.config as config
        from services.cert_issuance import CertIssuanceError, issue_p12

        monkeypatch.setattr(config, "step_ca_configured", lambda: True)
        with pytest.raises(CertIssuanceError):
            issue_p12("   ")


class TestP12Password:
    """#307：未設環境變數→每張隨機；顯式設→固定（runbook 相容）。"""

    def test_random_when_unset(self, monkeypatch):
        import core.config as config
        from services.cert_issuance import _p12_password

        monkeypatch.setattr(config, "STEP_CLIENT_CERT_P12_PASS", None)
        a, b = _p12_password(), _p12_password()
        assert a != b and len(a) >= 12  # 每次不同、足夠長
        assert a != "icsclient"  # 弱默認已廢除

    def test_fixed_when_set(self, monkeypatch):
        import core.config as config
        from services.cert_issuance import _p12_password

        monkeypatch.setattr(config, "STEP_CLIENT_CERT_P12_PASS", "my-fixed-pw")
        assert _p12_password() == "my-fixed-pw" == _p12_password()


class TestMobileconfig:
    """#312：iOS .mobileconfig 描述檔（root CA + p12 + 內嵌密碼）。"""

    def test_build_embeds_password_and_payloads(self, monkeypatch):
        import base64

        import services.cert_issuance as ci

        monkeypatch.setattr(ci.ssl, "PEM_cert_to_DER_cert", lambda pem: b"ROOTDER")
        out = ci.build_mobileconfig("my-ipad", b"P12BYTES", "secret-pw", "-----BEGIN CERT-----", "https://1.2.3.4/")
        text = out.decode("utf-8")
        assert "com.apple.security.root" in text and "com.apple.security.pkcs12" in text
        assert "<key>Password</key><string>secret-pw</string>" in text  # 密碼內嵌→免手打
        assert base64.b64encode(b"P12BYTES").decode() in text
        assert base64.b64encode(b"ROOTDER").decode() in text
        assert "my-ipad" in text

    def test_xml_escapes_cn_and_password(self, monkeypatch):
        import services.cert_issuance as ci

        monkeypatch.setattr(ci.ssl, "PEM_cert_to_DER_cert", lambda pem: b"D")
        out = ci.build_mobileconfig("a&b", b"x", "p<w>&", "pem", "https://h/").decode("utf-8")
        assert "a&amp;b" in out and "p&lt;w&gt;&amp;" in out  # XML 注入防護


class TestFingerprintResolution:
    def test_env_fingerprint_wins(self, monkeypatch):
        import core.config as config

        monkeypatch.setattr(config, "STEP_CA_FINGERPRINT", "ENVFP")
        assert config.step_ca_fingerprint() == "ENVFP"

    def test_fingerprint_from_file(self, monkeypatch, tmp_path):
        import core.config as config

        f = tmp_path / "fp"
        f.write_text("FILEFP\n", encoding="ascii")
        monkeypatch.setattr(config, "STEP_CA_FINGERPRINT", "")
        monkeypatch.setattr(config, "STEP_CA_FINGERPRINT_FILE", str(f))
        assert config.step_ca_fingerprint() == "FILEFP"

    def test_configured_requires_all_three(self, monkeypatch, tmp_path):
        import core.config as config

        pw = tmp_path / "pw"
        pw.write_text("p", encoding="ascii")
        monkeypatch.setattr(config, "STEP_CA_URL", "https://step-ca:9000")
        monkeypatch.setattr(config, "STEP_CA_PROVISIONER_PASSWORD_FILE", str(pw))
        monkeypatch.setattr(config, "STEP_CA_FINGERPRINT", "FP")
        monkeypatch.setattr(config, "STEP_CA_FINGERPRINT_FILE", "")
        assert config.step_ca_configured() is True
        monkeypatch.setattr(config, "STEP_CA_URL", "")
        assert config.step_ca_configured() is False
