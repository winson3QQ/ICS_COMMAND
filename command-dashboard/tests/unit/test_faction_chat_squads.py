"""
tests/unit/test_faction_chat_squads.py — #343 review 收尾：squads(A) + GeoChat(B) + 空集 guard(E)

鎖住 code/security review 找出的 live 讀取面洩漏修補：
- A：aggregate_squads(visible_factions=) 排除紅/NULL tak（否則 centroid/兵力洩漏紅軍位置）
- B：GeoChat ingest 解發話端 faction + list_chats/broadcast 過濾（紅軍通聯不漏藍方）
- E：_faction_clause 空集 → source != 'tak'（fail-closed，避非法 IN ()）
"""

import asyncio

import pytest

from repositories import client_faction_repo, cop_entity_repo
from schemas.cop import CoPEntity
from schemas.tak import CoTEventIn
from services import chat_service

BLUE = frozenset({"blue", "neutral"})


@pytest.fixture(autouse=True)
def _db(tmp_db):
    yield


def _tak_entity(uid, faction, team_color):
    cop_entity_repo.insert_cop_entity(
        CoPEntity(
            uid=uid,
            type="a-f-G-U-C",
            time="2026-06-22T00:00:00Z",
            start="2026-06-22T00:00:00Z",
            stale="2099-01-01T00:00:00Z",
            how="m-g",
            lat=24.1,
            lon=120.6,
            source="tak",
            faction=faction,
            team_color=team_color,
        )
    )


# ── A：squads 聚合 faction 過濾 ───────────────────────────────────────────────


def test_aggregate_squads_excludes_red_and_null_for_blue():
    _tak_entity("u-blue", "blue", "Cyan")
    _tak_entity("u-red", "red", "Red")
    _tak_entity("u-null", None, "Gray")  # 未分類
    teams_all = {s["team_color"] for s in cop_entity_repo.aggregate_squads()}
    assert teams_all == {"Cyan", "Red", "Gray"}  # 無過濾 → 全在
    teams_blue = {s["team_color"] for s in cop_entity_repo.aggregate_squads(visible_factions=BLUE)}
    assert teams_blue == {"Cyan"}  # red + 未分類(NULL) 都被擋


# ── E：_faction_clause 空集 guard ─────────────────────────────────────────────


def test_faction_clause_empty_set_excludes_all_tak():
    _tak_entity("u-blue", "blue", "Cyan")
    cop_entity_repo.insert_cop_entity(
        CoPEntity(
            uid="m-own",
            type="a-f-G",
            time="2026-06-22T00:00:00Z",
            start="2026-06-22T00:00:00Z",
            stale="2099-01-01T00:00:00Z",
            how="h-e",
            lat=24.1,
            lon=120.6,
            source="manual",  # 自建非 tak
        )
    )
    # 空可見集 → 看不到任何 tak（fail-closed），但 manual 自建仍在
    uids = {e["uid"] for e in cop_entity_repo.list_cop_entities(visible_factions=frozenset())}
    assert uids == {"m-own"}


# ── B：GeoChat 發話端 faction 解析 + 過濾 ──────────────────────────────────────


def test_geochat_client_key_parsing():
    assert chat_service._geochat_client_key("GeoChat.ANDROID-2eba.All Chat Rooms.abc") == "ANDROID-2eba"
    assert chat_service._geochat_client_key("not-a-geochat-uid") is None
    assert chat_service._geochat_client_key(None) is None


def _geochat(uid, text="敵情回報"):
    return CoTEventIn(
        uid=uid,
        type="b-t-f",
        time="2026-06-22T01:00:00Z",
        start="2026-06-22T01:00:00Z",
        stale="2099-01-01T00:00:00Z",
        how="h-g-i-g-o",
        lat=24.1,
        lon=120.6,
        detail={"__chat": {"senderCallsign": "ALPHA-1", "chatroom": "All Chat Rooms"}, "remarks": {"_text": text}},
        remarks=text,
    )


def test_ingest_geochat_resolves_sender_faction(monkeypatch):
    captured = []

    async def _fake(message, exercise_id=None, source=None, faction=None):
        captured.append((source, faction))

    monkeypatch.setattr(chat_service.cop_hub, "broadcast", _fake)
    client_faction_repo.upsert_faction(None, "ANDROID-RED", "red", "敵", "admin")

    asyncio.run(chat_service.ingest_chat(_geochat("GeoChat.ANDROID-RED.All Chat Rooms.m1")))
    # broadcast 帶 source='tak' + 解析出的 faction='red' → 藍方 WS 連線會被擋
    assert captured == [("tak", "red")]


def test_geochat_read_filters_red_from_blue():
    client_faction_repo.upsert_faction(None, "ANDROID-RED", "red", "敵", "admin")
    client_faction_repo.upsert_faction(None, "ANDROID-BLUE", "blue", "友", "admin")
    asyncio.run(chat_service.ingest_chat(_geochat("GeoChat.ANDROID-RED.All Chat Rooms.r1", "紅軍通聯")))
    asyncio.run(chat_service.ingest_chat(_geochat("GeoChat.ANDROID-BLUE.All Chat Rooms.b1", "藍軍通聯")))

    # 藍方視角：只見藍軍通聯，紅軍被擋
    feed = chat_service.build_chat_feed(None, visible_factions=BLUE)
    msgs = {c["message"] for c in feed["chats"]}
    assert msgs == {"藍軍通聯"}
    # 全見（sysadmin/開關關）：兩則都在
    feed_all = chat_service.build_chat_feed(None, visible_factions=None)
    assert len(feed_all["chats"]) == 2


def test_geochat_unclassified_sender_fail_closed():
    """發話端未分類 → faction NULL → 藍方看不到（fail-closed）。"""
    asyncio.run(chat_service.ingest_chat(_geochat("GeoChat.ANDROID-UNKNOWN.All Chat Rooms.x1", "未分類通聯")))
    assert chat_service.build_chat_feed(None, visible_factions=BLUE)["chats"] == []
    assert len(chat_service.build_chat_feed(None, visible_factions=None)["chats"]) == 1


def test_geochat_ics_self_origin_classified_blue():
    """#216 review：ICS 自身出向 GeoChat（sender=ICS_SELF_UID）經 server 回送 → 歸 blue，
    不因不在 client_faction 名冊而 fail-closed 隱藏（否則發話的 commander 自己都看不到）。"""
    from services.tak_downlink import ICS_SELF_UID

    asyncio.run(chat_service.ingest_chat(_geochat(f"GeoChat.{ICS_SELF_UID}.All Chat Rooms.s1", "指揮部廣播")))
    # 藍方視角看得到 ICS 自己的出向訊息（非被當未分類擋掉）
    feed = chat_service.build_chat_feed(None, visible_factions=BLUE)
    assert {c["message"] for c in feed["chats"]} == {"指揮部廣播"}
