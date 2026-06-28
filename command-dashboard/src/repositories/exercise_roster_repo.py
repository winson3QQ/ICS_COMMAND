# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""#267 Slice 2 — 演習 roster（參與 + 編制）repo。

per-(exercise, cert CN) 的「參與 + 編制(unit)」。敵我另在 client_faction（#344），與此並列。
後刀 scope 解析查 is_in_roster × exercise_repo.ts_in_active_window 決定 entity 歸屬。表/索引見
database._m037_exercise_roster（UNIQUE(exercise_id, client_cn)）。
"""

from __future__ import annotations

from core.database import get_conn

from ._helpers import audit, now_utc


def upsert_member(exercise_id: int, client_cn: str, unit: str | None, operator: str) -> dict:
    """把 CN 加進某場 roster（或改其編制）。一場一 CN 一筆（UNIQUE upsert）。回該列。"""
    now = now_utc()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO exercise_roster (exercise_id, client_cn, unit, joined_at, joined_by) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(exercise_id, client_cn) DO UPDATE SET unit=excluded.unit",
            (exercise_id, client_cn, unit, now, operator),
        )
        row = conn.execute(
            "SELECT * FROM exercise_roster WHERE exercise_id=? AND client_cn=?", (exercise_id, client_cn)
        ).fetchone()
    audit(
        operator,
        None,
        "exercise_roster_upsert",
        "exercise_roster",
        client_cn,
        {"exercise_id": exercise_id, "unit": unit},
    )
    return dict(row)


def remove_member(exercise_id: int, client_cn: str, operator: str) -> bool:
    """把 CN 移出某場 roster。回是否真的刪到（False=本就不在）。"""
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM exercise_roster WHERE exercise_id=? AND client_cn=?", (exercise_id, client_cn))
    removed = cur.rowcount > 0
    if removed:
        audit(operator, None, "exercise_roster_remove", "exercise_roster", client_cn, {"exercise_id": exercise_id})
    return removed


def list_roster(exercise_id: int) -> list[dict]:
    """某場全部 roster 成員（CN + 編制），依 CN。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT client_cn, unit, joined_at, joined_by FROM exercise_roster WHERE exercise_id=? ORDER BY client_cn",
            (exercise_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def is_in_roster(exercise_id: int, client_cn: str) -> bool:
    """CN 是否在某場 roster（供 scope 解析「WHO」判定）。"""
    if not client_cn:
        return False
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM exercise_roster WHERE exercise_id=? AND client_cn=? LIMIT 1", (exercise_id, client_cn)
        ).fetchone()
    return row is not None


def unit_for(exercise_id: int, client_cn: str) -> str | None:
    """CN 在某場的編制（unit）；不在 roster 或未指定 → None。"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT unit FROM exercise_roster WHERE exercise_id=? AND client_cn=?", (exercise_id, client_cn)
        ).fetchone()
    return row["unit"] if row else None
