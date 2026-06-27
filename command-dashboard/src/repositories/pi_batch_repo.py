# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
pi_batch_repo.py — 上游節點批次推送資料存取層

kept for upstream node federation (PWA / Pi reconnection); see ROADMAP P1-04 / Wave 7+
Shelter / Medical PWA 未來回流時，本層介面直接承接，無需架構翻修。
"""

from core.database import get_conn

from ._helpers import now_utc
from .pi_node_repo import touch_pi_node_data


def insert_pi_batch(unit_id: str, pushed_at: str, records_json: str) -> int:
    now = now_utc()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO pi_received_batches (unit_id, pushed_at, received_at, records_json) VALUES (?,?,?,?)",
            (unit_id, pushed_at, now, records_json),
        )
    touch_pi_node_data(unit_id)
    return cur.lastrowid


def get_latest_pi_batch(unit_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, unit_id, pushed_at, received_at, records_json "
            "FROM pi_received_batches WHERE unit_id=? "
            "ORDER BY received_at DESC LIMIT 1",
            (unit_id,),
        ).fetchone()
    return dict(row) if row else None


def get_recent_pi_batches(unit_id: str, limit: int = 40) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, unit_id, pushed_at, received_at, records_json "
            "FROM pi_received_batches WHERE unit_id=? "
            "ORDER BY received_at DESC LIMIT ?",
            (unit_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]
