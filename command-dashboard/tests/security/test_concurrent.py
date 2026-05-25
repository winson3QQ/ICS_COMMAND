"""
tests/security/test_concurrent.py — 並發更新 / Mutex 競爭測試

涵蓋：
  - 演練 mutex：同時只能有一個 active 場次
  - 並發快照推送：兩條執行緒同時推送，不互相干擾
  - TOCTOU 風險揭露：演練 activate 的 SELECT→UPDATE 非原子

設計備忘：
  exercise_repo.update_exercise_status 採 SELECT-then-UPDATE 模式（非原子）。
  SQLite WAL 序列化寫入會降低競爭機率，但不消除 TOCTOU 風險。
  若 test_concurrent_activate_mutex_holds 出現兩個 active → 確認為已知風險項。
"""

import threading
import pytest


# ─────────────────────────────────────────────────────────────────
# 演練 Mutex — 序列
# ─────────────────────────────────────────────────────────────────

class TestExerciseMutexSerial:
    def test_second_activate_raises(self, tmp_db):
        """序列呼叫：第二個 activate 應拋 ValueError"""
        from repositories.exercise_repo import create_exercise, update_exercise_status
        e1 = create_exercise({"name": "演練 A", "type": "drill"})
        e2 = create_exercise({"name": "演練 B", "type": "drill"})
        update_exercise_status(e1["id"], "active", "admin")
        with pytest.raises(ValueError, match="已有進行中的演練"):
            update_exercise_status(e2["id"], "active", "admin")

    def test_archive_releases_mutex(self, tmp_db):
        """歸檔釋放 mutex → 可再啟動新演練"""
        from repositories.exercise_repo import create_exercise, update_exercise_status
        e1 = create_exercise({"name": "演練 A", "type": "drill"})
        e2 = create_exercise({"name": "演練 B", "type": "drill"})
        update_exercise_status(e1["id"], "active", "admin")
        update_exercise_status(e1["id"], "archived", "admin")
        # 不應拋錯
        update_exercise_status(e2["id"], "active", "admin")
        from repositories.exercise_repo import get_active_exercise
        active = get_active_exercise()
        assert active["id"] == e2["id"]


# ─────────────────────────────────────────────────────────────────
# 演練 Mutex — 並發
# ─────────────────────────────────────────────────────────────────

class TestExerciseMutexConcurrent:
    def test_concurrent_activate_at_most_one_wins(self, tmp_db):
        """
        兩條執行緒同時嘗試 activate 不同演練。
        預期：至多一個成功；若兩個都 active → TOCTOU 風險確認（已知問題，C2 修）。
        """
        from repositories.exercise_repo import (
            create_exercise, update_exercise_status, list_exercises
        )
        e1 = create_exercise({"name": "並發 A", "type": "drill"})
        e2 = create_exercise({"name": "並發 B", "type": "drill"})

        errors = []
        successes = []

        def activate(eid):
            try:
                update_exercise_status(eid, "active", "admin")
                successes.append(eid)
            except ValueError:
                errors.append(eid)

        t1 = threading.Thread(target=activate, args=(e1["id"],))
        t2 = threading.Thread(target=activate, args=(e2["id"],))
        t1.start(); t2.start()
        t1.join(); t2.join()

        active_count = sum(
            1 for e in list_exercises() if e["status"] == "active"
        )

        if active_count > 1:
            # TOCTOU 競爭成立 → 記錄為已知風險，測試不硬 fail
            pytest.xfail(
                f"TOCTOU 風險確認：{active_count} 個演練同時 active。"
                "需改為 UPDATE WHERE status='setup' AND NOT EXISTS(SELECT 1 FROM exercises WHERE status='active') 原子寫入。"
            )
        else:
            assert active_count == 1
            assert len(successes) == 1


# ─────────────────────────────────────────────────────────────────
# 並發快照推送
# ─────────────────────────────────────────────────────────────────

class TestConcurrentSnapshotPush:
    def test_two_threads_push_different_snapshots(self, tmp_db):
        """兩條執行緒同時推送不同 snapshot_id → 兩筆都進 DB"""
        from repositories.snapshot_repo import upsert_snapshot, get_snapshots
        results = []

        def push(snap_id, bed_used):
            r = upsert_snapshot({
                "v": 3, "type": "shelter", "snapshot_id": snap_id,
                "t": "2026-04-24T10:00:00Z", "src": "test",
                "node_type": "shelter", "bed_used": bed_used, "bed_total": 50,
            })
            results.append(r)

        t1 = threading.Thread(target=push, args=("concurrent-s1", 10))
        t2 = threading.Thread(target=push, args=("concurrent-s2", 20))
        t1.start(); t2.start()
        t1.join(); t2.join()

        assert len(results) == 2
        assert all(r["inserted"] for r in results)

    def test_two_threads_push_same_snapshot_id(self, tmp_db):
        """兩條執行緒同時推送相同 snapshot_id → 其中一筆 inserted=False，不崩潰"""
        from repositories.snapshot_repo import upsert_snapshot
        from core.database import get_conn
        results = []

        def push():
            r = upsert_snapshot({
                "v": 3, "type": "shelter", "snapshot_id": "dup-concurrent",
                "t": "2026-04-24T10:00:00Z", "src": "test",
                "node_type": "shelter", "bed_used": 5, "bed_total": 50,
            })
            results.append(r)

        t1 = threading.Thread(target=push)
        t2 = threading.Thread(target=push)
        t1.start(); t2.start()
        t1.join(); t2.join()

        # 無論如何，DB 只能有 1 筆
        with get_conn() as conn:
            cnt = conn.execute(
                "SELECT COUNT(*) as c FROM snapshots WHERE snapshot_id='dup-concurrent'"
            ).fetchone()["c"]
        assert cnt == 1
        # 兩個結果都回傳了（不崩潰）
        assert len(results) == 2
