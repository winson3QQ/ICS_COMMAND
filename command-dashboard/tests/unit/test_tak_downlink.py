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


# ── #214 出向忠實度：<__group> 隊伍色/角色 + 不偽造遙測 ──────────────────────


def test_group_emitted_with_team_color_and_role():
    cot = build_command_cot(uid="U", type_="a-f-G", lat=0, lon=0, team_color="Cyan", role="Team Member", now=_NOW)
    g = _parse(cot).find("detail/__group")
    assert g is not None
    assert g.get("name") == "Cyan"
    assert g.get("role") == "Team Member"


def test_group_name_only_when_no_role():
    g = _parse(build_command_cot(uid="U", type_="a-f-G", lat=0, lon=0, team_color="Blue", now=_NOW)).find(
        "detail/__group"
    )
    assert g is not None and g.get("name") == "Blue" and g.get("role") is None


def test_no_group_when_no_team_color():
    # ICS 自建標記無 team_color → 不偽造 <__group>（誠實）
    assert _parse(build_command_cot(uid="U", type_="a-f-G", lat=0, lon=0, now=_NOW)).find("detail/__group") is None


def test_role_without_team_color_emits_no_group():
    # role 無 group name 在 wire 無意義 → team_color 缺席則整個 <__group> 不出（含 role）
    cot = build_command_cot(uid="U", type_="a-f-G", lat=0, lon=0, role="HQ", now=_NOW)
    assert _parse(cot).find("detail/__group") is None


def test_blank_team_color_emits_no_group():
    # 純空白 team_color（truthy 但 strip 後為空）→ 不出空 name 的 <__group>
    cot = build_command_cot(uid="U", type_="a-f-G", lat=0, lon=0, team_color="   ", now=_NOW)
    assert _parse(cot).find("detail/__group") is None


def test_team_color_canonicalized_outbound():
    # #214 review：未正規化色名（_m015 回填可能留大小寫不一的 'dark blue'）出向標準化成 title-case，
    # 與入向 _extract_squad 同標準 → round-trip 不漂移（否則出向後再 ingest 會落入不同小隊桶）。
    from services.cop_service import _extract_squad

    g = _parse(build_command_cot(uid="U", type_="a-f-G", lat=0, lon=0, team_color="dark blue", now=_NOW)).find(
        "detail/__group"
    )
    assert g.get("name") == "Dark Blue"  # 出向已正規化（對齊 _extract_squad）
    team_color, _role, _battery = _extract_squad({"__group": dict(g.attrib)})
    assert team_color == "Dark Blue"  # 再 ingest 仍同值（穩定，不漂移）


def test_outbound_does_not_fabricate_device_telemetry():
    # #214 doctrine：ICS 非 GPS 裝置 → 出向絕不送裝置遙測（偽造會誤導現場）
    cot = build_command_cot(
        uid="U", type_="a-f-G", lat=0, lon=0, callsign="X", remarks="Y", team_color="Cyan", role="Team Member", now=_NOW
    )
    for forbidden in ("<takv", "<status", "<track", "<precisionlocation"):
        assert forbidden not in cot, f"出向不得偽造裝置遙測：{forbidden}"


def test_group_roundtrips_through_extract_squad():
    # 出向 <__group> 再被 ICS ingest（_extract_squad）→ 同 team_color/role，不掉欄位
    from services.cop_service import _extract_squad

    g = _parse(
        build_command_cot(uid="U", type_="a-f-G", lat=0, lon=0, team_color="Dark Blue", role="HQ", now=_NOW)
    ).find("detail/__group")
    team_color, role, _battery = _extract_squad({"__group": dict(g.attrib)})
    assert team_color == "Dark Blue" and role == "HQ"


def test_entity_to_cot_point_carries_team_color():
    entity = {
        "uid": "E1",
        "type": "a-f-G-U-C",
        "lat": 25.0,
        "lon": 121.5,
        "hae": 0.0,
        "callsign": "Blue-1",
        "team_color": "Blue",
        "role": "Team Member",
        "attributes": {},
    }
    g = _parse(tak_downlink.entity_to_cot(entity, now=_NOW)).find("detail/__group")
    assert g is not None and g.get("name") == "Blue" and g.get("role") == "Team Member"


def test_entity_to_cot_ics_marker_without_team_color_emits_no_group():
    entity = {"uid": "E2", "type": "a-f-G", "lat": 25.0, "lon": 121.5, "hae": 0.0, "callsign": "CMD", "attributes": {}}
    assert _parse(tak_downlink.entity_to_cot(entity, now=_NOW)).find("detail/__group") is None


