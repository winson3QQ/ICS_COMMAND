# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""services/wg_provision.py — #434 ICS 端 WireGuard peer 控制面（發證連帶配 VPN 的接縫）。

機制（對照 services/tak_user_enroll 的 registrar 模式）：
WG 改跑進 Linux 容器（`deploy/perimeter/wireguard/container/`）後，ICS 即可經**共享卷檔佇列**驅動它。
本模組（ICS 端）寫請求檔（atomic：先 .tmp 再 rename）→ 輪詢結果 → 回 {ok, reason}；容器內的
`wg-peer-registrar.sh` 收請求後跑 `wg set` 即時加/刪 peer + 持久化。

**best-effort**：queue 未配置 / 容器沒跑 / 逾時 / 失敗 → 回 ok=False，**不 raise**（不拖垮發證主流程；
裝置仍拿到 TAK 證，只是 VPN peer 待補/重試）。輸入（pubkey / allowed_ip）在此先驗一道，容器內再驗一道。
"""

from __future__ import annotations

import base64
import logging
import os
import re
import time
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from core import config

log = logging.getLogger(__name__)

# WireGuard public key＝base64 32 bytes → 43 字 +「=」（wg genkey | wg pubkey 標準輸出）。
_PUBKEY_RE = re.compile(r"^[A-Za-z0-9+/]{43}=$")
# allowed-ip＝本 VPN 子網內單一 /32（fail-closed，擋路由污染）。預設段見 config.WG_SUBNET_PREFIX。
_IP_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}/32$")


def is_configured() -> bool:
    """是否已配置 WG peer 控制佇列（未配置 → 發證不連帶配 peer，純 TAK 證流程不受影響）。"""
    return bool(getattr(config, "WG_QUEUE_DIR", ""))


def is_valid_pubkey(pubkey: str) -> bool:
    return bool(_PUBKEY_RE.match(pubkey or ""))


def is_valid_allowed_ip(allowed_ip: str) -> bool:
    """格式 + 落在配置子網（WG_SUBNET_PREFIX，如 '10.13.13.'）內，且非保留的 .0/.1。"""
    if not _IP_RE.match(allowed_ip or ""):
        return False
    prefix = getattr(config, "WG_SUBNET_PREFIX", "10.13.13.")
    if not allowed_ip.startswith(prefix):
        return False
    host = allowed_ip[len(prefix) :].split("/", 1)[0]
    return host.isdigit() and 2 <= int(host) <= 254


def _safe_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _submit_op(pubkey: str, allowed_ip: str, op: str, label: str, timeout_s: float) -> tuple[str, str]:
    """寫請求檔（4 行 pubkey/allowed_ip/op/label，atomic）→ 輪詢結果。回 (outcome, body)，
    outcome ∈ {"ok","err","timeout","write-failed"}。best-effort：**絕不 raise**。"""
    qdir = config.WG_QUEUE_DIR
    req_dir = os.path.join(qdir, "requests")
    res_dir = os.path.join(qdir, "results")
    req_id = uuid.uuid4().hex
    req_path = os.path.join(req_dir, f"{req_id}.req")
    tmp_path = req_path + ".tmp"
    res_path = os.path.join(res_dir, f"{req_id}.res")
    # label 濾成 ASCII 安全單行（記錄用，不進 wg 參數；戒慎避免換行破壞協定）。
    label = "".join(c for c in (label or "") if c.isascii() and c.isprintable() and c not in "\r\n")[:64]
    try:
        os.makedirs(req_dir, exist_ok=True)
        os.makedirs(res_dir, exist_ok=True)
        with open(tmp_path, "w", encoding="ascii") as f:
            f.write(f"{pubkey}\n{allowed_ip}\n{op}\n{label}\n")
        os.replace(tmp_path, req_path)
    except (OSError, ValueError) as exc:
        log.warning("[wg-provision] 寫請求檔失敗（best-effort 跳過）：%s", exc)
        _safe_unlink(tmp_path)
        return ("write-failed", str(exc))

    deadline = time.monotonic() + max(1.0, timeout_s)
    while time.monotonic() < deadline:
        if os.path.exists(res_path):
            try:
                with open(res_path, encoding="utf-8", errors="replace") as f:
                    body = f.read().strip()
            except OSError:
                body = ""
            _safe_unlink(res_path)
            return ("ok" if body.startswith("OK") else "err", body)
        time.sleep(0.3)

    _safe_unlink(req_path)
    log.warning("[wg-provision] op=%s 等容器逾時 %.1fs（best-effort；ics-wg 未啟動?）", op, timeout_s)
    return ("timeout", "")


def add_peer(pubkey: str, allowed_ip: str, label: str = "") -> dict:
    """把裝置 WG pubkey 註冊進容器 WG（指派 allowed_ip /32）。回 {ok, reason}。best-effort 不 raise。"""
    if not is_configured():
        return {"ok": False, "reason": "wg-not-configured"}
    if not is_valid_pubkey(pubkey):
        return {"ok": False, "reason": "bad-pubkey"}
    if not is_valid_allowed_ip(allowed_ip):
        return {"ok": False, "reason": "bad-ip"}
    outcome, body = _submit_op(pubkey, allowed_ip, "add", label, getattr(config, "WG_PEER_TIMEOUT_S", 8))
    if outcome == "ok":
        log.info("[wg-provision] peer 註冊 %s… → %s (%s)", pubkey[:12], allowed_ip, label)
        return {"ok": True, "reason": "ok", "allowed_ip": allowed_ip}
    return {"ok": False, "reason": outcome if outcome in ("timeout", "write-failed") else f"registrar:{body[:120]}"}


def remove_peer(pubkey: str) -> dict:
    """撤除裝置 WG peer（撤證連動撤 VPN）。回 {ok, reason}。best-effort 不 raise。"""
    if not is_configured():
        return {"ok": False, "reason": "wg-not-configured"}
    if not is_valid_pubkey(pubkey):
        return {"ok": False, "reason": "bad-pubkey"}
    outcome, body = _submit_op(pubkey, "", "remove", "", getattr(config, "WG_PEER_TIMEOUT_S", 8))
    if outcome == "ok":
        log.info("[wg-provision] peer 撤除 %s…", pubkey[:12])
        return {"ok": True, "reason": "removed"}
    return {"ok": False, "reason": outcome if outcome in ("timeout", "write-failed") else f"registrar:{body[:120]}"}


def gen_keypair() -> tuple[str, str]:
    """產 WireGuard keypair（X25519，純 Python，免 wg 工具 / 免 ICS 容器裝 wireguard-tools）。

    回 (private_b64, public_b64)，格式與 `wg genkey | wg pubkey` 相容（base64 32 bytes；clamping 在
    X25519 scalar-mult 時套用，故 raw private 之 public 與 `wg pubkey` 一致——已容器實證）。#434 採方案 B
    （ICS 產 keypair、私鑰夾進裝置包，與現行 p12 一站式對齊，對非技術測試者最省事）。
    """
    priv = X25519PrivateKey.generate()
    priv_raw = priv.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    pub_raw = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(priv_raw).decode("ascii"), base64.b64encode(pub_raw).decode("ascii")


def build_device_conf(
    private_key: str,
    address: str,
    server_pubkey: str,
    endpoint: str,
    *,
    allowed_ips: str = "",
    keepalive: int = 25,
) -> str:
    """組裝裝置端 WireGuard `.conf`（夾進裝置包 / 轉 QR）。

    allowed_ips 預設＝整個 VPN 子網（WG_SUBNET_PREFIX + '0/24'），只把 ICS/TAK 私網導進隧道（非全流量）。
    keepalive=25 撐 cellular CGNAT 對應、保漫遊不斷。
    """
    if not allowed_ips:
        prefix = getattr(config, "WG_SUBNET_PREFIX", "10.13.13.")
        allowed_ips = f"{prefix}0/24"
    return (
        "[Interface]\n"
        f"PrivateKey = {private_key}\n"
        f"Address = {address}\n\n"
        "[Peer]\n"
        f"PublicKey = {server_pubkey}\n"
        f"Endpoint = {endpoint}\n"
        f"AllowedIPs = {allowed_ips}\n"
        f"PersistentKeepalive = {keepalive}\n"
    )
