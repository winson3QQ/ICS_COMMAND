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


def _ingest(uid, time=None):
    # live 點時間預設用「現在」（須 ≥ 演習活化時間，才落在活躍窗內；過去時間=演習開始前=待命）。
    ts = time or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    ev = CoTEventIn(
        uid=uid,
        type="a-f-G-U-C",
        time=ts,
        start=ts,
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


def test_member_before_window_null(_no_ws):
    """活躍窗 rejection arm：rostered 成員但 ts 在演習活化「之前」（過去時間）→ NULL（中途加入從加入起算）。"""
    ex = _active_exercise()
    client_identity_repo.upsert_many({"DEV1": "cn1"})
    exercise_roster_repo.upsert_member(ex, "cn1", None, "admin")
    assert _ingest("DEV1", time="2000-01-01T00:00:00Z")["exercise_id"] is None  # 開場前 → 不在窗 → 待命


def test_member_noncanonical_ts_scoped(_no_ws):
    """review 修：REST push 送非典範時間戳（毫秒）。接縫正規化後須與字典序活躍窗對齊 → 不被誤判落 NULL。"""
    ex = _active_exercise()
    client_identity_repo.upsert_many({"DEV1": "cn1"})
    exercise_roster_repo.upsert_member(ex, "cn1", None, "admin")
    now_ms = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")  # 帶毫秒（'.' < 'Z' 會字典序失準）
    row = _ingest("DEV1", time=now_ms)
    assert row["exercise_id"] == ex  # 正規化掉毫秒 → 落在窗 → 歸場（修前會錯落 NULL）
    assert row["time"].endswith("Z") and "." not in row["time"]  # 入庫已秒精度正規化


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


def test_archive_releases_entity_and_preserves_aar(_no_ws):
    """bug 2 修：演習結束（archive）→ 該場 entity 重 stamp 回 NULL（回待命視圖顯示，不再從地圖消失），
    且軌跡仍掛該場 exercise_id（denormalize）→ AAR 回放不破。"""
    from repositories import exercise_repo

    ex = _active_exercise()
    client_identity_repo.upsert_many({"DEV1": "cn1"})
    exercise_roster_repo.upsert_member(ex, "cn1", None, "admin")
    assert _ingest("DEV1")["exercise_id"] == ex  # 在場（同時寫了一筆 exercise_id=ex 的軌跡）
    exercise_repo.update_exercise_status(ex, "archived", "admin")  # 結束 → 無 active 場
    assert asyncio.run(cop_service.restamp_all_tak_entities()) == 1  # 模擬 archive 後 hook
    assert cop_entity_repo.get_cop_entity("DEV1")["exercise_id"] is None  # entity 回待命（bug 2 修）
    tracks = cop_entity_repo.list_tracks_by_exercise(ex)  # 軌跡查該場
    assert any(t["uid"] == "DEV1" for t in tracks)  # 仍查得到（AAR 不破，軌跡凍結 exercise_id）


def test_restamp_all_pulls_in_window_rostered(_no_ws):
    """restamp_all_tak_entities（activate hook 用）：roster 內、ts 在窗 的 live entity 被全域重 stamp 歸場。"""
    ex = _active_exercise()
    client_identity_repo.upsert_many({"DEV1": "cn1"})
    assert _ingest("DEV1")["exercise_id"] is None  # 連線在窗、但還沒 roster → NULL
    exercise_roster_repo.upsert_member(ex, "cn1", None, "admin")  # 後勾
    assert asyncio.run(cop_service.restamp_all_tak_entities()) == 1
    assert cop_entity_repo.get_cop_entity("DEV1")["exercise_id"] == ex  # 全域重 stamp 歸場
