# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""#315 P2-26 L2 — TAK 裝置證自助發放（data package）。

dashboard 線上發 ATAK/iTAK 的 TAK data package（取代 deploy-time CLI gen-device-pkg.sh）。
**證由 TAK 自己的 CA（ICS-TAK-SVC-CA，offline，`TAK_DEVICE_CA_DIR/tak-ca.key`）簽** —— TAK truststore
只信它、不信 step-ca（reality check 2026-06-21）；**與 ICS 登入證的 step-ca 刻意隔離**（#305）。
本服務組 data package（p12 + truststore + pref 的 zip）。p12 密碼＝TAK 慣例 `atakatak` 內嵌進 pref →
裝置免打。package 即產即交、私鑰在內、不落 DB（守 #255 紅線）。doctrine 見 threat_model §8.3。

reality check（2026-06-21 真機 iTAK dogfood）：iTAK 吃 **legacy p12** + **flat zip**；client 證
**必須 ICS-TAK-SVC-CA 簽**（step-ca 證 TAK 回 peer not verified）；truststore = TAK server CA；
connectString 須用**對外可達 TAK 位址**（非容器內網 takserver:8089）。
"""

from __future__ import annotations

import io
import os
import re
import subprocess  # nosec B404 - 固定 binary（openssl）+ 參數列，無 shell、無使用者字串拼接
import tempfile
import uuid
import zipfile
from xml.sax.saxutils import escape

from services.cert_issuance import CertIssuanceError

# TAK 慣例公開預設密碼（非機密）：內嵌進 pref（caPassword/clientPassword）→ 裝置免打。
P12_PASS = "atakatak"  # nosec B105 - TAK 約定俗成的 data package 密碼，非後端機密
_OPENSSL = os.getenv("OPENSSL_BIN", "openssl")


def _split_pem_certs(pem_text: str) -> list[str]:
    """拆 PEM 文字成各張憑證 block（fullchain → [leaf, intermediate, ...]）。"""
    return re.findall(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", pem_text or "", re.DOTALL)


def _xa(s: str) -> str:
    """XML attribute-safe escape：含雙引號（security-review #315 L1 defense-in-depth；
    callsign 已被 is_valid_cert_cn allowlist 擋 "，此為第二層，未來 allowlist 放寬亦安全）。"""
    return escape(s, {'"': "&quot;"})


