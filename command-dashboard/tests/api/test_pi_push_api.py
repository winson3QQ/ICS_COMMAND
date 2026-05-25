"""
tests/api/test_pi_push_api.py — Pi 節點推送 API 測試

模擬 shelter / medical 兩個 Pi 節點推送資料的完整流程：
- HMAC-SHA256 簽名（Option-A：/api/pi-push 現在需要 HMAC）
- Bearer token 認證
- 心跳（空 records）
- 實際資料推送（多 table_name）
- 錯誤路徑（無效 token、未知節點、無 HMAC）
- 推送後查詢 /api/pi-data/{unit_id}/list
"""

import pytest

pytestmark = pytest.mark.api

# ── Fixture：建立測試用 Pi 節點 ─────────────────────────────────────────────

@pytest.fixture
def shelter_node(tmp_db):
    from repositories.pi_node_repo import create_pi_node
    return create_pi_node("shelter", "收容組測試節點")


@pytest.fixture
def medical_node(tmp_db):
    from repositories.pi_node_repo import create_pi_node
    return create_pi_node("medical", "醫療組測試節點")


# ── 輔助：HMAC + Bearer push ──────────────────────────────────────────────────

def _push(c, sign, unit_id: str, records: list, api_key: str):
    """Pi push helper：HMAC 簽名 + Bearer token。

    Option-A：/api/pi-push/{unit_id} 現在需要 HMAC 驗證，
    測試必須同時提供 HMAC 簽名和 Bearer token。
    """
    body_bytes, hdrs = sign("POST", f"/api/pi-push/{unit_id}", {"records": records})
    hdrs["Authorization"] = f"Bearer {api_key}"
    return c.post(f"/api/pi-push/{unit_id}", content=body_bytes, headers=hdrs)


# ── 心跳測試 ──────────────────────────────────────────────────────────────────

class TestHeartbeat:
    def test_shelter_heartbeat(self, hmac_client, shelter_node):
        c, sign = hmac_client
        r = _push(c, sign, "shelter", [], shelter_node["api_key"])
        assert r.status_code == 200
        assert r.json().get("ok") is True

    def test_medical_heartbeat(self, hmac_client, medical_node):
        c, sign = hmac_client
        r = _push(c, sign, "medical", [], medical_node["api_key"])
        assert r.status_code == 200


# ── 認證錯誤 ──────────────────────────────────────────────────────────────────

class TestAuthErrors:
    def test_no_bearer_returns_401(self, client, shelter_node):
        """無 Bearer 且無 HMAC headers → 401（HMAC 層先攔截 no_sig）。"""
        r = client.post("/api/pi-push/shelter", json={"records": []})
        assert r.status_code == 401

    def test_wrong_token_returns_403(self, hmac_client, shelter_node):
        """valid HMAC + 錯誤 Bearer → HMAC 通過，Bearer 驗證失敗 → 403。"""
        c, sign = hmac_client
        body_bytes, hdrs = sign("POST", "/api/pi-push/shelter", {"records": []})
        hdrs["Authorization"] = "Bearer wrongtoken"
        r = c.post("/api/pi-push/shelter", content=body_bytes, headers=hdrs)
        assert r.status_code == 403

    def test_unknown_unit_returns_403(self, hmac_client, shelter_node):
        """valid HMAC + shelter token → 推到 unknown_unit → pi_nodes 查無 → 403。"""
        c, sign = hmac_client
        r = _push(c, sign, "unknown_unit", [], shelter_node["api_key"])
        assert r.status_code == 403

    def test_cross_unit_token_rejected(self, hmac_client, shelter_node, medical_node):
        """shelter 的 token 不能用來推 medical → 403。"""
        c, sign = hmac_client
        r = _push(c, sign, "medical", [], shelter_node["api_key"])
        assert r.status_code == 403


# ── Shelter Pi 推送（人員 + 床位）─────────────────────────────────────────────

