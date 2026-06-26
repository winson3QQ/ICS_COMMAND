"""
api/test_squads_query.py — P2-06d 小隊聚合 endpoint（issue #128）

驗 GET /api/cop/squads：
  - READ_ROLES（observer 含在內）→ 200，回 per team_color 聚合
  - 跨場 isolation：active 場 GET 只看當前場小隊（不洩漏別場）
  - live vs AAR 歷史場：commander 帶 ?exercise_id 看封存場；observer 被 resolve_scope 擋回 active
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


def test_active_scope_excludes_other_exercise(client, auth):
    # A 啟動→建 A 隊→封存；B 啟動→建 B 隊
    a = _mk_exercise(client, auth, "A")
    client.post(f"/api/exercises/{a['id']}/activate", json={}, headers=auth)
    client.post("/api/cop/entities", json=_mk_cop("a1", "Red"), headers=auth)
    client.post(f"/api/exercises/{a['id']}/archive", json={}, headers=auth)
    b = _mk_exercise(client, auth, "B")
    client.post(f"/api/exercises/{b['id']}/activate", json={}, headers=auth)
    client.post("/api/cop/entities", json=_mk_cop("b1", "Green"), headers=auth)
    # active=B 的預設 GET → 只看到 Green（B），看不到 Red（A）
    by = _squads_by_color(client.get("/api/cop/squads", headers=auth).json())
    assert "Green" in by
    assert "Red" not in by


# ── live vs AAR 歷史場（commander override / observer 被擋）────────────────────


def test_commander_can_query_historical_exercise(client, auth, commander_auth):
    a = _mk_exercise(client, auth, "A")
    client.post(f"/api/exercises/{a['id']}/activate", json={}, headers=auth)
    client.post("/api/cop/entities", json=_mk_cop("a1", "Red"), headers=auth)
    client.post(f"/api/exercises/{a['id']}/archive", json={}, headers=auth)
    b = _mk_exercise(client, auth, "B")
    client.post(f"/api/exercises/{b['id']}/activate", json={}, headers=auth)
    # commander 帶 ?exercise_id=A（歷史封存場）→ 看得到 A 的 Red 隊
    by = _squads_by_color(client.get(f"/api/cop/squads?exercise_id={a['id']}", headers=commander_auth).json())
    assert "Red" in by and by["Red"]["total"] == 1


def test_observer_override_blocked_back_to_active(client, auth, observer_auth):
    # A 啟動→建 A 隊→封存；B 啟動→建 B 隊
    a = _mk_exercise(client, auth, "A")
    client.post(f"/api/exercises/{a['id']}/activate", json={}, headers=auth)
    client.post("/api/cop/entities", json=_mk_cop("a1", "Red"), headers=auth)
    client.post(f"/api/exercises/{a['id']}/archive", json={}, headers=auth)
    b = _mk_exercise(client, auth, "B")
    client.post(f"/api/exercises/{b['id']}/activate", json={}, headers=auth)
    client.post("/api/cop/entities", json=_mk_cop("b1", "Green"), headers=auth)
    # observer 帶 ?exercise_id=A（歷史）→ resolve_scope 擋回 active=B → 只看 Green
    by = _squads_by_color(client.get(f"/api/cop/squads?exercise_id={a['id']}", headers=observer_auth).json())
    assert "Green" in by and "Red" not in by