def _sign_with_tak_ca(callsign: str, ca_dir: str) -> tuple[str, str, str, str]:
    """用 TAK 自己的 CA（ICS-TAK-SVC-CA，offline）簽 clientAuth 裝置證，回 (cert_pem, key_pem, serial, fingerprint)。

    serial（hex）供 #317 盤點記錄 + #318 CRL 撤銷對位。
    fingerprint（SHA-256 冒號分隔大寫）供 #344 enrollment：TAK usermod -f 用此 fingerprint 把裝置證
    註冊成 managed user（TAK 比對裝置出示的 leaf 證 fingerprint）。格式同 register-tak-fingerprint.sh。

    #315 reality check：TAK truststore 只信 ICS-TAK-SVC-CA、不信 step-ca → 裝置證**必須**這把 CA 簽
    （否則 TAK 回 peer not verified）。同 deploy/.../gen-device-pkg.sh。CA dir 含 tak-ca.pem +
    tak-ca.key（掛載 ro）；serial 寫入 workdir（CA dir 唯讀）。**後端用此 offline CA 鑰**——與 ICS
    登入證的 step-ca daemon 隔離（doctrine 見 threat_model §8.3）。
    """
    ca_pem = os.path.join(ca_dir, "tak-ca.pem")
    ca_key = os.path.join(ca_dir, "tak-ca.key")
    if not (os.path.isfile(ca_pem) and os.path.isfile(ca_key)):
        raise CertIssuanceError(f"TAK 裝置 CA 不全（缺 tak-ca.pem / tak-ca.key @ {ca_dir}）")
    with tempfile.TemporaryDirectory(prefix="ics-taksign-") as td:
        key = os.path.join(td, "d.key")
        csr = os.path.join(td, "d.csr")
        cnf = os.path.join(td, "cl.cnf")
        crt = os.path.join(td, "d.pem")
        srl = os.path.join(td, "ca.srl")
        with open(cnf, "w", encoding="ascii") as f:
            f.write(
                "basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=clientAuth\n"
            )
        steps = [
            [_OPENSSL, "genrsa", "-out", key, "2048"],
            [_OPENSSL, "req", "-new", "-key", key, "-subj", f"/C=TW/O=ICS/OU=COP/CN={callsign}", "-out", csr],
            [
                _OPENSSL,
                "x509",
                "-req",
                "-in",
                csr,
                "-CA",
                ca_pem,
                "-CAkey",
                ca_key,
                "-CAserial",
                srl,
                "-CAcreateserial",
                "-days",
                "825",
                "-sha256",
                "-extfile",
                cnf,
                "-out",
                crt,
            ],
        ]
        for args in steps:
            r = subprocess.run(args, capture_output=True, text=True, timeout=30, check=False)  # nosec B603
            if r.returncode != 0:
                raise CertIssuanceError(f"TAK 裝置證簽發失敗：{(r.stderr or '').strip()[:200]}")
        with open(crt, encoding="ascii") as f:
            cert_pem = f.read()
        with open(key, encoding="ascii") as f:
            key_pem = f.read()
        # 讀回 serial（hex，#317 盤點 / #318 CRL）
        rs = subprocess.run(  # nosec B603
            [_OPENSSL, "x509", "-in", crt, "-noout", "-serial"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        serial = ""
        if rs.returncode == 0 and "=" in rs.stdout:
            serial = rs.stdout.strip().split("=", 1)[1]
        # 讀回 SHA-256 fingerprint（#344 enrollment：usermod -f 註冊鍵）。冒號分隔大寫，去 "...Fingerprint=" 前綴。
        rf = subprocess.run(  # nosec B603
            [_OPENSSL, "x509", "-in", crt, "-noout", "-fingerprint", "-sha256"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        fingerprint = ""
        if rf.returncode == 0 and "=" in rf.stdout:
            fingerprint = rf.stdout.strip().split("=", 1)[1]
    return cert_pem, key_pem, serial, fingerprint


def _make_p12_materials(cert_pem: str, key_pem: str, tak_ca_pem: str, callsign: str) -> tuple[bytes, bytes]:
    """產 (client.p12 [device cert+key], truststore.p12 [ICS-TAK-SVC-CA])。openssl 打包，無 keytool。

    device 證直接由 ICS-TAK-SVC-CA（單層 root）簽 → client p12 放 leaf 即可（無 intermediate）。
    truststore = 同一個 ICS-TAK-SVC-CA（裝置驗 TAK server 用）。
    """
    trust_pem = (tak_ca_pem or "").strip()
    if "BEGIN CERTIFICATE" not in trust_pem or "BEGIN" not in (cert_pem or ""):
        raise CertIssuanceError("device 證或 TAK CA 內容無效")
    trust_pem += "\n"
    with tempfile.TemporaryDirectory(prefix="ics-takdp-") as td:
        cc = os.path.join(td, "client.crt")
        ky = os.path.join(td, "client.key")
        tr = os.path.join(td, "trust.pem")
        client_p12 = os.path.join(td, "client.p12")
        trust_p12 = os.path.join(td, "trust.p12")
        for path, content in ((cc, cert_pem), (ky, key_pem), (tr, trust_pem)):
            with open(path, "w", encoding="ascii") as f:
                f.write(content)
        # --legacy：PBE+SHA1+RC2/3DES。iTAK(iOS) 吃不下 openssl3 預設 PBES2/AES p12（匯入沒反應，
        # dogfood 2026-06-21 實證，同 #307）。client + truststore 都 legacy；ATAK 對 legacy 亦相容。
        r = subprocess.run(  # nosec B603 - 固定 binary + 參數列
            [
                _OPENSSL,
                "pkcs12",
                "-export",
                "-legacy",
                "-in",
                cc,
                "-inkey",
                ky,
                "-name",
                callsign,
                "-out",
                client_p12,
                "-passout",
                f"pass:{P12_PASS}",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if r.returncode != 0:
            raise CertIssuanceError(f"client p12 打包失敗：{(r.stderr or '').strip()[:200]}")
        # truststore p12：ICS-TAK-SVC-CA（公開證、無私鑰）；同 legacy
        r = subprocess.run(  # nosec B603
            [
                _OPENSSL,
                "pkcs12",
                "-export",
                "-legacy",
                "-nokeys",
                "-in",
                tr,
                "-out",
                trust_p12,
                "-passout",
                f"pass:{P12_PASS}",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if r.returncode != 0:
            raise CertIssuanceError(f"truststore p12 打包失敗：{(r.stderr or '').strip()[:200]}")
        with open(client_p12, "rb") as f:
            client_bytes = f.read()
        with open(trust_p12, "rb") as f:
            trust_bytes = f.read()
    return client_bytes, trust_bytes


def _connect_string(host: str, port: int) -> str:
    return f"{host}:{port}:ssl"


def _atak_pref(callsign: str, connect: str) -> str:
    cs = _xa(callsign)
    return f"""<?xml version='1.0' standalone='yes'?>
<preferences>
    <preference version="1" name="cot_streams">
        <entry key="count" class="class java.lang.Integer">1</entry>
        <entry key="description0" class="class java.lang.String">ICS_TAK_{cs}</entry>
        <entry key="enabled0" class="class java.lang.Boolean">true</entry>
        <entry key="connectString0" class="class java.lang.String">{_xa(connect)}</entry>
    </preference>
    <preference version="1" name="com.atakmap.app_preferences">
        <entry key="deviceProfileEnableOnConnect" class="class java.lang.Boolean">true</entry>
        <entry key="displayServerConnectionWidget" class="class java.lang.Boolean">true</entry>
        <entry key="caLocation" class="class java.lang.String">/storage/emulated/0/atak/cert/truststore-root.p12</entry>
        <entry key="caPassword" class="class java.lang.String">{P12_PASS}</entry>
        <entry key="clientPassword" class="class java.lang.String">{P12_PASS}</entry>
        <entry key="certificateLocation" class="class java.lang.String">/storage/emulated/0/atak/cert/{cs}.p12</entry>
    </preference>
</preferences>
"""


def _aware_pref(callsign: str, connect: str) -> str:
    # 對齊使用者提供的成功 iTAK 樣本（2026-06-21）：cot_streams 區塊放 connectString + app_preferences
    # 用 clientPassword（非 certificatePassword）+ cert/ 相對路徑（樣本即此寫法，iTAK 認）。
    cs = _xa(callsign)
    return f"""<?xml version='1.0' standalone='yes'?>
<preferences>
  <preference version="1" name="cot_streams">
    <entry key="count" class="class java.lang.Integer">1</entry>
    <entry key="description0" class="class java.lang.String">ICS_TAK_{cs}</entry>
    <entry key="enabled0" class="class java.lang.Boolean">true</entry>
    <entry key="connectString0" class="class java.lang.String">{_xa(connect)}</entry>
  </preference>
  <preference version="1" name="com.atakmap.app_preferences">
    <entry key="displayServerConnectionWidget" class="class java.lang.Boolean">true</entry>
    <entry key="caLocation" class="class java.lang.String">cert/truststore-root.p12</entry>
    <entry key="caPassword" class="class java.lang.String">{P12_PASS}</entry>
    <entry key="clientPassword" class="class java.lang.String">{P12_PASS}</entry>
    <entry key="certificateLocation" class="class java.lang.String">cert/{cs}.p12</entry>
  </preference>
</preferences>
"""


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    """把 {路徑: bytes} 組成 zip bytes（決定論、無外部 zip 依賴）。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


def assemble_package(
    mode: str,
    callsign: str,
    connect_host: str,
    connect_port: int,
    client_p12: bytes,
    truststore_p12: bytes,
    *,
    _slot: str | None = None,
    _uids: list[str] | None = None,
) -> bytes:
    """組 TAK data package zip（純 Python，可測）。mode=atak（嵌套）/aware（flat）。

    _slot/_uids 供測試注入決定論值；正式呼叫留空（隨機 uuid）。
    """
    cs = _xa(callsign)
    connect = _connect_string(connect_host, connect_port)
    uids = list(_uids) if _uids else [uuid.uuid4().hex, str(uuid.uuid4()), str(uuid.uuid4())]
    slot = _slot or uids[0]

    if mode == "atak":
        # 嵌套 zip：inner data package → 包進 outer wrapper
        inner_name = f"ICS_TAK_{callsign}.zip"
        inner_manifest = (
            f'<MissionPackageManifest version="2"><Configuration>'
            f'<Parameter name="uid" value="{uids[1]}"/>'
            f'<Parameter name="name" value="ICS_TAK_{cs}"/>'
            f'<Parameter name="onReceiveDelete" value="true"/></Configuration><Contents>'
            f'<Content ignore="false" zipEntry="{slot}/preference.pref"/>'
            f'<Content ignore="false" zipEntry="{slot}/truststore-root.p12"/>'
            f'<Content ignore="false" zipEntry="{slot}/{cs}.p12"/></Contents></MissionPackageManifest>'
        )
        inner = _zip_bytes(
            {
                "MANIFEST/manifest.xml": inner_manifest.encode("utf-8"),
                f"{slot}/preference.pref": _atak_pref(callsign, connect).encode("utf-8"),
                f"{slot}/{callsign}.p12": client_p12,
                f"{slot}/truststore-root.p12": truststore_p12,
            }
        )
        outer_manifest = (
            f'<MissionPackageManifest version="2"><Configuration>'
            f'<Parameter name="uid" value="{uids[2]}"/>'
            f'<Parameter name="name" value="ICS_TAK_{cs}_CONFIG"/></Configuration><Contents>'
            f'<Content ignore="false" zipEntry="{slot}/{inner_name}"/></Contents></MissionPackageManifest>'
        )
        return _zip_bytes(
            {
                "MANIFEST/manifest.xml": outer_manifest.encode("utf-8"),
                f"{slot}/{inner_name}": inner,
            }
        )

    # aware（iTAK/iOS）：**flat zip，全檔在根、無 MANIFEST、無子目錄** —— 對齊使用者提供的
    # 成功 iTAK 樣本（2026-06-21）。gen-device-dp.sh 的 cert/ pref/ MANIFEST 結構 iTAK 吃不下
    # （匯入沒反應）。p12 檔名在根（pref 內仍寫 cert/ 路徑，樣本即此、iTAK 認）。
    return _zip_bytes(
        {
            "config.pref": _aware_pref(callsign, connect).encode("utf-8"),
            f"{callsign}.p12": client_p12,
            "truststore-root.p12": truststore_p12,
        }
    )


def build_device_package(
    callsign: str,
    mode: str,
    connect_host: str,
    connect_port: int,
    tak_ca_dir: str,
    *,
    use_enrollment: bool = False,
    group: str | None = None,
) -> tuple[bytes, str, str]:
    """端到端：簽 device 證 → 組 client p12 + truststore → 組 data package zip bytes。
    回 (zip_bytes, serial, fingerprint)。

    證源（同一把 ICS-TAK-SVC-CA，truststore 不變）：
    - `use_enrollment=False`（#315）：offline `_sign_with_tak_ca`（tak_ca_dir 的 tak-ca.key 直簽）。
    - `use_enrollment=True`（#429）：ICS 代理 TAK :8446 signClient（建 managed user → CSR → 簽）
      → **證進 TAK 帳本**（對齊 #401/#318）。`group`＝初始群（預設 neutral fail-closed）。

    serial 供 #317 盤點 / #318；fingerprint（SHA-256）供盤點/比對。tak_ca_dir 的 **tak-ca.pem** 兩模式
    都要（組 truststore）；enrollment 模式不用 tak-ca.key。
    raise CertIssuanceError（簽發失敗 / openssl 打包失敗）；ValueError（mode 非法 / host 空）。
    """
    if mode not in ("atak", "aware"):
        raise ValueError("mode 須為 atak 或 aware")
    # 防禦：callsign 驗證與 sink 同住（不只靠 HTTP 層），未來新增 caller 亦安全（security-review #315）。
    from repositories.account_cert_repo import is_valid_cert_cn

    if not is_valid_cert_cn((callsign or "").strip()):
        raise ValueError("callsign 不合法")
    host = (connect_host or "").strip()
    if not host:
        raise ValueError("connect_host 不可為空（對外 TAK 位址未設）")
    if use_enrollment:
        # #429 代理 enrollment：建 managed user + signClient（async）→ 證進 TAK 帳本。
        import asyncio

        from services.tak_enrollment import issue_via_enrollment

        cert_pem, key_pem, serial, fingerprint = asyncio.run(issue_via_enrollment(callsign, group))
    else:
        # 先簽（_sign_with_tak_ca 驗 tak-ca.pem + .key 兩檔皆在 → CertIssuanceError），再讀 .pem 作 truststore
        # （此時確定存在，不會 FileNotFoundError 漏出 500；code-review #315）。
        cert_pem, key_pem, serial, fingerprint = _sign_with_tak_ca(callsign, tak_ca_dir)
    with open(os.path.join(tak_ca_dir, "tak-ca.pem"), encoding="ascii") as f:
        tak_ca_pem = f.read()
    client_p12, truststore_p12 = _make_p12_materials(cert_pem, key_pem, tak_ca_pem, callsign)
    return assemble_package(mode, callsign, host, connect_port, client_p12, truststore_p12), serial, fingerprint