# ── #216 出向 GeoChat：build_geochat_cot（b-t-f 文字通聯，對稱入向 chat_service）─────────


def test_geochat_broadcast_structure():
    # 全體廣播：uid/chatgrp/__chat 三處 room_seg = "All Chat Rooms"；type b-t-f；發話者站台身分。
    cot = tak_downlink.build_geochat_cot(sender_callsign="CMD-1", message="全體注意", msg_id="m1", now=_NOW)
    e = _parse(cot)
    assert e.get("type") == "b-t-f"
    assert e.get("uid") == "GeoChat.ICS-CMD.All Chat Rooms.m1"
    chat = e.find("detail/__chat")
    assert chat.get("chatroom") == "All Chat Rooms" and chat.get("id") == "All Chat Rooms"
    assert chat.get("senderCallsign") == "CMD-1" and chat.get("messageId") == "m1"
    grp = e.find("detail/__chat/chatgrp")
    assert grp.get("uid0") == "ICS-CMD" and grp.get("uid1") == "All Chat Rooms"
    assert e.find("detail/link").get("uid") == "ICS-CMD"
    assert e.find("detail/remarks").text == "全體注意"
    # 通聯非持久態勢 → 不帶 <archive/>（與 marker/geometry 區別）
    assert e.find("detail/archive") is None


def test_geochat_dm_routes_to_recipient():
    # 點對點 DM：room_seg = 收件裝置 uid；chatroom 帶顯示呼號。
    cot = tak_downlink.build_geochat_cot(
        sender_callsign="CMD-1", message="單獨呼叫", msg_id="m2", chatroom="BRAVO", recipient_uid="ANDROID-9", now=_NOW
    )
    e = _parse(cot)
    assert e.get("uid") == "GeoChat.ICS-CMD.ANDROID-9.m2"
    chat = e.find("detail/__chat")
    assert chat.get("chatroom") == "BRAVO" and chat.get("id") == "ANDROID-9"
    assert e.find("detail/__chat/chatgrp").get("uid1") == "ANDROID-9"
    assert e.find("detail/remarks").get("to") == "ANDROID-9"


def test_geochat_named_room():
    cot = tak_downlink.build_geochat_cot(
        sender_callsign="CMD-1", message="隊伍頻道", msg_id="m3", chatroom="Blue Team", now=_NOW
    )
    e = _parse(cot)
    assert e.get("uid") == "GeoChat.ICS-CMD.Blue Team.m3"
    assert e.find("detail/__chat/chatgrp").get("uid1") == "Blue Team"


def test_geochat_escapes_xml_metachars():
    # 訊息含 XML metachar → escape，產出仍是合法單一 event（不被注入撐破）
    cot = tak_downlink.build_geochat_cot(sender_callsign='ev"il', message="<script>&</bad>", msg_id="m4", now=_NOW)
    e = _parse(cot)  # 能 parse = 結構未破
    assert e.find("detail/__chat").get("senderCallsign") == 'ev"il'
    assert e.find("detail/remarks").text == "<script>&</bad>"


def test_geochat_uid_roundtrips_through_client_key():
    # 出向 uid 再被 ICS ingest（_geochat_client_key）→ 解回站台 sender uid（ICS-CMD）。
    from services.chat_service import _geochat_client_key

    cot = tak_downlink.build_geochat_cot(sender_callsign="CMD-1", message="x", msg_id="m5", now=_NOW)
    assert _geochat_client_key(_parse(cot).get("uid")) == "ICS-CMD"


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
    # 出向改 ATAK 原生 <link> 序列（#211）：開放線 2 點、不補閉合、無填色
    assert len(e.findall("detail/link")) == 2
    assert e.find("detail/fillColor") is None
    assert e.find("detail/strokeColor") is not None


def test_entity_to_cot_polygon_closed():
    ent = {
        "uid": "Z-1",
        "type": "u-d-f",
        "lat": 0,
        "lon": 0,
        "attributes": {"kind": "polygon", "vertices": [[25.0, 121.0], [25.1, 121.0], [25.1, 121.1]]},
    }
    e = _parse_event(tak_downlink.entity_to_cot(ent, now=_NOW))
    # closed polygon：3 頂點 + 閉合 link（首尾相同）+ 填色（ATAK 原生格式，#211）
    assert len(e.findall("detail/link")) == 4
    assert e.find("detail/fillColor") is not None
