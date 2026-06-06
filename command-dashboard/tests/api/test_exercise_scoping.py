"""
api/test_exercise_scoping.py — P1-14 exercise scoping wiring 測試（issue #89 PR-1）

驗證：
  - events / cop create 由 server 端 active 場決定 exercise_id（不信任 client 帶的值）
  - 無 active 時 → exercise_id NULL（實戰/未分場池）
  - GET 預設只回當前 active 場
  - cop create 綁定 active（= P1-16 placement 圖釘綁 exercise 的點）
"""

import pytest

pytestmark = pytest.mark.api

# operator_auth / observer_auth / commander_auth fixtures 在 tests/api/conftest.py 共用

_EVENT = {
    "reported_by_unit": "shelter",
    "event_type": "fire",
    "severity": "critical",
    "description": "scoping 測試事件",
    "operator_name": "admin",
}
_COP = {"type": "a-f-G-U-C", "lat": 25.0, "lon": 121.0, "callsign": "SCOPE-TEST"}


def _ev_by_desc(events, desc):
    return next((e for e in events if e.get("description") == desc), None)


class TestEventScoping:
    def test_create_stamps_active_and_ignores_client_value(self, client, auth, active_exercise):
        # 帶一個「假」exercise_id，server 應忽略、改用 active 場
        bogus = active_exercise["id"] + 99999
        r = client.post("/api/events", json={**_EVENT, "exercise_id": bogus}, headers=auth)
        assert r.status_code == 200
        # GET 預設只回 active 場 → 找得到，且 exercise_id == active（非 bogus）
        evs = client.get("/api/events", headers=auth).json()
        ev = _ev_by_desc(evs, _EVENT["description"])
        assert ev is not None, "active 場應看得到剛建的事件"
        assert ev["exercise_id"] == active_exercise["id"]
        assert ev["exercise_id"] != bogus

    def test_create_without_active_is_null(self, client, auth):
        # 無 active exercise → exercise_id 落 NULL（實戰池）
        r = client.post("/api/events", json=_EVENT, headers=auth)
        assert r.status_code == 200
        evs = client.get("/api/events", headers=auth).json()  # 無 active → 不過濾，回全部
        ev = _ev_by_desc(evs, _EVENT["description"])
        assert ev is not None
        assert ev["exercise_id"] is None


class TestCopScoping:
    def test_create_stamps_active_exercise(self, client, auth, active_exercise):
        # P1-16 銜接點：cop create 自動綁 active 場
        r = client.post("/api/cop/entities", json=_COP, headers=auth)
        assert r.status_code == 201
        assert r.json()["exercise_id"] == active_exercise["id"]

    def test_create_without_active_is_null(self, client, auth):
        r = client.post("/api/cop/entities", json=_COP, headers=auth)
        assert r.status_code == 201
        assert r.json()["exercise_id"] is None


def _mk_exercise(client, auth, name):
    return client.post("/api/exercises", json={"name": name, "type": "ttx"}, headers=auth).json()


class TestCopByUidScope:
    def test_operator_cannot_read_other_exercise_entity_by_uid(self, client, auth, operator_auth):
        # A 啟動→建 entity（綁 A）→封存；B 啟動
        a = _mk_exercise(client, auth, "A")
        client.post(f"/api/exercises/{a['id']}/activate", json={}, headers=auth)
        uid_a = client.post("/api/cop/entities", json=_COP, headers=auth).json()["uid"]
        client.post(f"/api/exercises/{a['id']}/archive", json={}, headers=auth)
        b = _mk_exercise(client, auth, "B")
        client.post(f"/api/exercises/{b['id']}/activate", json={}, headers=auth)
        # operator（scope=B）by-uid 取 A 的 entity → 404（不在 scope，不洩漏存在性）
        assert client.get(f"/api/cop/entities/{uid_a}", headers=operator_auth).status_code == 404
        # 指揮層（admin）可取任意 uid（對齊 list override）
        assert client.get(f"/api/cop/entities/{uid_a}", headers=auth).status_code == 200
        # operator 取當前 scope（B）內 entity → 200
        uid_b = client.post("/api/cop/entities", json=_COP, headers=auth).json()["uid"]
        assert client.get(f"/api/cop/entities/{uid_b}", headers=operator_auth).status_code == 200


class TestStrictIsolation:
    def test_active_view_excludes_realops_null_events(self, client, auth):
        # 無 active → 建「實戰」事件（exercise_id NULL）
        client.post("/api/events", json={**_EVENT, "description": "實戰事件"}, headers=auth)
        # 啟動 A → 建 A 事件
        a = _mk_exercise(client, auth, "A")
        client.post(f"/api/exercises/{a['id']}/activate", json={}, headers=auth)
        client.post("/api/events", json={**_EVENT, "description": "A事件"}, headers=auth)
        # active=A 的 GET（strict）→ 看得到 A、看不到實戰 NULL
        evs = client.get("/api/events", headers=auth).json()
        descs = [e["description"] for e in evs]
        assert "A事件" in descs
        assert "實戰事件" not in descs


