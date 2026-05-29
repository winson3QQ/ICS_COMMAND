"""map_config_store.py — `map_config.json` runtime 檔的讀寫 + seed fallback。

P1-13（issue #27）：把 `map_config.json` 從 tracked + runtime-mutated 改為
seed/runtime 分離。本 service 負責三件事：

1. **ensure()**：startup 階段呼叫；runtime path 不存在則從 seed 複製過去。
2. **read()**：給 GET /api/map_config 用；runtime path 必存在（ensure 已跑過），
   讀失敗（檔被 user 手動刪）則再嘗試 seed fallback。
3. **write_atomic(body)**：給 POST /api/map_config 用；寫 tmp + os.replace 保證
   不會半寫（防 crash mid-write 造成空檔重演 issue #24 dogfood 事件）。

註：本 service **不做 XSS validation** — 那是 routers/map.py 的職責（API 層守門）。
本層只負責 disk 邊界與原子寫。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any

from core.config import MAP_CONFIG_PATH, MAP_CONFIG_SEED

log = logging.getLogger(__name__)

# 極端 fallback：seed 也不存在時用最小空殼（保證後端不會 NPE）。
# 正式部署不該走到這條，但 dev / fresh clone 時 seed 可能還沒 commit。
_EMPTY_SHELL: dict[str, Any] = {
    "maps": {
        "indoor": {"zones": []},
        "outdoor": {"zones": []},
    }
}


def ensure(
    path: Path = MAP_CONFIG_PATH,
    seed: Path = MAP_CONFIG_SEED,
) -> Path:
    """Startup 階段呼叫。runtime path 不存在 → 從 seed 複製過去（idempotent）。

    參數允許覆寫是為了 unit test 用 tmp_path 隔離真實檔案系統。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        log.debug("[map_config_store] runtime exists, skip ensure: %s", path)
        return path
    if seed.exists():
        shutil.copyfile(seed, path)
        log.info("[map_config_store] copied seed → runtime: %s → %s", seed, path)
    else:
        # seed 也沒 → 寫最小空殼，**不要靜默不做事**（否則 GET 會 500）
        path.write_text(
            json.dumps(_EMPTY_SHELL, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log.warning(
            "[map_config_store] seed 不存在（%s），寫入最小空殼到 %s — 正式部署應該要有 seed",
            seed,
            path,
        )
    return path


def read(
    path: Path = MAP_CONFIG_PATH,
    seed: Path = MAP_CONFIG_SEED,
) -> dict[str, Any]:
    """GET /api/map_config：讀 runtime；讀失敗再 fallback seed；都失敗回空殼。

    永遠回 dict（不 raise），這樣 GET 不會 500，frontend 拿到空殼也能 render
    （比 NPE 好）。詳細錯誤走 log。
    """
    for candidate, label in [(path, "runtime"), (seed, "seed")]:
        try:
            if candidate.exists():
                return json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            log.warning(
                "[map_config_store] 讀 %s 失敗（%s），嘗試下一層 fallback：%s",
                label,
                candidate,
                e,
            )
    log.warning("[map_config_store] runtime + seed 都讀不到，回最小空殼")
    return dict(_EMPTY_SHELL)  # defensive copy


def write_atomic(
    body: dict[str, Any],
    path: Path = MAP_CONFIG_PATH,
) -> None:
    """POST /api/map_config：write to .tmp → os.replace 為原子操作。

    crash mid-write 不會留下半檔（issue #24 dogfood 撞過：runtime 中斷 → 空檔，
    next read 就以為使用者把所有東西刪光）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(body, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp, path)  # 原子 — POSIX rename 保證同 filesystem 內 atomic
