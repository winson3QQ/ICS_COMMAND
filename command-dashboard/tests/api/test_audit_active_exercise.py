"""稽核日誌 — active exercise 綁定 + COP 操作 audit（issue #93）。

驗：
- Model B：audit 未明傳 exercise_id → 戳當前 active session（演習/實戰）；無 active → NULL。
- COP 操作 audit：create（跳過 event kind）/ update（全 kind，捕捉移動）/ delete。
"""

from __future__ import annotations

import json

from core.database import get_conn

_COP = {"type": "a-f-G-U-C", "lat": 25.0, "lon": 121.0, "callsign": "AUDIT-TEST"}
_COP_EVENT = {**_COP, "callsign": "EVT", "attributes": {"kind": "event"}}
# #338：polygon 區域——attributes 帶 kind + vertices（幾何形狀），audit 須記下供 AAR 折疊重現。
_POLY_VERTS = [[24.70, 121.00], [24.72, 121.03], [24.69, 121.05]]
_COP_POLY = {
    "type": "u-d-f",
    "lat": 24.70,
    "lon": 121.00,
    "callsign": "封鎖區A",
    "attributes": {"kind": "polygon", "vertices": _POLY_VERTS, "color": "#ff0000", "dash": False, "poly_type": "no_go"},
}


def _audits(action_type: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT action_type, exercise_id, detail FROM audit_log WHERE action_type=? ORDER BY id",
            (action_type,),
        ).fetchall()
    return [dict(r) for r in rows]


class TestModelBActiveBinding:
    def test_cop_create_audit_stamps_active(self, client, auth, active_exercise):
        # COP 建立（非 event）→ cop_entity_created audit，exercise_id = active（Model B 自動戳）
        r = client.post("/api/cop/entities", json=_COP, headers=auth)
        assert r.status_code == 201
        rows = _audits("cop_entity_created")
        assert len(rows) == 1
        assert rows[0]["exercise_id"] == active_exercise["id"]

    def test_cop_create_audit_null_when_no_active(self, client, auth):
        # 無 active session → exercise_id NULL（實戰/未分場）
        r = client.post("/api/cop/entities", json=_COP, headers=auth)
        assert r.status_code == 201
        rows = _audits("cop_entity_created")
        assert len(rows) == 1
        assert rows[0]["exercise_id"] is None


class TestCopOperationAudit:
    def test_create_non_event_audited(self, client, auth):
        r = client.post("/api/cop/entities", json=_COP, headers=auth)
        assert r.status_code == 201
        rows = _audits("cop_entity_created")
        assert len(rows) == 1
        d = json.loads(rows[0]["detail"])
        assert d["label"] == "AUDIT-TEST" and d["lat"] == 25.0 and d["lon"] == 121.0

    def test_create_event_kind_skipped(self, client, auth):
        # 🔑 建立 event kind 不寫 cop_entity_created（event_created 已涵蓋，避免雙記）
        r = client.post("/api/cop/entities", json=_COP_EVENT, headers=auth)
        assert r.status_code == 201
        assert _audits("cop_entity_created") == []

    def test_update_audited_captures_movement(self, client, auth):
        # 建立 → 移動（PUT 新座標）→ cop_entity_updated 記新位置 + fields（QRF 移動軌跡）
        r = client.post("/api/cop/entities", json=_COP_EVENT, headers=auth)  # event kind 也要記移動
        uid, etag = r.json()["uid"], r.headers["ETag"]
        r2 = client.put(
            f"/api/cop/entities/{uid}",
            json={"lat": 26.5, "lon": 122.5},
            headers={**auth, "If-Match": etag},
        )
        assert r2.status_code == 200
        rows = _audits("cop_entity_updated")
        assert len(rows) == 1
        d = json.loads(rows[0]["detail"])
        assert d["lat"] == 26.5 and d["lon"] == 122.5  # 新位置（移動後）
        assert "lat" in d["fields"] and "lon" in d["fields"]

    def test_delete_audited(self, client, auth):
        r = client.post("/api/cop/entities", json=_COP, headers=auth)
        uid, etag = r.json()["uid"], r.headers["ETag"]
        r2 = client.delete(f"/api/cop/entities/{uid}", headers={**auth, "If-Match": etag})
        assert r2.status_code == 200
        rows = _audits("cop_entity_deleted")
        assert len(rows) == 1


class TestZoneGeometryAudit:
    """#338：polygon/route 區域的 audit detail 須記**幾何快照**，AAR 才能時間精確重現
    （畫/改/刪 @ T）。audit 不可回填 → 此記錄為演習前必上線的關鍵路徑。"""

    def test_create_polygon_records_full_attributes(self, client, auth):
        r = client.post("/api/cop/entities", json=_COP_POLY, headers=auth)
        assert r.status_code == 201
        rows = _audits("cop_entity_created")
        assert len(rows) == 1
        d = json.loads(rows[0]["detail"])
        assert d["kind"] == "polygon"
        # 整包 attributes 快照：形狀 + 樣式皆在
        assert d["attributes"]["vertices"] == _POLY_VERTS
        assert d["attributes"]["color"] == "#ff0000" and d["attributes"]["poly_type"] == "no_go"

    def test_update_label_move_recorded(self, client, auth):
        # dogfood 實證情境：移動 label（只改 attributes.label_anchor、不動 vertices/lat/lon）。
        # 整包快照才抓得到 label_anchor——cherry-pick vertices/color 會漏掉標籤位置。
        r = client.post("/api/cop/entities", json=_COP_POLY, headers=auth)
        uid, etag = r.json()["uid"], r.headers["ETag"]
        moved = {
            "kind": "polygon",
            "vertices": _POLY_VERTS,
            "color": "#ff0000",
            "poly_type": "no_go",
            "label_anchor": [24.80, 121.09],
        }
        r2 = client.put(
            f"/api/cop/entities/{uid}",
            json={"attributes": moved},
            headers={**auth, "If-Match": etag},
        )
        assert r2.status_code == 200
        rows = _audits("cop_entity_updated")
        assert len(rows) == 1
        d = json.loads(rows[0]["detail"])
        assert d["attributes"]["label_anchor"] == [24.80, 121.09]  # 標籤位置被記下
        assert d["attributes"]["vertices"] == _POLY_VERTS  # 形狀仍完整（label 移動不動形狀）

    def test_unit_kind_no_attributes_snapshot(self, client, auth):
        # 單位/點（非 polygon/route）不記 attributes 快照——位置由 tracks 承載，不塞 audit
        r = client.post("/api/cop/entities", json=_COP, headers=auth)
        assert r.status_code == 201
        rows = _audits("cop_entity_created")
        d = json.loads(rows[0]["detail"])
        assert "attributes" not in d


class TestExerciseLifecycleStaysNull:
    """exercise_* 生命週期 audit 必須保持 NULL（review #93-1）——否則會被該場 cascade
    刪除，刪除/狀態軌跡無法留存。即使有 active 場也不可被戳成 active id。"""

    def test_exercise_deleted_audit_null_despite_active(self, client, auth, active_exercise):
        other = client.post("/api/exercises", json={"name": "to-delete", "type": "ttx"}, headers=auth).json()
        r = client.delete(f"/api/exercises/{other['id']}", headers=auth)
        assert r.status_code in (200, 204)
        rows = _audits("exercise_deleted")
        assert rows, "應有 exercise_deleted audit"
        assert rows[-1]["exercise_id"] is None  # 不被戳成 active 場（active_exercise）
