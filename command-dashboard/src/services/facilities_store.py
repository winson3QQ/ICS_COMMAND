"""facilities_store.py — P1-17 永久設施公開資料底圖層的唯讀讀取（issue #88）。

與 event_taxonomy_store / map_config_store **不同**：本層是**純唯讀基準資料**，
只讀 `static/facilities.seed.json`（tracked factory default），**無 runtime/data 副本、
無 write、不受 exercise scoping / reset 影響**。資料由維護者腳本
`scripts/import_facilities.py` 從台灣政府開放資料產生（供應鏈非中國，政府開放授權第1版）。

快取：seed 隨 code 走、runtime 不變，故載入一次後快取於記憶體（以 mtime 失效，
方便 dev 換檔即生效）。read() 永遠回 dict（讀不到回最小空殼，不 raise / 不 500）。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from core.config import FACILITIES_SEED

log = logging.getLogger(__name__)

# 讀不到 seed 時的最小空殼（保證後端不 NPE / GET 不 500）。
_EMPTY_SHELL: dict[str, Any] = {
    "license": "",
    "attribution": "",
    "sources": [],
    "count": 0,
    "facilities": [],
}

# 記憶體快取：(path, mtime_ns, size, data)。size 入鍵：同 mtime 重寫（粗解析度 FS / 快速重寫）
# 時靠檔案大小變化仍能失效，避免服務舊資料（review finding）。
_cache: tuple[str, int, int, dict[str, Any]] | None = None


def read(seed: Path | None = None) -> dict[str, Any]:
    """GET /api/facilities：讀 static/facilities.seed.json。永遠回 dict（不 raise）。"""
    global _cache
    if seed is None:
        seed = FACILITIES_SEED
    try:
        st = seed.stat()
        mtime, size = st.st_mtime_ns, st.st_size
    except OSError:
        log.warning("[facilities_store] seed 不存在（%s），回最小空殼 — 正式部署應有 seed", seed)
        return dict(_EMPTY_SHELL)

    if _cache is not None and _cache[0] == str(seed) and _cache[1] == mtime and _cache[2] == size:
        return _cache[3]

    try:
        data = json.loads(seed.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning("[facilities_store] 讀 seed 失敗（%s）：%s，回最小空殼", seed, e)
        return dict(_EMPTY_SHELL)

    if not isinstance(data, dict):
        log.warning("[facilities_store] seed 非 dict（%s），回最小空殼", type(data).__name__)
        return dict(_EMPTY_SHELL)

    _cache = (str(seed), mtime, size, data)
    return data
