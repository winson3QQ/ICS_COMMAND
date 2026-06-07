"""P2-03（#107）— routers/tak.py REST ingest endpoint。

`POST /api/tak/events`（CoTEventIn）→ #105 接縫 `ingest_cot_event` → 落 cop_entities + 廣播。
真 ingest（非 mock）以滿足 DoD「entity 進 cop_entities」；RBAC 用 create_account 建非 admin 角色驗。
"""

from __future__ import annotations

from auth.role_enum import ROLE_OBSERVER_ZH, ROLE_OPERATOR_ZH
from repositories import cop_entity_repo
from repositories.account_repo import create_account


def _cot_body(uid="TAK-REST-1", time="2026-06-05T09:00:00Z", **over):
    body = {
        "uid": uid,
        "type": "a-f-G-U-C",
        "time": time,
        "start": time,
        "stale": "2026-06-05T09:30:00Z",
        "how": "m-g",
        "lat": 24.137,
        "lon": 120.687,
        "callsign": "REST-PROBE",
    }
    body.update(over)
    return body


def _login(client, username="admin", pin="1234"):
    r = client.post("/api/auth/login", json={"username": username, "pin": pin})
    assert r.status_code == 200, r.text
    return {"X-Session-Token": r.json()["session_id"]}


def test_post_valid_cot_ingests_to_cop(client, auth):
    r = client.post("/api/tak/events", json=_cot_body(), headers=auth)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "ingested"
    assert data["uid"] == "TAK-REST-1"
    assert data["version_clock"] == 1
    # 真的落 cop_entities
    row = cop_entity_repo.get_cop_entity("TAK-REST-1")
    assert row is not None and row["source"] == "tak" and row["callsign"] == "REST-PROBE"


def test_post_newer_event_updates_version_clock(client, auth):
    client.post("/api/tak/events", json=_cot_body(uid="TAK-UPD", time="2026-06-05T09:00:00Z"), headers=auth)
    r = client.post("/api/tak/events", json=_cot_body(uid="TAK-UPD", time="2026-06-05T09:05:00Z"), headers=auth)
    assert r.status_code == 200
    assert r.json()["status"] == "ingested"
    assert r.json()["version_clock"] == 2  # CAS +1


def test_post_resend_not_newer_skipped(client, auth):
    client.post("/api/tak/events", json=_cot_body(uid="TAK-DUP", time="2026-06-05T09:00:00Z"), headers=auth)
    # 同 time（非更新）→ 順序守門擋下
    r = client.post("/api/tak/events", json=_cot_body(uid="TAK-DUP", time="2026-06-05T09:00:00Z"), headers=auth)
    assert r.status_code == 200
    assert r.json()["status"] == "skipped"


def test_post_requires_auth(client):
    r = client.post("/api/tak/events", json=_cot_body())
    assert r.status_code == 401


def test_observer_cannot_post(client):
    create_account("obs_tak", "1234", ROLE_OBSERVER_ZH, "Observer TAK", "observer")
    headers = _login(client, "obs_tak", "1234")
    r = client.post("/api/tak/events", json=_cot_body(uid="TAK-OBS"), headers=headers)
    assert r.status_code == 403  # observer 非 COMMAND_ROLES


def test_operator_cannot_post(client):
    """#146：REST ingest 收緊到 COMMAND_ROLES —— operator（WRITE_ROLES 但非指揮層）不可
    經此端點注入/竄改 tak 物件（繞過 cop PUT/DELETE 來源守門的後門已關）。"""
    create_account("op_tak", "1234", ROLE_OPERATOR_ZH, "Operator TAK", "operator")
    headers = _login(client, "op_tak", "1234")
    r = client.post("/api/tak/events", json=_cot_body(uid="TAK-OP"), headers=headers)
    assert r.status_code == 403
    # 真的沒落 cop_entities（注入被擋）
    assert cop_entity_repo.get_cop_entity("TAK-OP") is None


def test_post_invalid_schema_422(client, auth):
    bad = _cot_body()
    del bad["how"]  # 缺 CoT 必填 how
    r = client.post("/api/tak/events", json=bad, headers=auth)
    assert r.status_code == 422


def test_status_reflects_disabled_default(client, auth):
    r = client.get("/api/tak/status", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False  # TAK_ENABLED 預設 false
    assert body["cot_url"] is None
