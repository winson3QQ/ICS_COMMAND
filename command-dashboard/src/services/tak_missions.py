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
from services.tak_rest_client import build_tak_rest_client

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
