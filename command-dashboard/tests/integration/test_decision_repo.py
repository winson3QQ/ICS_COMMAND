"""
integration/test_decision_repo.py — 決策建立、決定、取代鏈
"""

import pytest

pytestmark = pytest.mark.integration

_BASE = {
    "decision_type": "evac",
    "severity": "high",
    "decision_title": "撤離計畫",
    "impact_description": "影響範圍：全區",
    "suggested_action_a": "立即撤離",
    "created_by": "admin",
}


class TestCreateDecision:
    def test_basic_create(self, tmp_db):
        from repositories.decision_repo import create_decision, get_decisions
        dec = create_decision(_BASE.copy())
        assert dec["id"] is not None
        # 查詢確認 status 預設為 pending
        found = next(d for d in get_decisions() if d["id"] == dec["id"])
        assert found["status"] == "pending"

    def test_with_primary_event(self, tmp_db):
        from repositories.event_repo import create_event
        from repositories.decision_repo import create_decision, get_decisions
        ev = create_event({
            "reported_by_unit": "shelter",
            "event_type": "fire",
            "severity": "critical",
            "description": "x",
            "operator_name": "admin",
        })
        dec = create_decision({**_BASE, "primary_event_id": ev["id"]})
        decisions = get_decisions()
        found = next((d for d in decisions if d["id"] == dec["id"]), None)
        assert found["primary_event_id"] == ev["id"]


class TestDecide:
    def test_approve_decision(self, tmp_db):
        from repositories.decision_repo import create_decision, decide, get_decisions
        dec = create_decision(_BASE.copy())
        decide(dec["id"], "approved", "指揮官A", "立即執行")
        decisions = get_decisions()
        found = next(d for d in decisions if d["id"] == dec["id"])
        assert found["status"] == "approved"
        assert found["decided_by"] == "指揮官A"

    def test_hold_decision(self, tmp_db):
        from repositories.decision_repo import create_decision, decide, get_decisions
        dec = create_decision(_BASE.copy())
        decide(dec["id"], "hold", "指揮官A", "暫緩觀察")
        decisions = get_decisions()
        found = next(d for d in decisions if d["id"] == dec["id"])
        assert found["status"] == "hold"

    def test_nonexistent_raises(self, tmp_db):
        from repositories.decision_repo import decide
        with pytest.raises(Exception):
            decide("nonexistent-id", "approved", "admin", "note")


class TestGetDecisions:
    def test_list_decisions(self, tmp_db):
        from repositories.decision_repo import create_decision, get_decisions
        create_decision(_BASE.copy())
        create_decision({**_BASE, "decision_title": "第二個決策"})
        decisions = get_decisions()
        assert len(decisions) >= 2

    def test_filter_by_exercise(self, tmp_db):
        from repositories.exercise_repo import create_exercise
        from repositories.decision_repo import create_decision, get_decisions
        ex = create_exercise({"name": "E", "type": "ttx"})
        create_decision({**_BASE, "exercise_id": ex["id"]}, exercise_id=ex["id"])
        create_decision(_BASE.copy())  # 無 exercise_id
        decisions = get_decisions(exercise_id=ex["id"])
        assert len(decisions) == 1
