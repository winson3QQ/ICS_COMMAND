"""tak_resync.py — Marti 權威 resync（P2-14 (C)，#194 / #173）

問題：TAK :8089 串流**不對重連者重播**既有靜態標記（`<latestSA>` 只對新連線補發、
`<repeater>` 僅 emergency 重播）。ICS 重啟或斷線後 COP 會漏掉 server 已持久化的 marker
（活 5.7 dogfood 實證，#161/#173）。

解法：主動拉 Marti `GET /Marti/api/cot/sa?start=&end=` 的 `<events>` 權威快照，逐筆走
既有 `cop_service.ingest_cot_event` 接縫補進 cop_entities——normalize / 場域歸屬
（`current_exercise_id`）/ CAS / WS 廣播 / 軌跡全部沿用，本檔只負責「拉快照 + 拆集合 + 餵接縫」。

關鍵設計（reality check #194 落地，全為活 server 實測結論）：
- **只 upsert、不刪**：resync 拿的是「server 現有 SA 快照」＝正集，只補 server 有、ICS 沒有的；
  **絕不**依「不在快照中」反推刪本地 entity——iTAK 刪除根本不傳到 server（client 硬限制，#194），
  server 自己也不知誰被刪；硬對帳會誤殺 `manual:`/`command` 源。`ingest_cot_event` 天然 upsert-only。
- **小時間窗**：`/cot/sa` 大窗（實測 40 天）回 `BAD_REQUEST(400)`，且 400 與真 auth 拒**共用同一頁**
  （memory `tak-marti-authz-model`）→ 用 `TAK_RESYNC_LOOKBACK_S` 小窗（預設 2h known-good）。
- **bbox 省略**：活 server 實測帶/不帶 bbox 回傳相同（200、同 size）→ resync 不算場域框，省一層。
- **讀身分**：用 `TAK_MARTI_READ_CERT/KEY`（truststore 信任即通，毋須 register、不卡 write gate）。

定位：本檔是 `/cot/sa` 的**消費方**，建於 P2-11 `tak_rest_client` + P2-02 `tak_service.parse_cot_events`
之上。觸發點：(1) (重)連線後背景自動跑一次（`tak_runtime.start`，#173 主訴求）；
(2) `POST /api/tak/resync` 手動觸發（COMMAND_ROLES + audit）。
"""

from datetime import UTC, datetime, timedelta

import structlog

from core import config
from services import cop_service, tak_service
from services.tak_rest_client import TakRestError, build_tak_rest_client

log = structlog.get_logger()  # 對齊 tak_service：on-connect resync 日誌須可見（#173 訴求）

_SA_PATH = "/Marti/api/cot/sa"


def _format_marti_time(dt: datetime) -> str:
    """datetime → Marti `/cot/sa` 要的 `YYYY-MM-DDTHH:MM:SS.000Z`（毫秒 + Z）。

    活 server 實測：此格式可用；缺 start/end 或用 secago 會落 generic 錯誤頁。
    """
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def build_window(now: datetime, lookback_s: float) -> tuple[str, str]:
    """回 (start, end) 字串：[now - lookback, now]。純函式，供測試。"""
    start = now - timedelta(seconds=max(1.0, lookback_s))
    return _format_marti_time(start), _format_marti_time(now)


def resync_enabled() -> bool:
    """resync 是否可用：需 Marti URL + 讀 cert（缺任一則功能停用）。"""
    return bool(config.TAK_MARTI_URL and config.TAK_MARTI_READ_CERT and config.TAK_MARTI_READ_KEY)


def _build_read_client():
    """建讀身分（READ_CERT/KEY）的 Marti REST client。caller 負責 close。"""
    return build_tak_rest_client(
        base_url=config.TAK_MARTI_URL,
        client_cert=config.TAK_MARTI_READ_CERT,
        client_key=config.TAK_MARTI_READ_KEY,
        cafile=config.TAK_CAFILE,
        allow_insecure_tls=config.TAK_ALLOW_INSECURE_TLS,
        min_interval_s=config.TAK_MARTI_MIN_INTERVAL_S,
        max_retries=config.TAK_MARTI_MAX_RETRIES,
    )


