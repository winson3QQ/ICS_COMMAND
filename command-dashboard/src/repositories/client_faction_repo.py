# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""#343 — admin 對連線 TAK client（裝置 self-SA uid = client_key）的紅藍陣營分類（SoT）。

faction ∈ blue/red/neutral，server-authoritative（不信 client 自宣告的 CoT type/__group）。
per-exercise：同一裝置跨場可不同陣營；exercise_id IS NULL = 實戰池。唯一性靠
`idx_client_faction_scope_key`（COALESCE(exercise_id,-1), client_key）。

設計 SoT：docs/design/red-blue-faction-isolation.md。entity 端 faction 解析（歸屬鏈）在
services/cop_service.py；本 repo 只管「client → faction」這層分類資料。
"""

from __future__ import annotations

from datetime import UTC, datetime

from core.database import get_conn

from ._helpers import audit


def _iso_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_faction(exercise_id: int | None, client_key: str) -> str | None:
    """查某 client 在某場的陣營；未分類 → None（→ fail-closed）。

    exercise_id NULL（實戰池）以 COALESCE(-1) 對齊唯一性語意（NULL 不等於 NULL 的坑）。
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT faction FROM client_faction WHERE client_key=? AND COALESCE(exercise_id,-1)=COALESCE(?,-1)",
            (client_key, exercise_id),
        ).fetchone()
    return row["faction"] if row else None


def get_faction_map(exercise_id: int | None) -> dict[str, str]:
    """某場全部 client→faction 的對照（ingest / 重解析批次查避免 N+1）。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT client_key, faction FROM client_faction WHERE COALESCE(exercise_id,-1)=COALESCE(?,-1)",
            (exercise_id,),
        ).fetchall()
    return {r["client_key"]: r["faction"] for r in rows}


def list_factions(exercise_id: int | None) -> list[dict]:
    """列某場已分類的 client（admin tab 顯示用），依 callsign。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM client_faction WHERE COALESCE(exercise_id,-1)=COALESCE(?,-1) "
            "ORDER BY COALESCE(callsign, client_key)",
            (exercise_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def upsert_faction(
    exercise_id: int | None,
    client_key: str,
    faction: str,
    callsign: str | None,
    operator: str,
) -> dict:
    """指派 / 改 client 陣營（per-exercise upsert）。回完整列。

    手動 upsert（非 ON CONFLICT）以正確處理 exercise_id NULL 的唯一性（複合鍵含 NULL 坑）。
    """
    now = _iso_now()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM client_faction WHERE client_key=? AND COALESCE(exercise_id,-1)=COALESCE(?,-1)",
            (client_key, exercise_id),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE client_faction SET faction=?, callsign=COALESCE(?, callsign), "
                "classified_by=?, classified_at=? WHERE id=?",
                (faction, callsign, operator, now, existing["id"]),
            )
            row_id = existing["id"]
        else:
            cur = conn.execute(
                "INSERT INTO client_faction (exercise_id, client_key, callsign, faction, classified_by, classified_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (exercise_id, client_key, callsign, faction, operator, now),
            )
            row_id = cur.lastrowid
        row = conn.execute("SELECT * FROM client_faction WHERE id=?", (row_id,)).fetchone()
    audit(
        operator,
        None,
        "client_faction_classify",
        "client_faction",
        client_key,
        {"exercise_id": exercise_id, "faction": faction, "callsign": callsign},
        exercise_id=exercise_id,  # #475：帶場別 → 重分隊進該場 AAR timeline（原漏傳=None、timeline 撈不到）
    )
    return dict(row)
