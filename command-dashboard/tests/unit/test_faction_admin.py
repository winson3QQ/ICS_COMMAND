# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
tests/unit/test_faction_admin.py — #343 PR-5 + #344：admin 分類 API 層（faction_service）

#344 改綁 **cert CN（穩定）** 非 uid：
- list_clients：來源 = subscriptions/all（在線）∩ tak_device_certs（發證），去重成 per-CN，順帶寫 client_identity。
- classify：client_key=CN → upsert（CN 鍵）→ 經 client_identity 解 CN→uids 重解析名下 auto entity → resync。
- ingest faction 解析：producer uid 經 client_identity 翻 CN 再查（裝置換 uid 不丟分類）。
- override_entity：手動點單一 entity（manual）→ 後續重解析不覆寫。
"""

import asyncio
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from repositories import (
    client_faction_repo,
    client_identity_repo,
    cop_entity_repo,
    exercise_roster_repo,
    tak_device_cert_repo,
)
from schemas.tak import CoTEventIn
from services import cop_service, exercise_service, faction_service, tak_group_sync


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


def _identity(uid, cn):
    """登記 uid→cert CN（模擬 subscriptions 寫入 client_identity）。"""
    client_identity_repo.upsert_many({uid: cn})


def _ingest_marker(uid, creator_uid, callsign="敵-A", *, time="2026-06-22T01:00:00Z"):
    """ingest 一個 tak 標記（creator=creator_uid=producer）→ entity faction 依當下分類解析。

    time 預設固定（多數測試不在意場次窗）；要落在「現在 active 窗」內的測試（#267 乙）須傳現在時間。
    """
    ev = CoTEventIn(
        uid=uid,
        type="a-h-G",
        time=time,
        start=time,
        stale="2099-01-01T00:00:00Z",
        how="h-g-i-g-o",
        lat=24.1,
        lon=120.6,
        callsign=callsign,
        detail={"creator": {"uid": creator_uid, "callsign": callsign}},
    )
    return asyncio.run(cop_service.ingest_cot_event(ev))


# ── classify：CN 鍵 + 經 client_identity 重解析 ──────────────────────────────


def test_classify_reresolves_existing_entities(_no_ws):
    _identity("DEV-X", "CN-X")  # uid→CN 已知（裝置在線過、面板載入寫入）
    _ingest_marker("MK-1", "DEV-X")
    _ingest_marker("MK-2", "DEV-X")
    assert cop_entity_repo.get_cop_entity("MK-1")["faction"] is None  # classify 前未分類

    res = asyncio.run(faction_service.classify(None, "CN-X", "red", "CN-X", "admin"))  # 綁 CN
    assert res["reresolved"] == 2
    assert cop_entity_repo.get_cop_entity("MK-1")["faction"] == "red"
    assert cop_entity_repo.get_cop_entity("MK-2")["faction"] == "red"
    assert "resync" in _no_ws


def test_ingest_inherits_faction_via_uid_to_cn(_no_ws):
    """#344：classify CN 後，新進 CoT（producer uid 經 client_identity 翻 CN）即繼承 faction。"""
    _identity("DEV-I", "CN-I")
    asyncio.run(faction_service.classify(None, "CN-I", "blue", None, "admin"))
    _ingest_marker("MK-NEW", "DEV-I")  # classify 後才 ingest
    assert cop_entity_repo.get_cop_entity("MK-NEW")["faction"] == "blue"


def test_classification_survives_uid_change(_no_ws):
    """#344 核心：同一 cert CN 的裝置換 uid（重裝/重 enroll）仍繼承既有分類。"""
    _identity("UID-OLD", "CN-DEV")
    asyncio.run(faction_service.classify(None, "CN-DEV", "blue", None, "admin"))
    _ingest_marker("MK-OLD", "UID-OLD")
    assert cop_entity_repo.get_cop_entity("MK-OLD")["faction"] == "blue"
    # 裝置換新 uid（同 CN，面板載入寫入新對照）→ 新 CoT 仍藍，分類沒丟
    _identity("UID-NEW", "CN-DEV")
    _ingest_marker("MK-NEW", "UID-NEW")
    assert cop_entity_repo.get_cop_entity("MK-NEW")["faction"] == "blue"


