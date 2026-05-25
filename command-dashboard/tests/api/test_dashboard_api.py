"""
tests/api/test_dashboard_api.py — Dashboard 聚合資料驗證

在 Pi 節點推送資料後，驗證 /api/dashboard 回傳正確的聚合結果：
- 傷患人數（紅/黃/綠）
- 床位使用率
- Pi 節點狀態列表
- 多節點同時上線
"""

import pytest

pytestmark = pytest.mark.api


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def shelter_node(tmp_db):
    from repositories.pi_node_repo import create_pi_node
    return create_pi_node("shelter", "收容組")


@pytest.fixture
def medical_node(tmp_db):
    from repositories.pi_node_repo import create_pi_node
    return create_pi_node("medical", "醫療組")


def _push(c, sign, unit_id: str, records: list, api_key: str):
    """Pi push helper：HMAC 簽名 + Bearer token。

    Option-A：/api/pi-push/{unit_id} 現在需要 HMAC 驗證，
    測試必須同時提供 HMAC 簽名和 Bearer token。
    """
    body_bytes, hdrs = sign("POST", f"/api/pi-push/{unit_id}", {"records": records})
    hdrs["Authorization"] = f"Bearer {api_key}"
    return c.post(f"/api/pi-push/{unit_id}", content=body_bytes, headers=hdrs)


SHELTER_RECORDS = [
    {"table_name": "persons",
     "record": {"id": "P001", "status": "已安置", "srt_color": "green"}},
    {"table_name": "persons",
     "record": {"id": "P002", "status": "已安置", "srt_color": "yellow"}},
    {"table_name": "persons",
     "record": {"id": "P003", "status": "等候中", "srt_color": "green"}},
    {"table_name": "beds",
     "record": {"id": "B001", "status": "occupied"}},
]

MEDICAL_RECORDS = [
    {"table_name": "patients",
     "record": {"id": "M001", "current_zone": "在場", "triage_color": "red",
                "care_status": "triaged", "disposition": "在場"}},
    {"table_name": "patients",
     "record": {"id": "M002", "current_zone": "在場", "triage_color": "yellow",
                "care_status": "triaged", "disposition": "在場"}},
    {"table_name": "patients",
     "record": {"id": "M003", "current_zone": "已離區", "triage_color": "green",
                "care_status": "discharged", "disposition": "離院"}},
]


# ── 空 Dashboard ──────────────────────────────────────────────────────────────

class TestEmptyDashboard:
    def test_returns_200(self, client, auth):
        r = client.get("/api/dashboard", headers=auth)
        assert r.status_code == 200

    def test_pi_nodes_empty_before_registration(self, client, auth):
        r = client.get("/api/dashboard", headers=auth)
        assert r.json()["pi_nodes"] == []

    def test_no_auth_returns_401(self, client):
        r = client.get("/api/dashboard")
        assert r.status_code == 401


# ── Shelter 推送後 Dashboard ──────────────────────────────────────────────────

class TestDashboardAfterShelterPush:
    def test_pi_node_appears_after_push(self, hmac_client, shelter_node, auth):
        c, sign = hmac_client
        _push(c, sign, "shelter", SHELTER_RECORDS, shelter_node["api_key"])
        nodes = c.get("/api/dashboard", headers=auth).json()["pi_nodes"]
        unit_ids = [n["unit_id"] for n in nodes]
        assert "shelter" in unit_ids

    def test_shelter_bed_used(self, hmac_client, shelter_node, auth):
        """2 人已安置 → shelter 快照 bed_used = 2"""
        c, sign = hmac_client
        _push(c, sign, "shelter", SHELTER_RECORDS, shelter_node["api_key"])
        dash = c.get("/api/dashboard", headers=auth).json()
        history = dash.get("shelter_history", [])
        assert len(history) > 0
        assert history[0]["bed_used"] == 2

    def test_shelter_pending_intake(self, hmac_client, shelter_node, auth):
        """1 人等候中 → pending_intake = 1"""
        c, sign = hmac_client
        _push(c, sign, "shelter", SHELTER_RECORDS, shelter_node["api_key"])
        dash = c.get("/api/dashboard", headers=auth).json()
        history = dash.get("shelter_history", [])
        assert len(history) > 0
        assert history[0]["pending_intake"] == 1


# ── Medical 推送後 Dashboard ──────────────────────────────────────────────────

class TestDashboardAfterMedicalPush:
    def test_medical_red_casualties(self, hmac_client, medical_node, auth):
        """1 位紅傷患在場 → casualties_red = 1"""
        c, sign = hmac_client
        _push(c, sign, "medical", MEDICAL_RECORDS, medical_node["api_key"])
        dash = c.get("/api/dashboard", headers=auth).json()
        history = dash.get("medical_history", [])
        assert len(history) > 0
        assert history[0]["casualties_red"] == 1

    def test_medical_excludes_discharged(self, hmac_client, medical_node, auth):
        """已離區的不計入 active bed_used"""
        c, sign = hmac_client
        _push(c, sign, "medical", MEDICAL_RECORDS, medical_node["api_key"])
        dash = c.get("/api/dashboard", headers=auth).json()
        history = dash.get("medical_history", [])
        assert len(history) > 0
        assert history[0]["bed_used"] == 2  # M001 + M002，M003 已離區不算


# ── 多節點同時上線 ────────────────────────────────────────────────────────────

class TestMultiNodeDashboard:
    def test_both_nodes_in_pi_nodes_list(self, hmac_client, shelter_node, medical_node, auth):
        c, sign = hmac_client
        _push(c, sign, "shelter", SHELTER_RECORDS, shelter_node["api_key"])
        _push(c, sign, "medical", MEDICAL_RECORDS, medical_node["api_key"])
        nodes = c.get("/api/dashboard", headers=auth).json()["pi_nodes"]
        unit_ids = {n["unit_id"] for n in nodes}
        assert "shelter" in unit_ids
        assert "medical" in unit_ids

    def test_both_histories_populated(self, hmac_client, shelter_node, medical_node, auth):
        c, sign = hmac_client
        _push(c, sign, "shelter", SHELTER_RECORDS, shelter_node["api_key"])
        _push(c, sign, "medical", MEDICAL_RECORDS, medical_node["api_key"])
        dash = c.get("/api/dashboard", headers=auth).json()
        assert len(dash.get("shelter_history", [])) > 0
        assert len(dash.get("medical_history", [])) > 0
