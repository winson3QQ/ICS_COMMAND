# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""tak_files.py — TAK Server file store（Enterprise Sync）client（#503 底層）

定位：ICS↔TAK 圖片/附件交換的**共用底層**（#503 地點型雙向照片 + #505 圖型訊息雙向
兩軸都建在這層上）。TAK 把附件（照片、data package…）存在 Enterprise Sync file store，
以 **SHA-256 hash 定址**；本檔封裝對它的「搜尋 / metadata / 下載」（讀側）與「上傳」（寫側）。

端點契約（官方 5.7 OpenAPI `docs/reference/takserver-5.7-openapispec.json` + #506 reality-check）：
- 搜尋   GET  /Marti/api/sync/search           —— box/circle/uid/keyword/mimetype/mission… filter → JSON
- metadata 走 **search?hash=**（`/files/{hash}/metadata` 實測回 405，見 get_file_metadata）
- 下載   GET  /Marti/api/files/{hash}           —— 原始 bytes（*/*）
- 刪除   DELETE /Marti/api/files/{hash}
- 上傳   **不在 /Marti/api spec 內**——走 legacy servlet `POST /Marti/sync/upload?name=&creatorUid=`
         + raw body（#506 reality-check 定死格式：檔名須 ASCII、無多餘 hash 參數；掛 uid=marker
         → search?uid= 撈得到）。見 upload_file。

認證：讀（搜尋/下載）用 `TAK_MARTI_READ_CERT/KEY`（truststore 信任即通）；**寫（上傳）用
`TAK_MARTI_WRITE_CERT/KEY`**（讀 client 無寫權）。複用 P2-11 `tak_rest_client`（cert mTLS + retry）。

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
_CREATOR_KEYS = ("creatorUid", "CreatorUid", "creatoruid")


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


def extract_creator_uid(meta: dict) -> str | None:
    """從 search 結果取 creatorUid（跨版本欄位容錯）——供 #509-P2 輪詢跳過 ICS 自傳（防自我 re-ingest）。"""
    return _first(meta, _CREATOR_KEYS)


def extract_keywords(meta: dict) -> list[str]:
    """從 search 結果取 keywords（跨版本 Keywords/keywords；可能是 list 或逗號字串）→ 正規化為 list。
    現場分享的 mission-package 帶 `missionpackage` keyword（#509-P2 輪詢用它辨識、避開 ICS 自傳的
    #503 檔——後者 keyword=marker_uid）。"""
    raw = meta.get("keywords") or meta.get("Keywords") or []
    if isinstance(raw, str):
        return [k.strip() for k in raw.split(",") if k.strip()]
    if isinstance(raw, list | tuple):
        return [str(k).strip() for k in raw if str(k).strip()]
    return []


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
    """取單檔 metadata（Resource：mimeType/name/uid/keywords…）。hash 先驗 SHA-256。

    **走 `GET /Marti/api/sync/search?hash=`**（非 `/files/{hash}/metadata`——後者實測回 405
    Method Not Allowed，#506 reality-check）；search 回同一份 Resource metadata。查無回 None。
    raise TakFilestoreError（hash 非法）/ TakRestError（HTTP）。
    """
    h = _require_valid_hash(file_hash)
    results = await search_files(client, hash=h)
    return results[0] if results else None


async def download_file(client, file_hash: str, *, max_bytes: int = _MAX_DOWNLOAD_BYTES) -> bytes | None:
    """下載單檔原始 bytes（GET /Marti/api/files/{hash}）。hash 先驗 SHA-256。

    回傳 bytes；查無 / 空回 None。max_bytes 上限保護（超過拋 TakRestError）。
    raise TakFilestoreError（hash 非法）/ TakRestError（HTTP / 超上限）。
    """
    h = _require_valid_hash(file_hash)
    return await client.get_bytes(f"/Marti/api/files/{h}", max_bytes=max_bytes)


async def download_content(client, file_hash: str, *, max_bytes: int = _MAX_DOWNLOAD_BYTES) -> bytes | None:
    """下載 fileshare content（`GET /Marti/sync/content?hash=`）——**現場分享的 mission-package zip 走此
    端點**（#509 reality-check 實證：現場 zip 於 /sync/content 回 200，不在 /Marti/api/files/{hash}）。
    hash 先驗 SHA-256。回 bytes；查無回 None。max_bytes 上限保護（超過拋 TakRestError）。"""
    h = _require_valid_hash(file_hash)
    return await client.get_bytes(f"/Marti/sync/content?hash={h}", max_bytes=max_bytes)


_UPLOAD_PATH = "/Marti/sync/upload"  # legacy Enterprise Sync 上傳 servlet（不在 /Marti/api OpenAPI）
# 上傳檔名須純 ASCII 安全字元——server ESAPI 擋重音/標點/非 ASCII（#506 reality-check 實測 400）。
_UNSAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]")


def filestore_write_enabled() -> bool:
    """file store 上傳是否可用：需 Marti URL + **寫 cert**（缺任一則停用）。"""
    return bool(config.TAK_MARTI_URL and config.TAK_MARTI_WRITE_CERT and config.TAK_MARTI_WRITE_KEY)


def _safe_upload_name(name: str) -> str:
    """檔名清成純 ASCII 安全字元（server ESAPI 擋非 ASCII/標點；#506 reality-check）。"""
    cleaned = _UNSAFE_NAME_RE.sub("_", (name or "").strip())
    return cleaned[:120] or "upload.bin"


def _build_write_client():
    """建寫身分（WRITE_CERT/KEY）的 Marti REST client（上傳用）。caller 負責 close。"""
    return build_tak_rest_client(
        base_url=config.TAK_MARTI_URL,
        client_cert=config.TAK_MARTI_WRITE_CERT,
        client_key=config.TAK_MARTI_WRITE_KEY,
        cafile=config.TAK_CAFILE,
        allow_insecure_tls=config.TAK_ALLOW_INSECURE_TLS,
        min_interval_s=config.TAK_MARTI_MIN_INTERVAL_S,
        max_retries=config.TAK_MARTI_MAX_RETRIES,
    )


async def upload_file(
    client,
    *,
    content: bytes,
    filename: str,
    mimetype: str,
    creator_uid: str,
    marker_uid: str | None = None,
    keywords: str | None = None,
) -> dict:
    """上傳檔案到 Enterprise Sync（POST /Marti/sync/upload）→ 回 server JSON（含 Hash/UID/…）。

    **格式（#506 reality-check 定死、非猜）**：`POST /Marti/sync/upload?name=<ASCII檔名>&
    creatorUid=<uid>[&uid=<marker>&keywords=<marker>]`，body=raw bytes（Content-Type=mimetype）。
    坑：**檔名須 ASCII、不帶多餘 `hash` 參數**（server ESAPI 擋非 ASCII/多餘參數，實測 400）。
    掛 `marker_uid` → 檔案 Resource 的 uid/keywords 設為該 marker → 之後 `search?uid=<marker>`
    撈得到（地點型連結，#503/M1b live 實證）。

    client 由 caller 注入（生產走 _build_write_client；WRITE cert，讀 client 無寫權）。
    raise TakRestError（HTTP，含 400 格式錯）/ TakFilestoreError（回應無 Hash）。
    """
    params = {"name": _safe_upload_name(filename), "creatorUid": creator_uid}
    if marker_uid:
        params["uid"] = marker_uid  # 地點型連結：search?uid=<marker> 撈得到
        params["keywords"] = marker_uid
    if keywords:
        params["keywords"] = keywords  # 覆寫（#509-P3 下行 zip 用 'missionpackage'，讓收方/ICS 輪詢認得）
    result = await client.post_bytes(
        _UPLOAD_PATH, content, params=params, content_type=mimetype or "application/octet-stream"
    )
    if not isinstance(result, dict) or not extract_hash(result):
        raise TakFilestoreError(f"上傳回應無 Hash（格式異常）：{str(result)[:200]}")
    return result