def test_unknown_uid_fail_closed(_no_ws):
    """uid 無 client_identity 對照 → fail-closed（None）；不退回用 uid/CoT 字串當鍵。"""
    asyncio.run(faction_service.classify(None, "CN-Z", "blue", None, "admin"))
    _ingest_marker("MK-U", "UID-UNKNOWN")  # 無對照
    assert cop_entity_repo.get_cop_entity("MK-U")["faction"] is None


def test_forged_creator_cn_does_not_inherit_faction(_no_ws):
    """#344 security（security-review MED）：攻擊者把 CoT creator.uid 偽造成已知藍方 CN（callsign 低熵
    可枚舉）→ 不得命中 CN-keyed 分類偽裝成藍。uid 無 client_identity 對照即 fail-closed（不退回用 CoT
    自報字串當 CN 鍵）。"""
    client_faction_repo.upsert_faction(None, "blue-01", "blue", "blue-01", "admin")  # 正常把 CN blue-01 分藍
    # 攻擊者（紅/未分類、無 client_identity 對照）發 marker，creator.uid 偽造成 "blue-01"
    _ingest_marker("MK-FORGE", "blue-01")
    assert cop_entity_repo.get_cop_entity("MK-FORGE")["faction"] is None  # 不繼承藍 → 無洩漏


def test_classify_validates_exercise_exists(_no_ws):
    """D：指定不存在的 exercise_id → 404（不留孤兒分類）。"""
    with pytest.raises(HTTPException) as ei:
        asyncio.run(faction_service.classify(99999, "CN-X", "blue", None, "admin"))
    assert ei.value.status_code == 404


def test_classify_real_exercise_ok(_no_ws):
    ex = exercise_service.create({"name": "drill", "type": "ttx"})
    exercise_service.set_active(ex["id"], "admin")
    _identity("DEV-Y", "CN-Y")
    exercise_roster_repo.upsert_member(ex["id"], "CN-Y", None, "admin")  # #267(乙)：須在 roster 才綁場
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")  # 須落在 active 窗（活化時間=現在）
    _ingest_marker("MK-EX", "DEV-Y", time=now)  # ingest 綁 active 場
    res = asyncio.run(faction_service.classify(ex["id"], "CN-Y", "blue", None, "admin"))
    assert res["reresolved"] == 1
    assert cop_entity_repo.get_cop_entity("MK-EX")["faction"] == "blue"


def test_classify_bumps_version_clock(_no_ws):
    """#358-2：faction 為前端顯示軸 → 重分類（auto）須 bump version_clock（前端 LWW 才套用新 faction）。"""
    _identity("DEV-VC", "CN-VC")
    _ingest_marker("MK-VC", "DEV-VC")
    v0 = cop_entity_repo.get_cop_entity("MK-VC")["version_clock"]
    asyncio.run(faction_service.classify(None, "CN-VC", "blue", None, "admin"))
    ent = cop_entity_repo.get_cop_entity("MK-VC")
    assert ent["faction"] == "blue"
    assert ent["version_clock"] > v0


# ── list_clients：online ∩ issued，CN 鍵 ────────────────────────────────────


def _mock_subs(monkeypatch, uid2cn):
    async def _f():
        return dict(uid2cn)

    monkeypatch.setattr(tak_group_sync, "online_uid_to_username", _f)


def _mock_issued(monkeypatch, callsigns):
    monkeypatch.setattr(
        tak_device_cert_repo,
        "list_device_certs",
        lambda: [{"callsign": c, "status": "active"} for c in callsigns],
    )


def test_list_clients_online_and_issued_only(_no_ws, monkeypatch):
    _mock_subs(monkeypatch, {"u1": "alpha", "u2": "bravo", "u3": "ghost"})
    _mock_issued(monkeypatch, ["alpha", "bravo"])  # ghost 在線但未發證 → 排除
    client_faction_repo.upsert_faction(None, "alpha", "blue", "alpha", "admin")
    clients = {c["client_key"]: c for c in asyncio.run(faction_service.list_clients(None))}
    assert set(clients) == {"alpha", "bravo"}
    assert clients["alpha"]["faction"] == "blue" and clients["alpha"]["classified"] is True
    assert clients["bravo"]["faction"] is None and clients["bravo"]["classified"] is False
    assert clients["alpha"]["online"] is True


