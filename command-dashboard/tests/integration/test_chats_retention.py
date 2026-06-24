"""integration/test_chats_retention.py — #348-F10 通聯（chats）PII TTL retention

驗證（鏡像 test_track_retention，軸用 received_at）：
- cleanup_expired_chats：過期刪 / 未過期留 / 開關關閉 no-op / 刪除留 RETENTION_CLEANUP(chats) audit
- TTL 天數防呆（≥1，CHATS_TTL_DAYS=0 不全清）
- chats 補進 _EXERCISE_SCOPED_TABLES：刪演習連帶清該場通聯；exercise_id=NULL（實戰）不誤刪
- 與 tracks 共用同一 ttl_enabled() 政策開關
"""

import pytest

from core.database import get_conn
from repositories.exercise_repo import create_exercise, delete_exercise
from services import retention_service

pytestmark = pytest.mark.integration


def _insert_chat(message, received_offset, exercise_id):
    """插一筆 chat，received_at 以 SQLite now+offset（如 '-200 days'）回填。"""
    with get_conn() as conn:
        conn.execute(
            'INSERT INTO chats (sender_uid, callsign, message, "group", lat, lon, time, '
            "exercise_id, faction, received_at) VALUES "
            "(?,?,?,?,?,?, strftime('%Y-%m-%dT%H:%M:%SZ','now'), ?, NULL, "
            "strftime('%Y-%m-%dT%H:%M:%SZ','now',?))",
            ("u1", "CS-1", message, "all", 24.0, 120.0, exercise_id, received_offset),
        )


def _chat_count(exercise_id=None):
    with get_conn() as conn:
        if exercise_id is None:
            return conn.execute("SELECT COUNT(*) FROM chats").fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM chats WHERE exercise_id=?", (exercise_id,)).fetchone()[0]


class TestCleanup:
    def test_default_enabled_expired_deleted_recent_kept(self, tmp_db):
        exid = create_exercise({"name": "R", "type": "ttx"})["id"]
        _insert_chat("old", "-200 days", exid)
        _insert_chat("new", "+0 days", exid)
        assert retention_service.ttl_enabled() is True  # 與 tracks 同政策開關，預設生效
        deleted = retention_service.cleanup_expired_chats()
        assert deleted == 1 and _chat_count() == 1  # 過期刪、新留
        with get_conn() as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE action_type='RETENTION_CLEANUP' AND target_table='chats'"
            ).fetchone()[0]
        assert n == 1  # 個資刪除留痕（target_table=chats）

    def test_disabled_is_noop(self, tmp_db):
        exid = create_exercise({"name": "R", "type": "ttx"})["id"]
        _insert_chat("old", "-200 days", exid)
        retention_service.set_ttl_enabled(False)
        assert retention_service.cleanup_expired_chats() == 0
        assert _chat_count() == 1  # 全留

    def test_ttl_days_floor_guard(self, tmp_db, monkeypatch):
        """CHATS_TTL_DAYS 誤設 0 → 夾到 ≥1 天，拒絕「全清」誤設。"""
        from core import config

        exid = create_exercise({"name": "R", "type": "ttx"})["id"]
        _insert_chat("now", "+0 days", exid)
        monkeypatch.setattr(config, "CHATS_TTL_DAYS", 0)
        retention_service.cleanup_expired_chats()
        assert _chat_count() >= 1  # 「現在」那筆不被 TTL=0 清掉


class TestExerciseCascade:
    def test_delete_exercise_cascades_chats_but_spares_null_scope(self, tmp_db):
        """#348-F10：chats 補進 _EXERCISE_SCOPED_TABLES → 刪演習清該場通聯；
        exercise_id=NULL（實戰/未分場廣播）不受 WHERE exercise_id=? 影響、保留。"""
        exid = create_exercise({"name": "to-delete", "type": "ttx"})["id"]
        _insert_chat("scoped", "+0 days", exid)
        _insert_chat("live-broadcast", "+0 days", None)  # exercise_id=NULL
        assert _chat_count(exid) == 1
        delete_exercise(exid)
        assert _chat_count(exid) == 0  # 該場通聯隨刪場清掉
        with get_conn() as conn:
            live = conn.execute("SELECT COUNT(*) FROM chats WHERE exercise_id IS NULL").fetchone()[0]
        assert live == 1  # 實戰廣播不誤刪
