#!/usr/bin/env python3
"""
migrate_map_config.py — P1-13 既有部署一鍵 migration

把舊位置 `static/map_config.json`（tracked + runtime-mutated）的**使用者資料**
搬到新位置 `data/map_config.json`（gitignored runtime）。

呼叫順序（idempotent，可重跑）：
  1. 若 NEW 已存在 → "already migrated"，no-op exit 0
  2. 若 OLD 存在 → 複製到 NEW（保留 user 資料；若 SEED 也不存在則同時 bootstrap SEED）
  3. 若 OLD 不存在但 SEED 存在 → 複製 SEED 到 NEW（fresh install 路徑）
  4. 都不存在 → 印警告但不寫（service ensure() 會在 server 啟動時補空殼）

**本 script 不執行 git op**（`git rm --cached`、`git add` 都不做）— 遵守 CLAUDE.md
規則，git 操作必須使用者明確指示。Script 跑完後，user 自行決定要不要把 OLD 從 git
追蹤拿掉。

使用：
    python3 command-dashboard/scripts/migrate_map_config.py
    python3 command-dashboard/scripts/migrate_map_config.py --dry-run
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

# 加入 src/ 到 path（systemd 從 command-dashboard/ 啟動時可 import）
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from core.config import MAP_CONFIG_PATH, MAP_CONFIG_SEED, STATIC_DIR  # noqa: E402

OLD_PATH = STATIC_DIR / "map_config.json"


def migrate(dry_run: bool = False) -> int:
    """Return 0 on success / no-op，非 0 on warning（都不算 error）。"""
    print(f"OLD:  {OLD_PATH}  exists={OLD_PATH.exists()}")
    print(f"SEED: {MAP_CONFIG_SEED}  exists={MAP_CONFIG_SEED.exists()}")
    print(f"NEW:  {MAP_CONFIG_PATH}  exists={MAP_CONFIG_PATH.exists()}")
    print()

    # case 1：NEW 已存在 → idempotent no-op
    if MAP_CONFIG_PATH.exists():
        print("[skip] runtime 已存在，已 migrate 過，no-op exit 0")
        return 0

    if dry_run:
        print("[dry-run] 不會執行實際 copy")

    # case 2：OLD 存在 → 搬 user 資料
    if OLD_PATH.exists():
        if not dry_run:
            MAP_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(OLD_PATH, MAP_CONFIG_PATH)
            print(f"[migrated] user data: {OLD_PATH} → {MAP_CONFIG_PATH}")
            # SEED 不存在則順手 bootstrap（first-time deploy 路徑）
            if not MAP_CONFIG_SEED.exists():
                shutil.copyfile(OLD_PATH, MAP_CONFIG_SEED)
                print(f"[bootstrap] seed 也不存在，順手 copy: {OLD_PATH} → {MAP_CONFIG_SEED}")
        else:
            print(f"[dry-run] 會 copy: {OLD_PATH} → {MAP_CONFIG_PATH}")
        return 0

    # case 3：OLD 沒有但 SEED 有 → fresh install
    if MAP_CONFIG_SEED.exists():
        if not dry_run:
            MAP_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(MAP_CONFIG_SEED, MAP_CONFIG_PATH)
            print(f"[fresh] seed → runtime: {MAP_CONFIG_SEED} → {MAP_CONFIG_PATH}")
        else:
            print(f"[dry-run] 會 copy: {MAP_CONFIG_SEED} → {MAP_CONFIG_PATH}")
        return 0

    # case 4：都沒有 — 警告但不寫（讓 service ensure() 在 server 啟動時補）
    print(
        "[warn] OLD + SEED 都不存在；server 啟動時 map_config_store.ensure() "
        "會寫最小空殼。建議檢查 deployment 為何缺 seed。"
    )
    return 2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="只印計畫，不實際 copy")
    args = ap.parse_args()
    return migrate(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
