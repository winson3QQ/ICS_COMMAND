"""#275 wave B-2 — 線上發證（選項 i 安全版）。

後端**不持 CA 鑰**：以 `step` CLI 向 step-ca **daemon**（provisioner token）請它簽一張
client 憑證，回傳 p12 bytes。CA 簽發鑰始終只在 daemon。未配置時呼叫端應回 503。

私鑰由本服務在臨時目錄產生並打進 p12（伺服器端產 key 模型，瀏覽器 mTLS 實務）；
完成即刪臨時目錄，不留存私鑰。provisioner 密碼以檔案傳給 step，不進 log。
"""

from __future__ import annotations

import os
import subprocess  # nosec B404 - 受控參數呼叫 step CLI（無 shell=True，無使用者字串拼接）
import tempfile

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


def issue_p12(cert_cn: str) -> bytes:
    """向 step-ca daemon 簽一張 CN=cert_cn 的 client 憑證，回傳 p12 bytes。

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

    with tempfile.TemporaryDirectory(prefix="ics-cert-") as td:
        root = os.path.join(td, "root.crt")
        crt = os.path.join(td, "client.crt")
        key = os.path.join(td, "client.key")
        p12 = os.path.join(td, "client.p12")
        p12pw = os.path.join(td, "p12pw")
        with open(p12pw, "w", encoding="ascii") as f:
            f.write(config.STEP_CLIENT_CERT_P12_PASS)

        # 1. 取 root（fingerprint 驗證，建立對 daemon API 的信任）
        r = _run(["ca", "root", root, "--ca-url", config.STEP_CA_URL,
                  "--fingerprint", config.step_ca_fingerprint(), "-f"])
        if r.returncode != 0:
            raise CertIssuanceError(f"取 root 失敗：{_tail(r.stderr)}")

        # 2. 向 daemon 請簽（provisioner token；本端不持 CA 鑰）
        r = _run(["ca", "certificate", cn, crt, key,
                  "--provisioner", config.STEP_CA_PROVISIONER,
                  "--provisioner-password-file", pw_file,
                  "--ca-url", config.STEP_CA_URL, "--root", root,
                  "--not-after", config.STEP_CLIENT_CERT_DURATION, "-f"])
        if r.returncode != 0:
            raise CertIssuanceError(f"簽發被拒：{_tail(r.stderr)}")

        # 3. 打包 p12（含私鑰，供瀏覽器/裝置匯入）
        r = _run(["certificate", "p12", p12, crt, key, "--password-file", p12pw])
        if r.returncode != 0:
            raise CertIssuanceError(f"p12 打包失敗：{_tail(r.stderr)}")

        with open(p12, "rb") as f:
            return f.read()


def _tail(s: str | None, n: int = 200) -> str:
    """取 step stderr 末段做錯誤訊息（避免洩漏過多內部細節；密碼不會出現在 stderr）。"""
    return (s or "").strip().splitlines()[-1][:n] if (s or "").strip() else "unknown"
