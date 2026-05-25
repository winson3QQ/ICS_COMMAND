"""
api/test_events_api.py — 事件管理 HTTP 端對端測試
"""

import pytest

pytestmark = pytest.mark.api

_VALID_EVENT = {
    "reported_by_unit": "shelter",
    "event_type": "fire",
    "severity": "critical",
    "description": "測試火災事件",
    "operator_name": "admin",
}


class TestCreateEvent:
    def test_success(self, client, auth):
        r = client.post("/api/events", json=_VALID_EVENT, headers=auth)
        assert r.status_code == 200
        body = r.json()
        assert body["event_code"].startswith("EV-")

    def test_invalid_severity_422(self, client, auth):
        bad = {**_VALID_EVENT, "severity": "high"}  # high 不合法
        r = client.post("/api/events", json=bad, headers=auth)
        assert r.status_code == 422

    def test_invalid_unit_422(self, client, auth):
        bad = {**_VALID_EVENT, "reported_by_unit": "unknown_unit"}
        r = client.post("/api/events", json=bad, headers=auth)
        assert r.status_code == 422

    def test_missing_operator_name_422(self, client, auth):
        bad = {k: v for k, v in _VALID_EVENT.items() if k != "operator_name"}
        r = client.post("/api/events", json=bad, headers=auth)
        assert r.status_code == 422

    def test_with_exercise_id(self, client, auth, active_exercise):
        payload = {**_VALID_EVENT, "exercise_id": active_exercise["id"]}
        r = client.post("/api/events", json=payload, headers=auth)
        assert r.status_code == 200


class TestListEvents:
    def test_empty(self, client, auth):
        r = client.get("/api/events", headers=auth)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_after_create(self, client, auth):
        client.post("/api/events", json=_VALID_EVENT, headers=auth)
        r = client.get("/api/events", headers=auth)
        assert len(r.json()) >= 1

    def test_filter_by_status(self, client, auth):
        r = client.get("/api/events?status=open", headers=auth)
        assert r.status_code == 200
        for ev in r.json():
            assert ev["status"] == "open"


class TestPatchEventStatus:
    def test_in_progress(self, client, auth):
        ev_id = client.post("/api/events", json=_VALID_EVENT,
                            headers=auth).json()["id"]
        r = client.patch(f"/api/events/{ev_id}/status?status=in_progress&operator=admin",
                         headers=auth)
        assert r.status_code == 200

    def test_resolve(self, client, auth):
        ev_id = client.post("/api/events", json=_VALID_EVENT,
                            headers=auth).json()["id"]
        client.patch(f"/api/events/{ev_id}/status?status=in_progress&operator=admin",
                     headers=auth)
        r = client.patch(f"/api/events/{ev_id}/status?status=resolved&operator=admin",
                         headers=auth)
        assert r.status_code == 200

    def test_nonexistent_event_returns_4xx(self, client, auth):
        r = client.patch("/api/events/nonexistent/status?status=in_progress&operator=admin",
                         headers=auth)
        assert r.status_code in (400, 404)


class TestAddNote:
    def test_add_note(self, client, auth):
        ev_id = client.post("/api/events", json=_VALID_EVENT,
                            headers=auth).json()["id"]
        r = client.post(f"/api/events/{ev_id}/notes",
                        json={"text": "現場確認完成", "operator": "admin"},
                        headers=auth)
        assert r.status_code == 200


class TestEventsStrict:
    def test_create_then_list_contains_exact_event_id(self, client, auth):
        """新建事件後，list 應包含完全相同的 event_id"""
        created = client.post("/api/events", json=_VALID_EVENT, headers=auth).json()

        listing = client.get("/api/events", headers=auth).json()
        found = [ev for ev in listing if ev["id"] == created["id"]]
        assert len(found) == 1
        assert found[0]["event_code"] == created["event_code"]

    def test_patch_nonexistent_event_status_returns_4xx(self, client, auth):
        """PATCH 不存在事件的 status → 4xx（repo 拋 ValueError，router 轉為 400）"""
        r = client.patch(
            "/api/events/999999/status?status=in_progress&operator=admin",
            headers=auth,
        )
        assert r.status_code in (400, 404)

    def test_add_note_to_nonexistent_event_returns_404(self, client, auth):
        """POST notes 到不存在事件 → 404（router 明確轉換 ValueError → 404）"""
        r = client.post(
            "/api/events/999999/notes",
            headers=auth,
            json={"text": "missing", "operator": "admin"},  # schema 要 text 不是 note
        )
        assert r.status_code == 404
