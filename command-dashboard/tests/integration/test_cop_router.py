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
