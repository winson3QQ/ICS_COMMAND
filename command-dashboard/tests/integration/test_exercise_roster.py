# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""tests/integration/test_exercise_roster.py — #267 Slice 2：演習 roster（參與 + 編制）repo + 端點。"""

import pytest

from repositories import exercise_repo, exercise_roster_repo

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _db(tmp_db):
    yield


def _mk():
    return exercise_repo.create_exercise({"name": "drill", "type": "ttx"})["id"]


# ── repo ──
def test_upsert_list_is_in_unit():
    eid = _mk()
    exercise_roster_repo.upsert_member(eid, "alpha", "Alpha 小隊", "admin")
    exercise_roster_repo.upsert_member(eid, "bravo", None, "admin")
    r = {m["client_cn"]: m for m in exercise_roster_repo.list_roster(eid)}
    assert set(r) == {"alpha", "bravo"}
    assert r["alpha"]["unit"] == "Alpha 小隊"
    assert exercise_roster_repo.is_in_roster(eid, "alpha") is True
    assert exercise_roster_repo.is_in_roster(eid, "ghost") is False
    assert exercise_roster_repo.unit_for(eid, "alpha") == "Alpha 小隊"
    assert exercise_roster_repo.unit_for(eid, "bravo") is None


def test_upsert_updates_unit():
    eid = _mk()
    exercise_roster_repo.upsert_member(eid, "alpha", "X", "admin")
    exercise_roster_repo.upsert_member(eid, "alpha", "Y", "admin")  # 同 CN → 改編制，不重複
    assert len(exercise_roster_repo.list_roster(eid)) == 1
    assert exercise_roster_repo.unit_for(eid, "alpha") == "Y"


def test_remove():
    eid = _mk()
    exercise_roster_repo.upsert_member(eid, "alpha", None, "admin")
    assert exercise_roster_repo.remove_member(eid, "alpha", "admin") is True
    assert exercise_roster_repo.remove_member(eid, "alpha", "admin") is False  # 已不在
    assert exercise_roster_repo.is_in_roster(eid, "alpha") is False


def test_per_exercise_isolation():
    e1, e2 = _mk(), _mk()
    exercise_roster_repo.upsert_member(e1, "alpha", "U1", "admin")
    assert exercise_roster_repo.is_in_roster(e1, "alpha") is True
    assert exercise_roster_repo.is_in_roster(e2, "alpha") is False  # 別場不含同 CN


def test_delete_exercise_clears_new_fk_tables():
    """review B1 回歸：新 FK 子表（interval/roster，無 ON DELETE CASCADE + foreign_keys=ON）須先清，
    否則 delete_exercise 撞 FK RESTRICT。開場必寫 interval ⇒ 幾乎每場都會中。"""
    eid = _mk()
    exercise_repo.update_exercise_status(eid, "active", "admin")  # 寫一筆 interval
    exercise_repo.update_exercise_status(eid, "archived", "admin")  # 關（非 active 才可刪）
    exercise_roster_repo.upsert_member(eid, "alpha", "U", "admin")  # roster 一筆
    res = exercise_repo.delete_exercise(eid)  # 不可拋 FK
    assert "skipped" not in res  # 真的刪了（非 active_or_missing）
    assert exercise_repo.list_active_intervals(eid) == []
    assert exercise_roster_repo.list_roster(eid) == []
    assert exercise_repo.get_exercise(eid) is None


# ── 端點（sysadmin） ──
def test_roster_endpoints(client, auth):
    eid = exercise_repo.create_exercise({"name": "drill", "type": "ttx"})["id"]
    base = f"/api/admin/exercises/{eid}/roster"
    assert client.post(base, json={"cn": "alpha", "unit": "Alpha"}, headers=auth).status_code == 200
    r = client.get(base, headers=auth)
    assert r.status_code == 200 and any(m["client_cn"] == "alpha" for m in r.json()["roster"])
    assert client.post(base + "/remove", json={"cn": "alpha"}, headers=auth).status_code == 200
    assert client.post(base + "/remove", json={"cn": "alpha"}, headers=auth).status_code == 404  # 已移除再移
    assert client.post(base, json={"cn": "a,b"}, headers=auth).status_code == 422  # 不合法 cn（逗號）


def test_roster_requires_sysadmin(client):
    assert client.get("/api/admin/exercises/1/roster").status_code == 401


def test_roster_add_connected(client, auth, monkeypatch):
    """#267 UI：「加入全部連線」一鍵把在線 client 全納入 roster；已在者不重計（added=新加入數）。"""
    eid = exercise_repo.create_exercise({"name": "drill", "type": "ttx"})["id"]
    base = f"/api/admin/exercises/{eid}/roster"
    client.post(base, json={"cn": "alpha"}, headers=auth)  # alpha 先在 roster

    async def _fake_clients(exercise_id):  # mock 在線∩發證（免起 TAK）
        return [{"cn": "alpha"}, {"cn": "bravo"}, {"cn": "charlie"}]

    from services import faction_service

    monkeypatch.setattr(faction_service, "list_clients", _fake_clients)
    r = client.post(base + "/add-connected", headers=auth)
    assert r.status_code == 200
    data = r.json()
    assert data["total_online"] == 3
    assert data["added"] == 2  # alpha 已在 → 只加 bravo/charlie
    assert {m["client_cn"] for m in data["roster"]} == {"alpha", "bravo", "charlie"}
