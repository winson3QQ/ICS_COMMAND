# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
core/input_safety.py — 使用者輸入 XSS 硬化（共用）

issue #24 起的防護：前端多處是既有 HTML sink（map.js innerHTML、Leaflet
bindTooltip、cop.js openModal title 等）。operator 可寫的字串若含 `<img onerror=...>`
或 `javascript:` URL，會在上層 role（commander / sysadmin）載入時執行 → 提權路徑。
策略是在「寫入 disk / DB 前」遞迴掃過所有 string value，命中危險字元就 422 reject。

issue #29 PR-B：原本此 validator 私有於 routers/map.py，現抽到共用模組，供
map_config（routers/map.py）與 cop entity（routers/cop.py）兩條寫入路徑共用 ——
未來任一處新增 sink，補在這裡即同時保護兩邊，避免兩份 regex 走樣。

策略選擇：recursive validator 而非 Pydantic schema —
(1) payload 結構鬆散（map_config legacy 欄位、cop attributes escape-hatch dict）
(2) 攻擊面只在 string content，不在 structure
(3) 未來新增欄位自動被保護，不需回頭補 schema
"""

import re

from fastapi import HTTPException

_UNSAFE_CHAR_RE = re.compile(r"[<>`{}]|&#|&\w+;|javascript:|data:|vbscript:", re.IGNORECASE)
DEFAULT_MAX_STRING_LEN = 512


def validate_no_unsafe_strings(
    obj: object,
    path: str = "$",
    *,
    label: str = "payload",
    max_len: int = DEFAULT_MAX_STRING_LEN,
) -> None:
    """遞迴檢查 obj 內所有 string value 不含 HTML / JS context-escape 危險字元，
    且長度不超過 max_len。違反 → HTTPException(422)，呼叫端不寫入。

    numbers / bool / None 視為安全 pass。dict 的 key 也檢（雖少被 render，但同樣
    可能流入 sink）。label 用於錯誤訊息前綴（例：'map_config' / 'cop_entity'）。
    """
    if isinstance(obj, str):
        if len(obj) > max_len:
            raise HTTPException(422, f"{label}: 字串過長（{len(obj)} > {max_len}）at {path}")
        if _UNSAFE_CHAR_RE.search(obj):
            raise HTTPException(422, f"{label}: 含 HTML / JS 危險字元 at {path}：{obj[:50]!r}")
    elif isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(key, str) and _UNSAFE_CHAR_RE.search(key):
                raise HTTPException(422, f"{label}: 危險 key at {path}：{key!r}")
            validate_no_unsafe_strings(value, f"{path}.{key}", label=label, max_len=max_len)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            validate_no_unsafe_strings(item, f"{path}[{i}]", label=label, max_len=max_len)
