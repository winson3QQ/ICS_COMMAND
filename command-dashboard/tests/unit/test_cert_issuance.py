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
        pw = tmp_path / "pw"; pw.write_text("p", encoding="ascii")
        monkeypatch.setattr(config, "STEP_CA_URL", "https://step-ca:9000")
        monkeypatch.setattr(config, "STEP_CA_PROVISIONER_PASSWORD_FILE", str(pw))
        monkeypatch.setattr(config, "STEP_CA_FINGERPRINT", "FP")
        monkeypatch.setattr(config, "STEP_CA_FINGERPRINT_FILE", "")
        assert config.step_ca_configured() is True
        monkeypatch.setattr(config, "STEP_CA_URL", "")
        assert config.step_ca_configured() is False