class TestScopeRoleGate:
    def test_operator_cannot_override_to_historical(self, client, auth, operator_auth):
        # A 啟動→建 A 事件→封存；B 啟動→建 B 事件
        a = _mk_exercise(client, auth, "A")
        client.post(f"/api/exercises/{a['id']}/activate", json={}, headers=auth)
        client.post("/api/events", json={**_EVENT, "description": "在A"}, headers=auth)
        client.post(f"/api/exercises/{a['id']}/archive", json={}, headers=auth)
        b = _mk_exercise(client, auth, "B")
        client.post(f"/api/exercises/{b['id']}/activate", json={}, headers=auth)
        client.post("/api/events", json={**_EVENT, "description": "在B"}, headers=auth)
        # operator 帶 ?exercise_id=A（歷史）→ 被 resolve_scope 擋回 active=B
        evs = client.get(f"/api/events?exercise_id={a['id']}", headers=operator_auth).json()
        descs = [e["description"] for e in evs]
        assert "在B" in descs and "在A" not in descs
        # commander/sysadmin（admin）帶 ?exercise_id=A → 看得到歷史 A
        descs2 = [e["description"] for e in
                  client.get(f"/api/events?exercise_id={a['id']}", headers=auth).json()]
        assert "在A" in descs2

    def test_observer_blocked_from_historical_aar_and_ai_report(self, client, auth, observer_auth, active_exercise):
        # HIGH-3/4：observer 帶任意 exercise_id 撈 AAR / 後分析 → 403（role gate）
        exid = active_exercise["id"]
        assert client.get(f"/api/exercises/{exid}/aar", headers=observer_auth).status_code == 403
        assert client.get(f"/api/ai/report/{exid}", headers=observer_auth).status_code == 403
        # 對照：sysadmin 不被擋（200 或非 403）
        assert client.get(f"/api/exercises/{exid}/aar", headers=auth).status_code != 403


class TestNodeInfraRBAC:
    """P1-16 security review HIGH-1：節點(zone)/設施(infra)建立+刪除限指揮層（後端授權，非只前端）。
    其餘 kind（route/polygon/event）operator 仍可寫，不誤傷。"""

    _ZONE = {"type": "a-f-G-I", "lat": 24.8, "lon": 121.0, "callsign": "收容組",
             "attributes": {"kind": "zone", "node_type": "shelter"}}
    _INFRA = {"type": "a-f-G-I", "lat": 24.8, "lon": 121.0, "callsign": "醫院",
              "attributes": {"kind": "infra", "infra_type": "hospital"}}
    _ROUTE = {"type": "a-f-G", "lat": 24.8, "lon": 121.0, "callsign": "R",
              "attributes": {"kind": "route", "vertices": [[24.8, 121.0], [24.9, 121.1]]}}

    def test_operator_cannot_create_zone_or_infra(self, client, operator_auth):
        assert client.post("/api/cop/entities", json=self._ZONE, headers=operator_auth).status_code == 403
        assert client.post("/api/cop/entities", json=self._INFRA, headers=operator_auth).status_code == 403

    def test_operator_can_still_create_route(self, client, operator_auth):
        # 非 zone/infra 的 cop 寫入（operator 日常畫路線/多邊形）不受限
        assert client.post("/api/cop/entities", json=self._ROUTE, headers=operator_auth).status_code == 201

    def test_commander_creates_and_deletes_zone(self, client, commander_auth):
        r = client.post("/api/cop/entities", json=self._ZONE, headers=commander_auth)
        assert r.status_code == 201, r.text
        uid, vc = r.json()["uid"], r.json()["version_clock"]
        d = client.delete(f"/api/cop/entities/{uid}", headers={**commander_auth, "If-Match": str(vc)})
        assert d.status_code == 200

    def test_operator_cannot_delete_zone(self, client, auth, operator_auth):
        r = client.post("/api/cop/entities", json=self._ZONE, headers=auth)  # 指揮層建
        uid, vc = r.json()["uid"], r.json()["version_clock"]
        assert client.delete(f"/api/cop/entities/{uid}",
                             headers={**operator_auth, "If-Match": str(vc)}).status_code == 403


class TestExerciseDelete:
    def test_delete_cascades_and_blocks_active(self, client, auth):
        a = _mk_exercise(client, auth, "刪除測試")
        client.post(f"/api/exercises/{a['id']}/activate", json={}, headers=auth)
        client.post("/api/events", json={**_EVENT, "description": "A事件"}, headers=auth)
        # 進行中不可刪 → 409
        assert client.delete(f"/api/exercises/{a['id']}", headers=auth).status_code == 409
        client.post(f"/api/exercises/{a['id']}/archive", json={}, headers=auth)
        # 刪前：admin override 看得到 A 的事件
        before = client.get(f"/api/events?exercise_id={a['id']}", headers=auth).json()
        assert any(e.get("description") == "A事件" for e in before)
        # 刪除（級聯）→ 200；exercise 與其事件都沒了
        assert client.delete(f"/api/exercises/{a['id']}", headers=auth).status_code == 200
        assert client.get(f"/api/exercises/{a['id']}", headers=auth).status_code == 404
        after = client.get(f"/api/events?exercise_id={a['id']}", headers=auth).json()
        assert not any(e.get("description") == "A事件" for e in after)

    def test_delete_is_sysadmin_only(self, client, auth, commander_auth, operator_auth):
        # 刪除限 sysadmin：commander（能建/啟）與 operator 都應 403
        a = _mk_exercise(client, auth, "權限測試")
        client.post(f"/api/exercises/{a['id']}/archive", json={}, headers=auth)  # 非 active
        assert client.delete(f"/api/exercises/{a['id']}", headers=commander_auth).status_code == 403
        assert client.delete(f"/api/exercises/{a['id']}", headers=operator_auth).status_code == 403
        # sysadmin 可刪
        assert client.delete(f"/api/exercises/{a['id']}", headers=auth).status_code == 200
