# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
api/test_tracks_query.py — P2-06b 軌跡查詢 endpoint（issue #123）

驗 GET /api/exercises/{id}/tracks：
  - COMMAND_ROLES（sysadmin/commander）→ 200，回該場軌跡（t 升序、全欄位）
  - 非 COMMAND_ROLES（operator/observer）→ 403（中央 gate /api/exercises/* 非 DELETE）
  - 場不存在 → 404
  - uid 過濾
"""

import pytest

from services import cop_service

pytestmark = pytest.mark.api

# operator_auth / observer_auth / commander_auth fixtures 在 tests/api/conftest.py 共用


@pytest.fixture(autouse=True)
def _scope_via_active(monkeypatch):
    """本檔測**軌跡查詢 endpoint**（非 #267 scope doctrine）：純乙後 ingest 不再 auto-capture，
    本檔不設 roster，故顯式還原「有 active 即綁」讓推入的點落場、寫軌跡；scope 解析（roster×窗）
    交給 test_exercise_scope_resolve.py 專責。TestClient 同進程跑 app → monkeypatch 直達 service。"""
    monkeypatch.setattr(cop_service, "_resolve_exercise_scope", lambda e: cop_service.current_exercise_id())


def _push_cot(client, auth, uid, t, lat=24.1, lon=120.6):
    """經 POST /api/tak/events 推一筆 CoT（active 場下 → entity 綁場 + 寫軌跡）。"""
    body = {
        "uid": uid,
        "type": "a-f-G-U-C",
        "time": t,
        "start": t,
        "stale": "2099-01-01T00:00:00Z",
        "how": "m-g",
        "lat": lat,
        "lon": lon,
    }
    r = client.post("/api/tak/events", json=body, headers=auth)
    assert r.status_code == 200, r.text


# ── 正常查詢（COMMAND_ROLES）──────────────────────────────────────────────────


def test_tracks_returns_ascending_full_fields(client, auth, active_exercise):
    eid = active_exercise["id"]
    _push_cot(client, auth, "T1", "2026-06-05T04:00:00Z")
    _push_cot(client, auth, "T1", "2026-06-05T04:00:10Z")  # +10s ≥ 抽樣 → 都寫
    client.post(f"/api/exercises/{eid}/archive", headers=auth)  # #343 §6：AAR/回放須演習結束（無 active）
    r = client.get(f"/api/exercises/{eid}/tracks", headers=auth)
    assert r.status_code == 200
    data = r.json()
    assert [d["t"] for d in data] == ["2026-06-05T04:00:00Z", "2026-06-05T04:00:10Z"]
    assert set(data[0]) >= {"uid", "t", "lat", "lon", "hae", "heading_deg", "speed_mps"}


def test_tracks_uid_filter(client, auth, active_exercise):
    eid = active_exercise["id"]
    _push_cot(client, auth, "A", "2026-06-05T04:00:00Z")
    _push_cot(client, auth, "B", "2026-06-05T04:00:00Z")
    client.post(f"/api/exercises/{eid}/archive", headers=auth)  # #343 §6
    r = client.get(f"/api/exercises/{eid}/tracks?uid=A", headers=auth)
    assert r.status_code == 200
    assert {d["uid"] for d in r.json()} == {"A"}


def test_tracks_date_only_filter(client, auth, active_exercise):
    """純日期 from/to 補當天起訖，不漏當天軌跡（review #2：修前 'YYYY-MM-DDZ' 字串比較漏掉）。"""
    eid = active_exercise["id"]
    _push_cot(client, auth, "D1", "2026-06-05T08:00:00Z")
    client.post(f"/api/exercises/{eid}/archive", headers=auth)  # #343 §6
    r = client.get(f"/api/exercises/{eid}/tracks?from=2026-06-05&to=2026-06-05", headers=auth)
    assert r.status_code == 200
    assert len(r.json()) == 1  # 當天軌跡查得到


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
    client.post(f"/api/exercises/{active_exercise['id']}/archive", headers=commander_auth)  # #343 §6
    r = client.get(f"/api/exercises/{active_exercise['id']}/tracks", headers=commander_auth)
    assert r.status_code == 200


def test_tracks_409_during_active_exercise(client, active_exercise, commander_auth):
    """#343 §6：演習進行中 AAR/回放對所有角色（含指揮層）關閉 → 409。"""
    r = client.get(f"/api/exercises/{active_exercise['id']}/tracks", headers=commander_auth)
    assert r.status_code == 409
