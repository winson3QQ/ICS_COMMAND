# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
tak_downlink.py — P2-13(A) TAK 下行指令：建 CoT + 寫進 :8089 streaming → server 廣播現場端。

設計依據（issue #176 reality check / memory `tak-marti-authz-model`）：
- 官方 Marti **無 REST 廣播端點**（`injectors/cot/uid` 只注入 detail 非廣播）。
- 下行最簡且實測可行路 = **寫 CoT 到 :8089**（與 `tak_service.subscribe` 同一條雙向 TAK Protocol
  連線協定）→ server 廣播給**同 group 所有現場 ATAK**（活 iTAK 實測收到）。
- 不需 mission / owner-role / admin，只要 truststore-trusted cert（複用 :8089 訂閱那張）。

邊界：
- 本模組只「建 CoT + 送出」；RBAC（COMMAND_ROLES）+ 強制 audit 在 `routers/tak.py`。
- **live broadcast only**——無持久性/可靠刪除/reconnect 重播保證（那是 P2-14 #173 未解問題）。
- v0 CoT XML（活 server 預設可用，對齊 `build_subscribe_config` 的 TAK_PROTO=0）。
- fail-closed：TAK 未配置（無 COT_URL/cert）→ raise，不靜默吞。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from xml.sax.saxutils import escape, quoteattr

import structlog

from core import config
from services.tak_service import build_subscribe_config

# structlog（對齊 tak_service）：info(event, **kwargs) 形式。
# ★ 勿用 stdlib logging.getLogger —— 它的 .info(msg, foo=..) 在 INFO 啟用時會 TypeError。
log = structlog.get_logger()

_CONNECT_TIMEOUT_S = 10.0
_WRITE_TIMEOUT_S = 10.0


