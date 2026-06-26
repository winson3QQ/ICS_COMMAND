# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
tests/integration/test_cop_router.py — issue #29 PR-B：/api/cop/* CRUD + 樂觀鎖 HTTP 層

鎖住的不變式：
- CRUD：POST 建立（source 強制 manual、defaults 兜底、201 + ETag）/ GET 單顆+列表 / PUT / DELETE
- 樂觀鎖：PUT/DELETE 需 If-Match；缺 → 428、版本對不上 → 409 + server_entity、成功 → version_clock +1
- soft-delete：DELETE 後預設 list 不見、但 include_stale 仍在（TAK 語意）
- RBAC（middleware 集中政策）：GET → READ_ROLES（observer 可）、寫 → WRITE_ROLES（observer 403、operator 可）
- XSS：寫入字串含 HTML/JS 危險字元 → 422（issue #24 sink 防護，與 map_config 共用 validator）
- 未帶 session → 401
"""

from __future__ import annotations

from auth.role_enum import ROLE_OBSERVER_ZH, ROLE_OPERATOR_ZH
from repositories.account_repo import create_account


def _login(client, username: str = "admin", pin: str = "1234") -> dict[str, str]:
    r = client.post("/api/auth/login", json={"username": username, "pin": pin})
    assert r.status_code == 200, r.text
    return {"X-Session-Token": r.json()["session_id"]}


def _create(client, headers, **fields) -> dict:
    body = {"type": "a-f-G-U-C", "lat": 25.0330, "lon": 121.5654, "callsign": "ALPHA"}
    body.update(fields)
    r = client.post("/api/cop/entities", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


# ── CRUD 基礎 ────────────────────────────────────────────────────────────────


def test_create_forces_manual_and_fills_defaults(client):
    h = _login(client)
    ent = _create(client, h, source="tak")  # 故意宣告 tak
    assert ent["source"] == "manual"  # server 強制覆蓋，不信任 client 來源
    assert ent["uid"].startswith("manual:")  # 未帶 uid → server 生成
    assert ent["version_clock"] == 1
    assert ent["how"] == "h-e"
    assert ent["callsign"] == "ALPHA"
    assert ent["time"] and ent["stale"]  # 時間戳兜底


def test_create_sets_etag_header(client):
    h = _login(client)
    r = client.post(
        "/api/cop/entities",
        json={"type": "a-f-G-U-C", "lat": 25.0, "lon": 121.0},
        headers=h,
    )
    assert r.status_code == 201
    assert r.headers.get("etag") == 'W/"1"'


def test_get_one_and_list(client):
    h = _login(client)
    ent = _create(client, h, callsign="BRAVO")
    uid = ent["uid"]

    r = client.get(f"/api/cop/entities/{uid}", headers=h)
    assert r.status_code == 200
    assert r.json()["callsign"] == "BRAVO"
    assert r.headers.get("etag") == 'W/"1"'

    r = client.get("/api/cop/entities", headers=h)
    assert r.status_code == 200
    assert uid in {e["uid"] for e in r.json()["entities"]}


def test_get_missing_404(client):
    h = _login(client)
    r = client.get("/api/cop/entities/nope", headers=h)
    assert r.status_code == 404


# ── 樂觀鎖：PUT ──────────────────────────────────────────────────────────────


def test_put_with_if_match_bumps_version(client):
    h = _login(client)
    uid = _create(client, h)["uid"]
    r = client.put(
        f"/api/cop/entities/{uid}",
        json={"callsign": "CHARLIE"},
        headers={**h, "If-Match": "1"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["callsign"] == "CHARLIE"
    assert r.json()["version_clock"] == 2
    assert r.headers.get("etag") == 'W/"2"'
    assert r.json()["updated_by"] == "admin"


def test_put_accepts_weak_etag_if_match(client):
    h = _login(client)
    uid = _create(client, h)["uid"]
    r = client.put(
        f"/api/cop/entities/{uid}",
        json={"remarks": "x"},
        headers={**h, "If-Match": 'W/"1"'},
    )
    assert r.status_code == 200, r.text


def test_put_without_if_match_428(client):
    h = _login(client)
    uid = _create(client, h)["uid"]
    r = client.put(f"/api/cop/entities/{uid}", json={"callsign": "X"}, headers=h)
    assert r.status_code == 428


def test_put_stale_if_match_409_with_server_entity(client):
    h = _login(client)
    uid = _create(client, h)["uid"]
    # 先成功 bump 一次 → version 2
    client.put(f"/api/cop/entities/{uid}", json={"callsign": "ONE"}, headers={**h, "If-Match": "1"})
    # 再用過期 version 1 → 409
    r = client.put(
        f"/api/cop/entities/{uid}",
        json={"callsign": "TWO"},
        headers={**h, "If-Match": "1"},
    )
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["server_entity"]["version_clock"] == 2
    assert body["server_entity"]["callsign"] == "ONE"  # 沒被 TWO 蓋掉
    assert r.headers.get("etag") == 'W/"2"'


def test_put_forbidden_source_422(client):
    h = _login(client)
    uid = _create(client, h)["uid"]
    r = client.put(
        f"/api/cop/entities/{uid}",
        json={"source": "tak"},
        headers={**h, "If-Match": "1"},
    )
    assert r.status_code == 422


def test_put_unknown_column_422(client):
    h = _login(client)
    uid = _create(client, h)["uid"]
    r = client.put(
        f"/api/cop/entities/{uid}",
        json={"definitely_not_a_column": 1},
        headers={**h, "If-Match": "1"},
    )
    assert r.status_code == 422


def test_put_missing_entity_404(client):
    h = _login(client)
    r = client.put(
        "/api/cop/entities/ghost",
        json={"callsign": "X"},
        headers={**h, "If-Match": "1"},
    )
    assert r.status_code == 404


# ── 樂觀鎖：DELETE（soft-delete）─────────────────────────────────────────────


def test_delete_soft_removes_from_default_list_but_row_persists(client):
    h = _login(client)
    uid = _create(client, h)["uid"]
    r = client.delete(f"/api/cop/entities/{uid}", headers={**h, "If-Match": "1"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "deleted"
    assert r.json()["version_clock"] == 2
    assert r.headers.get("etag") == 'W/"2"'  # 成功 delete 也回 ETag（與 PUT 一致）

    # 預設 list 不見
    listed = client.get("/api/cop/entities", headers=h).json()["entities"]
    assert uid not in {e["uid"] for e in listed}
    # include_stale 仍在（soft-delete，可 audit / 回放）
    all_listed = client.get("/api/cop/entities?include_stale=true", headers=h).json()["entities"]
    assert uid in {e["uid"] for e in all_listed}


def test_delete_without_if_match_428(client):
    h = _login(client)
    uid = _create(client, h)["uid"]
    r = client.delete(f"/api/cop/entities/{uid}", headers=h)
    assert r.status_code == 428


def test_delete_stale_if_match_409(client):
    h = _login(client)
    uid = _create(client, h)["uid"]
    client.put(f"/api/cop/entities/{uid}", json={"callsign": "X"}, headers={**h, "If-Match": "1"})
    r = client.delete(f"/api/cop/entities/{uid}", headers={**h, "If-Match": "1"})
    assert r.status_code == 409


def test_delete_missing_404(client):
    h = _login(client)
    r = client.delete("/api/cop/entities/ghost", headers={**h, "If-Match": "1"})
    assert r.status_code == 404


# ── RBAC（middleware 集中政策）───────────────────────────────────────────────


def test_observer_can_read_cannot_write(client):
    admin_h = _login(client)
    uid = _create(client, admin_h)["uid"]

    create_account("obs_cop", "1234", ROLE_OBSERVER_ZH, "Observer", "observer")
    obs_h = _login(client, "obs_cop", "1234")

    # GET 允許
    assert client.get("/api/cop/entities", headers=obs_h).status_code == 200
    assert client.get(f"/api/cop/entities/{uid}", headers=obs_h).status_code == 200
    # 寫 403
    post = client.post("/api/cop/entities", json={"type": "a", "lat": 1, "lon": 1}, headers=obs_h)
    assert post.status_code == 403
    put = client.put(f"/api/cop/entities/{uid}", json={"callsign": "X"}, headers={**obs_h, "If-Match": "1"})
    assert put.status_code == 403
    delete = client.delete(f"/api/cop/entities/{uid}", headers={**obs_h, "If-Match": "1"})
    assert delete.status_code == 403


def test_operator_can_write(client):
    create_account("op_cop", "1234", ROLE_OPERATOR_ZH, "Operator", "operator")
    op_h = _login(client, "op_cop", "1234")
    ent = _create(client, op_h, callsign="OPDRAW")
    assert ent["source"] == "manual"
    r = client.put(
        f"/api/cop/entities/{ent['uid']}",
        json={"callsign": "OPEDIT"},
        headers={**op_h, "If-Match": "1"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["updated_by"] == "op_cop"


def test_no_session_401(client):
    assert client.get("/api/cop/entities").status_code == 401
    assert client.post("/api/cop/entities", json={"type": "a", "lat": 1, "lon": 1}).status_code == 401


# ── XSS 防護（與 map_config 共用 validator）──────────────────────────────────


def test_create_rejects_xss_in_callsign(client):
    h = _login(client)
    r = client.post(
        "/api/cop/entities",
        json={"type": "a-f-G-U-C", "lat": 25.0, "lon": 121.0, "callsign": "<script>alert(1)</script>"},
        headers=h,
    )
    assert r.status_code == 422


def test_update_rejects_xss_in_attributes(client):
    h = _login(client)
    uid = _create(client, h)["uid"]
    r = client.put(
        f"/api/cop/entities/{uid}",
        json={"attributes": {"note": "<img src=x onerror=alert(1)>"}},
        headers={**h, "If-Match": "1"},
    )
    assert r.status_code == 422


# ── #146（TAK-B-rev）：來源所有權 + 情境守門 — 實戰鎖死外部來源，演習(TTX)可編輯 ──


def _insert_tak_entity(uid: str = "tak:UNIT-1", lat: float = 24.0, lon: float = 120.0) -> str:
    """直插一筆 source='tak' entity（_create 一律強制 manual，無法測外部來源）。"""
    from core.database import get_conn

    with get_conn() as conn:
        conn.execute(
            "INSERT INTO cop_entities (uid,type,time,start,stale,how,lat,lon,source,callsign,version_clock) "
            "VALUES (?,?,?,?,?,?,?,?,'tak','REAL-UNIT',1)",
            (uid, "a-f-G-U-C", "2026-06-05T04:00:00Z", "2026-06-05T04:00:00Z", "2099-01-01T00:00:00Z", "m-g", lat, lon),
        )
    return uid


def test_put_tak_blocked_in_realops(client):
    """實戰（無 active exercise）：不可 PUT 覆寫 tak 來源 entity（403），位置不被改。"""
    h = _login(client)
    uid = _insert_tak_entity()
    r = client.put(
        f"/api/cop/entities/{uid}",
        json={"lat": 0.0, "lon": 0.0, "callsign": "SPOOF"},
        headers={**h, "If-Match": "1"},
    )
    assert r.status_code == 403, r.text
    cur = client.get(f"/api/cop/entities/{uid}", headers=h).json()
    assert cur["lat"] == 24.0 and cur["callsign"] == "REAL-UNIT"  # 沒被覆寫


def test_delete_tak_blocked_in_realops(client):
    """實戰：不可手動 DELETE tak 來源 entity（403）—— 真實單位移除走 stale CoT，非手刪。"""
    h = _login(client)
    uid = _insert_tak_entity(uid="tak:UNIT-2")
    r = client.delete(f"/api/cop/entities/{uid}", headers={**h, "If-Match": "1"})
    assert r.status_code == 403, r.text


def test_put_tak_allowed_in_ttx(client, active_exercise):
    """演習(TTX)模式：tak 來源 entity 可編輯（道具/合成）—— server 權威 type='ttx' 放行。"""
    h = _login(client)
    uid = _insert_tak_entity(uid="tak:SIM-1")
    r = client.put(
        f"/api/cop/entities/{uid}",
        json={"lat": 25.0},
        headers={**h, "If-Match": "1"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["lat"] == 25.0


def test_put_manual_allowed_in_realops(client):
    """對照：manual 來源（指揮部自建）實戰模式仍可編輯（永遠可，不誤殺）。"""
    h = _login(client)
    uid = _create(client, h)["uid"]  # source=manual
    r = client.put(
        f"/api/cop/entities/{uid}",
        json={"callsign": "EDIT"},
        headers={**h, "If-Match": "1"},
    )
    assert r.status_code == 200, r.text


# ── P2-33b：event↔marker first-class 連結（glue 退役）──────────────────────────


def _mk_event_row():
    from repositories.event_repo import create_event

    return create_event(
        {
            "reported_by_unit": "command",
            "event_type": "fire",
            "description": "x",
            "operator_name": "admin",
            "severity": "warning",
        }
    )["id"]


def test_event_pin_links_junction_via_first_class_event_id(client):
    """POST cop entity 帶**頂層** event_id → 後端 link_marker 建 junction、序列化回頂層
    event_id、且 **attributes 不存 event_id**（glue 退役，#196 step ③）。"""
    from repositories.event_marker_repo import get_events_for_marker

    h = _login(client)
    ev = _mk_event_row()
    ent = _create(client, h, event_id=ev, attributes={"kind": "event", "event_code": "EV-001"})
    assert ent["event_id"] == ev  # 序列化帶頂層 junction event_id（廣播/回應皆然）
    assert "event_id" not in (ent.get("attributes") or {})  # glue 未入 DB attributes
    assert [e["id"] for e in get_events_for_marker(ent["uid"])] == [ev]  # junction 已建


def test_event_pin_back_compat_attributes_event_id(client):
    """back-compat：舊 / 快取 client 仍把 event_id 放 attributes → 後端 fallback 取之、
    junction 照建、頂層 event_id 照回（過渡期不破）。"""
    from repositories.event_marker_repo import get_events_for_marker

    h = _login(client)
    ev = _mk_event_row()
    ent = _create(client, h, attributes={"kind": "event", "event_id": ev, "event_code": "EV-002"})
    assert ent["event_id"] == ev
    assert [e["id"] for e in get_events_for_marker(ent["uid"])] == [ev]


def test_event_pin_forged_event_id_orphans_not_500(client):
    """不存在的 event_id（FK 撞 IntegrityError）→ best-effort：圖釘照建（201）但 event_id=None
    （orphan）、不噴 500、**不反射 forged 值**（review 硬化：link 失敗不 set created.event_id）。"""
    h = _login(client)
    ent = _create(client, h, event_id="no-such-event", attributes={"kind": "event"})
    assert ent["event_id"] is None
