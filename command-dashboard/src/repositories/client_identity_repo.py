# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""#344 — 裝置 uid → TAK username（= cert CN）對照（紅藍分類綁穩定 CN 的橋接）。

CoT 流只有 uid；CN 來自 TAK 連線元資料（subscriptions/all uid→username）。本 repo 持久化此對照：
- 寫：面板載入 / 分類時，從 `tak_group_sync.list_online_subscriptions()` 得 {uid: username} 批次 upsert。
- 讀：ingest faction 解析（cop_service）同步把 uid 翻成 CN 再查分類；面板/傳播找某 CN 的所有 uid。

uid 為 PK（一裝置一身分；裝置換 username 罕見，後寫覆蓋）。表/索引見 database._m035_client_identity。
"""

from __future__ import annotations

from core.database import get_conn


def upsert_many(uid_to_username: dict[str, str]) -> int:
    """批次寫 uid→username（空字串/空鍵略過）。回實際寫入筆數。"""
    rows = [(u, n) for u, n in (uid_to_username or {}).items() if u and n]
    if not rows:
        return 0
    with get_conn() as conn:
        conn.executemany(
            "INSERT INTO client_identity (uid, username, updated_at) "
            "VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%SZ','now')) "
            "ON CONFLICT(uid) DO UPDATE SET username=excluded.username, updated_at=excluded.updated_at",
            rows,
        )
    return len(rows)


def get_username(uid: str) -> str | None:
    """uid → username（CN）；無對照 → None（caller fallback 用 uid 當鍵 → fail-closed）。"""
    if not uid:
        return None
    with get_conn() as conn:
        row = conn.execute("SELECT username FROM client_identity WHERE uid=?", (uid,)).fetchone()
    return row["username"] if row else None


def uids_for_username(username: str) -> list[str]:
    """某 username（CN）對應的所有 uid（裝置換 uid 後可能多筆）→ 供分類傳播到 entity（by uid）。"""
    if not username:
        return []
    with get_conn() as conn:
        rows = conn.execute("SELECT uid FROM client_identity WHERE username=?", (username,)).fetchall()
    return [r["uid"] for r in rows]
