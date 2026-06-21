"""
unit/test_tak_device_cert.py — #315 P2-26 L2：TAK 裝置證 data package 組裝（純 Python）。

只測 assemble_package / 輔助函式（不碰 step-ca daemon / openssl）；端到端簽發走 integration。
"""

import io
import zipfile

import pytest

pytestmark = pytest.mark.unit

_SLOT = "deadbeef"
_UIDS = ["deadbeef", "uid-inner", "uid-outer"]
_CLIENT = b"CLIENT-P12-BYTES"
_TRUST = b"TRUST-P12-BYTES"


def _open_zip(data: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(data))


class TestHelpers:
    def test_split_pem_certs(self):
        from services.tak_device_cert import _split_pem_certs

        pem = (
            "-----BEGIN CERTIFICATE-----\nLEAF\n-----END CERTIFICATE-----\n"
            "-----BEGIN CERTIFICATE-----\nINT\n-----END CERTIFICATE-----\n"
        )
        blocks = _split_pem_certs(pem)
        assert len(blocks) == 2 and "LEAF" in blocks[0] and "INT" in blocks[1]

    def test_connect_string(self):
        from services.tak_device_cert import _connect_string

        assert _connect_string("1.2.3.4", 8089) == "1.2.3.4:8089:ssl"


class TestAssembleAware:
    def _pkg(self):
        from services.tak_device_cert import assemble_package

        return assemble_package("aware", "3QQ-AWARE", "1.2.3.4", 8089, _CLIENT, _TRUST, _slot=_SLOT, _uids=_UIDS)

    def test_flat_structure_and_contents(self):
        # #315 iTAK：flat zip，全檔在根、無 MANIFEST（對齊成功 iTAK 樣本）
        z = _open_zip(self._pkg())
        names = set(z.namelist())
        assert names == {
            "config.pref",
            "3QQ-AWARE.p12",
            "truststore-root.p12",
        }
        assert z.read("3QQ-AWARE.p12") == _CLIENT
        assert z.read("truststore-root.p12") == _TRUST
        pref = z.read("config.pref").decode("utf-8")
        assert 'name="cot_streams"' in pref  # 連線設定獨立區塊（樣本格式）
        assert "1.2.3.4:8089:ssl" in pref  # 對外位址
        assert "clientPassword" in pref and "atakatak" in pref  # 內嵌密碼（免打）
        assert "cert/3QQ-AWARE.p12" in pref  # pref 內 cert/ 路徑（樣本即此寫法）


class TestAssembleAtak:
    def _pkg(self):
        from services.tak_device_cert import assemble_package

        return assemble_package("atak", "atak-phone-01", "5.6.7.8", 8089, _CLIENT, _TRUST, _slot=_SLOT, _uids=_UIDS)

    def test_nested_structure(self):
        outer = _open_zip(self._pkg())
        names = outer.namelist()
        assert "MANIFEST/manifest.xml" in names
        inner_name = f"{_SLOT}/ICS_TAK_atak-phone-01.zip"
        assert inner_name in names
        # inner 是巢狀 zip → 取出再開
        inner = _open_zip(outer.read(inner_name))
        innames = set(inner.namelist())
        assert innames == {
            "MANIFEST/manifest.xml",
            f"{_SLOT}/preference.pref",
            f"{_SLOT}/atak-phone-01.p12",
            f"{_SLOT}/truststore-root.p12",
        }
        assert inner.read(f"{_SLOT}/atak-phone-01.p12") == _CLIENT
        pref = inner.read(f"{_SLOT}/preference.pref").decode("utf-8")
        assert "5.6.7.8:8089:ssl" in pref
        assert "atakatak" in pref


class TestModeGuard:
    def test_invalid_mode_in_build(self):
        from services.tak_device_cert import build_device_package

        with pytest.raises(ValueError):
            build_device_package("x", "bogus", "1.2.3.4", 8089, "CA-PEM")

    def test_empty_host_in_build(self):
        from services.tak_device_cert import build_device_package

        with pytest.raises(ValueError):
            build_device_package("x", "atak", "  ", 8089, "CA-PEM")
