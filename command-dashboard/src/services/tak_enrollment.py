# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""services/tak_enrollment.py — #429 ICS 代理 TAK Certificate Enrollment（iTAK/ATAK 發證）。

取代 #315 的 offline 簽（`tak_device_cert._sign_with_tak_ca`）：證改由 **TAK 自己的 :8446 signClient
簽**（CoreConfig `<certificateSigning>` = ICS-TAK-SVC-CA，設定見 `deploy/tak-server/ENROLLMENT.md`）→
**簽出證進 TAK `certificate` 帳本** → 對齊 #401 reconcile + #318 撤銷，消除 offline 簽的撤銷盲區。

代理流程（per device，一次發證）：
  1. `create_managed_user` —— REST `POST /user-management/api/new-user`（admin cert，:8443）建密碼
     managed user（username=callsign、群=neutral fail-closed）。取代 #344 registrar usermod -f。
  2. `sign_client_csr` —— ICS 產 keypair + CSR（subject 須含 nameEntries：`O/OU/CN=callsign`，否則
     TAK 回 `CSR validation failed`；reality check 2026-06-27 實證），用該帳號 Basic-auth 打 :8446
     signClient（clientAuth=false 門）→ 回 leaf cert PEM（TAK 用 ICS-TAK-SVC-CA 簽）。
  3. 回 `(cert_pem, key_pem, serial, fingerprint)` —— 與 `_sign_with_tak_ca` 同簽名，供 `tak_device_cert`
     的 p12 打包 / 組包 / record_issued 原樣複用。

密碼：每台隨機產（過 UserManager 複雜度：≥15 + 大小寫+數字+特殊符），僅本流程內用於 signClient
認證，**裝置不需要**（證在 package 內），即用即棄、不落 DB。
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import ssl
import subprocess  # nosec B404 - 固定 binary（openssl）+ 參數列，無 shell、無使用者字串拼接
import tempfile
import uuid
from pathlib import Path

import aiohttp

from core import config
from services.cert_issuance import CertIssuanceError
from services.tak_rest_client import TakRestError, build_tak_rest_client

log = logging.getLogger(__name__)

_OPENSSL = "openssl"
_SPECIAL = "-_!@#$%^&*"  # UserManager 接受的特殊符子集（避開 shell/HTTP 麻煩字元）


def is_configured() -> bool:
    """代理 enrollment 是否可用：admin cert（建帳號）+ enroll URL（signClient）皆備。"""
    return bool(
        config.TAK_MARTI_URL and config.TAK_MARTI_ADMIN_CERT and config.TAK_MARTI_ADMIN_KEY and config.TAK_ENROLL_URL
    )


def _gen_password() -> str:
    """產合規隨機密碼（UserManager：≥15、含大小寫+數字+特殊符）。"""
    base = secrets.token_urlsafe(18)  # 含大小寫+數字，~24 字元
    # 保證四類齊全（token_urlsafe 不保證有大寫/特殊符）
    return f"A{base}a9{secrets.choice(_SPECIAL)}"


async def create_managed_user(username: str, password: str, group: str) -> None:
    """REST 建 TAK 密碼 managed user（admin cert，:8443）。失敗 raise CertIssuanceError。

    `new-user` body = NewUserModel {username, password, groupList, groupListIN, groupListOUT}。
    ⚠ **三個 group 欄都要帶**（device 讀+寫該群）——漏 IN/OUT → TAK `Arrays.asList(null)` NPE → 500
    （#344 update-groups 同坑，2026-06-27 #429 dogfood 實證）。冪等：同名重發更新（addOrUpdateUser）；
    呼叫端應已驗 callsign 不撞 infra（_is_infra_callsign）。
    """
    client = build_tak_rest_client(
        base_url=config.TAK_MARTI_URL,
        client_cert=config.TAK_MARTI_ADMIN_CERT,
        client_key=config.TAK_MARTI_ADMIN_KEY,
        cafile=config.TAK_CAFILE,
        allow_insecure_tls=config.TAK_ALLOW_INSECURE_TLS,
        min_interval_s=config.TAK_MARTI_MIN_INTERVAL_S,
        max_retries=config.TAK_MARTI_MAX_RETRIES,
    )
    try:
        await client.post_json(
            "/user-management/api/new-user",
            {
                "username": username,
                "password": password,
                "groupList": [group],
                "groupListIN": [group],
                "groupListOUT": [group],
            },
        )
    except (TakRestError, OSError, ssl.SSLError, ValueError) as exc:
        raise CertIssuanceError(f"建 TAK 帳號失敗（new-user）：{exc}") from exc
    finally:
        await client.close()


