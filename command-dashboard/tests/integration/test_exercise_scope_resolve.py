# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""tests/integration/test_exercise_scope_resolve.py — #267 Slice 3：ingest scope 解析（roster × 活躍窗）+ 重 stamp。

entity 屬某場 ⟺ 唯一 active 場 × producer CN 在 roster × ts 在活躍窗。純 (乙)：空 roster = 沒人（不 auto-capture），
「忘了勾」靠 UI 解（Slice 4）。roster 變動 → 重 stamp 該 CN live entity（歸位/離場）。軌跡逐點繼承（免重算）。
"""

import asyncio
from datetime import UTC, datetime

import pytest

from repositories import client_identity_repo, cop_entity_repo, exercise_roster_repo
from schemas.tak import CoTEventIn
from services import cop_service, exercise_service

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _db(tmp_db):
    yield


@pytest.fixture
def _no_ws(monkeypatch):
    async def _b(*a, **k):
        pass

    monkeypatch.setattr(cop_service.cop_hub, "broadcast", _b)
    monkeypatch.setattr(cop_service.cop_hub, "broadcast_all", _b)


def _active_exercise():
    ex = exercise_service.create({"name": "drill", "type": "ttx"})
    exercise_service.set_active(ex["id"], "admin")
    return ex["id"]


def _ingest(uid):
    # live 點時間用「現在」（須 ≥ 演習活化時間，才落在活躍窗內；過去時間=演習開始前=待命）。
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    ev = CoTEventIn(
        uid=uid,
        type="a-f-G-U-C",
        time=now,
        start=now,
        stale="2099-01-01T00:00:00Z",
        how="m-g",
        lat=24.0,
        lon=120.5,
        callsign="dev",
    )
    return asyncio.run(cop_service.ingest_cot_event(ev))


def test_no_active_exercise_null(_no_ws):
    client_identity_repo.upsert_many({"DEV1": "cn1"})
    assert _ingest("DEV1")["exercise_id"] is None  # 無 active 場 → 待命池


def test_empty_roster_scopes_nobody(_no_ws):
    """純 (乙)：空 roster = 沒人（不 auto-capture）。「忘了勾」靠 UI 解（Slice 4），不靠程式 fallback。"""
    _active_exercise()  # roster 空
    client_identity_repo.upsert_many({"DEV1": "cn1"})
    assert _ingest("DEV1")["exercise_id"] is None  # 空 roster → 待命池（沒人在場）


def test_roster_member_scoped(_no_ws):
    ex = _active_exercise()
    client_identity_repo.upsert_many({"DEV1": "cn1"})
    exercise_roster_repo.upsert_member(ex, "cn1", None, "admin")  # roster 非空 + cn1 在
    assert _ingest("DEV1")["exercise_id"] == ex


def test_non_roster_member_null(_no_ws):
    ex = _active_exercise()
    client_identity_repo.upsert_many({"DEV1": "cn1", "DEV2": "cn2"})
    exercise_roster_repo.upsert_member(ex, "cn1", None, "admin")  # roster 非空（cn1）但 cn2 不在
    assert _ingest("DEV2")["exercise_id"] is None  # 不在 roster → 待命池


def test_restamp_on_roster_add(_no_ws):
    ex = _active_exercise()
    client_identity_repo.upsert_many({"DEV1": "cn1"})
    assert _ingest("DEV1")["exercise_id"] is None  # cn1 待命連線、不在 roster → NULL
    exercise_roster_repo.upsert_member(ex, "cn1", None, "admin")  # 勾進 roster
    assert asyncio.run(cop_service.restamp_exercise_for_cns(["cn1"])) == 1  # 重 stamp
    assert cop_entity_repo.get_cop_entity("DEV1")["exercise_id"] == ex  # live 點歸位


def test_restamp_on_roster_remove(_no_ws):
    ex = _active_exercise()
    client_identity_repo.upsert_many({"DEV1": "cn1"})
    exercise_roster_repo.upsert_member(ex, "cn1", None, "admin")
    assert _ingest("DEV1")["exercise_id"] == ex  # 在 roster → ex
    exercise_roster_repo.remove_member(ex, "cn1", "admin")  # 移除（roster 變空）
    assert asyncio.run(cop_service.restamp_exercise_for_cns(["cn1"])) == 1
    assert cop_entity_repo.get_cop_entity("DEV1")["exercise_id"] is None  # 離場 → 待命（純乙：空 roster=沒人）
