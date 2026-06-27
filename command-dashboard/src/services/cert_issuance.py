# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""#275 wave B-2 — 線上發證（選項 i 安全版）。

後端**不持 CA 鑰**：以 `step` CLI 向 step-ca **daemon**（provisioner token）請它簽一張
client 憑證，回傳 p12 bytes。CA 簽發鑰始終只在 daemon。未配置時呼叫端應回 503。

私鑰由本服務在臨時目錄產生並打進 p12（伺服器端產 key 模型，瀏覽器 mTLS 實務）；
完成即刪臨時目錄，不留存私鑰。provisioner 密碼以檔案傳給 step，不進 log。
"""

from __future__ import annotations

import base64
import os
import secrets
import ssl
import subprocess  # nosec B404 - 受控參數呼叫 step CLI（無 shell=True，無使用者字串拼接）
import tempfile
import uuid
from xml.sax.saxutils import escape

import core.config as config


class CertIssuanceError(Exception):
    """發證失敗（step-ca 未配置 / daemon 不可達 / 簽發被拒）。"""


_STEP_BIN = os.getenv("STEP_BIN", "step")
_TIMEOUT = 30


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(  # nosec B603 - 固定 binary + 參數列（非 shell），CN 已上游驗證
        [_STEP_BIN, *args],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
        check=False,
    )


def _p12_password() -> str:
    """p12 匯入密碼。#307：未顯式設 STEP_CLIENT_CERT_P12_PASS → 每張隨機（廢弱默認
    icsclient）；顯式設了才用固定值（runbook 相容）。token_urlsafe(12) ≈ 16 字元。"""
    return config.STEP_CLIENT_CERT_P12_PASS or secrets.token_urlsafe(12)


def issue_p12(cert_cn: str) -> tuple[bytes, str]:
    """向 step-ca daemon 簽一張 CN=cert_cn 的 client 憑證，回傳 (p12 bytes, 匯入密碼)。

    密碼預設每張隨機（呼叫端負責顯示給管理者轉交）；不寫 log、不進 audit。
    raise CertIssuanceError：未配置、daemon 不可達、或簽發被 CA 拒絕。
    """
    if not config.step_ca_configured():
        raise CertIssuanceError("step-ca 線上發證未配置（STEP_CA_URL/指紋/provisioner 密碼）")
    cn = (cert_cn or "").strip()
    if not cn:
        raise CertIssuanceError("cert_cn 不可為空")

    pw_file = config.STEP_CA_PROVISIONER_PASSWORD_FILE
    if not os.path.isfile(pw_file):
        raise CertIssuanceError("provisioner 密碼檔不存在")

    p12_pass = _p12_password()
    with tempfile.TemporaryDirectory(prefix="ics-cert-") as td:
        root = os.path.join(td, "root.crt")
        crt = os.path.join(td, "client.crt")
        key = os.path.join(td, "client.key")
        p12 = os.path.join(td, "client.p12")
        p12pw = os.path.join(td, "p12pw")
        with open(p12pw, "w", encoding="utf-8") as f:
            f.write(p12_pass)

        # 1. 取 root（fingerprint 驗證，建立對 daemon API 的信任）
        r = _run(
            ["ca", "root", root, "--ca-url", config.STEP_CA_URL, "--fingerprint", config.step_ca_fingerprint(), "-f"]
        )
        if r.returncode != 0:
            raise CertIssuanceError(f"取 root 失敗：{_tail(r.stderr)}")

        # 2. 向 daemon 請簽（provisioner token；本端不持 CA 鑰）
        r = _run(
            [
                "ca",
                "certificate",
                cn,
                crt,
                key,
                "--provisioner",
                config.STEP_CA_PROVISIONER,
                "--provisioner-password-file",
                pw_file,
                "--ca-url",
                config.STEP_CA_URL,
                "--root",
                root,
                "--not-after",
                config.STEP_CLIENT_CERT_DURATION,
                "-f",
            ]
        )
        if r.returncode != 0:
            raise CertIssuanceError(f"簽發被拒：{_tail(r.stderr)}")

        # 3. 打包 p12（含私鑰，供瀏覽器/裝置匯入）
        #    --legacy：PBE+SHA1+RC2（憑證）/ PBE+SHA1+3DES（私鑰）舊式編碼。iOS 不吃 openssl3/
        #    step 預設的 PBES2/AES-256 p12（會誤報「密碼不正確」），故統一用 legacy 確保 iPhone/
        #    iPad 可安裝；桌機（Windows/Mac/Linux）對 legacy 同樣相容。dogfood 實證 2026-06-21。
        r = _run(["certificate", "p12", p12, crt, key, "--password-file", p12pw, "--legacy"])
        if r.returncode != 0:
            raise CertIssuanceError(f"p12 打包失敗：{_tail(r.stderr)}")

        with open(p12, "rb") as f:
            return f.read(), p12_pass


def fetch_root_ca_pem() -> str:
    """取 step-ca root CA 憑證 PEM（mobileconfig 內嵌信任根用）。fingerprint 驗證。"""
    if not config.step_ca_configured():
        raise CertIssuanceError("step-ca 線上發證未配置")
    with tempfile.TemporaryDirectory(prefix="ics-root-") as td:
        root = os.path.join(td, "root.crt")
        r = _run(
            ["ca", "root", root, "--ca-url", config.STEP_CA_URL, "--fingerprint", config.step_ca_fingerprint(), "-f"]
        )
        if r.returncode != 0:
            raise CertIssuanceError(f"取 root 失敗：{_tail(r.stderr)}")
        with open(root, encoding="ascii") as f:
            return f.read()


def build_mobileconfig(cert_cn: str, p12_bytes: bytes, p12_pass: str, root_pem: str, server_url: str) -> bytes:
    """#312：把 root CA + 裝置 p12（含內嵌密碼）包成 iOS .mobileconfig 設定描述檔。

    iOS 點開直接安裝「信任根 + mTLS 身分」，**密碼已內嵌→免手打**（隨機混合大小寫密碼
    在 iOS 安裝框手打/貼上皆卡的根治）。port 自 deploy/ics-validation/mtls/make-ios-profile.py。
    密碼隨檔內嵌＝此 .mobileconfig 敏感度同 p12，只走 mTLS 回 sysadmin、不進 log。
    """
    root_der = ssl.PEM_cert_to_DER_cert(root_pem)
    ca_uuid, id_uuid, top_uuid = (str(uuid.uuid4()).upper() for _ in range(3))
    cn_x = escape(cert_cn)
    url_x = escape(server_url)
    ca_b64 = base64.b64encode(root_der).decode("ascii")
    p12_b64 = base64.b64encode(p12_bytes).decode("ascii")
    pass_x = escape(p12_pass)
    top_desc = (
        f"安裝後本裝置即可從 {url_x} 登入 ICS 指揮儀表板（仍需登入 PIN）。"
        f"內含：信任根憑證 + 本裝置 mTLS 身分憑證（{cn_x}）。遺失裝置請通知管理員撤銷此憑證。"
    )
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>PayloadContent</key>
  <array>
    <dict>
      <key>PayloadType</key><string>com.apple.security.root</string>
      <key>PayloadVersion</key><integer>1</integer>
      <key>PayloadIdentifier</key><string>local.ics.ca.{ca_uuid}</string>
      <key>PayloadUUID</key><string>{ca_uuid}</string>
      <key>PayloadDisplayName</key><string>ICS 指揮部 根憑證 (CA)</string>
      <key>PayloadDescription</key><string>信任 ICS 內部憑證簽發機構，使本裝置能驗證指揮伺服器並出示裝置憑證。</string>
      <key>PayloadCertificateFileName</key><string>ics-root-ca.crt</string>
      <key>PayloadContent</key>
      <data>{ca_b64}</data>
    </dict>
    <dict>
      <key>PayloadType</key><string>com.apple.security.pkcs12</string>
      <key>PayloadVersion</key><integer>1</integer>
      <key>PayloadIdentifier</key><string>local.ics.identity.{id_uuid}</string>
      <key>PayloadUUID</key><string>{id_uuid}</string>
      <key>PayloadDisplayName</key><string>ICS 裝置憑證 — {cn_x}</string>
      <key>PayloadDescription</key><string>本裝置登入 ICS 指揮儀表板的 mTLS 身分憑證（第二因子）。</string>
      <key>PayloadCertificateFileName</key><string>{cn_x}.p12</string>
      <key>Password</key><string>{pass_x}</string>
      <key>PayloadContent</key>
      <data>{p12_b64}</data>
    </dict>
  </array>
  <key>PayloadType</key><string>Configuration</string>
  <key>PayloadVersion</key><integer>1</integer>
  <key>PayloadIdentifier</key><string>local.ics.enroll.{top_uuid}</string>
  <key>PayloadUUID</key><string>{top_uuid}</string>
  <key>PayloadOrganization</key><string>ICS 指揮部</string>
  <key>PayloadDisplayName</key><string>ICS 指揮部 裝置接入 — {cn_x}</string>
  <key>PayloadDescription</key><string>{top_desc}</string>
</dict>
</plist>
"""
    return plist.encode("utf-8")


def _tail(s: str | None, n: int = 200) -> str:
    """取 step stderr 末段做錯誤訊息（避免洩漏過多內部細節；密碼不會出現在 stderr）。"""
    return (s or "").strip().splitlines()[-1][:n] if (s or "").strip() else "unknown"
