"""kpi_service — 演習指標聚合（P2-21 子集 / #204）。

唯讀聚合既有表，**不寫** `exercise_kpis`（該表為 C0 沉睡 stub、零 writer；即時算即可，
快取等量測證明慢才加——同 #199「先量測再最佳化」紀律）。

誠實邊界（#204 切分）：量不出來的指標**回 null + reason**，不假裝——
- `medevac_reaction`：「承接」動作尚未實作（#204 決策 C，落地後本欄位接上）。
- `excheck_completion`：卡 P2-18（P2-22 TTX gate 後）。

時間差用 SQLite `julianday`（接受 ISO 8601 尾 Z），單位分鐘；occurred_at 已由
P2-20(A) review 修正保證入庫即 Z 正規化（event_repo iso_utc）。
"""

from core.database import get_conn


def _rows(conn, sql, *args):
    return conn.execute(sql, args).fetchall()  # nosec B608 — 本檔 SQL 全為常數字串


def _group_count(conn, sql, exercise_id) -> dict:
    # NULL 與 '' 都映「（未分類）」→ 須**相加**而非 dict comprehension 覆蓋（review #204-1：
    # 兩者並存時後者蓋前者、計數遺失）。
    out: dict = {}
    for r in _rows(conn, sql, exercise_id):
        key = r[0] or "（未分類）"
        out[key] = out.get(key, 0) + r[1]
    return out


def build_kpis(exercise_id: int) -> dict:
    """一場演習的 KPI 快照。全部即時算（資料量級＝單場，毋須快取）。"""
    with get_conn() as conn:
        # ── 事件 ────────────────────────────────────────────────────────
        ev_total = _rows(conn, "SELECT COUNT(*) FROM events WHERE exercise_id=?", exercise_id)[0][0]
        ev_by_severity = _group_count(
            conn, "SELECT severity, COUNT(*) FROM events WHERE exercise_id=? GROUP BY severity", exercise_id
        )
        ev_by_status = _group_count(
            conn, "SELECT status, COUNT(*) FROM events WHERE exercise_id=? GROUP BY status", exercise_id
        )
        # 處置時長：occurred_at → resolved_at（只算已結案；julianday 差 × 24×60 = 分鐘）
        res = _rows(
            conn,
            "SELECT COUNT(*), AVG((julianday(resolved_at)-julianday(occurred_at))*1440.0), "
            "MAX((julianday(resolved_at)-julianday(occurred_at))*1440.0) "
            "FROM events WHERE exercise_id=? AND resolved_at IS NOT NULL AND occurred_at IS NOT NULL",
            exercise_id,
        )[0]
        resolution = {
            "resolved_count": res[0],
            "avg_minutes": round(res[1], 1) if res[1] is not None else None,
            "max_minutes": round(res[2], 1) if res[2] is not None else None,
        }

        # ── 通聯（GeoChat）─────────────────────────────────────────────
        chat_total = _rows(conn, "SELECT COUNT(*) FROM chats WHERE exercise_id=?", exercise_id)[0][0]
        chat_by_group = _group_count(
            conn, 'SELECT "group", COUNT(*) FROM chats WHERE exercise_id=? GROUP BY "group"', exercise_id
        )

        # ── 決策 ────────────────────────────────────────────────────────
        dec = _rows(
            conn,
            "SELECT COUNT(*), SUM(CASE WHEN decided_at IS NOT NULL THEN 1 ELSE 0 END), "
            "AVG(CASE WHEN decided_at IS NOT NULL "
            "THEN (julianday(decided_at)-julianday(created_at))*1440.0 END) "
            "FROM decisions WHERE exercise_id=?",
            exercise_id,
        )[0]
        decisions = {
            "total": dec[0],
            "decided": dec[1] or 0,
            "avg_decide_minutes": round(dec[2], 1) if dec[2] is not None else None,
        }

        # ── 軌跡 / COP 活動量 ───────────────────────────────────────────
        trk = _rows(
            conn,
            "SELECT COUNT(*), COUNT(DISTINCT t.uid) FROM cop_entity_tracks t "
            "JOIN cop_entities e ON t.uid=e.uid WHERE e.exercise_id=?",
            exercise_id,
        )[0]

        # ── AAR 條目（含 bookmark；P2-22 gate「每場 ≥5 條課程標記」的對帳數字）──
        aar_by_category = _group_count(
            conn, "SELECT category, COUNT(*) FROM aar_entries WHERE exercise_id=? GROUP BY category", exercise_id
        )

    return {
        "exercise_id": exercise_id,
        "events": {
            "total": ev_total,
            "by_severity": ev_by_severity,
            "by_status": ev_by_status,
            "resolution": resolution,
        },
        "chats": {"total": chat_total, "by_group": chat_by_group},
        "decisions": decisions,
        "tracks": {"points": trk[0], "distinct_units": trk[1]},
        "aar_entries": {"by_category": aar_by_category},
        # 誠實邊界：量不出 → null + reason（#204；不假裝有數字）
        "medevac_reaction": {"value": None, "reason": "「承接」動作未實作（#204 決策 C 待落地）"},
        "excheck_completion": {"value": None, "reason": "EXCHECK 整合未做（P2-18，P2-22 gate 後）"},
    }
