# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""integration/test_timeline_api.py — P2-20(A)（#199）AAR 統一時間軸

驗證：
- 四源（tracks/events/chats/decision audit）合併、按 t 排序、統一 {type,t,actor,payload} shape
- audit 白名單（decision_* 取、event_created 不取——events 表已載，避免雙計）
- from/to 時間窗過濾、meta（count/t_start/t_end/truncated）、limit 裁切 truncated=true
- 跨場隔離（A 場資料不出現在 B 場 timeline）、空場、不存在場 404
- RBAC：中央 gate /api/exercises/* → COMMAND_ROLES（operator 403）
"""

import pytest

from auth.role_enum import ROLE_OPERATOR_ZH
from repositories import cop_entity_repo
from repositories._helpers import audit
from repositories.account_repo import create_account
from repositories.chat_repo import insert_chat
from repositories.decision_repo import create_decision
from repositories.event_repo import create_event
from repositories.exercise_repo import create_exercise
from schemas.chat import ChatIn
from schemas.cop import CoPEntity, CoPEntityTrack
from services.timeline_service import build_timeline

pytestmark = pytest.mark.integration

_T = "2026-01-01T0{h}:00:00Z"  # 確定過去：decision audit 的 created_at=now 必排最後


def _mk_exercise(name="時間軸測試"):
    return create_exercise({"name": name, "type": "ttx"})["id"]


def _mk_entity(uid, exid, callsign=None):
    cop_entity_repo.insert_cop_entity(
        CoPEntity(
            uid=uid,
            type="a-f-G-U-C",
            time="2026-01-01T00:00:00Z",
            start="2026-01-01T00:00:00Z",
            stale="2099-01-01T00:00:00Z",
            how="m-g",
            lat=24.0,
            lon=120.0,
            source="tak",
            exercise_id=exid,
            callsign=callsign,
        )
    )


def _seed(exid):
    """塞四源各一筆，時間刻意亂序進、驗排序：chat(01) < track(02) < event(03) < decision(04)。"""
    _mk_entity("u-alpha", exid, callsign="ALPHA")
    create_event(
        {
            "reported_by_unit": "shelter",
            "event_type": "fire",
            "severity": "critical",
            "description": "x",
            "operator_name": "admin",
            "occurred_at": _T.format(h=3),
        },
        exercise_id=exid,
    )
    # #267：軌跡自帶 exercise_id（denormalize，m038）；timeline 改查 t.exercise_id → seed 須帶。
    cop_entity_repo.insert_cop_track(
        CoPEntityTrack(uid="u-alpha", t=_T.format(h=2), lat=24.1, lon=120.1, exercise_id=exid)
    )
    insert_chat(
        ChatIn(
            sender_uid="u-alpha", callsign="ALPHA", message="到位", group="ops", time=_T.format(h=1), exercise_id=exid
        )
    )
    create_decision(
        {
            "decision_type": "evac",
            "severity": "high",
            "decision_title": "撤離",
            "impact_description": "i",
            "suggested_action_a": "a",
            "created_by": "admin",
        },
        exercise_id=exid,
    )  # decision audit created_at=now（必為當日最大 t）


class TestMergeAndOrder:
    def test_four_sources_merged_sorted_with_shape(self, tmp_db):
        exid = _mk_exercise()
        _seed(exid)
        out = build_timeline(exid)
        types = [it["type"] for it in out["items"]]
        assert types == ["chat", "track", "event", "decision"]  # t 升序
        for it in out["items"]:
            assert set(it) == {"type", "t", "actor", "payload"}  # 統一 shape，_seq 不外洩
        assert out["items"][0]["actor"] == "ALPHA"  # chat actor=callsign
        assert out["items"][1]["payload"]["uid"] == "u-alpha"  # track payload
        assert out["items"][2]["payload"]["event_code"].startswith("EV-")
        assert out["items"][3]["payload"]["action"] == "decision_created"
        m = out["meta"]
        assert m["count"] == 4 and m["truncated"] is False
        assert m["t_start"] == out["items"][0]["t"] and m["t_end"] == out["items"][3]["t"]

    def test_event_created_audit_not_double_counted(self, tmp_db):
        """events 表已載事件建立 → audit 的 event_created 不進 timeline（白名單擋）。"""
        exid = _mk_exercise()
        _seed(exid)
        items = build_timeline(exid)["items"]
        assert sum(1 for it in items if it["type"] == "event") == 1


class TestWindowAndLimit:
    def test_from_to_window(self, tmp_db):
        exid = _mk_exercise()
        _seed(exid)
        out = build_timeline(exid, since="2026-01-01T01:30:00Z", until="2026-01-01T03:30:00Z")
        assert [it["type"] for it in out["items"]] == ["track", "event"]

    def test_limit_truncates_and_flags(self, tmp_db):
        exid = _mk_exercise()
        _seed(exid)
        out = build_timeline(exid, limit=2)
        assert out["meta"]["truncated"] is True
        assert len(out["items"]) == 2
        assert [it["type"] for it in out["items"]] == ["chat", "track"]  # 取時序最前 2 筆


class TestScopeIsolation:
    def test_cross_exercise_isolated(self, tmp_db):
        ex_a = _mk_exercise("A 場")
        ex_b = _mk_exercise("B 場")
        _seed(ex_a)
        out_b = build_timeline(ex_b)
        assert out_b["items"] == [] and out_b["meta"]["count"] == 0
        assert out_b["meta"]["t_start"] is None

    def test_track_timeline_survives_entity_restamp_not_pollute_next(self, tmp_db):
        """#476/#267：A 收場 archive → entity 重 stamp（exercise_id 變/回 NULL），但**軌跡自身
        exercise_id（m038 denormalize，ingest 時凍結）不動** → ① A 場 AAR 軌跡不被抹（timeline 查
        t.exercise_id 非 e.exercise_id）② 不污染下場 B（stale 政策「收場不清圖」的不污染保證）。"""
        ex_a = _mk_exercise("A 場")
        ex_b = _mk_exercise("B 場")
        # entity 已 restamp 回 NULL（模擬 A 收場後），但其軌跡 exercise_id 仍凍結在 A
        _mk_entity("u-a", None, callsign="A1")
        cop_entity_repo.insert_cop_track(
            CoPEntityTrack(uid="u-a", t=_T.format(h=2), lat=24.1, lon=120.1, exercise_id=ex_a)
        )
        # ① A 場 AAR 仍含該軌跡（查 t.exercise_id，不因 entity restamp 回 NULL 而空白）
        assert any(it["type"] == "track" for it in build_timeline(ex_a)["items"])
        # ② B 場乾淨——A 的殘留不污染下場 baseline
        assert build_timeline(ex_b)["items"] == []

    def test_empty_exercise(self, tmp_db):
        exid = _mk_exercise()
        out = build_timeline(exid)
        assert out["items"] == [] and out["meta"]["truncated"] is False


class TestEndpoint:
    def test_endpoint_shape_and_404(self, client, auth):
        from repositories.exercise_repo import create_exercise as mk

        exid = mk({"name": "E", "type": "ttx"})["id"]
        r = client.get(f"/api/exercises/{exid}/timeline", headers=auth)
        assert r.status_code == 200
        assert set(r.json()) == {"meta", "items"}
        assert client.get("/api/exercises/99999/timeline", headers=auth).status_code == 404

    def test_operator_403(self, client):
        create_account("op_tl", "5678", ROLE_OPERATOR_ZH, "Op", "operator")
        login = client.post("/api/auth/login", json={"username": "op_tl", "pin": "5678"})
        headers = {"X-Session-Token": login.json()["session_id"]}
        r = client.get("/api/exercises/1/timeline", headers=headers)
        assert r.status_code == 403  # COMMAND_ROLES gate（含 PII：軌跡/通聯）


def _zone_audit(exid, uid, op, verts, label_anchor=None):
    """寫一筆區域生命週期 audit（模擬 cop._audit_cop 對 polygon 的記錄，#338）。"""
    action = {"created": "cop_entity_created", "updated": "cop_entity_updated", "deleted": "cop_entity_deleted"}[op]
    attrs = {"kind": "polygon", "vertices": verts, "color": "#ff0000", "poly_type": "no_go"}
    if label_anchor:
        attrs["label_anchor"] = label_anchor
    detail = {"kind": "polygon", "label": "封鎖區", "lat": verts[0][0], "lon": verts[0][1], "attributes": attrs}
    audit("admin", None, action, "cop_entities", uid, detail, exercise_id=exid)


class TestZoneSource:
    """#338：polygon/route 區域生命週期進 timeline（type=zone），供 AAR 折疊重現。"""

    _V = [[24.70, 121.00], [24.72, 121.03], [24.69, 121.05]]

    def test_zone_lifecycle_in_timeline(self, tmp_db):
        exid = _mk_exercise()
        _zone_audit(exid, "manual:z1", "created", self._V)
        _zone_audit(exid, "manual:z1", "updated", self._V, label_anchor=[24.80, 121.09])
        _zone_audit(exid, "manual:z1", "deleted", self._V, label_anchor=[24.80, 121.09])
        items = [it for it in build_timeline(exid)["items"] if it["type"] == "zone"]
        assert [it["payload"]["op"] for it in items] == ["created", "updated", "deleted"]  # 同秒以 id 穩定序
        assert items[0]["payload"]["uid"] == "manual:z1"
        assert items[0]["payload"]["attributes"]["vertices"] == self._V  # 形狀
        assert items[1]["payload"]["attributes"]["label_anchor"] == [24.80, 121.09]  # 標籤位置

    def test_unit_audit_not_zone(self, tmp_db):
        # 單位 audit（detail 無 attributes 快照）不產生 zone item
        exid = _mk_exercise()
        audit(
            "admin",
            None,
            "cop_entity_created",
            "cop_entities",
            "u-x",
            {"kind": None, "label": "X", "lat": 24.0, "lon": 120.0},
            exercise_id=exid,
        )
        assert [it for it in build_timeline(exid)["items"] if it["type"] == "zone"] == []

    def test_zone_cross_exercise_isolated(self, tmp_db):
        a, b = _mk_exercise("A"), _mk_exercise("B")
        _zone_audit(a, "manual:z1", "created", self._V)
        assert [it for it in build_timeline(b)["items"] if it["type"] == "zone"] == []


class TestEventMarkerPosition:
    """#339：timeline 事件附關聯標記位置（AAR 把事件畫在標記上、跟隨移動標的）。"""

    def test_event_carries_primary_marker(self, tmp_db):
        from repositories.event_marker_repo import link_marker

        exid = _mk_exercise()
        _mk_entity("m-1", exid, callsign="敵無人機")  # 標記 entity（lat/lon=24.0,120.0）
        ev = create_event(
            {
                "reported_by_unit": "RTF",
                "event_type": "contact",
                "severity": "high",
                "description": "敵情",
                "operator_name": "admin",
                "occurred_at": _T.format(h=5),
            },
            exercise_id=exid,
        )
        link_marker(ev["id"], "m-1", role="primary")
        e = next(it for it in build_timeline(exid)["items"] if it["type"] == "event")
        ms = e["payload"]["markers"]
        assert len(ms) == 1
        assert ms[0]["uid"] == "m-1" and ms[0]["lat"] == 24.0 and ms[0]["lon"] == 120.0
        assert ms[0]["role"] == "primary" and ms[0]["callsign"] == "敵無人機"

    def test_event_without_marker_empty_list(self, tmp_db):
        exid = _mk_exercise()
        create_event(
            {
                "reported_by_unit": "shelter",
                "event_type": "fire",
                "severity": "low",
                "description": "x",
                "operator_name": "admin",
                "occurred_at": _T.format(h=5),
            },
            exercise_id=exid,
        )
        e = next(it for it in build_timeline(exid)["items"] if it["type"] == "event")
        assert e["payload"]["markers"] == []  # 無標記 → 空（前端不上圖、側欄仍在）

    def test_marker_carries_audit_position_history(self, tmp_db):
        # #2：手動標記移動只記在 audit（cop_entity_updated lat/lon）→ payload 帶 history 供 AAR 折疊重現
        from repositories.event_marker_repo import link_marker

        exid = _mk_exercise()
        _mk_entity("m-2", exid, callsign="移動無人機")
        ev = create_event(
            {
                "reported_by_unit": "RTF",
                "event_type": "drone",
                "severity": "critical",
                "description": "移動威脅",
                "operator_name": "admin",
                "occurred_at": _T.format(h=5),
            },
            exercise_id=exid,
        )
        link_marker(ev["id"], "m-2", role="primary")
        audit(
            "admin",
            None,
            "cop_entity_updated",
            "cop_entities",
            "m-2",
            {"kind": "sighting", "lat": 24.1, "lon": 120.1},
            exercise_id=exid,
        )
        audit(
            "admin",
            None,
            "cop_entity_updated",
            "cop_entities",
            "m-2",
            {"kind": "sighting", "lat": 24.2, "lon": 120.2},
            exercise_id=exid,
        )
        e = next(it for it in build_timeline(exid)["items"] if it["type"] == "event")
        hist = e["payload"]["markers"][0]["history"]
        assert [h[1:] for h in hist] == [[24.1, 120.1], [24.2, 120.2]]  # 位移序列（按時序）


class TestContactMarkers:
    """長按「TAK 標記」建立的敵我接觸（attributes.kind='contact'）→ AAR 以 track 單位顯示、
    affiliation 來自 cot_type、隨 audit 位置歷史移動。"""

    def _mk_contact(self, uid, exid, cot_type, callsign):
        cop_entity_repo.insert_cop_entity(
            CoPEntity(
                uid=uid,
                type=cot_type,
                time="2026-01-01T00:00:00Z",
                start="2026-01-01T00:00:00Z",
                stale="2099-01-01T00:00:00Z",
                how="m-g",
                lat=24.0,
                lon=120.0,
                source="manual",
                exercise_id=exid,
                callsign=callsign,
                attributes={"kind": "contact"},
            )
        )

    def test_contact_renders_as_track_with_affiliation(self, tmp_db):
        exid = _mk_exercise()
        self._mk_contact("c-1", exid, "a-h-G", "敵情")  # 敵軍接觸
        audit(
            "admin",
            None,
            "cop_entity_created",
            "cop_entities",
            "c-1",
            {"kind": "contact", "lat": 24.0, "lon": 120.0},
            exercise_id=exid,
        )
        audit(
            "admin",
            None,
            "cop_entity_updated",
            "cop_entities",
            "c-1",
            {"kind": "contact", "lat": 24.1, "lon": 120.1},
            exercise_id=exid,
        )
        tracks = [it for it in build_timeline(exid)["items"] if it["type"] == "track" and it["payload"]["uid"] == "c-1"]
        assert len(tracks) == 2  # 兩筆位置歷史 → 兩個 track 點（會在 AAR 動）
        assert tracks[0]["payload"]["cot_type"] == "a-h-G"  # affiliation 來源（敵軍 → 紅菱）
        assert [t["payload"]["lat"] for t in tracks] == [24.0, 24.1]

    def test_non_contact_not_emitted(self, tmp_db):
        # 一般 entity（無 kind=contact）不被 _contacts 抓（避免與 TAK tracks / 其他源重疊）
        exid = _mk_exercise()
        _mk_entity("u-x", exid, callsign="X")  # 無 attributes.kind
        audit(
            "admin",
            None,
            "cop_entity_updated",
            "cop_entities",
            "u-x",
            {"kind": None, "lat": 24.5, "lon": 120.5},
            exercise_id=exid,
        )
        tracks = [it for it in build_timeline(exid)["items"] if it["type"] == "track" and it["payload"]["uid"] == "u-x"]
        assert tracks == []  # u-x 無 cop_entity_tracks、非 contact → 不出現


class TestReviewFixes:
    def test_occurred_at_offset_normalized_to_z(self, tmp_db):
        """review #199：offset ISO 入庫前正規化為 Z——字串序比較才正確。"""
        from repositories.event_repo import create_event, get_events

        exid = _mk_exercise()
        ev = create_event(
            {
                "reported_by_unit": "shelter",
                "event_type": "fire",
                "severity": "info",
                "description": "tz",
                "operator_name": "admin",
                "occurred_at": "2026-01-01T11:00:00+08:00",  # = 03:00Z
            },
            exercise_id=exid,
        )
        found = next(e for e in get_events(exercise_id=exid) if e["id"] == ev["id"])
        assert found["occurred_at"] == "2026-01-01T03:00:00Z"

    def test_exactly_limit_rows_not_flagged_truncated(self, tmp_db):
        """review #199：剛好 limit 筆（無更多資料）→ truncated=False（limit+1 探測）。"""
        exid = _mk_exercise()
        _seed(exid)  # 共 4 筆
        out = build_timeline(exid, limit=4)
        assert out["meta"]["count"] == 4
        assert out["meta"]["truncated"] is False

    def test_timeline_track_survives_entity_rescope(self, tmp_db):
        """#267 bug 修（review HIGH）：archive 後 entity 重 stamp 回 NULL，AAR timeline 仍須用軌跡自身
        exercise_id 取到軌跡（修前用 e.exercise_id JOIN → 回 0、AAR 空白）。"""
        exid = _mk_exercise()
        _mk_entity("u-r", exid, callsign="R")
        cop_entity_repo.insert_cop_track(
            CoPEntityTrack(uid="u-r", t=_T.format(h=2), lat=24.1, lon=120.1, exercise_id=exid)
        )
        cop_entity_repo.set_exercise_for_uid("u-r", None)  # 模擬 archive→restamp（entity 回 NULL）
        items = [it for it in build_timeline(exid)["items"] if it["type"] == "track"]
        assert len(items) == 1 and items[0]["payload"]["uid"] == "u-r"  # 軌跡仍在 AAR
