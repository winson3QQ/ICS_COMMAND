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


class TestRevokeAtStepCa:
    """#232軌1-S1：撤銷同步 step-ca（best-effort、不持 CA 鑰、token 兩步 token→revoke）。"""

    def _cfg(self, monkeypatch, tmp_path):
        import core.config as config

        pw = tmp_path / "pw"
        pw.write_text("p", encoding="ascii")
        monkeypatch.setattr(config, "step_ca_configured", lambda: True)
        monkeypatch.setattr(config, "STEP_CA_PROVISIONER_PASSWORD_FILE", str(pw))
        monkeypatch.setattr(config, "STEP_CA_URL", "https://step-ca:9000")
        monkeypatch.setattr(config, "STEP_CA_PROVISIONER", "ics")
        monkeypatch.setattr(config, "step_ca_fingerprint", lambda: "FP")

    def _mk_run(self, results):
        import subprocess

        calls = []

        def _run(args):
            calls.append(args)
            rc, out = results.get(args[1], (0, ""))  # args=[ca, <sub>, ...]
            return subprocess.CompletedProcess(args, rc, stdout=out, stderr="")

        return _run, calls

    def test_not_configured_false(self, monkeypatch):
        import core.config as config
        from services.cert_issuance import revoke_at_step_ca

        monkeypatch.setattr(config, "step_ca_configured", lambda: False)
        assert revoke_at_step_ca("ABC123") is False

    def test_empty_serial_false(self, monkeypatch, tmp_path):
        from services.cert_issuance import revoke_at_step_ca

        self._cfg(monkeypatch, tmp_path)
        assert revoke_at_step_ca("   ") is False

    def test_happy_path_two_step(self, monkeypatch, tmp_path):
        import services.cert_issuance as ci

        self._cfg(monkeypatch, tmp_path)
        run, calls = self._mk_run({"root": (0, ""), "token": (0, "REVOKE_OTT\n"), "revoke": (0, "")})
        monkeypatch.setattr(ci, "_run", run)
        assert ci.revoke_at_step_ca("SERIAL9") is True
        tok = next(c for c in calls if c[1] == "token")
        assert "SERIAL9" in tok and "--revoke" in tok  # 產撤銷 token 帶 serial
        rev = next(c for c in calls if c[1] == "revoke")
        assert "SERIAL9" in rev and "--token" in rev and "REVOKE_OTT" in rev  # 憑 token 撤銷

    def test_token_failure_best_effort_false(self, monkeypatch, tmp_path):
        import services.cert_issuance as ci

        self._cfg(monkeypatch, tmp_path)
        run, _ = self._mk_run({"root": (0, ""), "token": (1, "")})  # token 產生失敗
        monkeypatch.setattr(ci, "_run", run)
        assert ci.revoke_at_step_ca("S") is False  # 不 raise

    def test_revoke_failure_best_effort_false(self, monkeypatch, tmp_path):
        import services.cert_issuance as ci

        self._cfg(monkeypatch, tmp_path)
        run, _ = self._mk_run({"root": (0, ""), "token": (0, "OTT"), "revoke": (1, "denied")})
        monkeypatch.setattr(ci, "_run", run)
        assert ci.revoke_at_step_ca("S") is False

    def test_run_timeout_swallowed_false(self, monkeypatch, tmp_path):
        """review-fix #2：daemon 卡住 → _run 拋 TimeoutExpired 須被吞成 False（不逃逸成 500、
        不破壞已 commit 的 App 層撤銷）。"""
        import subprocess

        import services.cert_issuance as ci

        self._cfg(monkeypatch, tmp_path)

        def _boom(args):
            raise subprocess.TimeoutExpired(cmd=args, timeout=30)

        monkeypatch.setattr(ci, "_run", _boom)
        assert ci.revoke_at_step_ca("S") is False  # 不 raise

    def test_run_oserror_swallowed_false(self, monkeypatch, tmp_path):
        """step binary 缺（OSError）同樣吞成 False。"""
        import services.cert_issuance as ci

        self._cfg(monkeypatch, tmp_path)

        def _boom(args):
            raise OSError("step: not found")

        monkeypatch.setattr(ci, "_run", _boom)
        assert ci.revoke_at_step_ca("S") is False


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