def build_command_cot(
    *,
    uid: str,
    type_: str,
    lat: float,
    lon: float,
    hae: float = 0.0,
    callsign: str | None = None,
    remarks: str | None = None,
    team_color: str | None = None,
    role: str | None = None,
    stale_minutes: int = 60,
    now: datetime | None = None,
) -> str:
    """組一個 v0 CoT XML 指令字串（server 端產 time/start/stale，不信 client 時鐘）。

    所有外來字串走 XML escape/quoteattr（縱深防護；上游 router 另有內容白名單）。
    含 `<archive/>`：要求 server 持久保留（過 stale 不丟）——對齊 #161 archive doctrine；
    即便 reconnect 重播在本 server 不可靠（P2-14 待解），archive 是正確的持久訊號。

    #214 出向忠實度：帶 `<__group name role>`（隊伍色/角色）——ICS 已存 team_color/role（schema
    _m015），出向補上讓現場端渲染隊伍色（TAK 隊伍色由 `<__group name>` 決定，非 `<color argb>`，
    後者是繪圖覆寫色、屬幾何路徑）。鏡像入向 `cop_service._extract_squad`，round-trip 不掉欄位。
    **刻意不送**裝置遙測 `<takv>`/`<status battery>`/`<track speed/course>`/`<precisionlocation GPS>`
    ——ICS 非 GPS 裝置，偽造遙測會誤導現場、違背 remarks 的 `source: ICS` 誠實原則（#214 doctrine）。
    """
    now = now or datetime.now(UTC)
    t = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    stale = (now + timedelta(minutes=stale_minutes)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    detail_parts: list[str] = []
    if callsign:
        detail_parts.append(f"<contact callsign={quoteattr(callsign)}/>")
    if remarks:
        detail_parts.append(f"<remarks>{escape(remarks)}</remarks>")
    # #214：隊伍色/角色。name 帶色名（如 'Cyan'/'Dark Blue'）；role 無 group name 在 wire 無意義，
    # 故 team_color 必填才出元素、role 選填。name 同步 _extract_squad 的 strip().title() 標準化——否則
    # _m015 回填的未正規化值（如 'darkBlue'）出向後再被 ingest 會漂成 'Darkblue'，落入不同小隊桶（review）。
    group_name = team_color.strip().title() if team_color else ""
    if group_name:
        if role:
            detail_parts.append(f"<__group name={quoteattr(group_name)} role={quoteattr(role)}/>")
        else:
            detail_parts.append(f"<__group name={quoteattr(group_name)}/>")
    detail_parts.append("<archive/>")  # 持久標記
    detail = "".join(detail_parts)

    return (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
        f"<event version='2.0' uid={quoteattr(uid)} type={quoteattr(type_)} "
        f"how='h-g-i-g-o' time='{t}' start='{t}' stale='{stale}'>"
        f"<point lat='{float(lat)}' lon='{float(lon)}' hae='{float(hae)}' ce='9999999' le='9999999'/>"
        f"<detail>{detail}</detail>"
        "</event>"
    )


# #216：ICS 出向 GeoChat 的「站台身分」。ICS 是 TAK 訂閱者、非 GPS 裝置（無自身 SA/PLI 上線），
# 故 wire 上原無 ICS 裝置 uid——出向通聯需要一個穩定 sender uid 才能組 chatgrp/link/uid。
# 用固定站台 uid（指揮部視為單一 contact），實際發話者由 senderCallsign 帶出（誠實標明是誰）。
ICS_SELF_UID = "ICS-CMD"
# 全體聊天室名（ATAK 慣例固定字串，真機抓包 fixtures/cot/geochat_btf.xml 實證）。
ALL_CHAT_ROOMS = "All Chat Rooms"


def build_geochat_cot(
    *,
    sender_callsign: str,
    message: str,
    msg_id: str,
    sender_uid: str = ICS_SELF_UID,
    chatroom: str = ALL_CHAT_ROOMS,
    recipient_uid: str | None = None,
    lat: float = 0.0,
    lon: float = 0.0,
    hae: float = 0.0,
    stale_minutes: int = 5,
    now: datetime | None = None,
) -> str:
    """組一則 ATAK 原生格式的 GeoChat（CoT `b-t-f`）出向通聯——#216 出向半（對稱入向 chat_service）。

    格式依真機抓包 `fixtures/cot/geochat_btf.xml` + ATAK GeoChat 契約：
    `<__chat chatroom id senderCallsign>` + `<chatgrp uid0/uid1>` + `<link>`（發話者自連）+
    `<remarks source to>`。所有外來字串走 XML escape/quoteattr（縱深防護；router 另有內容白名單）。

    收件人路由模型（三態）——由 `room_seg`（uid 第三段 + chatgrp uid1 + __chat id）區分：
    - **全體廣播**：`recipient_uid=None` + `chatroom="All Chat Rooms"` → room_seg=「All Chat Rooms」。
    - **命名聊天室/隊伍**：`recipient_uid=None` + `chatroom=<房名>` → room_seg=房名。
    - **點對點 DM**：`recipient_uid=<裝置uid>` → room_seg=收件 uid、`chatroom` 帶收件呼號（顯示用）。
      DM 另補 `<marti><dest callsign>` 讓 **server 只投遞給該呼號**——否則 server 對無 dest 的
      GeoChat 廣播全發、靠 client 過濾顯示，而 ATAK/iTAK 鬆緊不一會外洩（#216 dogfood 實證）。

    sender_uid 預設站台身分 `ICS-CMD`（見 ICS_SELF_UID）；`sender_callsign` 帶實際發話者（誠實）。
    uid 含 `msg_id`（呼叫端給的唯一 GUID）——回送 ICS 自身時靠它冪等去重（chat_repo.chat_exists）。
    stale 預設 5 分鐘（對齊 ATAK GeoChat 短時效；通聯非持久態勢，故**不**帶 `<archive/>`）。
    """
    now = now or datetime.now(UTC)
    t = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    stale = (now + timedelta(minutes=stale_minutes)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    # room_seg = uid 第三段 / chatgrp uid1 / __chat id 三者一致：DM 用收件 uid，否則用聊天室名。
    room_seg = recipient_uid if recipient_uid else chatroom
    uid = f"GeoChat.{sender_uid}.{room_seg}.{msg_id}"
    source = f"BAO.F.ICS.{sender_uid}"  # 標明 ICS 出向來源（對齊 marker 的 source: ICS 誠實原則）

    detail = (
        f"<__chat parent='RootContactGroup' groupOwner='false' messageId={quoteattr(msg_id)} "
        f"chatroom={quoteattr(chatroom)} id={quoteattr(room_seg)} senderCallsign={quoteattr(sender_callsign)}>"
        f"<chatgrp uid0={quoteattr(sender_uid)} uid1={quoteattr(room_seg)} id={quoteattr(room_seg)}/>"
        "</__chat>"
        # 發話者自連（ATAK 用以解析「誰發的」→ reply 路由）；ICS 站台身分。
        f"<link uid={quoteattr(sender_uid)} type='a-f-G-U-C-I' relation='p-p'/>"
    )
    # DM：補 server 路由指令——只投遞給該呼號（chatroom 在 DM 模式即收件呼號）。廣播/聊天室不帶
    # dest（讓 server 群發）。沒這個 → server 廣播全發、私訊外洩給非收件端（#216 dogfood）。
    if recipient_uid:
        detail += f"<marti><dest callsign={quoteattr(chatroom)}/></marti>"
    detail += f"<remarks source={quoteattr(source)} to={quoteattr(room_seg)} time='{t}'>{escape(message)}</remarks>"

    return (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
        f"<event version='2.0' uid={quoteattr(uid)} type='b-t-f' "
        f"how='h-g-i-g-o' time='{t}' start='{t}' stale='{stale}'>"
        f"<point lat='{float(lat)}' lon='{float(lon)}' hae='{float(hae)}' ce='9999999' le='9999999'/>"
        f"<detail>{detail}</detail>"
        "</event>"
    )


# 出向幾何預設樣式（無 entity color 時）。stroke 不透明白（ATAK 預設可見）；fill 半透明黑。
_DEFAULT_STROKE_ARGB = -1  # 0xFFFFFFFF 白
_FILL_ALPHA = 0x40  # 出向填色透明度（~25%，現場端看得到底圖）
_DEFAULT_FILL_ARGB = _FILL_ALPHA << 24  # 0x40000000 半透明黑（與 _FILL_ALPHA 同源，避免 drift）


def build_geometry_cot(
    *,
    uid: str,
    type_: str,
    vertices: list[list[float]],
    closed: bool,
    callsign: str | None = None,
    remarks: str | None = None,
    color: str | None = None,
    dashed: bool = False,
    dotted: bool = False,
    waypoints: list | None = None,
    link_attr: dict | None = None,
    stale_minutes: int = 60,
    now: datetime | None = None,
) -> str:
    """組一個 ATAK 原生格式的幾何 CoT 指令（線/區下行，P2-30 / #180；格式 #211 ATAK dogfood 修正）。

    幾何序列化委派 `geometry_service.vertices_to_cot_links`（`<link point=...>` 序列，對稱 P2-08
    入向 round-trippable）。**帶樣式**（strokeColor/strokeWeight/strokeStyle，closed 另加 fillColor）
    —— #211 dogfood：ATAK 不渲染無樣式繪圖（iTAK 寬鬆才吃舊 `<shape><polyline>`）；`<link>`+樣式
    為 ATAK/iTAK 雙吃超集。樣式色由 entity `attributes.color`（hex）衍生；`strokeStyle` 三態與入向
    對稱（`dotted` > `dashed` > solid，分別由 `attributes.dotted` / `attributes.dash` 來，不漏 dotted）。
    event `<point>` 取頂點形心（ATAK 標籤錨點）。含 `<archive/>`；callsign/remarks 走 XML escape。
    type_ 慣例：closed polygon → `u-d-f`、line/route → `b-m-r` 或 `u-d-f`（呼叫端定，schema 已驗白名單）。
    """
    from services import geometry_service

    # 同一組 cleaned 點供 links 與形心用（避免 links 過濾、形心沒過濾的分歧，review #180）。
    pts = geometry_service.clean_vertices(vertices, closed=closed)
    # #260 Slice B：route 有原始 waypoints（入向保留的 attributes.link）→ 忠實序列化（保 waypoint
    # 名字/type + link_attr 導航屬性）；ICS 自建 route（僅 vertices）或無合法 waypoint → 退回光禿 links。
    links = None
    if not closed and waypoints:
        raw = geometry_service.route_links_to_cot(waypoints)
        if raw:
            links = raw + geometry_service.link_attr_to_cot(link_attr)
    if links is None:
        links = geometry_service.vertices_to_cot_links(vertices, closed=closed)
    now = now or datetime.now(UTC)
    t = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    stale = (now + timedelta(minutes=stale_minutes)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    # 形心（點落在頂點集中心；純為 event <point> 錨點，不影響 link 幾何）
    lat_c = sum(la for la, _ in pts) / len(pts)
    lon_c = sum(lo for _, lo in pts) / len(pts)

    stroke = geometry_service.hex_to_argb_int(color, alpha=0xFF)
    stroke = _DEFAULT_STROKE_ARGB if stroke is None else stroke
    style = "dotted" if dotted else ("dashed" if dashed else "solid")

    detail_parts: list[str] = [
        links,
        f"<strokeColor value='{stroke}'/>",
        "<strokeWeight value='3.0'/>",
        f"<strokeStyle value='{style}'/>",
    ]
    if closed:
        fill = geometry_service.hex_to_argb_int(color, alpha=_FILL_ALPHA)
        detail_parts.append(f"<fillColor value='{_DEFAULT_FILL_ARGB if fill is None else fill}'/>")
    elif type_.startswith("b-m-r"):
        # #260：僅真 route(b-m-r) 帶 <__routeinfo> —— ATAK 用它認定「這是 route」才渲染；缺了則 b-m-r
        # event 收得到卻不畫（真機 dogfood 實證）。u-d-f 等開放繪圖非導航 route，不加（避免 ATAK 誤當
        # route；review：__routeinfo gate 在 route type 而非 not-closed，免波及 freehand 繪圖）。
        detail_parts.append("<__routeinfo><__navcues/></__routeinfo>")
    if callsign:
        detail_parts.append(f"<contact callsign={quoteattr(callsign)}/>")
    if remarks:
        detail_parts.append(f"<remarks>{escape(remarks)}</remarks>")
    detail_parts.append("<labels_on value='true'/>")
    detail_parts.append("<archive/>")
    detail = "".join(detail_parts)

    return (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
        f"<event version='2.0' uid={quoteattr(uid)} type={quoteattr(type_)} "
        f"how='h-g-i-g-o' time='{t}' start='{t}' stale='{stale}'>"
        f"<point lat='{lat_c}' lon='{lon_c}' hae='0.0' ce='9999999' le='9999999'/>"
        f"<detail>{detail}</detail>"
        "</event>"
    )


# #467 B：節點 node_type → MIL-STD-2525 CoT type（友軍地面組織單位；function 碼互不重複 →
# ATAK/iTAK 渲染成可辨的不同符號）。實作 mil_symbol.js 註解預留的「cot_type 字典擴充」。
# 只在**出向分享時**套用：節點的 cop_entity.type 存通用 'a-f-G-I'、ICS 本地渲染靠 node_type 白色象形
# → 不改存儲、不需 migration、server-authoritative（出向代表由 server 決定）。未列 node_type → caller
# 退回 entity.type。設施（kind='infra'）暫不做（#467 follow-up：民事概念無淨 2525 對應，待 EMS/HADR 符號）。
# ⚠ 符號碼待真機 ATAK dogfood 定稿；調整只改此表一行。
_NODE_COT_TYPE = {
    "command": "a-f-G-U-H",  # 指揮所 / Headquarters
    "medical": "a-f-G-U-U-S-M",  # 後勤醫療 / CSS medical
    "security": "a-f-G-U-U-M",  # 憲兵 / 警戒
    "forward": "a-f-G-U-C-I",  # 步兵 / 前進元素
    "shelter": "a-f-G-U-U-S",  # 後勤支援 / 收容
}


def node_cot_type(node_type: str | None) -> str | None:
    """節點 node_type → 出向 CoT type（#467 B）；未列 → None（caller 退回 entity.type）。"""
    return _NODE_COT_TYPE.get(node_type or "")


def entity_to_cot(entity: dict, *, stale_minutes: int = 60, now: datetime | None = None) -> str:
    """**cop_entity（既有感知標記）→ CoT**（P2-30 part 2 / #180）：分享既有標記到 TAK 用。

    點 vs 幾何分流（key 在 `attributes.kind`，與 P2-08 入向一致；不依賴 P2-27 的 event↔marker 重構）：
      - `kind ∈ {route, polygon}` → 幾何（`attributes.vertices`）→ `build_geometry_cot`
        （polygon → closed=True；route → closed=False）
      - 否則 → 點 → `build_command_cot`（用 entity 的 lat/lon）
    沿用 entity 的 uid/type/callsign/remarks；time/start/stale 由 server 重產（不信舊時鐘），含 `<archive/>`。
    唯讀 entity dict，不改 cop_entities（與 P2-27 並行不衝突）。
    """
    attrs = entity.get("attributes") or {}
    kind = attrs.get("kind")
    uid = entity["uid"]
    type_ = entity["type"]
    # #467 B：節點出向套 per-node_type 2525 符號（存儲 type=通用 a-f-G-I，出向才換可辨符號）。
    # 未列 node_type → 保留 entity.type（fail-safe，退通用友軍設施框）。zone 恆走點路徑（下方 else）。
    if kind == "zone":
        type_ = node_cot_type(attrs.get("node_type")) or type_
    callsign = entity.get("callsign")
    # P2-30 part 3：所有 ICS→TAK 外送標記在 remarks 標 `source: ICS` —— 現場端（iTAK）點開即知此標
    # 由指揮部送出（解「iTAK 送的 vs ICS 送的同款 2525 框肉眼難分」）。tag 只在出口加，不存進
    # cop_entities（entity.remarks 保持使用者原註記乾淨）。
    _user_remarks = entity.get("remarks")
    remarks = f"source: ICS\n{_user_remarks}" if _user_remarks else "source: ICS"

    if kind in ("route", "polygon"):
        vertices = attrs.get("vertices") or []
        return build_geometry_cot(
            uid=uid,
            type_=type_,
            vertices=vertices,
            closed=(kind == "polygon"),
            callsign=callsign,
            remarks=remarks,
            # #214 / #211：帶 entity 樣式（color hex、dash/dotted bool）→ ATAK 才渲染（無樣式不畫）。
            color=attrs.get("color"),
            dashed=bool(attrs.get("dash")),
            dotted=bool(attrs.get("dotted")),
            # #260 Slice B：route 忠實 round-trip —— 傳入向保留的原始 waypoints + 導航屬性；
            # polygon 無 waypoint 語意（不傳，走 vertices）。
            waypoints=attrs.get("link") if kind == "route" else None,
            link_attr=attrs.get("link_attr") if kind == "route" else None,
            stale_minutes=stale_minutes,
            now=now,
        )
    return build_command_cot(
        uid=uid,
        type_=type_,
        lat=float(entity["lat"]),
        lon=float(entity["lon"]),
        hae=float(entity.get("hae") or 0.0),
        callsign=callsign,
        remarks=remarks,
        # #214：帶 entity 的隊伍色/角色（正規化欄位，非 echo 原始 attributes —— 避免連帶送出
        # 不該編的裝置遙測 takv/status/track）。ICS 自建標記無 team_color → 不出 <__group>（誠實）。
        team_color=entity.get("team_color"),
        role=entity.get("role"),
        stale_minutes=stale_minutes,
        now=now,
    )


# 註：刪除已廣播標記到 TAK（t-x-d-d / 墓碑）經真機 + 活 server 實證**無效**（server `<repository>`
# 持久層不靠 stale 移除、不認 streaming t-x-d-d；Marti REST 亦無單顆 CoT DELETE）→ 可靠刪除只在
# Mission/DataSync 層 = **P2-14**。見 docs/roadmap/tak-integration-strategy.md §4b、memory
# tak-streaming-archive-stale-vs-mission。故不在此提供 build_delete/tombstone（避免無效死碼污染 server）。


def _build_config():
    """從 core.config 組 :8089 連線設定（複用訂閱那套 cert，fail-closed）。"""
    if not config.TAK_COT_URL or not config.TAK_CLIENT_CERT or not config.TAK_CLIENT_KEY:
        raise RuntimeError("TAK 下行未配置：需 TAK_COT_URL / TAK_CLIENT_CERT / TAK_CLIENT_KEY")
    return build_subscribe_config(
        cot_url=config.TAK_COT_URL,
        client_cert=config.TAK_CLIENT_CERT,
        client_key=config.TAK_CLIENT_KEY,
        cafile=config.TAK_CAFILE,
        allow_insecure_tls=config.TAK_ALLOW_INSECURE_TLS,
    )


async def send_cot(cot_xml: str) -> None:
    """開一條 :8089 mTLS 連線，寫出 CoT，drain 後關閉。

    每次下達開短連線（指令稀疏，免維持常駐 writer 的複雜度）。連線/寫入失敗 → raise，
    交 router 回 503（不靜默；指令未送達必須讓操作員知道）。
    """
    import pytak  # lazy：與 tak_service 一致，避免 parser 路徑載 pytak

    cfg = _build_config()
    writer = None
    try:
        reader, writer = await asyncio.wait_for(pytak.protocol_factory(cfg), timeout=_CONNECT_TIMEOUT_S)
        writer.write(cot_xml.encode("utf-8"))
        await asyncio.wait_for(writer.drain(), timeout=_WRITE_TIMEOUT_S)
        log.info("tak.downlink_sent", cot_url=cfg.get("COT_URL"), bytes=len(cot_xml))
    finally:
        if writer is not None:
            try:
                writer.close()
            except Exception:  # noqa: BLE001 — 關閉 best-effort，不掩蓋上游錯誤
                pass


# ── #507 Phase4：ICS 自報 SA presence（下行定址：現身現場 Contacts）─────────────────


def build_presence_cot(
    *,
    callsign: str,
    lat: float,
    lon: float,
    uid: str = ICS_SELF_UID,
    stale_seconds: int = 180,
    team_color: str = "Cyan",
    role: str = "Team Member",
    now: datetime | None = None,
) -> str:
    """組 ICS 自我 SA presence CoT（`a-f-G-U-C`）——讓 ICS 現身現場 ATAK/iTAK 的 Contacts，供派工/
    指定通訊/指定傳檔。`endpoint='*:-1:stcp'`＝『經 server 連我』（TAK StreamingEndpointRewriteFilter
    於串流端改寫成 ICS 實際連線）→ 對 ICS 的定向傳輸走 server 中介、非 P2P。

    **誠實原則（#214，見 build_command_cot doc）**：位置為固定指揮部座標（config，非 GPS）；`how='m-g'`
    （machine-generated，非 human-input）；**刻意不送**偽造裝置遙測 `<takv>/<status battery>/<track>/
    `<precisionlocation GPS>`（ICS 非 GPS 裝置，偽造會誤導現場）。remarks 誠實標 `source: ICS`。
    """
    now = now or datetime.now(UTC)
    t = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    stale = (now + timedelta(seconds=max(1, stale_seconds))).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    group_name = (team_color or "").strip().title()
    detail = (
        f"<contact callsign={quoteattr(callsign)} endpoint='*:-1:stcp'/>"
        + (f"<__group name={quoteattr(group_name)} role={quoteattr(role)}/>" if group_name else "")
        + "<remarks>source: ICS 指揮部（自動 presence；非 GPS 裝置）</remarks>"
    )
    return (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
        f"<event version='2.0' uid={quoteattr(uid)} type='a-f-G-U-C' "
        f"how='m-g' time='{t}' start='{t}' stale='{stale}'>"
        f"<point lat='{float(lat)}' lon='{float(lon)}' hae='0' ce='9999999' le='9999999'/>"
        f"<detail>{detail}</detail>"
        "</event>"
    )


def presence_enabled() -> bool:
    """#507 Phase4：presence beacon 是否啟用（config opt-in，預設 OFF）。"""
    return bool(config.TAK_PRESENCE_ENABLED)


async def presence_beacon_loop(stop_event: asyncio.Event) -> None:
    """週期送 ICS SA presence 直到 stop_event。best-effort：單次送失敗只 log、續跑（TAK 選配外部來源，
    presence 中斷不擋 ICS）。sleep 走 stop_event.wait 可即時中斷（軟停立即收）。"""
    interval = max(10, int(config.TAK_PRESENCE_INTERVAL_S))
    log.info("tak.presence_beacon_start", callsign=config.TAK_PRESENCE_CALLSIGN, interval_s=interval)
    while not stop_event.is_set():
        try:
            await send_cot(
                build_presence_cot(
                    callsign=config.TAK_PRESENCE_CALLSIGN,
                    lat=config.TAK_PRESENCE_LAT,
                    lon=config.TAK_PRESENCE_LON,
                    stale_seconds=interval * 3,
                )
            )
        except Exception:  # noqa: BLE001 — presence best-effort，送失敗只 log、續跑
            log.debug("tak.presence_beacon_send_failed", exc_info=True)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except TimeoutError:
            pass  # 間隔到 → 續送下一輪
    log.info("tak.presence_beacon_stop")
