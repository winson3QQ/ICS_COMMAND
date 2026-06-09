"""
integration/test_decision_fk.py — P2-31（#184）decisions / ai_recommendations FK 規範化

驗證 m023/m024 rebuild 後：
- decisions 三欄補 FK（primary_event_id→events、parent_decision_id/superseded_by→decisions），
  全 ON DELETE SET NULL（問責鏈不隨上游硬刪而消失）
- ai_recommendations.related_decision_id 由 INTEGER 改 TEXT + FK→decisions
- delete_exercise 白名單刪除順序（events→…→decisions→ai_recommendations）在 FK on 下不炸
- 整庫 foreign_key_check 乾淨
"""

import pytest

from core.database import get_conn

pytestmark = pytest.mark.integration

_BASE = {
    "decision_type": "evac",
    "severity": "high",
    "decision_title": "撤離計畫",
    "impact_description": "影響範圍：全區",
    "suggested_action_a": "立即撤離",
    "created_by": "admin",
}

_EVENT = {
    "reported_by_unit": "shelter",
    "event_type": "fire",
    "severity": "critical",
    "description": "x",
    "operator_name": "admin",
}


def _fk_map(conn, table):
    """{from_col: (table, on_delete)} from PRAGMA foreign_key_list。"""
    rows = conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()  # nosec B608 — 常數表名
    return {r["from"]: (r["table"], r["on_delete"]) for r in rows}


def _col_type(conn, table, col):
    return next((r[2] for r in conn.execute(f"PRAGMA table_info({table})") if r[1] == col), None)  # nosec B608


class TestDecisionFkSchema:
    def test_decisions_has_three_set_null_fks(self, tmp_db):
        with get_conn() as conn:
            fks = _fk_map(conn, "decisions")
        assert fks["primary_event_id"] == ("events", "SET NULL")
        assert fks["parent_decision_id"] == ("decisions", "SET NULL")
        assert fks["superseded_by"] == ("decisions", "SET NULL")

    def test_ai_rec_related_decision_id_is_text_fk(self, tmp_db):
        with get_conn() as conn:
            assert _col_type(conn, "ai_recommendations", "related_decision_id") == "TEXT"
            fks = _fk_map(conn, "ai_recommendations")
        assert fks["related_decision_id"] == ("decisions", "SET NULL")

    def test_foreign_key_check_clean(self, tmp_db):
        """fresh init 後整庫無 FK 違規。"""
        with get_conn() as conn:
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        assert violations == []


class TestDecisionFkEnforced:
    def test_dangling_primary_event_rejected(self, tmp_db):
        """FK on：建決策指向不存在 event → 被擋（證明 FK 真強制，非裝飾）。"""
        import sqlite3
        from repositories.decision_repo import create_decision
        with pytest.raises(sqlite3.IntegrityError):
            create_decision({**_BASE, "primary_event_id": "no-such-event"})

    def test_null_primary_event_ok(self, tmp_db):
        """primary_event_id 為 NULL（指揮官無事件直接決策）→ FK 不擋。"""
        from repositories.decision_repo import create_decision, get_decisions
        dec = create_decision(_BASE.copy())
        found = next(d for d in get_decisions() if d["id"] == dec["id"])
        assert found["primary_event_id"] is None


class TestOnDeleteSetNull:
    def test_event_delete_nulls_decision_ref(self, tmp_db):
        """硬刪 event → 決策本體保留、primary_event_id 變 NULL（問責紀錄不消失）。"""
        from repositories.event_repo import create_event
        from repositories.decision_repo import create_decision, get_decisions
        ev = create_event(_EVENT.copy())
        dec = create_decision({**_BASE, "primary_event_id": ev["id"]})
        with get_conn() as conn:
            conn.execute("DELETE FROM events WHERE id=?", (ev["id"],))
        found = next((d for d in get_decisions() if d["id"] == dec["id"]), None)
        assert found is not None  # 決策仍在
        assert found["primary_event_id"] is None  # 參照被 SET NULL

    def test_parent_decision_delete_nulls_self_ref(self, tmp_db):
        """硬刪上游決策 → 下游 parent_decision_id 變 NULL（自我參照 SET NULL）。"""
        from repositories.decision_repo import create_decision, get_decisions
        parent = create_decision(_BASE.copy())
        child = create_decision({**_BASE, "parent_decision_id": parent["id"]})
        with get_conn() as conn:
            conn.execute("DELETE FROM decisions WHERE id=?", (parent["id"],))
        found = next((d for d in get_decisions() if d["id"] == child["id"]), None)
        assert found is not None
        assert found["parent_decision_id"] is None


class TestDeleteExerciseCascadeWithFk:
    def test_delete_exercise_does_not_crash_under_fk(self, tmp_db):
        """載重測試：events 先於 decisions、decisions 先於 ai_recommendations 被刪，
        SET NULL 路徑使 FK on 下整場級聯不炸。"""
        from repositories.exercise_repo import create_exercise, delete_exercise
        from repositories.event_repo import create_event
        from repositories.decision_repo import create_decision
        from repositories.ai_repo import create_recommendation, update_outcome

        ex = create_exercise({"name": "刪除測試", "type": "ttx"})
        exid = ex["id"]
        ev = create_event(_EVENT.copy(), exercise_id=exid)
        dec = create_decision({**_BASE, "primary_event_id": ev["id"]}, exercise_id=exid)
        rec = create_recommendation(exid, "decision", "建議撤離")
        update_outcome(rec["id"], True, related_decision_id=dec["id"])

        cleared = delete_exercise(exid)  # 不應拋 FK 例外
        assert "skipped" not in cleared
        with get_conn() as conn:
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
            assert conn.execute(
                "SELECT COUNT(*) FROM decisions WHERE exercise_id=?", (exid,)
            ).fetchone()[0] == 0


class TestRebuildScrub:
    def test_rebuild_nulls_dangling_refs(self, tmp_db):
        """模擬舊 DB（無 FK + dangling 資料）→ down 退回無 FK、塞 dangling、再 up rebuild：
        scrub 把指向不存在 event / decision 的欄位 null 化，foreign_key_check 乾淨。"""
        from core.database import _m023_decisions_fk, _m023_decisions_fk_down
        _ins = (
            "INSERT INTO decisions "
            "(id, primary_event_id, parent_decision_id, decision_type, severity, "
            " decision_title, impact_description, suggested_action_a, created_by, created_at) "
            "VALUES (?,?,?, 'evac','high','t','i','a','admin','2026-01-01T00:00:00Z')"
        )
        conn = get_conn()
        try:
            _m023_decisions_fk_down(conn)  # 退回無 FK
            conn.execute(_ins, ("d-dangling-evt", "ghost-event", None))
            conn.execute(_ins, ("d-dangling-dec", None, "ghost-decision"))
            conn.commit()
            _m023_decisions_fk(conn)  # 重新加 FK + scrub
            r1 = conn.execute("SELECT primary_event_id FROM decisions WHERE id='d-dangling-evt'").fetchone()
            r2 = conn.execute("SELECT parent_decision_id FROM decisions WHERE id='d-dangling-dec'").fetchone()
            assert r1["primary_event_id"] is None  # dangling event ref → NULL
            assert r2["parent_decision_id"] is None  # dangling self ref → NULL
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        finally:
            conn.close()
