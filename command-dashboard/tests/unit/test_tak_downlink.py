# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
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


def test_geochat_dm_emits_marti_dest():
    # #216 dogfood 修：DM 補 <marti><dest callsign> 讓 server 只投遞給該呼號（chatroom=收件呼號）。
    cot = tak_downlink.build_geochat_cot(
        sender_callsign="CMD-1", message="私訊", msg_id="m6", chatroom="3QQ-itak", recipient_uid="ITAK-9", now=_NOW
    )
    dest = _parse(cot).find("detail/marti/dest")
    assert dest is not None and dest.get("callsign") == "3QQ-itak"


def test_geochat_broadcast_has_no_marti_dest():
    # 廣播不帶 dest → server 群發（帶 dest 反而只發給某呼號）。
    cot = tak_downlink.build_geochat_cot(sender_callsign="CMD-1", message="全體", msg_id="m7", now=_NOW)
    assert _parse(cot).find("detail/marti") is None


def test_geochat_named_room_has_no_marti_dest():
    # 命名聊天室（非 DM）不帶 dest → 走聊天室群發語意。
    cot = tak_downlink.build_geochat_cot(
        sender_callsign="CMD-1", message="隊伍", msg_id="m8", chatroom="Blue Team", now=_NOW
    )
    assert _parse(cot).find("detail/marti") is None


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


def test_node_cot_type_mapping():
    """#467 B：節點 node_type → per-type 2525 CoT type（五類互不重複 → 現場端可辨）；未列 → None。"""
    assert tak_downlink.node_cot_type("command") == "a-f-G-U-H"
    assert tak_downlink.node_cot_type("medical") == "a-f-G-U-U-S-M"
    types = [tak_downlink.node_cot_type(n) for n in ("command", "medical", "security", "forward", "shelter")]
    assert len(set(types)) == 5 and all(types)  # B 的重點：五類不同符號
    assert tak_downlink.node_cot_type("nonexistent") is None
    assert tak_downlink.node_cot_type(None) is None


def test_entity_to_cot_node_maps_per_type_symbol():
    """#467 B：kind='zone' 出向套 node_type 對應 2525 符號（存儲 type=通用 a-f-G-I，出向才換）。"""
    ent = {
        "uid": "N-1",
        "type": "a-f-G-I",
        "lat": 25.0,
        "lon": 121.0,
        "callsign": "指揮部",
        "attributes": {"kind": "zone", "node_type": "command"},
    }
    e = _parse_event(tak_downlink.entity_to_cot(ent, now=_NOW))
    assert e.get("type") == "a-f-G-U-H"  # 換成指揮所符（非存儲的 a-f-G-I）
    assert e.find("point") is not None and e.find("detail/shape") is None  # 點路徑
    assert e.find("detail/contact").get("callsign") == "指揮部"


def test_entity_to_cot_node_unknown_type_falls_back():
    """未列的 node_type → 保留 entity.type（fail-safe，不誤成別的符號）。"""
    ent = {
        "uid": "N-2",
        "type": "a-f-G-I",
        "lat": 25.0,
        "lon": 121.0,
        "attributes": {"kind": "zone", "node_type": "mystery"},
    }
    assert _parse_event(tak_downlink.entity_to_cot(ent, now=_NOW)).get("type") == "a-f-G-I"


# ── #507 Phase4：presence beacon（ICS 自報 SA，下行定址）──────────────────────────


def test_build_presence_cot_structure():
    cot = tak_downlink.build_presence_cot(callsign="ICS-Command", lat=24.8, lon=121.0, now=_NOW)
    e = _parse(cot)
    assert e.get("uid") == "ICS-CMD"  # 站台身分
    assert e.get("type") == "a-f-G-U-C"  # 友軍地面單位 SA
    assert e.get("how") == "m-g"  # machine-generated（誠實非 human-input）
    pt = e.find("point")
    assert float(pt.get("lat")) == 24.8 and float(pt.get("lon")) == 121.0
    c = e.find("detail/contact")
    assert c.get("callsign") == "ICS-Command"
    assert c.get("endpoint") == "*:-1:stcp"  # 經 server 連我（定向走 server 中介、非 P2P）
    assert e.find("detail/__group") is not None
    assert "source: ICS" in e.find("detail/remarks").text


def test_build_presence_cot_honesty_no_fake_telemetry():
    """#214 誠實原則：ICS 非 GPS 裝置，不送偽造遙測（takv/battery/track/precisionlocation）。"""
    cot = tak_downlink.build_presence_cot(callsign="ICS-Command", lat=0, lon=0, now=_NOW)
    e = _parse(cot)
    assert e.find("detail/takv") is None
    assert e.find("detail/status") is None
    assert e.find("detail/track") is None
    assert e.find("detail/precisionlocation") is None


def test_build_presence_cot_stale_from_seconds():
    cot = tak_downlink.build_presence_cot(callsign="X", lat=0, lon=0, stale_seconds=180, now=_NOW)
    e = _parse(cot)
    assert e.get("time") == "2026-06-09T05:00:00.000Z"
    assert e.get("stale") == "2026-06-09T05:03:00.000Z"  # +180s


