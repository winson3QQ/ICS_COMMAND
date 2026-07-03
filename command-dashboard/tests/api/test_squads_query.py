# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
api/test_squads_query.py — P2-06d 小隊聚合 endpoint（issue #128）

驗 GET /api/cop/squads：
  - READ_ROLES（observer 含在內）→ 200，回 per team_color 聚合
  - #472：可見性軸改 faction（非 exercise scope）→ cop＝跨場共享池，自建小隊跨場都聚合、
    不再吃 ?exercise_id override；紅方隔離改由 faction 守門（見 test_faction_admin/enforce）
  - 未分隊（team_color 缺）聚成 None 組
"""

import pytest

pytestmark = pytest.mark.api

# operator_auth / observer_auth / commander_auth fixtures 在 tests/api/conftest.py 共用


def _mk_cop(callsign, team_color=None, lat=25.0, lon=121.0):
    body = {"type": "a-f-G-U-C", "lat": lat, "lon": lon, "callsign": callsign}
    if team_color is not None:
        body["team_color"] = team_color
    return body


def _squads_by_color(payload):
    return {s["team_color"]: s for s in payload["squads"]}


# ── READ_ROLES（observer 可讀）─────────────────────────────────────────────────


def test_observer_can_read_squads(client, auth, observer_auth, active_exercise):
    # admin 在 active 場建兩隊
    client.post("/api/cop/entities", json=_mk_cop("A1", "Cyan"), headers=auth)
    client.post("/api/cop/entities", json=_mk_cop("A2", "Cyan"), headers=auth)
    client.post("/api/cop/entities", json=_mk_cop("B1", "Blue"), headers=auth)
    # observer（READ_ROLES）可讀
    r = client.get("/api/cop/squads", headers=observer_auth)
    assert r.status_code == 200, r.text
    by = _squads_by_color(r.json())
    assert by["Cyan"]["total"] == 2
    assert by["Cyan"]["online"] == 2
    assert by["Blue"]["total"] == 1


def test_unassigned_null_group(client, auth, active_exercise):
    client.post("/api/cop/entities", json=_mk_cop("U1"), headers=auth)  # 無 team_color
    client.post("/api/cop/entities", json=_mk_cop("C1", "Cyan"), headers=auth)
    by = _squads_by_color(client.get("/api/cop/squads", headers=auth).json())
    assert None in by  # 未分隊組存在
    assert by[None]["total"] == 1


# ── 跨場 isolation ────────────────────────────────────────────────────────────


def _mk_exercise(client, auth, name):
    return client.post("/api/exercises", json={"name": name, "type": "ttx"}, headers=auth).json()


def test_squads_include_cross_exercise_selfbuilt(client, auth):
    # #472：squads 可見性軸改 faction（非 exercise scope）→ cop＝跨場共享池，自建小隊跨場都聚合。
    # （team_color Red/Green 是小隊色、非 faction；自建 manual 恆對藍可見。）
    a = _mk_exercise(client, auth, "A")
    client.post(f"/api/exercises/{a['id']}/activate", json={}, headers=auth)
    client.post("/api/cop/entities", json=_mk_cop("a1", "Red"), headers=auth)
    client.post(f"/api/exercises/{a['id']}/archive", json={}, headers=auth)
    b = _mk_exercise(client, auth, "B")
    client.post(f"/api/exercises/{b['id']}/activate", json={}, headers=auth)
    client.post("/api/cop/entities", json=_mk_cop("b1", "Green"), headers=auth)
    # #472：跨場共享 → Red（A）與 Green（B）都聚合得到
    by = _squads_by_color(client.get("/api/cop/squads", headers=auth).json())
    assert "Green" in by
    assert "Red" in by


def test_squads_shared_pool_all_read_roles(client, auth, observer_auth):
    # #472：squads 不再按 exercise scope 過濾，也不吃 ?exercise_id override。observer（READ_ROLES）
    # 看得到共享池全部自建小隊（自建恆對藍可見；紅方隔離改由 faction 守門，見 test_faction_admin）。
    a = _mk_exercise(client, auth, "A")
    client.post(f"/api/exercises/{a['id']}/activate", json={}, headers=auth)
    client.post("/api/cop/entities", json=_mk_cop("a1", "Red"), headers=auth)
    client.post(f"/api/exercises/{a['id']}/archive", json={}, headers=auth)
    b = _mk_exercise(client, auth, "B")
    client.post(f"/api/exercises/{b['id']}/activate", json={}, headers=auth)
    client.post("/api/cop/entities", json=_mk_cop("b1", "Green"), headers=auth)
    by = _squads_by_color(client.get("/api/cop/squads", headers=observer_auth).json())
    assert "Green" in by and "Red" in by
