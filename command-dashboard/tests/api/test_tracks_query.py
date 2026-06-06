"""
api/test_tracks_query.py — P2-06b 軌跡查詢 endpoint（issue #123）

驗 GET /api/exercises/{id}/tracks：
  - COMMAND_ROLES（sysadmin/commander）→ 200，回該場軌跡（t 升序、全欄位）
  - 非 COMMAND_ROLES（operator/observer）→ 403（中央 gate /api/exercises/* 非 DELETE）
  - 場不存在 → 404
  - uid 過濾
"""

import pytest

from repositories.account_repo import create_account

pytestmark = pytest.mark.api


def _login(client, username, pin):
    r = client.post("/api/auth/login", json={"username": username, "pin": pin})
    assert r.status_code == 200, r.text
    return {"X-Session-Token": r.json()["session_id"]}


@pytest.fixture
def operator_auth(client):
    create_account("op1", "5678", "操作員", "前進組", "operator")
    return _login(client, "op1", "5678")


@pytest.fixture
def observer_auth(client):
    create_account("ob1", "5678", "觀察員", "", "observer")
    return _login(client, "ob1", "5678")


@pytest.fixture
def commander_auth(client):
    create_account("cmd1", "5678", "指揮官", "", "commander")
    return _login(client, "cmd1", "5678")


def _push_cot(client, auth, uid, t, lat=24.1, lon=120.6):
    """經 POST /api/tak/events 推一筆 CoT（active 場下 → entity 綁場 + 寫軌跡）。"""
    body = {
        "uid": uid, "type": "a-f-G-U-C", "time": t, "start": t,
        "stale": "2099-01-01T00:00:00Z", "how": "m-g", "lat": lat, "lon": lon,
    }
    r = client.post("/api/tak/events", json=body, headers=auth)
    assert r.status_code == 200, r.text


# ── 正常查詢（COMMAND_ROLES）──────────────────────────────────────────────────


def test_tracks_returns_ascending_full_fields(client, auth, active_exercise):
    eid = active_exercise["id"]
    _push_cot(client, auth, "T1", "2026-06-05T04:00:00Z")
    _push_cot(client, auth, "T1", "2026-06-05T04:00:10Z")  # +10s ≥ 抽樣 → 都寫
    r = client.get(f"/api/exercises/{eid}/tracks", headers=auth)
    assert r.status_code == 200
    data = r.json()
    assert [d["t"] for d in data] == ["2026-06-05T04:00:00Z", "2026-06-05T04:00:10Z"]
    assert set(data[0]) >= {"uid", "t", "lat", "lon", "hae", "heading_deg", "speed_mps"}


def test_tracks_uid_filter(client, auth, active_exercise):
    eid = active_exercise["id"]
    _push_cot(client, auth, "A", "2026-06-05T04:00:00Z")
    _push_cot(client, auth, "B", "2026-06-05T04:00:00Z")
    r = client.get(f"/api/exercises/{eid}/tracks?uid=A", headers=auth)
    assert r.status_code == 200
    assert {d["uid"] for d in r.json()} == {"A"}


# ── 404 / RBAC（非 COMMAND_ROLES → 403）───────────────────────────────────────


def test_tracks_404_when_exercise_missing(client, auth):
    r = client.get("/api/exercises/999999/tracks", headers=auth)
    assert r.status_code == 404


def test_tracks_403_operator(client, active_exercise, operator_auth):
    r = client.get(f"/api/exercises/{active_exercise['id']}/tracks", headers=operator_auth)
    assert r.status_code == 403


def test_tracks_403_observer(client, active_exercise, observer_auth):
    r = client.get(f"/api/exercises/{active_exercise['id']}/tracks", headers=observer_auth)
    assert r.status_code == 403


def test_tracks_200_commander(client, active_exercise, commander_auth):
    r = client.get(f"/api/exercises/{active_exercise['id']}/tracks", headers=commander_auth)
    assert r.status_code == 200
