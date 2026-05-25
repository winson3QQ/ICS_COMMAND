"""
integration/test_event_repo.py — 事件建立、event_code 生成、狀態機
"""

import pytest

pytestmark = pytest.mark.integration

_BASE = {
    "reported_by_unit": "shelter",
    "event_type": "fire",
    "severity": "critical",
    "description": "測試事件",
    "operator_name": "admin",
}


class TestCreateEvent:
    def test_creates_with_code(self, tmp_db):
        from repositories.event_repo import create_event
        ev = create_event(_BASE.copy())
        assert ev["event_code"].startswith("EV-")

    def test_code_sequential(self, tmp_db):
        from repositories.event_repo import create_event
        ev1 = create_event(_BASE.copy())
        ev2 = create_event(_BASE.copy())
        # 序號遞增
        seq1 = int(ev1["event_code"].split("-")[-1])
        seq2 = int(ev2["event_code"].split("-")[-1])
        assert seq2 > seq1

    def test_status_defaults_to_open(self, tmp_db):
        from repositories.event_repo import create_event, get_events
        ev = create_event(_BASE.copy())
        events = get_events()
        found = next((e for e in events if e["id"] == ev["id"]), None)
        assert found["status"] == "open"

    def test_with_exercise_id(self, tmp_db):
        from repositories.exercise_repo import create_exercise
        from repositories.event_repo import create_event, get_events
        ex = create_exercise({"name": "E", "type": "ttx"})
        data = {**_BASE, "exercise_id": ex["id"]}
        ev = create_event(data, exercise_id=ex["id"])
        events = get_events(exercise_id=ex["id"])
        assert any(e["id"] == ev["id"] for e in events)


class TestEventStateMachine:
    def test_open_to_in_progress(self, tmp_db):
        from repositories.event_repo import create_event, update_event_status, get_events
        ev = create_event(_BASE.copy())
        update_event_status(ev["id"], "in_progress", "admin")
        events = get_events()
        found = next(e for e in events if e["id"] == ev["id"])
        assert found["status"] == "in_progress"

    def test_in_progress_to_resolved(self, tmp_db):
        from repositories.event_repo import create_event, update_event_status, get_events
        ev = create_event(_BASE.copy())
        update_event_status(ev["id"], "in_progress", "admin")
        update_event_status(ev["id"], "resolved", "admin")
        events = get_events(status="resolved")
        assert any(e["id"] == ev["id"] for e in events)

    def test_open_to_resolved_directly(self, tmp_db):
        from repositories.event_repo import create_event, update_event_status, get_events
        ev = create_event(_BASE.copy())
        update_event_status(ev["id"], "resolved", "admin")
        events = get_events(status="resolved")
        assert any(e["id"] == ev["id"] for e in events)

    def test_filter_by_status(self, tmp_db):
        from repositories.event_repo import create_event, update_event_status, get_events
        ev1 = create_event(_BASE.copy())
        ev2 = create_event(_BASE.copy())
        update_event_status(ev1["id"], "in_progress", "admin")
        open_events      = get_events(status="open")
        progress_events  = get_events(status="in_progress")
        assert any(e["id"] == ev2["id"] for e in open_events)
        assert any(e["id"] == ev1["id"] for e in progress_events)


class TestAddEventNote:
    def test_note_appended(self, tmp_db):
        from repositories.event_repo import create_event, add_event_note, get_events
        ev = create_event(_BASE.copy())
        add_event_note(ev["id"], "初步評估完成", "admin")
        events = get_events()
        found = next(e for e in events if e["id"] == ev["id"])
        assert found["notes"] is not None
        assert "初步評估完成" in str(found["notes"])
