"""
integration/test_exercise_repo.py — 演練 CRUD 與 mutex 邏輯
"""

import pytest

pytestmark = pytest.mark.integration


class TestCreateExercise:
    def test_basic_create(self, tmp_db):
        from repositories.exercise_repo import create_exercise
        ex = create_exercise({"name": "測試演練", "type": "ttx"})
        assert ex["id"] is not None
        assert ex["name"] == "測試演練"
        assert ex["status"] == "setup"

    def test_defaults(self, tmp_db):
        from repositories.exercise_repo import create_exercise
        ex = create_exercise({"name": "X", "type": "real"})
        assert ex["mutex_locked"] == 0
        assert ex["ended_at"] is None

    def test_get_exercise(self, tmp_db):
        from repositories.exercise_repo import create_exercise, get_exercise
        ex = create_exercise({"name": "get-test", "type": "ttx"})
        found = get_exercise(ex["id"])
        assert found["name"] == "get-test"

    def test_list_exercises(self, tmp_db):
        from repositories.exercise_repo import create_exercise, list_exercises
        create_exercise({"name": "A", "type": "ttx"})
        create_exercise({"name": "B", "type": "ttx"})
        exercises = list_exercises()
        assert len(exercises) >= 2


class TestActivateExercise:
    def test_activate_sets_status(self, tmp_db):
        from repositories.exercise_repo import (
            create_exercise, update_exercise_status, get_exercise
        )
        ex = create_exercise({"name": "E1", "type": "ttx"})
        update_exercise_status(ex["id"], "active", operator="admin")
        updated = get_exercise(ex["id"])
        assert updated["status"] == "active"
        assert updated["mutex_locked"] == 1
        assert updated["started_at"] is not None

    def test_only_one_active_allowed(self, tmp_db):
        from repositories.exercise_repo import (
            create_exercise, update_exercise_status
        )
        ex1 = create_exercise({"name": "E1", "type": "ttx"})
        ex2 = create_exercise({"name": "E2", "type": "ttx"})
        update_exercise_status(ex1["id"], "active", operator="admin")
        with pytest.raises(Exception, match="已有進行中"):
            update_exercise_status(ex2["id"], "active", operator="admin")

    def test_get_active_exercise(self, tmp_db):
        from repositories.exercise_repo import (
            create_exercise, update_exercise_status, get_active_exercise
        )
        assert get_active_exercise() is None
        ex = create_exercise({"name": "E1", "type": "ttx"})
        update_exercise_status(ex["id"], "active", operator="admin")
        active = get_active_exercise()
        assert active is not None
        assert active["id"] == ex["id"]


class TestArchiveExercise:
    def test_archive_releases_mutex(self, tmp_db):
        from repositories.exercise_repo import (
            create_exercise, update_exercise_status, get_exercise
        )
        ex = create_exercise({"name": "E1", "type": "ttx"})
        update_exercise_status(ex["id"], "active", operator="admin")
        update_exercise_status(ex["id"], "archived", operator="admin")
        updated = get_exercise(ex["id"])
        assert updated["status"] == "archived"
        assert updated["mutex_locked"] == 0
        assert updated["ended_at"] is not None

    def test_can_activate_new_after_archive(self, tmp_db):
        from repositories.exercise_repo import (
            create_exercise, update_exercise_status
        )
        ex1 = create_exercise({"name": "E1", "type": "ttx"})
        ex2 = create_exercise({"name": "E2", "type": "ttx"})
        update_exercise_status(ex1["id"], "active", operator="admin")
        update_exercise_status(ex1["id"], "archived", operator="admin")
        # 歸檔後可以啟動新演練，不應拋出例外
        update_exercise_status(ex2["id"], "active", operator="admin")
        from repositories.exercise_repo import get_active_exercise
        assert get_active_exercise()["id"] == ex2["id"]
