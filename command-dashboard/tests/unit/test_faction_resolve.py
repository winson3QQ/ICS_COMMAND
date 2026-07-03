# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
tests/unit/test_faction_resolve.py — #343 PR-2：producer 歸屬鏈 + ingest faction 落地

鎖住的不變式（設計 SoT docs/design/red-blue-faction-isolation.md §2.2）：
- 歸屬鏈（全 by-uid）：creator.uid → link[relation=p-p].uid → fallback entity.uid
- 真機平台差異：ATAK 標記/繪圖帶 creator；iTAK 標記帶 link p-p；iTAK 繪圖兩者皆無 → fail-closed
- ingest 時依 client_faction 分類解 faction；未分類 / 解不到 → None（fail-closed）
- #344：分類綁 cert CN（非 uid）；ingest 經 client_identity 把 producer uid 翻 CN 再查（無對照→fail-closed，
  不退回用 CoT 自報字串當鍵——擋偽造 creator.uid=已知藍方 CN 的洩漏，security-review MED）
- faction 綁 entity 的 exercise_id（per-exercise 分類）
"""

import asyncio

import pytest

from repositories import client_faction_repo, client_identity_repo
from repositories.cop_entity_repo import get_cop_entity
from schemas.cop import CoPEntity
from schemas.tak import CoTEventIn
from services import cop_service, exercise_service


def _entity(uid: str = "UID-1", *, attributes: dict | None = None, exercise_id: int | None = None) -> CoPEntity:
    """最小 CoPEntity（_resolve_client_key 純函式測試用，不需 DB）。"""
    return CoPEntity(
        uid=uid,
        type="a-h-G",
        time="2026-06-22T00:00:00Z",
        start="2026-06-22T00:00:00Z",
        stale="2099-01-01T00:00:00Z",
        how="h-g-i-g-o",
        lat=24.1,
        lon=120.6,
        source="tak",
        exercise_id=exercise_id,
        attributes=attributes or {},
    )


# ── 1. _resolve_client_key 歸屬鏈（純函式，無 DB）─────────────────────────────


def test_self_sa_falls_back_to_uid():
    """單位自身（無 creator/link）→ client_key = entity.uid（uid 即裝置 self-SA）。"""
    assert cop_service._resolve_client_key(_entity("ANDROID-DEV")) == "ANDROID-DEV"


def test_creator_uid_wins():
    """ATAK 標記/繪圖：attributes.creator.uid = 產生裝置。"""
    e = _entity("marker-guid", attributes={"creator": {"uid": "ANDROID-DEV", "callsign": "CAP"}})
    assert cop_service._resolve_client_key(e) == "ANDROID-DEV"


def test_link_pp_uid():
    """iTAK 標記：無 creator，但 link[relation=p-p].uid = 產生裝置。"""
    e = _entity("marker-guid", attributes={"link": {"uid": "ITAK-DEV", "relation": "p-p"}})
    assert cop_service._resolve_client_key(e) == "ITAK-DEV"


def test_creator_takes_priority_over_link():
    """creator 與 link p-p 同在 → creator 優先（鏈順序）。"""
    e = _entity(
        "marker-guid",
        attributes={"creator": {"uid": "DEV-CREATOR"}, "link": {"uid": "DEV-LINK", "relation": "p-p"}},
    )
    assert cop_service._resolve_client_key(e) == "DEV-CREATOR"


def test_link_as_list_picks_pp():
    """link 多筆（list，如 route 多個 <link point>）→ 挑 relation=p-p 那筆。"""
    e = _entity(
        "route-guid",
        attributes={"link": [{"point": "24.1,120.6"}, {"uid": "DEV-PP", "relation": "p-p"}, {"point": "24.2,120.7"}]},
    )
    assert cop_service._resolve_client_key(e) == "DEV-PP"


def test_link_without_pp_relation_falls_back_to_uid():
    """link 存在但無 relation=p-p（如純幾何 <link point>）→ fallback uid（iTAK 繪圖 fail-closed 路徑）。"""
    e = _entity("itak-drawing-guid", attributes={"link": [{"point": "24.1,120.6"}, {"point": "24.2,120.7"}]})
    assert cop_service._resolve_client_key(e) == "itak-drawing-guid"


def test_creator_without_uid_falls_back():
    """creator dict 缺 uid → 不採，往下走 → fallback uid。"""
    e = _entity("guid-x", attributes={"creator": {"callsign": "no-uid"}})
    assert cop_service._resolve_client_key(e) == "guid-x"


# ── 2. _resolve_faction（查 client_faction 分類）───────────────────────────────


@pytest.fixture(autouse=True)
def _db(tmp_db):
    yield


def test_resolve_faction_classified_blue():
    # #344：分類綁 CN；ingest 經 client_identity 把 producer uid 翻 CN 再查
    client_identity_repo.upsert_many({"ANDROID-DEV": "CAP-CN"})
    client_faction_repo.upsert_faction(None, "CAP-CN", "blue", "CAP-CN", "admin")
    e = _entity("marker", attributes={"creator": {"uid": "ANDROID-DEV"}})
    assert cop_service._resolve_faction(e) == "blue"


def test_resolve_faction_unclassified_is_none():
    """未分類 client → None（fail-closed）。"""
    e = _entity("marker", attributes={"creator": {"uid": "UNKNOWN-DEV"}})
    assert cop_service._resolve_faction(e) is None


def test_resolve_faction_keys_on_active_exercise():
    """#473-A：faction 解析 key 在「當前 active 演習」（非 entity.exercise_id）——因 #472 cop 為跨場共享池。"""
    client_identity_repo.upsert_many({"DEV": "DEV-CN"})
    client_faction_repo.upsert_faction(None, "DEV-CN", "blue", None, "admin")  # 平時/全域分類
    e_pool = _entity("m1", attributes={"creator": {"uid": "DEV"}}, exercise_id=None)
    e_ex = _entity("m2", attributes={"creator": {"uid": "DEV"}}, exercise_id=999)
    # 平時（無 active 演習）→ 用 None 分類 → blue，**不論 entity.exercise_id**
    assert cop_service._resolve_faction(e_pool) == "blue"
    assert cop_service._resolve_faction(e_ex) == "blue"
    # 開一場並分類 neutral → active 演習中，解析改用**該場**分類（不論 entity.exercise_id）
    ex = exercise_service.create({"name": "x", "type": "ttx"})
    exercise_service.set_active(ex["id"], "admin")
    client_faction_repo.upsert_faction(ex["id"], "DEV-CN", "neutral", None, "admin")
    assert cop_service._resolve_faction(e_pool) == "neutral"
    assert cop_service._resolve_faction(e_ex) == "neutral"
    # active 場沒分類此 CN → None（fail-closed），即使平時有全域分類
    client_identity_repo.upsert_many({"DEV3": "DEV3-CN"})
    client_faction_repo.upsert_faction(None, "DEV3-CN", "blue", None, "admin")
    e3 = _entity("m3", attributes={"creator": {"uid": "DEV3"}}, exercise_id=None)
    assert cop_service._resolve_faction(e3) is None