SHELTER_RECORDS = [
    {"table_name": "persons",
     "record": {"id": "P001", "status": "admitted", "srt_color": "green"}},
    {"table_name": "persons",
     "record": {"id": "P002", "status": "admitted", "srt_color": "yellow"}},
    {"table_name": "beds",
     "record": {"id": "B001", "status": "occupied"}},
    {"table_name": "beds",
     "record": {"id": "B002", "status": "available"}},
    {"table_name": "beds_meta",
     "record": {"capacity_max": 20}},
    {"table_name": "resources",
     "record": {"name": "water", "qty_current": 80, "qty_initial": 100, "disabled": False}},
]


class TestShelterPush:
    def test_push_returns_ok(self, hmac_client, shelter_node):
        c, sign = hmac_client
        r = _push(c, sign, "shelter", SHELTER_RECORDS, shelter_node["api_key"])
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["records_count"] == len(SHELTER_RECORDS)

    def test_push_stores_batch(self, hmac_client, shelter_node, auth):
        c, sign = hmac_client
        _push(c, sign, "shelter", SHELTER_RECORDS, shelter_node["api_key"])
        r = c.get("/api/pi-data/shelter/list", headers=auth)
        assert r.status_code == 200
        data = r.json()
        assert data["offline"] is False
        assert len(data["records"]) == len(SHELTER_RECORDS)

    def test_push_groups_by_table(self, hmac_client, shelter_node, auth):
        c, sign = hmac_client
        _push(c, sign, "shelter", SHELTER_RECORDS, shelter_node["api_key"])
        grouped = c.get("/api/pi-data/shelter/list", headers=auth).json()["grouped"]
        assert "persons" in grouped
        assert "beds" in grouped
        assert len(grouped["persons"]) == 2
        assert len(grouped["beds"]) == 2


# ── Medical Pi 推送（傷患 + 資源）────────────────────────────────────────────

MEDICAL_RECORDS = [
    {"table_name": "patients",
     "record": {"id": "M001", "current_zone": "在場", "triage_color": "red",
                "care_status": "triaged", "disposition": "在場"}},
    {"table_name": "patients",
     "record": {"id": "M002", "current_zone": "在場", "triage_color": "yellow",
                "care_status": "triaged", "disposition": "在場"}},
    {"table_name": "patients",
     "record": {"id": "M003", "current_zone": "在場", "triage_color": "green",
                "care_status": "waiting", "disposition": "在場"}},
    {"table_name": "resources",
     "record": {"name": "oxygen", "qty_current": 45, "qty_initial": 100, "disabled": False}},
    {"table_name": "incidents",
     "record": {"id": "I001", "status": "open", "severity": "高"}},
]


class TestMedicalPush:
    def test_push_returns_ok(self, hmac_client, medical_node):
        c, sign = hmac_client
        r = _push(c, sign, "medical", MEDICAL_RECORDS, medical_node["api_key"])
        assert r.status_code == 200
        assert r.json()["records_count"] == len(MEDICAL_RECORDS)

    def test_push_stores_batch(self, hmac_client, medical_node, auth):
        c, sign = hmac_client
        _push(c, sign, "medical", MEDICAL_RECORDS, medical_node["api_key"])
        r = c.get("/api/pi-data/medical/list", headers=auth)
        assert r.status_code == 200
        assert r.json()["offline"] is False

    def test_offline_before_push(self, client, medical_node, auth):
        """推送前查詢應回傳 offline: True（不需要 HMAC，只是 GET）"""
        r = client.get("/api/pi-data/medical/list", headers=auth)
        assert r.status_code == 200
        assert r.json()["offline"] is True


# ── 多節點同時推送 ────────────────────────────────────────────────────────────

class TestMultiNodePush:
    def test_two_nodes_push_independently(self, hmac_client, shelter_node, medical_node, auth):
        c, sign = hmac_client
        _push(c, sign, "shelter", SHELTER_RECORDS, shelter_node["api_key"])
        _push(c, sign, "medical", MEDICAL_RECORDS, medical_node["api_key"])

        shelter_data = c.get("/api/pi-data/shelter/list", headers=auth).json()
        medical_data = c.get("/api/pi-data/medical/list", headers=auth).json()

        assert shelter_data["offline"] is False
        assert medical_data["offline"] is False
        assert len(shelter_data["records"]) == len(SHELTER_RECORDS)
        assert len(medical_data["records"]) == len(MEDICAL_RECORDS)