async def resync_once(client, *, lookback_s: float, now: datetime | None = None) -> dict:
    """拉一次 `/cot/sa` 快照 → 逐筆 ingest。回 summary dict。

    client 由 caller 注入（生產走 `_build_read_client`；測試注入 mock）→ 純邏輯可測。
    best-effort 逐筆：單筆 ingest 失敗只記數不中斷（一顆壞 event 不拖垮整批 resync）。

    回傳：{"fetched": 抓到幾筆 event, "ingested": 成功進 COP 幾筆,
           "skipped": ingest 回 None（重送/亂序/GeoChat/t-x-d-d）幾筆, "errors": 逐筆例外幾筆}
    raise TakRestError：HTTP 層失敗（4xx 不重試 / 重試耗盡）—— caller 決定吞或拋。
    """
    now = now or datetime.now(UTC)
    start, end = build_window(now, lookback_s)
    raw = await client.get_text(_SA_PATH, {"start": start, "end": end})
    if not raw:  # 空窗 server 可能回空 body → 視同 0 筆
        return {"fetched": 0, "ingested": 0, "skipped": 0, "errors": 0}

    events = tak_service.parse_cot_events(raw)
    ingested = skipped = errors = 0
    for event in events:
        try:
            result = await cop_service.ingest_cot_event(event)
        except Exception:  # noqa: BLE001 — 單筆失敗不拖垮整批（best-effort resync）
            errors += 1
            log.warning("tak.resync.ingest_failed", uid=event.uid, exc_info=True)
            continue
        if result is None:  # 重送/亂序/GeoChat 分流/t-x-d-d → 非錯，正常跳過
            skipped += 1
        else:
            ingested += 1

    summary = {"fetched": len(events), "ingested": ingested, "skipped": skipped, "errors": errors}
    log.info(
        "tak.resync.done",
        fetched=summary["fetched"],
        ingested=ingested,
        skipped=skipped,
        errors=errors,
        window_start=start,
        window_end=end,
    )
    return summary


async def run_resync(lookback_s: float | None = None) -> dict:
    """config 驅動的 resync 入口：建讀 client → resync_once → close。

    lookback_s 預設取 `TAK_RESYNC_LOOKBACK_S`。resync 未啟用（缺 URL/cert）→ 回 disabled summary。
    HTTP 失敗（TakRestError）/ server 回畸形 `<events>`（CoTParseError）往上拋，由 caller
    （endpoint 兩者皆回 503 / 背景 task 吞）決定。
    """
    if not resync_enabled():
        log.info("tak.resync.skipped_not_configured")  # 缺 TAK_MARTI_URL / READ cert
        return {"enabled": False, "fetched": 0, "ingested": 0, "skipped": 0, "errors": 0}

    lookback = config.TAK_RESYNC_LOOKBACK_S if lookback_s is None else lookback_s
    client = _build_read_client()
    try:
        summary = await resync_once(client, lookback_s=lookback)
    finally:
        await client.close()
    return {"enabled": True, **summary}


async def resync_on_connect() -> None:
    """(重)連線後背景自動 resync（#173）：best-effort，失敗只 log 不影響訂閱。

    由 `tak_runtime.start` 在訂閱 task 啟動後 fire-and-forget 呼叫。受
    `TAK_RESYNC_ON_CONNECT` 開關控制。
    """
    if not config.TAK_RESYNC_ON_CONNECT:
        return
    try:
        await run_resync()
    except TakRestError:
        log.warning("tak.resync.on_connect_failed", exc_info=True)  # 不影響訂閱
    except Exception:  # noqa: BLE001 — 自動 resync 絕不拖垮連線啟動
        log.warning("tak.resync.on_connect_unexpected", exc_info=True)  # 不影響訂閱