# ── 3. ingest_cot_event 落地 faction（端到端 tak 路徑）──────────────────────────


def _ingest(**overrides):
    base = {
        "uid": "TAK-MK-1",
        "type": "a-h-G",
        "time": "2026-06-22T01:00:00Z",
        "start": "2026-06-22T01:00:00Z",
        "stale": "2099-01-01T00:00:00Z",
        "how": "h-g-i-g-o",
        "lat": 24.1,
        "lon": 120.6,
    }
    base.update(overrides)
    return asyncio.run(cop_service.ingest_cot_event(CoTEventIn(**base)))


def test_ingest_sets_faction_for_classified_producer():
    client_identity_repo.upsert_many({"ANDROID-DEV": "ENEMY-CN"})
    client_faction_repo.upsert_faction(None, "ENEMY-CN", "red", "敵-A", "admin")
    _ingest(uid="TAK-MK-1", detail={"creator": {"uid": "ANDROID-DEV"}})
    row = get_cop_entity("TAK-MK-1")
    assert row["faction"] == "red"
    assert row["faction_source"] == "auto"


def test_ingest_unclassified_producer_faction_none():
    _ingest(uid="TAK-MK-2", detail={"creator": {"uid": "NOPE"}})
    row = get_cop_entity("TAK-MK-2")
    assert row["faction"] is None
    assert row["faction_source"] == "auto"


def test_ingest_self_sa_uses_uid_as_client_key():
    """單位自身 PLI（無 creator/link）→ client_key=uid → 經 client_identity 翻 CN 後繼承分類。"""
    client_identity_repo.upsert_many({"ANDROID-SELF": "ALPHA-CN"})
    client_faction_repo.upsert_faction(None, "ALPHA-CN", "blue", "ALPHA-1", "admin")
    _ingest(uid="ANDROID-SELF", type="a-f-G-U-C", how="m-g")
    row = get_cop_entity("ANDROID-SELF")
    assert row["faction"] == "blue"
