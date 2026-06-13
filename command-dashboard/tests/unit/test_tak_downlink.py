"""P2-13(A)（#176）— tak_downlink.build_command_cot 純建構 unit 測試。

純字串建構 + XML 解析驗證，不碰網路 / DB。送出（send_cot）走真連線，於 API 測試 mock。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from xml.etree import ElementTree as ET

import pytest

from core import config
from services import tak_downlink
from services.tak_downlink import build_command_cot

_NOW = datetime(2026, 6, 9, 5, 0, 0, tzinfo=UTC)


def _parse(cot: str):
    # 去掉 XML 宣告後給 ET（ElementTree 不吃 standalone 宣告前綴混入時的容錯，直接 fromstring 可）
    return ET.fromstring(cot[cot.index("<event") :])


def test_build_basic_structure():
    cot = build_command_cot(
        uid="ICS-CMD-abc", type_="a-f-G", lat=25.03, lon=121.56, callsign="CMD-1", remarks="推進", now=_NOW
    )
    e = _parse(cot)
    assert e.tag == "event"
    assert e.get("uid") == "ICS-CMD-abc"
    assert e.get("type") == "a-f-G"
    assert e.get("version") == "2.0"
    pt = e.find("point")
    assert float(pt.get("lat")) == 25.03 and float(pt.get("lon")) == 121.56
    assert e.find("detail/contact").get("callsign") == "CMD-1"
    assert e.find("detail/remarks").text == "推進"
    # 持久訊號
    assert e.find("detail/archive") is not None


def test_stale_computed_from_minutes():
    cot = build_command_cot(uid="U", type_="a-f-G", lat=0, lon=0, stale_minutes=30, now=_NOW)
    e = _parse(cot)
    assert e.get("time") == "2026-06-09T05:00:00.000Z"
    assert e.get("stale") == "2026-06-09T05:30:00.000Z"  # +30 分鐘


def test_optional_fields_omitted():
    # 無 callsign / remarks → 不產對應 detail 子節點（仍有 archive）
    cot = build_command_cot(uid="U", type_="a-f-G", lat=0, lon=0, now=_NOW)
    e = _parse(cot)
    assert e.find("detail/contact") is None
    assert e.find("detail/remarks") is None
    assert e.find("detail/archive") is not None


def test_xml_injection_escaped():
    # remarks / callsign 含 XML metachar → 必須被 escape，產出仍是合法單一 event（不被撐破）
    cot = build_command_cot(
        uid="U",
        type_="a-f-G",
        lat=0,
        lon=0,
        callsign='ev"il',
        remarks="<script>&</bad>",
        now=_NOW,
    )
    e = _parse(cot)  # 能 parse = 沒被注入撐破結構
    assert e.find("detail/contact").get("callsign") == 'ev"il'
    assert e.find("detail/remarks").text == "<script>&</bad>"


class _FakeWriter:
    def __init__(self):
        self.written = b""
        self.drained = False
        self.closed = False

    def write(self, b):
        self.written += b

    async def drain(self):
        self.drained = True

    def close(self):
        self.closed = True


def test_send_cot_writes_drains_closes(monkeypatch):
    """send_cot 快樂路徑（mock transport，不碰網路）：write → drain → close，且 log 不爆。

    INFO 啟用下跑 —— 守住「stdlib logger + structlog kwargs 在 INFO 時 TypeError」這類回歸
    （send_cot 成功時的 log.info 必須能執行）。async 以 asyncio.run 驅動（對齊本 repo 慣例）。
    """
    logging.getLogger().setLevel(logging.INFO)
    monkeypatch.setattr(config, "TAK_COT_URL", "tls://localhost:8089")
    monkeypatch.setattr(config, "TAK_CLIENT_CERT", "/tmp/c.pem")
    monkeypatch.setattr(config, "TAK_CLIENT_KEY", "/tmp/k.pem")
    monkeypatch.setattr(config, "TAK_CAFILE", None)
    monkeypatch.setattr(config, "TAK_ALLOW_INSECURE_TLS", True)

    fw = _FakeWriter()

    async def _fake_factory(cfg):
        return (object(), fw)

    import pytak  # noqa: PLC0415 — 與 send_cot 的 lazy import 對齊

    monkeypatch.setattr(pytak, "protocol_factory", _fake_factory)

    asyncio.run(tak_downlink.send_cot("<event uid='X'/>"))
    assert fw.written == b"<event uid='X'/>"
    assert fw.drained and fw.closed


def test_send_cot_fail_closed_when_unconfigured(monkeypatch):
    """未配置 TAK（無 COT_URL）→ raise（fail-closed，不靜默）。"""
    monkeypatch.setattr(config, "TAK_COT_URL", "")
    with pytest.raises(RuntimeError):
        asyncio.run(tak_downlink.send_cot("<event/>"))


# ── entity_to_cot adapter（P2-30 part 2 / #180）：cop_entity → CoT，點/幾何分流 ──
def _parse_event(cot):
    return ET.fromstring(cot[cot.index("<event") :])


def test_entity_to_cot_point():
    ent = {"uid": "M-1", "type": "a-h-G", "lat": 25.0, "lon": 121.0, "callsign": "敵情", "attributes": {}}
    e = _parse_event(tak_downlink.entity_to_cot(ent, now=_NOW))
    assert e.get("uid") == "M-1" and e.get("type") == "a-h-G"
    assert e.find("detail/shape") is None  # 點，無 shape
    pt = e.find("point")
    assert float(pt.get("lat")) == 25.0 and float(pt.get("lon")) == 121.0
    assert e.find("detail/contact").get("callsign") == "敵情"


def test_entity_to_cot_tags_source_ics():
    """P2-30 part 3：ICS→TAK 外送標記 remarks 前綴 `source: ICS`（現場端區分指揮部送出的標記）。
    使用者原註記接在 tag 之後；無註記時 remarks=純 `source: ICS`。entity.remarks 本身不被汙染。"""
    with_note = {"uid": "M-2", "type": "a-h-G", "lat": 25.0, "lon": 121.0, "remarks": "兩名可疑人士", "attributes": {}}
    r = _parse_event(tak_downlink.entity_to_cot(with_note, now=_NOW)).find("detail/remarks").text
    assert r == "source: ICS\n兩名可疑人士"
    no_note = {"uid": "M-3", "type": "a-h-G", "lat": 25.0, "lon": 121.0, "attributes": {}}
    assert _parse_event(tak_downlink.entity_to_cot(no_note, now=_NOW)).find("detail/remarks").text == "source: ICS"
    assert with_note.get("remarks") == "兩名可疑人士"  # 原 entity 未被改


def test_entity_to_cot_route_geometry():
    ent = {
        "uid": "R-1",
        "type": "b-m-r",
        "lat": 0,
        "lon": 0,
        "attributes": {"kind": "route", "vertices": [[25.0, 121.0], [25.1, 121.1]]},
    }
    e = _parse_event(tak_downlink.entity_to_cot(ent, now=_NOW))
    pl = e.find("detail/shape/polyline")
    assert pl is not None and pl.get("closed") == "false"
    assert len(pl.findall("vertex")) == 2


def test_entity_to_cot_polygon_closed():
    ent = {
        "uid": "Z-1",
        "type": "u-d-f",
        "lat": 0,
        "lon": 0,
        "attributes": {"kind": "polygon", "vertices": [[25.0, 121.0], [25.1, 121.0], [25.1, 121.1]]},
    }
    e = _parse_event(tak_downlink.entity_to_cot(ent, now=_NOW))
    assert e.find("detail/shape/polyline").get("closed") == "true"
