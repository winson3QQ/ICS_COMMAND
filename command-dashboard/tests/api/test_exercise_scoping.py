"""
api/test_exercise_scoping.py — P1-14 exercise scoping wiring 測試（issue #89 PR-1）

驗證：
  - events / cop create 由 server 端 active 場決定 exercise_id（不信任 client 帶的值）
  - 無 active 時 → exercise_id NULL（實戰/未分場池）
  - GET 預設只回當前 active 場
  - cop create 綁定 active（= P1-16 placement 圖釘綁 exercise 的點）
"""

import pytest

pytestmark = pytest.mark.api

_EVENT = {
    "reported_by_unit": "shelter",
    "event_type": "fire",
    "severity": "critical",
    "description": "scoping 測試事件",
    "operator_name": "admin",
}
_COP = {"type": "a-f-G-U-C", "lat": 25.0, "lon": 121.0, "callsign": "SCOPE-TEST"}


def _ev_by_desc(events, desc):
    return next((e for e in events if e.get("description") == desc), None)


class TestEventScoping:
    def test_create_stamps_active_and_ignores_client_value(self, client, auth, active_exercise):
        # 帶一個「假」exercise_id，server 應忽略、改用 active 場
        bogus = active_exercise["id"] + 99999
        r = client.post("/api/events", json={**_EVENT, "exercise_id": bogus}, headers=auth)
        assert r.status_code == 200
        # GET 預設只回 active 場 → 找得到，且 exercise_id == active（非 bogus）
        evs = client.get("/api/events", headers=auth).json()
        ev = _ev_by_desc(evs, _EVENT["description"])
        assert ev is not None, "active 場應看得到剛建的事件"
        assert ev["exercise_id"] == active_exercise["id"]
        assert ev["exercise_id"] != bogus

    def test_create_without_active_is_null(self, client, auth):
        # 無 active exercise → exercise_id 落 NULL（實戰池）
        r = client.post("/api/events", json=_EVENT, headers=auth)
        assert r.status_code == 200
        evs = client.get("/api/events", headers=auth).json()  # 無 active → 不過濾，回全部
        ev = _ev_by_desc(evs, _EVENT["description"])
        assert ev is not None
        assert ev["exercise_id"] is None


class TestCopScoping:
    def test_create_stamps_active_exercise(self, client, auth, active_exercise):
        # P1-16 銜接點：cop create 自動綁 active 場
        r = client.post("/api/cop/entities", json=_COP, headers=auth)
        assert r.status_code == 201
        assert r.json()["exercise_id"] == active_exercise["id"]

    def test_create_without_active_is_null(self, client, auth):
        r = client.post("/api/cop/entities", json=_COP, headers=auth)
        assert r.status_code == 201
        assert r.json()["exercise_id"] is None
