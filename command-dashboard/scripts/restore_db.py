#!/usr/bin/env python3
"""
restore_db.py — CLI entry point for SQLite backup 還原

對應 docs/ops/disaster_recovery.md 情境 A/B 步驟 4。
邏輯實作在 src/services/backup_service.py。

使用方式：
    # 還原到新位置（safe, plaintext .db.gz）
    python scripts/restore_db.py --backup data/backups/X.db.gz --target /tmp/test.db

    # 還原 #41 加密 backup (.db.gz.enc) — auto-detect
    python scripts/restore_db.py --backup data/backups/X.db.gz.enc --target /tmp/test.db
    # 或顯式 --decrypt
    python scripts/restore_db.py --backup data/backups/X.db.gz.enc --decrypt --target /tmp/test.db

    # 覆寫 production DB（destructive — 必須明確帶 --overwrite）
    python scripts/restore_db.py --backup data/backups/X.db.gz.enc \\
        --target data/ics.db --overwrite

注意：
- 必須先停服務（systemctl stop ics-command）
- 必須先備份當前 DB（cp data/ics.db data/ics.db.suspect.<timestamp>）
- 還原後驗證 schema_migrations + events 計數再啟服務
- ⭐ #41 加密 backup: BACKUP_ENCRYPTION_KEY env var 必須設 (E-3)
"""

from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from services.backup_service import (  # noqa: E402
    decrypt_file,
    list_backups,
    restore_backup,
    verify_backup,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="ICS_DMAS 從 backup 還原 SQLite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--backup", type=Path, required=True, help="backup 檔（.db.gz 或 .db.gz.enc）")
    parser.add_argument("--target", type=Path, required=True, help="還原目標路徑")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="允許覆寫已存在的 target（destructive — 確認已備份當前 DB）",
    )
    parser.add_argument("--list", action="store_true", help="列出 backup_dir 所有 backup")
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=ROOT / "data" / "backups",
        help="backup 目錄（搭配 --list；預設 data/backups/）",
    )
    # #41 Backup DR Drill: --decrypt flag (auto-detect by .enc suffix)
    parser.add_argument(
        "--decrypt",
        action="store_true",
        help="顯式 decrypt (default auto-detect by .enc suffix; key 從 BACKUP_ENCRYPTION_KEY env var)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )
    log = logging.getLogger("ics.restore.cli")

    if args.list:
        backups = list_backups(args.backup_dir)
        if not backups:
            print(f"(no backup in {args.backup_dir})")
            return 0
        for b in backups:
            print(f"{b.timestamp.isoformat()}  {b.size_bytes:>10}  {b.path.name}")
        return 0

    # #41 加密 backup: auto-detect by .enc suffix; 先 decrypt 到 tmp 再 restore
    is_encrypted = args.backup.name.endswith(".enc") or args.decrypt
    actual_backup = args.backup
    tmp_decrypted: Path | None = None

    if is_encrypted:
        try:
            tmp_decrypted = Path(tempfile.mktemp(prefix="restore-decrypt-", suffix=".db.gz"))
            decrypt_file(args.backup, tmp_decrypted)
            actual_backup = tmp_decrypted
            log.info("decrypted_to_tmp src=%s tmp=%s", args.backup, tmp_decrypted)
        except RuntimeError as e:
            log.error("decrypt_failed reason=%s (BACKUP_ENCRYPTION_KEY env var 未設?)", e)
            return 4
        except Exception as e:
            log.exception("decrypt_failed reason=%s (key 不對 / 檔案損毀?)", e)
            return 5

    try:
        if not verify_backup(actual_backup):
            log.error("backup 無效或損毀：%s", args.backup)
            return 1

        if args.target.exists() and not args.overwrite:
            log.error(
                "目標已存在：%s — 確認你已備份當前 DB，再加 --overwrite 重跑",
                args.target,
            )
            return 2

        restored = restore_backup(actual_backup, args.target, overwrite=args.overwrite)
        log.info(
            "restore_done from=%s to=%s size=%d encrypted_src=%s",
            args.backup,
            restored,
            restored.stat().st_size,
            is_encrypted,
        )
        log.info("驗證指令：sqlite3 %s 'SELECT version FROM schema_migrations;'", restored)
        return 0
    except Exception as e:
        log.exception("restore_failed reason=%s", e)
        return 3
    finally:
        # 清理解密後的 tmp file (security: 不留 plaintext on disk)
        if tmp_decrypted is not None and tmp_decrypted.exists():
            tmp_decrypted.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
