"""map_config_store.py — `map_config.json` runtime 檔的讀寫 + seed fallback。

P1-13（issue #27）：把 `map_config.json` 從 tracked + runtime-mutated 改為
seed/runtime 分離。本 service 負責三件事：

1. **ensure()**：startup 階段呼叫；runtime path 不存在則從 seed 複製過去。
2. **read()**：給 GET /api/map_config 用；runtime path 必存在（ensure 已跑過），
   讀失敗（檔被 user 手動刪）則再嘗試 seed fallback。
3. **write_atomic(body)**：給 POST /api/map_config 用；寫 tmp + os.replace 保證
   不會半寫（防 crash mid-write 造成空檔重演 issue #24 dogfood 事件）。
4. **compare_and_swap(expected, body, actor)**：β（issue #29）Phase 1 — version
   樂觀鎖。在 process-wide asyncio.Lock 內讀現值 → 比對 version → 不符回 (False,
   current) 讓 caller 回 409；相符則 bump version + 寫入。**單 uvicorn worker**
   假設下這個 in-process lock 才有效（systemd ExecStart 已 pin --workers 1）。

註：本 service **不做 XSS validation** — 那是 routers/map.py 的職責（API 層守門）。
本層只負責 disk 邊界、原子寫、version 樂觀鎖。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any

from core.config import MAP_CONFIG_PATH, MAP_CONFIG_SEED

log = logging.getLogger(__name__)

# β（issue #29）Phase 1：process-wide write lock。compare_and_swap 的 read→check→write
# 必須 atomic（不然兩個並發 POST 都讀到 v17、都 pass check、都寫 v18，後者靜默覆蓋前者）。
# 單 uvicorn worker 下 in-process Lock 足夠；多 worker 會破（需 Redis），故 systemd pin
# --workers 1（見 systemd/ics-command.service 註解）。
_write_lock = asyncio.Lock()

# 極端 fallback：seed 也不存在時用最小空殼（保證後端不會 NPE）。
# 正式部署不該走到這條，但 dev / fresh clone 時 seed 可能還沒 commit。
# version=0：β Phase 1 起所有 map_config 帶 version 整數（ETag 樂觀鎖用）。
_EMPTY_SHELL: dict[str, Any] = {
    "version": 0,
    "maps": {
        "indoor": {"zones": []},
        "outdoor": {"zones": []},
    },
}


def ensure(
    path: Path | None = None,
    seed: Path | None = None,
) -> Path:
    """Startup 階段呼叫。runtime path 不存在 → 從 seed 複製過去（idempotent）。

    參數允許覆寫是為了 unit test 用 tmp_path 隔離真實檔案系統。
    **預設 None + 內部讀模組級常數** — 不直接用 default arg 是因為 default
    在 function def 時就 bind，pytest conftest monkeypatch 模組屬性後仍指向
    原始 Path，造成 isolate fixture 失效（PR #28 code-review HIGH 1）。
    """
    if path is None:
        path = MAP_CONFIG_PATH
    if seed is None:
        seed = MAP_CONFIG_SEED
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
    path: Path | None = None,
    seed: Path | None = None,
) -> dict[str, Any]:
    """GET /api/map_config：讀 runtime；讀失敗再 fallback seed；都失敗回空殼。

    永遠回 dict（不 raise），這樣 GET 不會 500，frontend 拿到空殼也能 render
    （比 NPE 好）。詳細錯誤走 log。
    """
    if path is None:
        path = MAP_CONFIG_PATH
    if seed is None:
        seed = MAP_CONFIG_SEED
    for candidate, label in [(path, "runtime"), (seed, "seed")]:
        try:
            if candidate.exists():
                body = json.loads(candidate.read_text(encoding="utf-8"))
                # β Phase 1：legacy map_config（P1-13 之前 / seed）無 version → 補 0。
                # 讓 ETag 樂觀鎖對舊檔也有一致起點（第一次 POST 會 bump 到 1）。
                if isinstance(body, dict) and "version" not in body:
                    body["version"] = 0
                return body
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
    path: Path | None = None,
) -> None:
    """POST /api/map_config：write to .tmp → fsync → os.replace → fsync parent dir。

    防 crash mid-write 半檔（issue #24 dogfood 撞過：runtime 中斷 → 空檔，
    next read 就以為使用者把所有東西刪光）。

    fsync 完整鏈（PR #28 code-review MEDIUM 3）：os.replace 只保證 rename
    metadata atomic；power loss 仍可能 metadata 已 commit 但 inode 內容是
    空 / 截斷。對 Pi 拔電場景必須加 (1) tmp fsync (2) parent dir fsync。
    """
    if path is None:
        path = MAP_CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    # 用 os.open + write + fsync + close 確保 inode 內容 persist 到 disk
    payload = json.dumps(body, ensure_ascii=False, indent=2).encode("utf-8")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)  # 原子 — POSIX rename 保證同 filesystem 內 atomic
    # fsync 父目錄讓 rename 也 persist（不然 power loss 後 rename 可能消失）
    dir_fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


async def compare_and_swap(
    expected_version: int,
    new_body: dict[str, Any],
    actor: str = "",
) -> tuple[bool, dict[str, Any]]:
    """β（issue #29）Phase 1：version 樂觀鎖。

    在 process-wide `_write_lock` 內 read → 比對 version → 寫。回 (ok, body)：
      - ok=True：version 相符，已寫入。body = 寫入後的新 body（version 已 +1）。
      - ok=False：version 衝突。body = 當前 disk 上的完整 body（含 server version），
        讓 router 回 409 + 把這份送回給 client 直接 render，不必再 fetch 一次。

    為什麼整段在 lock 內：read→check→write 必須 atomic，否則兩並發 POST 都讀到 v17、
    都 pass check、都寫 v18，後者靜默覆蓋前者（正是 commit 58bb5d4 race 的 server 版）。

    `read()` 是 sync disk I/O，在 async lock 內呼叫會短暫阻塞 event loop（map_config
    < 256 KB，Pi NVMe/SD 毫秒級，可接受）。要更乾淨可 run_in_executor，Phase 1 不強求。
    """
    async with _write_lock:
        current = read()
        current_version = current.get("version", 0)
        if current_version != expected_version:
            return False, current
        # 相符 → bump version + 注入 audit metadata，寫入
        next_body = dict(new_body)
        next_body["version"] = current_version + 1
        next_body["updated_at"] = _now_iso()
        next_body["updated_by"] = actor or "unknown"
        write_atomic(next_body)
        return True, next_body


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