def test_build_presence_cot_escapes_callsign():
    # 惡意 callsign 經 quoteattr 跳脫 → 仍能 parse（不破 XML）。
    cot = tak_downlink.build_presence_cot(callsign='"><evil', lat=0, lon=0, now=_NOW)
    assert _parse(cot).find("detail/contact").get("callsign") == '"><evil'


def test_presence_enabled_reflects_config(monkeypatch):
    monkeypatch.setattr(config, "TAK_PRESENCE_ENABLED", False)
    assert tak_downlink.presence_enabled() is False
    monkeypatch.setattr(config, "TAK_PRESENCE_ENABLED", True)
    assert tak_downlink.presence_enabled() is True


def test_presence_beacon_loop_sends_then_stops(monkeypatch):
    sent: list = []
    stop = asyncio.Event()

    async def fake_send(cot):
        sent.append(cot)
        stop.set()  # 送一次後停 → 下一輪頂端退出

    monkeypatch.setattr(tak_downlink, "send_cot", fake_send)
    monkeypatch.setattr(config, "TAK_PRESENCE_INTERVAL_S", 10)
    monkeypatch.setattr(config, "TAK_PRESENCE_CALLSIGN", "ICS-Command")
    monkeypatch.setattr(config, "TAK_PRESENCE_LAT", 24.8)
    monkeypatch.setattr(config, "TAK_PRESENCE_LON", 121.0)
    asyncio.run(tak_downlink.presence_beacon_loop(stop))
    assert len(sent) == 1
    assert "a-f-G-U-C" in sent[0] and "ICS-Command" in sent[0]


def test_presence_beacon_loop_best_effort_on_send_failure(monkeypatch):
    """單次送失敗只 log、迴圈不炸、不外拋（best-effort）。"""
    calls: list = []
    stop = asyncio.Event()

    async def failing_send(cot):
        calls.append(1)
        stop.set()  # 設停（下一輪頂端退出）
        raise RuntimeError("boom")  # 但本次拋錯

    monkeypatch.setattr(tak_downlink, "send_cot", failing_send)
    monkeypatch.setattr(config, "TAK_PRESENCE_INTERVAL_S", 10)
    asyncio.run(tak_downlink.presence_beacon_loop(stop))  # 不得外拋
    assert len(calls) == 1


# ── #509-P3 下行 fileshare 通告（build_fileshare_cot）─────────────────────────────────


def test_fileshare_broadcast_structure(monkeypatch):
    """廣播 b-f-t-r：純 <fileshare>、senderUrl 指裝置面對位址、無 marti dest/peerHosted/ackrequest。"""
    monkeypatch.setattr(config, "TAK_DEVICE_CONNECT_HOST", "10.13.13.1")
    cot = tak_downlink.build_fileshare_cot(
        file_hash="a" * 64, filename="scene.zip", size_bytes=1234, name="scene", lat=24.7, lon=121.0, now=_NOW
    )
    ev = _parse(cot)
    assert ev.get("type") == "b-f-t-r" and ev.get("how") == "h-e"
    fs = ev.find("detail/fileshare")
    assert fs.get("senderUrl") == "https://10.13.13.1:8443/Marti/sync/content?hash=" + "a" * 64
    assert fs.get("sha256") == "a" * 64 and fs.get("sizeInBytes") == "1234"
    assert fs.get("filename") == "scene.zip" and fs.get("name") == "scene"
    assert ev.find("detail/marti") is None  # 廣播無定址
    assert "peerHosted" not in fs.attrib and ev.find("detail/ackrequest") is None  # 對齊真機廣播


def test_fileshare_point_to_point_marti_dest(monkeypatch):
    """dest_callsigns 給定 → <marti><dest callsign/> 點對點。"""
    monkeypatch.setattr(config, "TAK_DEVICE_CONNECT_HOST", "10.13.13.1")
    cot = tak_downlink.build_fileshare_cot(
        file_hash="b" * 64, filename="s.zip", size_bytes=1, name="s", dest_callsigns=["3QQ-iTAK", "3QQ-atak"], now=_NOW
    )
    ev = _parse(cot)
    dests = [d.get("callsign") for d in ev.findall("detail/marti/dest")]
    assert dests == ["3QQ-iTAK", "3QQ-atak"]


def test_fileshare_escapes_xml_metachars(monkeypatch):
    """filename/name 走 quoteattr——XML 注入被中和。"""
    monkeypatch.setattr(config, "TAK_DEVICE_CONNECT_HOST", "10.13.13.1")
    cot = tak_downlink.build_fileshare_cot(
        file_hash="c" * 64, filename='x"><evil/>.zip', size_bytes=1, name="n", now=_NOW
    )
    ev = _parse(cot)  # 能被解析＝未破壞結構
    assert ev.find("detail/fileshare").get("filename") == 'x"><evil/>.zip'
    assert ev.find("detail/evil") is None
