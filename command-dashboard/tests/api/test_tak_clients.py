# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""api/test_tak_clients.py — GET /api/tak/clients 收件人清單（#509-P3 下行推照片挑收件人）。

驗「照隊伍修」：收件人來源＝COP `cop_entities`（source='tak'），與「隊伍」面板同源——
  - 只列 source='tak' 的在線單位（非 tak 自建物件排除）
  - 排除 ICS 自身 presence beacon（uid=ICS-CMD）
  - 過 stale 的離線單位不列（include_stale=False）
  - callsign 排序、uid 去重
（原走 Marti /clientEndPoints 漏憑證直連現場 client 的 bug 之修正回歸測試。）
"""

import asyncio

import pytest

from schemas.tak import CoTEventIn
from services import cop_service

pytestmark = pytest.mark.api

# client / auth（sysadmin）fixtures 在 tests/api/conftest.py 共用


def _ingest_tak(uid, callsign, *, type_="a-f-G-U-C", team_color=None, stale="2099-01-01T00:00:00Z"):
    """經 TAK 串流接縫塞一個 source='tak' entity。type_/team_color 可造裝置 vs 放置標記。
    team_color 走 CoT `<__group name>`（ingest 由 detail 提取）；stale 過去值造離線。
    裝置端點＝有 team_color 或自報 a-f；標記＝a-n/a-u… 且無 team_color。"""
    ev = CoTEventIn(
        uid=uid,
        type=type_,
        time="2026-07-08T00:00:00Z",
        start="2026-07-08T00:00:00Z",
        stale=stale,
        how="m-g",
        lat=24.8,
        lon=121.0,
        callsign=callsign,
        detail={"__group": {"name": team_color}} if team_color else {},
    )
    asyncio.run(cop_service.ingest_cot_event(ev))


def test_clients_sourced_from_cop_tak_units(client, auth):
    _ingest_tak("UID-ITAK", "3QQ-iTAK")  # 現場 iTAK（憑證直連，clientEndPoints 撈不到、COP 有）
    _ingest_tak("ICS-CMD", "ICS-Command")  # ICS 自身 presence beacon → 應排除
    # 非 tak 自建物件（source='manual'）→ 應排除
    client.post(
        "/api/cop/entities", json={"type": "a-f-G-U-C", "lat": 24.8, "lon": 121.0, "callsign": "MANUAL-1"}, headers=auth
    )

    r = client.get("/api/tak/clients", headers=auth)
    assert r.status_code == 200, r.text
    callsigns = [c["callsign"] for c in r.json()["clients"]]
    assert "3QQ-iTAK" in callsigns  # 現場 client 選得到（原 bug：空白）
    assert "ICS-Command" not in callsigns  # 排除 ICS 自身
    assert "MANUAL-1" not in callsigns  # 非 tak 源排除


def test_clients_excludes_offline_stale(client, auth):
    _ingest_tak("UID-LIVE", "LIVE-1")
    _ingest_tak("UID-OLD", "OFFLINE-1", stale="2000-01-01T00:00:00Z")  # 過 stale = 離線
    callsigns = [c["callsign"] for c in client.get("/api/tak/clients", headers=auth).json()["clients"]]
    assert "LIVE-1" in callsigns
    assert "OFFLINE-1" not in callsigns  # 離線不列（同「隊伍」在線定義）


def test_clients_excludes_placed_markers(client, auth):
    """只列真裝置端點——放置標記（a-n/a-u，無 team_color）排除（DM 送不到，真機坐實）。"""
    _ingest_tak("UID-DEV-F", "DEV-FRIENDLY")  # a-f-G-U-C（自報友軍）→ 裝置
    _ingest_tak("UID-DEV-TC", "DEV-TEAM", type_="a-h-G", team_color="Cyan")  # 有 team_color → 裝置（即使 a-h）
    _ingest_tak("UID-MARK-N", "MARK-N", type_="a-n-G")  # 中立點位標記、無 team → 標記
    _ingest_tak("UID-MARK-U", "MARK-U", type_="a-u-G")  # 未知點位標記、無 team → 標記
    callsigns = [c["callsign"] for c in client.get("/api/tak/clients", headers=auth).json()["clients"]]
    assert "DEV-FRIENDLY" in callsigns
    assert "DEV-TEAM" in callsigns  # 有 team_color 即裝置
    assert "MARK-N" not in callsigns  # 標記排除
    assert "MARK-U" not in callsigns  # 標記排除


def test_clients_shape_carries_team_and_faction(client, auth):
    """回傳帶 team_color/faction 供前端分「隊伍/陣營」兩軸群組。"""
    _ingest_tak("UID-G", "GRP-DEV", team_color="Blue")
    row = next(c for c in client.get("/api/tak/clients", headers=auth).json()["clients"] if c["callsign"] == "GRP-DEV")
    assert row["team_color"] == "Blue"
    assert "faction" in row  # 未分類 → None，但欄位在
    assert row["uid"] == "UID-G"


def test_clients_dedup_and_sorted(client, auth):
    _ingest_tak("UID-B", "bravo")
    _ingest_tak("UID-A", "alpha")
    _ingest_tak("UID-A", "alpha")  # 同 uid 重送 → 去重
    clients = client.get("/api/tak/clients", headers=auth).json()["clients"]
    names = [c["callsign"] for c in clients]
    assert names == sorted(names, key=str.lower)  # callsign 排序
    assert len([c for c in clients if c["uid"] == "UID-A"]) == 1  # uid 去重