def _gen_csr(callsign: str) -> tuple[str, str]:
    """產 RSA keypair + CSR（subject `/O=ICS/OU=COP/CN=callsign`）。回 (csr_pem, key_pem)。

    nameEntries DN（O/OU）為 TAK signClient CSR 驗證必填（漏則 `CSR validation failed` 500，
    reality check 2026-06-27 實證）。openssl 固定參數、無 shell、callsign 已於呼叫端 allowlist 驗。
    """
    with tempfile.TemporaryDirectory() as td:
        key = Path(td) / "k.pem"
        csr = Path(td) / "c.csr"
        subj = f"/O=ICS/OU=COP/CN={callsign}"
        r = subprocess.run(  # nosec B603 - 固定 binary + 參數列
            [
                _OPENSSL,
                "req",
                "-new",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-keyout",
                str(key),
                "-out",
                str(csr),
                "-subj",
                subj,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if r.returncode != 0 or not csr.exists():
            raise CertIssuanceError(f"產 CSR 失敗：{r.stderr.strip()[:200]}")
        return csr.read_text("ascii"), key.read_text("ascii")


async def sign_client_csr(username: str, password: str, csr_pem: str) -> str:
    """用帳號 Basic-auth 打 :8446 signClient（clientAuth=false 門）→ 回簽出的 leaf cert PEM。

    端點契約（reality check 2026-06-27 實證）：`POST {enroll}/Marti/api/tls/signClient/v2
    ?clientUid=<uid>&version=<v>`、Content-Type text/plain、body=PEM CSR、HTTP Basic。
    **回 200 + JSON `{"signedCert": "<base64 DER>"}`**（非裸 PEM！dogfood 實證）→ 本函式解 JSON
    抽 signedCert（base64 DER）包成 PEM。非 200 / 無 signedCert → CertIssuanceError。
    """
    # TLS：對齊 tak_rest_client.build_marti_ssl_context 的 fail-closed 語意（無 cafile 又非 insecure → raise，
    # 不默默裸奔）。**不直接複用該 helper**：它強制 load_cert_chain（mTLS client 證），但 signClient 走 :8446
    # clientAuth=false + HTTP Basic、無 client 證 → 自建 server-only 驗證 context。
    ssl_ctx: ssl.SSLContext | bool
    if config.TAK_CAFILE:
        ssl_ctx = ssl.create_default_context(cafile=config.TAK_CAFILE)
    elif config.TAK_ALLOW_INSECURE_TLS:
        log.warning("[tak-enroll] allow_insecure_tls：signClient 不驗 server 憑證（MITM 風險，僅 dev）")
        ssl_ctx = False
    else:
        raise CertIssuanceError("signClient TLS 須 TAK_CAFILE 驗 server 憑證；dev 須顯式 TAK_ALLOW_INSECURE_TLS=True")
    url = config.TAK_ENROLL_URL.rstrip("/") + "/Marti/api/tls/signClient/v2"
    params = {"clientUid": f"ics-{uuid.uuid4().hex[:12]}", "version": "1.8"}
    auth = aiohttp.BasicAuth(username, password)
    timeout = aiohttp.ClientTimeout(total=30)
    try:
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as sess:
            async with sess.post(
                url,
                params=params,
                data=csr_pem.encode("ascii"),
                headers={"Content-Type": "text/plain"},
                auth=auth,
            ) as resp:
                text = await resp.text()
                if resp.status != 200:
                    raise CertIssuanceError(f"signClient HTTP {resp.status}")
    except (aiohttp.ClientError, OSError, ssl.SSLError) as exc:
        raise CertIssuanceError(f"signClient 連線失敗：{exc}") from exc
    return _parse_signed_cert(text)


def _parse_signed_cert(body: str) -> str:
    """解 signClient/v2 回應 → leaf cert PEM。

    v2 回 JSON `{"signedCert": "<base64 DER，含換行>", ...}`（dogfood 實證）→ 抽 signedCert 包成 PEM。
    保險：若回應本身就是裸 PEM（v1 / 其他版本），直接取第一張 CERTIFICATE。
    """
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        data = None
    if data is not None:
        # v2 正常回 dict {signedCert}；JSON 解析成功但非該形狀 = 非預期回應（給清楚錯誤，不誤導成「解析不出」）。
        if isinstance(data, dict):
            b64 = (data.get("signedCert") or "").strip()
            if b64:
                return "-----BEGIN CERTIFICATE-----\n" + b64 + "\n-----END CERTIFICATE-----\n"
        raise CertIssuanceError("signClient 回應 JSON 無 signedCert（非預期格式）")
    # 非 JSON → 保險：裸 PEM（v1 / 其他版本）取第一張 CERTIFICATE
    m = re.search(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", body, re.S)
    if not m:
        raise CertIssuanceError("signClient 回應解析不出憑證")
    return m.group(0) + "\n"


def _cert_serial_fingerprint(cert_pem: str) -> tuple[str, str]:
    """openssl 讀 leaf cert 的 serial（hex）+ SHA-256 fingerprint（冒號大寫，對齊 TAK certificate.hash）。"""
    with tempfile.TemporaryDirectory() as td:
        cp = Path(td) / "c.pem"
        cp.write_text(cert_pem, "ascii")
        out = subprocess.run(  # nosec B603
            [_OPENSSL, "x509", "-in", str(cp), "-noout", "-serial", "-fingerprint", "-sha256"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        ).stdout
    serial = ""
    fp = ""
    for line in out.splitlines():
        if line.startswith("serial="):
            serial = line.split("=", 1)[1].strip()
        elif "Fingerprint=" in line:
            fp = line.split("=", 1)[1].strip()
    return serial, fp


async def issue_via_enrollment(callsign: str, group: str | None = None) -> tuple[str, str, str, str]:
    """端到端代理發證：建帳號 → 產 CSR → signClient → 回 (cert_pem, key_pem, serial, fingerprint)。

    與 `tak_device_cert._sign_with_tak_ca` 同回傳形狀 → `tak_device_cert` 的 p12 打包 / 組包 / record_issued
    原樣複用，只是證源由 offline CA 換成 TAK signClient（進帳本）。簽失敗時帳號已建（冪等，重發即可）。
    """
    if not is_configured():
        raise CertIssuanceError("代理 enrollment 未配置（需 TAK_MARTI_ADMIN_CERT/KEY + TAK_ENROLL_URL）")
    grp = (group or config.TAK_ENROLL_DEFAULT_GROUP or "neutral").strip()
    password = _gen_password()
    csr_pem, key_pem = _gen_csr(callsign)
    await create_managed_user(callsign, password, grp)
    cert_pem = await sign_client_csr(callsign, password, csr_pem)
    serial, fingerprint = _cert_serial_fingerprint(cert_pem)
    return cert_pem, key_pem, serial, fingerprint
