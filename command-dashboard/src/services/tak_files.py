# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""tak_files.py — TAK Server file store（Enterprise Sync）client（#503 底層）

定位：ICS↔TAK 圖片/附件交換的**共用底層**（#503 地點型雙向照片 + #505 圖型訊息雙向
兩軸都建在這層上）。TAK 把附件（照片、data package…）存在 Enterprise Sync file store，
以 **SHA-256 hash 定址**；本檔封裝對它的「搜尋 / metadata / 下載」（讀側，協定已驗）與
「上傳」（寫側，格式待真機 ATAK 抓包定，見下）。

端點契約（官方 5.7 OpenAPI `docs/reference/takserver-5.7-openapispec.json`）：
- 搜尋   GET  /Marti/api/sync/search           —— box/circle/uid/keyword/mimetype/mission… filter → JSON
- metadata GET /Marti/api/files/{hash}/metadata —— 單檔 metadata
- 下載   GET  /Marti/api/files/{hash}           —— 原始 bytes（*/*）
- 刪除   DELETE /Marti/api/files/{hash}
- 上傳   **不在 /Marti/api spec 內**（整份 spec 唯一 POST 是 /files/api/config）——
         上傳走**未進 OpenAPI 的 legacy servlet**（`/Marti/sync/upload`），multipart 格式
         需真機 ATAK 傳圖抓封包定死（之前手打 400，根因即格式未定）→ 見 upload_file 留樁。

認證：下載/搜尋皆為**讀**，用 `TAK_MARTI_READ_CERT/KEY`（truststore 信任即通、毋須 register、
不卡 write gate，對齊 tak_resync）。複用 P2-11 `tak_rest_client`（cert mTLS + retry + rate-limit）。

安全：hash 進 URL path 前一律驗 SHA-256 hex（`_require_valid_hash`）——附件 hash 可能源自
現場裝置 CoT（不可信），杜絕 path 注入 / 遍歷。下載走 `get_bytes` 的 max_bytes 上限防灌爆。
"""

import re

import structlog

from core import config
from services.tak_rest_client import build_tak_rest_client

log = structlog.get_logger()

_SEARCH_PATH = "/Marti/api/sync/search"
# 下載單檔上限：TAK file store 附件為現場照片/小 data package，32MB 綽綽有餘；
# 超過視為異常（惡意巨檔 / 非預期內容）→ get_bytes 中止防灌爆記憶體。
_MAX_DOWNLOAD_BYTES = 32 * 1024 * 1024

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")

# search / metadata 回傳的 dict 欄位在不同 TAK 版本大小寫不一（Hash/hash、Name/name…），
# 消費方靠這些候選鍵防禦式取值，不寫死單一拼法。
_HASH_KEYS = ("hash", "Hash", "hashValue")
_NAME_KEYS = ("name", "Name", "filename", "Filename")
_MIME_KEYS = ("mimeType", "MIMEType", "mimetype", "contentType")


class TakFilestoreError(Exception):
    """TAK file store 操作錯誤（hash 非法 / REST 失敗 / 上傳未實作）。"""


def filestore_enabled() -> bool:
    """file store 讀側是否可用：需 Marti URL + 讀 cert（缺任一則停用）。"""
    return bool(config.TAK_MARTI_URL and config.TAK_MARTI_READ_CERT and config.TAK_MARTI_READ_KEY)


def is_valid_hash(file_hash: str) -> bool:
    """是否為合法 SHA-256 hex（64 位 hex）。TAK file store 一律以此定址。"""
    return bool(file_hash) and bool(_SHA256_RE.match(file_hash))


def _require_valid_hash(file_hash: str) -> str:
    """驗 hash 為 SHA-256 hex，否則拋——擋 path 注入/遍歷（hash 可能源自不可信 CoT）。"""
    if not is_valid_hash(file_hash):
        raise TakFilestoreError(f"非法檔案 hash（須 SHA-256 hex）：{file_hash!r}")
    return file_hash


def _first(meta: dict, keys: tuple[str, ...]) -> str | None:
    """從 metadata dict 取第一個存在且非空的鍵值（跨版本大小寫容錯）。"""
    for k in keys:
        v = meta.get(k)
        if v:
            return str(v)
    return None


def extract_hash(meta: dict) -> str | None:
    """從 search/metadata 結果 dict 取檔案 hash（跨版本欄位容錯）。"""
    return _first(meta, _HASH_KEYS)


def extract_name(meta: dict) -> str | None:
    """從結果 dict 取檔名（跨版本欄位容錯）。"""
    return _first(meta, _NAME_KEYS)


def extract_mimetype(meta: dict) -> str | None:
    """從結果 dict 取 MIME type（跨版本欄位容錯）。"""
    return _first(meta, _MIME_KEYS)


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


def _parse_search_results(payload) -> list[dict]:
    """把 /sync/search 回應正規化為 Resource metadata dict list（跨版本結構容錯）。

    TAK 5.7 契約（OpenAPI `ApiResponseNavigableSetResource`）：結果在 **`data`** 陣列，
    每筆為 Resource（hash/filename/mimeType/uid/creatorUid/latitude/longitude/groups…）。
    舊版 / 其他端點可能用 `results` 或裸 list → 一併容錯。非預期結構回空 list（不拋，best-effort）。
    """
    if payload is None:
        return []
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("data", "results"):  # 5.7 用 data；舊版/相容 results
            arr = payload.get(key)
            if isinstance(arr, list):
                return [x for x in arr if isinstance(x, dict)]
    return []


async def search_files(client, **filters) -> list[dict]:
    """搜尋 file store → metadata dict list。

    filters 對應 /Marti/api/sync/search 的 query 參數（uid/keyword/mimetype/mission/
    filename/box/circle/startTime/endTime/tool…）；None/空值自動略去。
    client 由 caller 注入（生產走 _build_read_client；測試注入 mock）→ 純邏輯可測。
    raise TakRestError：HTTP 層失敗。
    """
    params = {k: v for k, v in filters.items() if v is not None and v != ""}
    data = await client.get_json(_SEARCH_PATH, params or None)
    return _parse_search_results(data)


async def get_file_metadata(client, file_hash: str) -> dict | None:
    """取單檔 metadata（GET /Marti/api/files/{hash}/metadata）。hash 先驗 SHA-256。

    回傳 dict；查無回 None。raise TakFilestoreError（hash 非法）/ TakRestError（HTTP）。
    """
    h = _require_valid_hash(file_hash)
    data = await client.get_json(f"/Marti/api/files/{h}/metadata")
    return data if isinstance(data, dict) else None


async def download_file(client, file_hash: str, *, max_bytes: int = _MAX_DOWNLOAD_BYTES) -> bytes | None:
    """下載單檔原始 bytes（GET /Marti/api/files/{hash}）。hash 先驗 SHA-256。

    回傳 bytes；查無 / 空回 None。max_bytes 上限保護（超過拋 TakRestError）。
    raise TakFilestoreError（hash 非法）/ TakRestError（HTTP / 超上限）。
    """
    h = _require_valid_hash(file_hash)
    return await client.get_bytes(f"/Marti/api/files/{h}", max_bytes=max_bytes)


async def upload_file(client, *, content: bytes, filename: str, mimetype: str) -> str:
    """上傳檔案到 file store → 回檔案 hash。**留樁：待真機定 legacy servlet 格式。**

    上傳端點**不在 /Marti/api OpenAPI spec 內**（見模組 docstring）：走 legacy 的
    `/Marti/sync/upload` servlet，multipart/form-data 欄位順序、必填 param（hash 預算、
    creatorUid、keywords…）需真機 ATAK 傳圖**抓封包**才能定死——之前手打得 400 即格式未定。

    故此處**刻意不猜格式**（猜錯的上傳碼比空樁更糟：假裝能用、真機一到就得重寫）。
    #503/#505 動工需上傳時，於真機 dogfood 定格式後在此落地。
    """
    raise TakFilestoreError(
        "TAK file store 上傳待真機定格式（legacy /Marti/sync/upload servlet 之 multipart "
        "欄位/必填 param 未進 OpenAPI，需 ATAK 抓包定死）——見 tak_files.upload_file docstring 與 #503"
    )
