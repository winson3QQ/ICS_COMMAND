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


def issue_p12(cert_cn: str) -> tuple[bytes, str, str]:
    """向 step-ca daemon 簽一張 CN=cert_cn 的 client 憑證，回傳 (p12 bytes, 匯入密碼, serial)。

    serial（#232軌1-S1）= 簽出證的 serial number，供綁定存入 account_certs → 撤銷時同步 step-ca；
    擷取失敗回 ""（不擋發證）。密碼預設每張隨機（呼叫端負責顯示給管理者轉交）；不寫 log、不進 audit。
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

        # #232軌1-S1：擷取簽出證的 serial（供撤銷時同步 step-ca revoke → CRL）。best-effort——擷取失敗
        # 回 ""（不擋發證；該證即無 CRL 撤銷、仍靠 App 層 status='revoked' 即時失效）。
        serial = _cert_serial(crt)
        with open(p12, "rb") as f:
            return f.read(), p12_pass, serial


def _cert_serial(crt_path: str) -> str:
    """由簽出的 PEM 證擷 serial（step inspect JSON 的 serial_number；step 自己的表示法，餵回 step ca
    revoke 自洽）。失敗回 ""（best-effort，不擋發證）。"""
    r = _run(["certificate", "inspect", crt_path, "--format", "json"])
    if r.returncode != 0:
        return ""
    try:
        import json

        return str(json.loads(r.stdout).get("serial_number", "")).strip()
    except Exception:  # noqa: BLE001 - 擷 serial 失敗不擋發證
        return ""


def revoke_at_step_ca(serial: str, reason: str = "ICS admin revocation") -> bool:
    """#232軌1-S1：向 step-ca daemon 撤銷指定 serial（passive revocation，記進 CA badger db，
    供後續 CRL（軌1-S2）曝露）。**best-effort**——未配置 / daemon 不可達 / 撤銷被拒 → 回 False（不 raise），
    App 層撤銷（`account_certs`）不受影響（撤銷即時失效不依賴 CA 可達）。

    不持 CA 鑰：經 provisioner token 兩步（`ca token <serial> --revoke` → `ca revoke <serial> --token`），
    與 issue_p12 同一組 provisioner 認證（STEP_CA_PROVISIONER + 密碼檔）。
    回 True＝step-ca 已記撤銷；False＝未配置或失敗。
    """
    serial = (serial or "").strip()
    if not serial or not config.step_ca_configured():
        return False
    pw_file = config.STEP_CA_PROVISIONER_PASSWORD_FILE
    if not os.path.isfile(pw_file):
        return False
    # review-fix #2：整段包 try/except——_run 的 subprocess.TimeoutExpired（daemon 卡住）/ OSError
    # （step binary 缺）等皆不得逃逸成 500、破壞已 commit 的 App 層撤銷（best-effort 契約，docstring 承諾）。
    try:
        with tempfile.TemporaryDirectory(prefix="ics-revoke-") as td:
            root = os.path.join(td, "root.crt")
            # 0. 取 root（fingerprint 驗證，建立對 daemon 的信任）
            r = _run(
                [
                    "ca",
                    "root",
                    root,
                    "--ca-url",
                    config.STEP_CA_URL,
                    "--fingerprint",
                    config.step_ca_fingerprint(),
                    "-f",
                ]
            )
            if r.returncode != 0:
                return False
            # 1. 產撤銷 token（provisioner 授權；revoke 子命令不吃 --provisioner，須先換 token）
            r = _run(
                [
                    "ca",
                    "token",
                    serial,
                    "--revoke",
                    "--provisioner",
                    config.STEP_CA_PROVISIONER,
                    "--provisioner-password-file",
                    pw_file,
                    "--ca-url",
                    config.STEP_CA_URL,
                    "--root",
                    root,
                ]
            )
            if r.returncode != 0 or not (r.stdout or "").strip():
                return False
            token = r.stdout.strip().splitlines()[-1].strip()
            # 2. 憑 token 撤銷（記進 CA db → 供 CRL）
            r = _run(
                [
                    "ca",
                    "revoke",
                    serial,
                    "--token",
                    token,
                    "--reason",
                    reason,
                    "--ca-url",
                    config.STEP_CA_URL,
                    "--root",
                    root,
                ]
            )
            return r.returncode == 0
    except Exception:  # noqa: BLE001 - best-effort：timeout/OSError 等皆吞成 False，不破壞 App 層撤銷
        return False


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


def _cert_package_readme(cert_cn: str, server_host: str) -> str:
    """桌機憑證安裝包的 README（分平台步驟）。**走「乙」：密碼不放包內**，只在發證面板顯示——
    包被轉交也不洩匯入密碼（憑證 .p12 的保護不被削弱，對齊 #307 密碼不落檔的安全姿態）。"""
    url = f"https://{server_host}/"
    return (
        "ICS 指揮儀表板 — 登入憑證安裝包\n"
        "================================\n\n"
        f"帳號憑證 CN：{cert_cn}\n"
        f"儀表板網址：{url}\n\n"
        "本包內含：\n"
        f"  · {cert_cn}.p12   ← 你的登入身分憑證（mTLS）\n"
        "  · root-ca.pem      ← ICS 根憑證（用來信任儀表板伺服器）\n"
        "  · 本說明檔\n\n"
        "⚠ 匯入密碼：請用「發證畫面上顯示的密碼」。基於安全，密碼不放在本包內、也不隨包轉交。\n\n"
        "────────────────────────────────────────\n"
        "■ Windows\n"
        "  1. 雙擊 root-ca.pem →「安裝憑證」→ 存放區選「受信任的根憑證授權單位」。\n"
        "  2. 雙擊 .p12 → 存放位置「目前使用者」→ 輸入上述匯入密碼 → 完成。\n"
        f"  3. Edge/Chrome 開 {url} → 提示選憑證時選此張 → 輸入登入 PIN。\n\n"
        "■ macOS\n"
        "  1. 雙擊 root-ca.pem → 加入「登入」鑰匙圈；在「鑰匙圈存取」對該根憑證右鍵→「取得資訊」→\n"
        "     信任→「使用此憑證時」設「永遠信任」。\n"
        "  2. 雙擊 .p12 → 加入「登入」鑰匙圈 → 輸入匯入密碼。\n"
        f"  3. Safari/Chrome 開 {url} → 選此憑證 → 登入 PIN。（Chrome 換證後須完全關閉重開才生效。）\n\n"
        "■ Android\n"
        "  1. 設定 → 安全性 → 加密與憑證 → 安裝憑證 →「CA 憑證」選 root-ca.pem。\n"
        "  2. 同處「VPN 與 App 使用者憑證」選 .p12 → 輸入匯入密碼。\n"
        f"  3. 瀏覽器開 {url} → 選此憑證 → 登入 PIN。\n\n"
        "■ iPhone / iPad\n"
        "  iOS 請改用發證時選「iOS 描述檔（.mobileconfig）」——一點即裝、密碼免打，不需本 .p12 包。\n\n"
        "────────────────────────────────────────\n"
        "· 憑證綁定帳號：裝了此證才登得進（cert-bound session）。一個帳號可綁多台。\n"
        "· 遺失裝置請立即通知管理員撤銷此憑證。\n"
    )


def build_cert_package(cert_cn: str, p12_bytes: bytes, root_pem: str, server_host: str) -> bytes:
    """桌機登入憑證打包：zip{<cn>.p12, root-ca.pem, README.txt}（比照 TAK data package，收斂多平台安裝）。

    取代「裸 .p12 + 另抓 root CA + 一大段文字」——一個 zip 到位。**密碼走「乙」不入包**（見 README）。
    iOS 不走此包（.mobileconfig 已自成一檔）。檔名收斂 alnum+-_.，root CA 固定 root-ca.pem。
    """
    import io
    import zipfile

    safe = "".join(c for c in cert_cn if c.isalnum() or c in "-_.") or "client"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{safe}.p12", p12_bytes)
        z.writestr("root-ca.pem", root_pem)
        z.writestr("README.txt", _cert_package_readme(cert_cn, server_host))
    return buf.getvalue()


def _tail(s: str | None, n: int = 200) -> str:
    """取 step stderr 末段做錯誤訊息（避免洩漏過多內部細節；密碼不會出現在 stderr）。"""
    return (s or "").strip().splitlines()[-1][:n] if (s or "").strip() else "unknown"
