"""
api/test_exercises_api.py — 演練管理 HTTP 端對端測試
"""

import pytest

pytestmark = pytest.mark.api


class TestListExercises:
    def test_empty_list(self, client, auth):
        r = client.get("/api/exercises", headers=auth)
        assert r.status_code == 200
        assert r.json() == []

    def test_after_create(self, client, auth):
        client.post("/api/exercises",
                    json={"name": "測試演練", "type": "ttx"}, headers=auth)
        r = client.get("/api/exercises", headers=auth)
        assert len(r.json()) == 1


class TestCreateExercise:
    def test_minimal_fields(self, client, auth):
        r = client.post("/api/exercises",
                        json={"name": "最小欄位演練", "type": "ttx"}, headers=auth)
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "最小欄位演練"
        assert body["status"] == "setup"
        assert body["id"] is not None

    def test_with_all_fields(self, client, auth):
        payload = {
            "name": "完整欄位演練",
            "type": "ttx",
            "date": "2026-05-31",
            "location": "台北市",
            "scenario_summary": "大規模地震情境",
            "participant_count": 50,
            "organizing_body": "民政局",
        }
        r = client.post("/api/exercises", json=payload, headers=auth)
        assert r.status_code == 200
        assert r.json()["location"] == "台北市"

    def test_missing_name_422(self, client, auth):
        r = client.post("/api/exercises", json={"type": "ttx"}, headers=auth)
        assert r.status_code == 422


class TestActivateExercise:
    def test_activate(self, client, auth):
        ex_id = client.post("/api/exercises",
                            json={"name": "E", "type": "ttx"},
                            headers=auth).json()["id"]
        r = client.post(f"/api/exercises/{ex_id}/activate",
                        json={}, headers=auth)
        assert r.status_code == 200
        assert r.json()["status"] == "active"

    def test_mutex_second_activate_fails(self, client, auth):
        ex1_id = client.post("/api/exercises",
                             json={"name": "E1", "type": "ttx"},
                             headers=auth).json()["id"]
        ex2_id = client.post("/api/exercises",
                             json={"name": "E2", "type": "ttx"},
                             headers=auth).json()["id"]
        client.post(f"/api/exercises/{ex1_id}/activate", json={}, headers=auth)
        r = client.post(f"/api/exercises/{ex2_id}/activate", json={}, headers=auth)
        assert r.status_code in (400, 409)

    def test_nonexistent_exercise_404(self, client, auth):
        r = client.post("/api/exercises/9999/activate", json={}, headers=auth)
        assert r.status_code == 404


class TestArchiveExercise:
    def test_archive_after_active(self, client, auth, active_exercise):
        ex_id = active_exercise["id"]
        r = client.post(f"/api/exercises/{ex_id}/archive", json={}, headers=auth)
        assert r.status_code == 200
        assert r.json()["status"] == "archived"

    def test_can_activate_after_archive(self, client, auth, active_exercise):
        ex1_id = active_exercise["id"]
        ex2_id = client.post("/api/exercises",
                             json={"name": "E2", "type": "ttx"},
                             headers=auth).json()["id"]
        client.post(f"/api/exercises/{ex1_id}/archive", json={}, headers=auth)
        r = client.post(f"/api/exercises/{ex2_id}/activate", json={}, headers=auth)
        assert r.status_code == 200


class TestExercisesStrict:
    def test_create_then_list_contains_exact_id(self, client, auth):
        """新建演練後，list 應包含完全相同的 ID 與初始 status"""
        created = client.post("/api/exercises",
                              json={"name": "Strict Exercise", "type": "ttx"},
                              headers=auth).json()

        listing = client.get("/api/exercises", headers=auth).json()
        found = [ex for ex in listing if ex["id"] == created["id"]]
        assert len(found) == 1
        assert found[0]["name"] == "Strict Exercise"
        assert found[0]["status"] == "setup"

    def test_second_activate_returns_exactly_409(self, client, auth):
        """第二次 activate（mutex 衝突）應精確回傳 409，不是 400"""
        ex1_id = client.post("/api/exercises",
                             json={"name": "S1", "type": "ttx"},
                             headers=auth).json()["id"]
        ex2_id = client.post("/api/exercises",
                             json={"name": "S2", "type": "ttx"},
                             headers=auth).json()["id"]

        r1 = client.post(f"/api/exercises/{ex1_id}/activate", json={}, headers=auth)
        assert r1.status_code == 200

        r2 = client.post(f"/api/exercises/{ex2_id}/activate", json={}, headers=auth)
        assert r2.status_code == 409

    def test_archive_nonexistent_exercise_returns_404(self, client, auth):
        """archive 不存在的演練 ID → 404"""
        r = client.post("/api/exercises/999999/archive", json={}, headers=auth)
        assert r.status_code == 404


class TestAAREntries:
    def test_create_aar(self, client, auth, active_exercise):
        ex_id = active_exercise["id"]
        r = client.post(f"/api/exercises/{ex_id}/aar",
                        json={"category": "well", "content": "通訊順暢",
                              "created_by": "admin"},
                        headers=auth)
        assert r.status_code == 200
        assert r.json()["id"] is not None

    def test_list_aar(self, client, auth, active_exercise):
        ex_id = active_exercise["id"]
        client.post(f"/api/exercises/{ex_id}/aar",
                    json={"category": "well", "content": "A", "created_by": "admin"},
                    headers=auth)
        client.post(f"/api/exercises/{ex_id}/aar",
                    json={"category": "improve", "content": "B", "created_by": "admin"},
                    headers=auth)
        r = client.get(f"/api/exercises/{ex_id}/aar", headers=auth)
        assert r.status_code == 200
        assert len(r.json()) == 2
