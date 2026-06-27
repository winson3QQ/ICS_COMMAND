# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""event_marker_repo — 事件↔感知標記 junction 關聯（P2-27）。

取代 `cop_entities.attributes.kind='event' + event_id` 的 JSON glue：
- **感知標記**（cop_entity）= 一等公民，感知層、可共享（不外流邊界由 P2-30 share adapter
  唯讀標記欄保證，本表不被它讀）。
- **事件**（event）= 事故層、留 ICS，**N:1 聚合**一或多個標記（N:M-ready）。

關係載於 `event_markers`（見 database.py `_m021`），cop_entities schema 不被事故層污染。
本 repo 提供關聯 CRUD + 導航鏈讀（P2-27 sub-goal 2：一處看「事→標記→決策」全脈絡）。
"""

import structlog

from core.database import get_conn

from ._helpers import NULL_SCOPE, row_to_dict
from .cop_entity_repo import _row_to_entity_dict  # JSON/bool 解碼，與全站 COP 端點同 shape

_log = structlog.get_logger()


def link_marker(event_id: str, cop_entity_uid: str, role: str = "primary") -> bool:
    """建立 event↔marker 關聯。idempotent（PK 撞→OR IGNORE）。回傳是否**新建**。

    FK 由 schema 保證：event_id / cop_entity_uid 任一不存在 → sqlite3.IntegrityError，
    由 caller 決定 best-effort 或拋（建議圖釘建立路徑 best-effort、不因關聯失敗擋主動作）。
    """
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO event_markers (event_id, cop_entity_uid, role) VALUES (?,?,?)",
            (event_id, cop_entity_uid, role),
        )
        return cur.rowcount > 0


def unlink_marker(event_id: str, cop_entity_uid: str) -> None:
    """解除單一關聯（不刪標記本體，標記生命週期獨立）。"""
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM event_markers WHERE event_id=? AND cop_entity_uid=?",
            (event_id, cop_entity_uid),
        )


def get_markers_for_event(event_id: str) -> list[dict]:
    """事件聚合的所有感知標記（JOIN cop_entities 回完整 entity + link_role）。

    含 soft-deleted 標記（deleted=1）—— 導航鏈要看完整脈絡（含已移除的標記歷史）；
    呼叫端要過濾可自行依 `deleted` 判。
    """
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT c.*, em.role AS link_role
                 FROM event_markers em
                 JOIN cop_entities c ON c.uid = em.cop_entity_uid
                WHERE em.event_id = ?
                ORDER BY em.created_at""",
            (event_id,),
        ).fetchall()
    # 用 cop_entity_repo 的 canonical decoder（JSON-decode attributes/visible_to + flags 轉 bool），
    # 否則 /chain 回的 marker shape 與其他 COP 端點不一致（attributes 變原始字串、flags 變 0/1）。
    # JOIN 多帶的 link_role 欄 _row_to_entity_dict 原樣保留。
    return [_row_to_entity_dict(r) for r in rows]


def get_events_for_marker(cop_entity_uid: str) -> list[dict]:
    """某標記關聯的事件（N:M-ready → 回 list，純 N:1 時通常 0 或 1 筆）。"""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT e.*, em.role AS link_role
                 FROM event_markers em
                 JOIN events e ON e.id = em.event_id
                WHERE em.cop_entity_uid = ?
                ORDER BY em.created_at""",
            (cop_entity_uid,),
        ).fetchall()
    return [row_to_dict(r) for r in rows]


def get_event_chain(event_id: str, scope) -> dict | None:
    """P2-27 sub-goal 2 導航鏈：一處看「事 → 標記 → 決策」完整脈絡。

    回傳 `{event, markers, decisions}`；event 不存在 → None。

    **`scope` = P1-14 exercise scope 守門（必填）**：由 router 的 `resolve_scope()` 解出
    （int=該場 / `NULL_SCOPE`=實戰/未分場池），與 `get_events(…, exercise_id)` 同模式。
    event 不在 caller 可見範圍 → **回 None（router 映 404，不洩漏跨場 event 的存在性）**。
    本函式只服務 PII 讀取，故 scope 不給預設值——強制每個 caller 顯式決定範圍，杜絕漏 scope。

    誠實邊界（尚缺 FK 的兩段，留後續 item）：
    - **報(chats)↔事**：chats 按 sender/group 收，無事件綁定 FK → P2-28 入向升級補。
    - **行(下行 tasking)↔事**：派任連結 originating event 依賴 P2-13 B → 該 slice 補。

    本函式先打通**已存在 FK 關係**的兩段（標記 via junction、決策 via primary_event_id）。
    """
    with get_conn() as conn:
        ev = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        if ev is None:
            return None
        # P1-14 scope 守門：NULL_SCOPE → 只准看實戰/未分場（exercise_id IS NULL）；int → 限該場。
        # 不符 → 視同不存在（與 router 的 missing 同走 404，不洩漏其他場 event 存在性）。
        ev_ex = ev["exercise_id"]
        out_of_scope = (ev_ex is not None) if scope is NULL_SCOPE else (ev_ex != scope)
        if out_of_scope:
            return None
        decisions = conn.execute(
            "SELECT * FROM decisions WHERE primary_event_id=? ORDER BY decision_seq, created_at",
            (event_id,),
        ).fetchall()
    return {
        "event": row_to_dict(ev),
        "markers": get_markers_for_event(event_id),
        "decisions": [row_to_dict(d) for d in decisions],
    }
