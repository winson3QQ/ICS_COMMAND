"""event_taxonomy_store.py — `event_taxonomy.json` runtime 檔的讀寫 + seed fallback。

P1-10d 地基（issue #60 / #66）：把事件分類（NAPSG_EVENTS / NAPSG_GROUPS）從寫死的
JS 常數改為 seed/runtime 可編資料，讓 admin 編輯器（#66）能 CRUD。沿用 map_config
的 seed/runtime 模式（見 services/map_config_store.py，P1-13）：

1. **ensure()**：startup 呼叫；runtime path 不存在 → 從 seed 複製（idempotent）。
2. **read()**：給 GET /api/event_taxonomy 用；runtime → seed → 空殼 三層 fallback。
3. **write_atomic(body)**：給 POST /api/event_taxonomy 用；tmp + fsync + os.replace 原子寫。

註：XSS / schema validation 在 router 層（API 守門），本層只負責 disk 邊界與原子寫。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any

from core.config import EVENT_TAXONOMY_PATH, EVENT_TAXONOMY_SEED

log = logging.getLogger(__name__)

# 極端 fallback：seed 也不存在時的最小空殼（保證後端不 NPE / GET 不 500）。
_EMPTY_SHELL: dict[str, Any] = {"version": 1, "groups": [], "events": []}


def ensure(path: Path | None = None, seed: Path | None = None) -> Path:
    """Startup：runtime path 不存在 → 從 seed 複製（idempotent）。

    參數可覆寫供 unit test 用 tmp_path 隔離；預設 None + 內部讀模組常數，避免
    default arg 在 def 時 bind 造成 monkeypatch 失效（沿用 map_config_store 經驗）。
    """
    if path is None:
        path = EVENT_TAXONOMY_PATH
    if seed is None:
        seed = EVENT_TAXONOMY_SEED
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return path
    if seed.exists():
        shutil.copyfile(seed, path)
        log.info("[event_taxonomy_store] copied seed → runtime: %s → %s", seed, path)
    else:
        path.write_text(
            json.dumps(_EMPTY_SHELL, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log.warning(
            "[event_taxonomy_store] seed 不存在（%s），寫最小空殼到 %s — 正式部署應有 seed",
            seed,
            path,
        )
    return path


# seed-authoritative 事實欄：舊 runtime（在這些欄加入前建立）會缺，ensure() 又不回填。
# source（#66 定義來源）、regime（#241 視覺規制軸）皆屬此類 → 依 key 從 seed 補。
_SEED_FACT_FIELDS = ("source", "regime")


def _backfill_seed_facts(data: dict[str, Any], seed: Path) -> dict[str, Any]:
    """為缺 seed-authoritative 事實欄（source / regime）的 runtime event 從 seed 補。

    舊 runtime（在 source / regime 欄加入前建立）不會有此欄；ensure() 又不回填 → 編輯器/
    render 顯示缺值。此處依 key 從 seed 補，使其一律正確，無需每台重建 runtime。存檔後隨
    整包寫回 runtime 持久化。in-place，僅補缺漏不覆蓋既有值。
    """
    try:
        if not seed.exists():
            return data
        seed_data = json.loads(seed.read_text(encoding="utf-8"))
        seed_by_key = {
            e["key"]: e
            for e in seed_data.get("events", [])
            if isinstance(e, dict) and e.get("key")
        }
    except (OSError, json.JSONDecodeError):
        return data
    for e in data.get("events", []):
        if not isinstance(e, dict):
            continue
        seed_e = seed_by_key.get(e.get("key"))
        if not seed_e:
            continue
        for field in _SEED_FACT_FIELDS:
            if not e.get(field) and seed_e.get(field):
                e[field] = seed_e[field]
    return data


def read(path: Path | None = None, seed: Path | None = None) -> dict[str, Any]:
    """GET /api/event_taxonomy：runtime → seed → 空殼。永遠回 dict（不 raise）。"""
    if path is None:
        path = EVENT_TAXONOMY_PATH
    if seed is None:
        seed = EVENT_TAXONOMY_SEED
    for candidate, label in [(path, "runtime"), (seed, "seed")]:
        try:
            if candidate.exists():
                data = json.loads(candidate.read_text(encoding="utf-8"))
                # 守 dict 契約：valid JSON 但非 dict（如手改成 []）→ 不回，續 fallback
                if isinstance(data, dict):
                    # runtime 缺 source / regime 從 seed 回填（seed 自身已含，不需補）
                    if label == "runtime":
                        data = _backfill_seed_facts(data, seed)
                    return data
                log.warning(
                    "[event_taxonomy_store] %s 非 dict（%s），續 fallback",
                    label, type(data).__name__,
                )
        except (OSError, json.JSONDecodeError) as e:
            log.warning(
                "[event_taxonomy_store] 讀 %s 失敗（%s），嘗試下一層：%s",
                label,
                candidate,
                e,
            )
    log.warning("[event_taxonomy_store] runtime + seed 都讀不到，回最小空殼")
    return dict(_EMPTY_SHELL)


def write_atomic(body: dict[str, Any], path: Path | None = None) -> None:
    """POST /api/event_taxonomy：tmp → fsync → os.replace → parent dir fsync（防半檔）。"""
    if path is None:
        path = EVENT_TAXONOMY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(body, ensure_ascii=False, indent=2).encode("utf-8")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    # parent dir fsync 讓 rename 也 persist（Pi 拔電場景）。best-effort：Windows 不支援
    # 對目錄 fsync（PermissionError），檔案 fsync + os.replace 已保證原子性，故吞掉即可。
    try:
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError as e:
        log.debug("[event_taxonomy_store] parent dir fsync 略過/失敗（非致命）：%s", e)
