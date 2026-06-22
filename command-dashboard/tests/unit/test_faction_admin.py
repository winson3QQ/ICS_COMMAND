"""
tests/unit/test_faction_admin.py — #343 PR-5：admin 分類 API 層（faction_service）

鎖住：
- list_clients：從 cop_entities 解 producer 聚合（非 team_color）+ 標目前分類
- classify：驗場存在（D）→ upsert → 重解析名下 auto entity（NULL→指定 faction）→ resync 廣播
- override_entity：手動點單一 entity（manual）→ 後續重解析不覆寫
"""

import asyncio

import pytest
from fastapi import HTTPException

from repositories import client_faction_repo, cop_entity_repo
from schemas.tak import CoTEventIn
from services import cop_service, exercise_service, faction_service


@pytest.fixture(autouse=True)
def _db(tmp_db):
    yield


@pytest.fixture
def _no_ws(monkeypatch):
    """攔截兩種廣播（per-entity + broadcast_all），不真開 WS。回收集到的 op。"""
    ops = []

    async def _b(message, exercise_id=None, source=None, faction=None):
        ops.append(message.get("op"))

    async def _ball(message):
        ops.append(message.get("op"))

    monkeypatch.setattr(cop_service.cop_hub, "broadcast", _b)
    monkeypatch.setattr(faction_service.cop_hub, "broadcast_all", _ball)
    return ops


def _ingest_marker(uid, creator_uid, callsign="敵-A"):
    """ingest 一個 tak 標記（creator=creator_uid）→ entity faction 依當下分類解析。"""
    ev = CoTEventIn(
        uid=uid,
        type="a-h-G",
        time="2026-06-22T01:00:00Z",
        start="2026-06-22T01:00:00Z",
        stale="2099-01-01T00:00:00Z",
        how="h-g-i-g-o",
        lat=24.1,
        lon=120.6,
        callsign=callsign,
        detail={"creator": {"uid": creator_uid, "callsign": callsign}},
    )
    return asyncio.run(cop_service.ingest_cot_event(ev))


def test_classify_reresolves_existing_entities(_no_ws):
    # 先 ingest（未分類 → faction NULL），再 classify → 重解析名下 entity
    _ingest_marker("MK-1", "DEV-X")
    _ingest_marker("MK-2", "DEV-X")
    assert cop_entity_repo.get_cop_entity("MK-1")["faction"] is None  # 未分類

    res = asyncio.run(faction_service.classify(None, "DEV-X", "red", "敵-A", "admin"))
    assert res["reresolved"] == 2
    assert cop_entity_repo.get_cop_entity("MK-1")["faction"] == "red"
    assert cop_entity_repo.get_cop_entity("MK-2")["faction"] == "red"
    assert "resync" in _no_ws  # 重分類後廣播 resync


def test_classify_validates_exercise_exists(_no_ws):
    """D：指定不存在的 exercise_id → 404（不留孤兒分類）。"""
    with pytest.raises(HTTPException) as ei:
        asyncio.run(faction_service.classify(99999, "DEV-X", "blue", None, "admin"))
    assert ei.value.status_code == 404


def test_classify_real_exercise_ok(_no_ws):
    ex = exercise_service.create({"name": "drill", "type": "ttx"})
    exercise_service.set_active(ex["id"], "admin")
    _ingest_marker("MK-EX", "DEV-Y")  # ingest 綁 active 場
    res = asyncio.run(faction_service.classify(ex["id"], "DEV-Y", "blue", None, "admin"))
    assert res["reresolved"] == 1
    assert cop_entity_repo.get_cop_entity("MK-EX")["faction"] == "blue"


def test_list_clients_aggregates_producers(_no_ws):
    _ingest_marker("MK-A", "DEV-A", "甲")
    _ingest_marker("MK-A2", "DEV-A", "甲")  # 同 producer 兩 entity → 聚成一 client
    _ingest_marker("MK-B", "DEV-B", "乙")
    client_faction_repo.upsert_faction(None, "DEV-A", "blue", "甲", "admin")

    clients = {c["client_key"]: c for c in faction_service.list_clients(None)}
    assert set(clients) == {"DEV-A", "DEV-B"}  # 兩個 producer（非 4 個 entity）
    assert clients["DEV-A"]["faction"] == "blue" and clients["DEV-A"]["classified"] is True
    assert clients["DEV-B"]["faction"] is None and clients["DEV-B"]["classified"] is False


def test_override_entity_not_clobbered_by_reresolve(_no_ws):
    """手動 override（manual）後，classify 該 producer 不覆寫 manual entity。"""
    _ingest_marker("MK-M", "DEV-Z")
    asyncio.run(faction_service.override_entity("MK-M", "neutral", "admin"))
    row = cop_entity_repo.get_cop_entity("MK-M")
    assert row["faction"] == "neutral" and row["faction_source"] == "manual"

    # classify DEV-Z=red → 重解析只動 auto，manual 的 MK-M 不變
    res = asyncio.run(faction_service.classify(None, "DEV-Z", "red", None, "admin"))
    assert res["reresolved"] == 0  # MK-M 是 manual → 不在重解析範圍
    assert cop_entity_repo.get_cop_entity("MK-M")["faction"] == "neutral"  # 保留 manual


def test_override_entity_missing_404(_no_ws):
    with pytest.raises(HTTPException) as ei:
        asyncio.run(faction_service.override_entity("NOPE", "blue", "admin"))
    assert ei.value.status_code == 404
