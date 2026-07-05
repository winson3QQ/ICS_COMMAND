# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""tak_missions.py — TAK Mission / Data Sync 讀取 client（#506 M0）

定位：TAK **第三資料平面 Mission/Data Sync** 的 ICS 消費地基（對照：stream=tak_service、
Enterprise Sync=tak_files）。Mission = server 上具名、持久、可訂閱、帶附件、有變更史的
CoT 容器；ATAK 分享 marker 附件（照片）走此機制而非單純 Enterprise Sync 上傳
（#506/真機 dogfood 定讞，見 memory tak-mission-datasync-plane）。

本檔 = **M0 讀取地基**（唯讀）。端點契約來自官方 5.7 OpenAPI（別猜）：
- 列出   GET /Marti/api/missions              → ApiResponseSetMission（data[Mission]）
- 讀單一 GET /Marti/api/missions/{name}        → 單一 Mission（含 uids[]=marker、contents[]=附件）
- CoT    GET /Marti/api/missions/{name}/cot    → **raw CoT XML 字串**（同 /cot/sa 格式 →
         M1 直接餵 tak_service.parse_cot_events + cop_service.ingest_cot_event，複用 resync 縫）
- 變更史 GET /Marti/api/missions/{name}/changes → ApiResponseSetMissionChange（M1 抽附件連結：
         MissionChange.contentUid=marker uid + contentResource=附件 Resource(hash)）

