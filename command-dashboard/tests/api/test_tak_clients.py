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


def _ingest_tak(uid, callsign, *, stale="2099-01-01T00:00:00Z"):
    """經 TAK 串流接縫塞一個 source='tak' 單位（有 callsign）。stale 可帶過去值造離線。"""
    ev = CoTEventIn(
        uid=uid,
        type="a-f-G-U-C",
        time="2026-07-08T00:00:00Z",
        start="2026-07-08T00:00:00Z",
        stale=stale,
        how="m-g",
        lat=24.8,
        lon=121.0,
        callsign=callsign,
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


def test_clients_dedup_and_sorted(client, auth):
    _ingest_tak("UID-B", "bravo")
    _ingest_tak("UID-A", "alpha")
    _ingest_tak("UID-A", "alpha")  # 同 uid 重送 → 去重
    clients = client.get("/api/tak/clients", headers=auth).json()["clients"]
    names = [c["callsign"] for c in clients]
    assert names == sorted(names, key=str.lower)  # callsign 排序
    assert len([c for c in clients if c["uid"] == "UID-A"]) == 1  # uid 去重