def test_list_clients_shows_live_callsign_keyed_on_cn(_no_ws, monkeypatch):
    """#344：顯示 live in-app callsign（角色，使用者可改），但 client_key 綁 cert CN（穩定）。

    指揮認的是角色名（如「紅軍-1」），分類依據是憑證 CN（如 cert-cn-01）——改 callsign 不丟分類。
    """
    # 裝置 self-SA（uid==裝置uid）帶 in-app callsign「紅軍-1」
    ev = CoTEventIn(
        uid="DEVUID",
        type="a-f-G-U-C",
        time="2026-06-22T00:00:00Z",
        start="2026-06-22T00:00:00Z",
        stale="2099-01-01T00:00:00Z",
        how="m-g",
        lat=24.0,
        lon=120.5,
        callsign="紅軍-1",
    )
    asyncio.run(cop_service.ingest_cot_event(ev))
    _mock_subs(monkeypatch, {"DEVUID": "cert-cn-01"})  # subscriptions：uid→CN
    _mock_issued(monkeypatch, ["cert-cn-01"])
    clients = asyncio.run(faction_service.list_clients(None))
    assert len(clients) == 1
    c = clients[0]
    assert c["client_key"] == "cert-cn-01" and c["cn"] == "cert-cn-01"  # 鍵/身分 = CN
    assert c["callsign"] == "紅軍-1"  # 顯示 = live 角色名


def test_list_clients_dedups_by_cn(_no_ws, monkeypatch):
    """同 CN 多 uid（換過 uid 都在線）→ 去重成一筆。"""
    _mock_subs(monkeypatch, {"uidA": "same", "uidB": "same"})
    _mock_issued(monkeypatch, ["same"])
    clients = asyncio.run(faction_service.list_clients(None))
    assert [c["client_key"] for c in clients] == ["same"]


def test_list_clients_populates_identity(_no_ws, monkeypatch):
    """list_clients 順帶把 {uid:CN} 寫入 client_identity（供 ingest 翻譯）。"""
    _mock_subs(monkeypatch, {"uidP": "papa"})
    _mock_issued(monkeypatch, ["papa"])
    asyncio.run(faction_service.list_clients(None))
    assert client_identity_repo.get_username("uidP") == "papa"


def test_list_clients_empty_when_tak_offline(_no_ws, monkeypatch):
    _mock_subs(monkeypatch, {})  # TAK 離線/未配置 → 無在線視圖
    _mock_issued(monkeypatch, ["alpha"])
    assert asyncio.run(faction_service.list_clients(None)) == []


# ── override：manual 不被重解析覆寫 ─────────────────────────────────────────


def test_override_entity_not_clobbered_by_reresolve(_no_ws):
    """手動 override（manual）後，classify 該 producer 不覆寫 manual entity。"""
    _identity("DEV-Z", "CN-OV")
    _ingest_marker("MK-M", "DEV-Z")
    asyncio.run(faction_service.override_entity("MK-M", "neutral", "admin"))
    row = cop_entity_repo.get_cop_entity("MK-M")
    assert row["faction"] == "neutral" and row["faction_source"] == "manual"

    res = asyncio.run(faction_service.classify(None, "CN-OV", "red", None, "admin"))
    assert res["reresolved"] == 0  # MK-M 是 manual → 不在重解析範圍
    assert cop_entity_repo.get_cop_entity("MK-M")["faction"] == "neutral"


def test_override_entity_missing_404(_no_ws):
    with pytest.raises(HTTPException) as ei:
        asyncio.run(faction_service.override_entity("NOPE", "blue", "admin"))
    assert ei.value.status_code == 404


def test_override_entity_bumps_version_clock(_no_ws):
    """manual override 同樣 bump version_clock（前端顯示同理）。"""
    _ingest_marker("MK-VM", "DEV-VM")
    v0 = cop_entity_repo.get_cop_entity("MK-VM")["version_clock"]
    asyncio.run(faction_service.override_entity("MK-VM", "red", "admin"))
    ent = cop_entity_repo.get_cop_entity("MK-VM")
    assert ent["faction"] == "red" and ent["version_clock"] > v0