認證：讀走 `TAK_MARTI_READ_CERT/KEY`（truststore 信任即通，同 tak_resync/tak_files）。
安全：mission name 進 URL path 前驗白名單（擋 path 注入/遍歷）。寫路徑（建/加 marker/上傳）
及附件連結抽取為 M1+（需 live 驗 change shape，本檔不猜）。
"""

import re

import structlog

from core import config
from services import cop_service, tak_service
from services.tak_rest_client import TakRestError, build_tak_rest_client

log = structlog.get_logger()

_MISSIONS_PATH = "/Marti/api/missions"

# mission name 進 URL path 前驗：TAK mission name 慣例為 alnum + - _ . （無斜線/空白/特殊字元）。
# 擋 path 注入/遍歷（name 可能源自不可信輸入）。長度上限保守取 255。
# \A...\Z 絕對錨定（非 ^...$）——$ 容許結尾換行，會放過 "name\n" 這類 header/path 注入。
_NAME_RE = re.compile(r"\A[A-Za-z0-9._-]{1,255}\Z")

# 跨版本欄位大小寫容錯（同 tak_files；Mission 5.7 用 camelCase）。
_NAME_KEYS = ("name", "Name")


class TakMissionError(Exception):
    """TAK Mission 操作錯誤（name 非法 / 回應結構異常）。"""


def missions_enabled() -> bool:
    """Mission 讀取是否可用：需 Marti URL + 讀 cert（缺任一則停用）。"""
    return bool(config.TAK_MARTI_URL and config.TAK_MARTI_READ_CERT and config.TAK_MARTI_READ_KEY)


def is_valid_mission_name(name: str) -> bool:
    """是否為合法 mission name（alnum + -_. ，≤255）。"""
    return bool(name) and bool(_NAME_RE.match(name))


def _require_valid_name(name: str) -> str:
    """驗 mission name，否則拋——擋 path 注入/遍歷。"""
    if not is_valid_mission_name(name):
        raise TakMissionError(f"非法 mission name（須 alnum/-_. 且 ≤255）：{name!r}")
    return name


def _parse_data(payload) -> list[dict]:
    """把 TAK ApiResponse 正規化為 dict list（結果在 `data` 陣列，同 tak_files search）。

    TAK 5.7 契約：`{version,type,data:[...],nodeId}`。裸 list / 其他結構容錯。
    非預期一律回空 list（不拋，best-effort 讀）。
    """
    if payload is None:
        return []
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        arr = payload.get("data")
        if isinstance(arr, list):
            return [x for x in arr if isinstance(x, dict)]
    return []


def extract_name(mission: dict) -> str | None:
    """從 Mission dict 取 name（跨版本欄位容錯）。"""
    for k in _NAME_KEYS:
        v = mission.get(k)
        if v:
            return str(v)
    return None


def _build_read_client():
    """建讀身分（READ_CERT/KEY）的 Marti REST client。caller 負責 close。（同 tak_resync/tak_files）"""
    return build_tak_rest_client(
        base_url=config.TAK_MARTI_URL,
        client_cert=config.TAK_MARTI_READ_CERT,
        client_key=config.TAK_MARTI_READ_KEY,
        cafile=config.TAK_CAFILE,
        allow_insecure_tls=config.TAK_ALLOW_INSECURE_TLS,
        min_interval_s=config.TAK_MARTI_MIN_INTERVAL_S,
        max_retries=config.TAK_MARTI_MAX_RETRIES,
    )


async def list_missions(client) -> list[dict]:
    """列出所有 mission（GET /Marti/api/missions）→ Mission dict list。

    client 由 caller 注入（生產走 _build_read_client；測試注入 mock）→ 純邏輯可測。
    raise TakRestError：HTTP 層失敗。
    """
    data = await client.get_json(_MISSIONS_PATH)
    return _parse_data(data)


async def get_mission(client, name: str) -> dict | None:
    """讀單一 mission（GET /Marti/api/missions/{name}）→ Mission dict（含 uids/contents）。

    name 先驗白名單。查無回 None。raise TakMissionError（name 非法）/ TakRestError（HTTP）。
    """
    n = _require_valid_name(name)
    data = await client.get_json(f"{_MISSIONS_PATH}/{n}")
    missions = _parse_data(data)
    return missions[0] if missions else None


async def get_mission_cot(client, name: str) -> str | None:
    """取 mission 的 CoT（GET /Marti/api/missions/{name}/cot）→ raw XML 字串。

    回 `<events>` 格式 CoT（同 /cot/sa），M1 餵 tak_service.parse_cot_events。空回 None。
    name 先驗白名單。raise TakMissionError（name 非法）/ TakRestError（HTTP）。
    """
    n = _require_valid_name(name)
    return await client.get_text(f"{_MISSIONS_PATH}/{n}/cot")


async def get_mission_changes(client, name: str) -> list[dict]:
    """取 mission 變更史（GET /Marti/api/missions/{name}/changes）→ MissionChange dict list。

    M1 用來抽 marker↔附件連結（ADD_CONTENT 的 contentUid + contentResource.hash）——
    **實際 change shape 待 live 驗（本檔僅取回原始 list，不在此解讀連結）**。
    name 先驗白名單。raise TakMissionError（name 非法）/ TakRestError（HTTP）。
    """
    n = _require_valid_name(name)
    data = await client.get_json(f"{_MISSIONS_PATH}/{n}/changes")
    return _parse_data(data)


async def sync_mission_once(client, name: str) -> dict:
    """拉一個 mission 的 CoT 快照 → 逐筆 ingest 進 COP（#506 M1a）。

    **複用 resync 那條縫**（`tak_service.parse_cot_events` + `cop_service.ingest_cot_event`）：
    mission `/cot` 回 `<events>` 格式（live reality-check 已驗），且**只含真正投遞進 mission 的
    marker**（掛 uid 參照不算，reality-check 實證）→ 消費的是現場真的加進 feed 的物件。
    normalize / 場域歸屬 / CAS / WS 廣播 / faction 全沿用既有 ingest 接縫，本函式只負責
    「拉 + 拆集合 + 餵接縫」（對稱 tak_resync.resync_once）。

    best-effort 逐筆：單筆 ingest 失敗只記數不中斷（一顆壞 event 不拖垮整個 mission 同步）。
    client 由 caller 注入（生產走 _build_read_client；測試注入 mock）。name 先驗白名單。
    回 {"fetched", "ingested", "skipped", "errors"}。raise TakMissionError（name 非法）/
    TakRestError（HTTP）/ CoTParseError（server 回畸形 <events>）。
    """
    raw = await get_mission_cot(client, name)
    if not raw:  # 空 mission → <events></events> 或空 body，視同 0 筆
        return {"fetched": 0, "ingested": 0, "skipped": 0, "errors": 0}

    events = tak_service.parse_cot_events(raw)
    ingested = skipped = errors = 0
    for event in events:
        try:
            result = await cop_service.ingest_cot_event(event)
        except Exception:  # noqa: BLE001 — 單筆失敗不拖垮整批（best-effort，對齊 resync）
            errors += 1
            log.warning("tak.mission.ingest_failed", mission=name, uid=getattr(event, "uid", None), exc_info=True)
            continue
        if result is None:  # 重送 / 亂序 / GeoChat 分流 / t-x-d-d → 非錯，正常跳過
            skipped += 1
        else:
            ingested += 1

    summary = {"fetched": len(events), "ingested": ingested, "skipped": skipped, "errors": errors}
    log.info("tak.mission.sync_done", mission=name, **summary)
    return summary


def configured_mission_names() -> list[str]:
    """從 `TAK_MISSION_FEEDS`（逗號分隔）解出要消費的 mission name 清單。

    去空白 + **驗白名單**（擋設定注入）+ 去重、保序。非法/空項目略去。
    """
    names: list[str] = []
    seen: set[str] = set()
    for part in (config.TAK_MISSION_FEEDS or "").split(","):
        n = part.strip()
        if n and is_valid_mission_name(n) and n not in seen:
            seen.add(n)
            names.append(n)
    return names


def mission_sync_enabled() -> bool:
    """mission 消費是否可用：讀取地基可用（URL+讀 cert）+ 至少配置一個合法 mission feed。"""
    return missions_enabled() and bool(configured_mission_names())


async def run_mission_sync() -> dict:
    """config 驅動的 mission 消費入口：對每個配置的 mission `sync_mission_once` → 聚合（對稱 run_resync）。

    未啟用（缺 URL/讀 cert 或無配置 feed）→ 回 `{"enabled": False, ...}`。
    **單一 mission 失敗不中斷其他**（HTTP/解析/name 錯只記入該 mission 的 error）。
    回 `{"enabled", "missions": {name: summary}, "fetched", "ingested", "skipped", "errors"}`。
    """
    if not mission_sync_enabled():
        log.info("tak.mission.sync_skipped_not_configured")
        return {"enabled": False, "missions": {}, "fetched": 0, "ingested": 0, "skipped": 0, "errors": 0}

    names = configured_mission_names()
    client = _build_read_client()
    per: dict[str, dict] = {}
    agg = {"fetched": 0, "ingested": 0, "skipped": 0, "errors": 0}
    try:
        for name in names:
            try:
                s = await sync_mission_once(client, name)
            except (TakRestError, tak_service.CoTParseError, TakMissionError) as e:
                # 單一 mission 失敗（HTTP 4xx/5xx、畸形 <events>、name 非法）不拖垮其他 mission。
                log.warning("tak.mission.sync_failed", mission=name, error=str(e))
                s = {"fetched": 0, "ingested": 0, "skipped": 0, "errors": 1}
            per[name] = s
            for k in agg:
                agg[k] += s.get(k, 0)
    finally:
        await client.close()
    return {"enabled": True, "missions": per, **agg}
