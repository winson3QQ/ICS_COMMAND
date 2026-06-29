# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""wg_dump.py — 解析 `wg show <iface> dump` 輸出（#447 collector 的最內核）。

`wg show <iface> dump` 格式（tab 分隔）：
  第一行＝interface 自身：<private-key> <public-key> <listen-port> <fwmark>   ← 4 欄、**含 server 私鑰**
  其後每行＝一個 peer（8 欄）：
    <public-key> <preshared-key> <endpoint> <allowed-ips>
    <latest-handshake> <transfer-rx> <transfer-tx> <persistent-keepalive>

安全紅線（#447 DoD）：**server 私鑰永不解析、永不落帳、永不外露**。
做法：只保留「**恰好 8 欄**」的行——interface 行是 4 欄，天然被濾掉，無論它在第幾行；
比「丟第一行」更穩（位置假設失效也不會把私鑰當 peer 解析）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PeerSample:
    """單次觀測到的一個 peer 狀態（純資料、無 IO）。"""

    pubkey: str
    endpoint: str | None  # 原始 "ip:port"；(none) / 空 → None
    endpoint_ip: str | None
    endpoint_port: int | None
    allowed_ips: str
    last_handshake: int  # unix 秒；0 = 從未握手
    rx_bytes: int
    tx_bytes: int


def _to_int(s: str) -> int:
    try:
        return int(s)
    except (ValueError, TypeError):
        return 0


def _to_int_or_none(s: str) -> int | None:
    try:
        return int(s)
    except (ValueError, TypeError):
        return None


def parse_endpoint(ep: str) -> tuple[str | None, int | None, str | None]:
    """拆 endpoint → (ip, port, raw)。處理 IPv4 `1.2.3.4:51820`、IPv6 `[::1]:51820`、`(none)`。"""
    if not ep or ep == "(none)":
        return None, None, None
    raw = ep
    if ep.startswith("["):  # IPv6：[addr]:port
        host, _, rest = ep[1:].partition("]")
        port = rest.lstrip(":")
        return host, _to_int_or_none(port), raw
    host, sep, port = ep.rpartition(":")
    if not sep:  # 沒有冒號 → 視為純 host（防禦，理論上不會發生）
        return ep, None, raw
    return host, _to_int_or_none(port), raw


def parse_wg_dump(text: str) -> list[PeerSample]:
    """把 `wg show <iface> dump` 純文字解析成 PeerSample 清單。

    只接受恰好 8 欄的行（peer 行）；interface 行（4 欄、含私鑰）與任何格式不符的行一律跳過。
    """
    samples: list[PeerSample] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != 8:  # interface 行（4 欄/含私鑰）或畸形行 → 跳過
            continue
        pubkey, _psk, endpoint, allowed_ips, hs, rx, tx, _keepalive = parts
        ep_ip, ep_port, ep_raw = parse_endpoint(endpoint)
        samples.append(
            PeerSample(
                pubkey=pubkey,
                endpoint=ep_raw,
                endpoint_ip=ep_ip,
                endpoint_port=ep_port,
                allowed_ips=allowed_ips,
                last_handshake=_to_int(hs),
                rx_bytes=_to_int(rx),
                tx_bytes=_to_int(tx),
            )
        )
    return samples
