# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""repositories/wg_peer_repo.py — #434 容器化 WireGuard peer 帳本 + IP pool（ICS 統管 VPN）。

發裝置證連帶配 WG peer（方案 B）→ 記 pubkey + 指派的 /32 + operator。撤證連動撤 peer。
配號在 get_conn() 交易內 SELECT 已用 → 取最低空號 → INSERT；m034 的 partial unique index
（status='active' 時 address 唯一）擋配號 race。對照 tak_device_cert_repo。
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from core import config
from core.database import get_conn

_COLS = ["id", "pubkey", "address", "callsign", "operator", "status", "created_at", "revoked_at", "revoked_by"]


def _iso_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def allocate_and_record(pubkey: str, callsign: str | None, operator: str) -> str | None:
    """配一個未用的 /32 + 記帳。回 address（如 '10.13.13.5/32'）；池滿（2..254 用盡）回 None。

    .0/.1 保留（網段/server），故配號 [2, 254]。同交易內 SELECT→INSERT，unique index 為 race 最後防線。
    """
    prefix = getattr(config, "WG_SUBNET_PREFIX", "10.13.13.")
    # 並發配號防護：SELECT→INSERT 間若別的 allocator 搶走同號，INSERT 撞 uq_wg_peers_active_addr
    # → IntegrityError；重算最低空號重試（而非冒成 500 穿透 best-effort 契約）。重試用盡視同池滿回 None。
    for _ in range(8):
        try:
            with get_conn() as conn:
                used: set[int] = set()
                for row in conn.execute("SELECT address FROM wg_peers WHERE status='active'"):
                    addr = row[0] or ""
                    if addr.startswith(prefix):
                        host = addr[len(prefix) :].split("/", 1)[0]
                        if host.isdigit():
                            used.add(int(host))
                host_n = next((h for h in range(2, 255) if h not in used), None)
                if host_n is None:
                    return None
                address = f"{prefix}{host_n}/32"
                conn.execute(
                    "INSERT INTO wg_peers(pubkey, address, callsign, operator, status, created_at) "
                    "VALUES(?, ?, ?, ?, 'active', ?)",
                    (pubkey, address, callsign, operator, _iso_now()),
                )
            return address
        except sqlite3.IntegrityError:
            continue  # 同號被搶 → 重算
    return None


def revoke_by_callsign(callsign: str, revoked_by: str) -> list[str]:
    """撤該 callsign 的 active peer（撤證連動）。回被撤的 pubkey 清單（供 wg_provision.remove_peer 連動）。"""
    with get_conn() as conn:
        pubkeys = [
            r[0] for r in conn.execute("SELECT pubkey FROM wg_peers WHERE callsign=? AND status='active'", (callsign,))
        ]
        if pubkeys:
            conn.execute(
                "UPDATE wg_peers SET status='revoked', revoked_at=?, revoked_by=? WHERE callsign=? AND status='active'",
                (_iso_now(), revoked_by, callsign),
            )
    return pubkeys


def revoke_by_pubkey(pubkey: str, revoked_by: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE wg_peers SET status='revoked', revoked_at=?, revoked_by=? WHERE pubkey=? AND status='active'",
            (_iso_now(), revoked_by, pubkey),
        )


def list_peers(status: str | None = None) -> list[dict]:
    q = f"SELECT {', '.join(_COLS)} FROM wg_peers"  # nosec B608 — 欄位名為常數、非使用者輸入
    args: tuple = ()
    if status:
        q += " WHERE status=?"
        args = (status,)
    q += " ORDER BY id DESC"
    with get_conn() as conn:
        return [dict(zip(_COLS, row, strict=False)) for row in conn.execute(q, args)]
