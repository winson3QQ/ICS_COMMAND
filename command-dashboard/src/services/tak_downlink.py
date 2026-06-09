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


def build_geometry_cot(
    *,
    uid: str,
    type_: str,
    vertices: list[list[float]],
    closed: bool,
    callsign: str | None = None,
    remarks: str | None = None,
    stale_minutes: int = 60,
    now: datetime | None = None,
) -> str:
    """組一個帶 `<shape>` 幾何的 CoT XML 指令（線/區下行，P2-30 / #180）。

    幾何序列化委派 `geometry_service.vertices_to_cot_shape`（對稱 P2-08 入向，round-trippable）。
    event `<point>` 取頂點形心（ATAK 標籤錨點）。含 `<archive/>`；callsign/remarks 走 XML escape。
    type_ 慣例：closed polygon → `u-d-f`、line/route → `b-m-r` 或 `u-d-f`（呼叫端定，schema 已驗白名單）。
    """
    from services import geometry_service

    # 同一組 cleaned 點供 shape 與形心用（避免 shape 過濾、形心沒過濾的分歧，review #180）。
    pts = geometry_service.clean_vertices(vertices, closed=closed)
    shape = geometry_service.vertices_to_cot_shape(vertices, closed=closed)
    now = now or datetime.now(UTC)
    t = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    stale = (now + timedelta(minutes=stale_minutes)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    # 形心（點落在頂點集中心；純為 event <point> 錨點，不影響 shape 幾何）
    lat_c = sum(la for la, _ in pts) / len(pts)
    lon_c = sum(lo for _, lo in pts) / len(pts)

    detail_parts: list[str] = [shape]
    if callsign:
        detail_parts.append(f"<contact callsign={quoteattr(callsign)}/>")
    if remarks:
        detail_parts.append(f"<remarks>{escape(remarks)}</remarks>")
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
