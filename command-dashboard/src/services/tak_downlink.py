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
    stale_minutes: int = 60,
    now: datetime | None = None,
) -> str:
    """組一個 v0 CoT XML 指令字串（server 端產 time/start/stale，不信 client 時鐘）。

    所有外來字串走 XML escape/quoteattr（縱深防護；上游 router 另有內容白名單）。
    含 `<archive/>`：要求 server 持久保留（過 stale 不丟）——對齊 #161 archive doctrine；
    即便 reconnect 重播在本 server 不可靠（P2-14 待解），archive 是正確的持久訊號。
    """
    now = now or datetime.now(UTC)
    t = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    stale = (now + timedelta(minutes=stale_minutes)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    detail_parts: list[str] = []
    if callsign:
        detail_parts.append(f"<contact callsign={quoteattr(callsign)}/>")
    if remarks:
        detail_parts.append(f"<remarks>{escape(remarks)}</remarks>")
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
