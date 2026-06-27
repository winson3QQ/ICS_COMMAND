#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
backup_db.py — CLI entry point for SQLite 自動備份

systemd timer 直接呼叫此腳本（每日 02:00, #41 B-1）。
邏輯實作在 src/services/backup_service.py。

使用方式：
    python scripts/backup_db.py                  # 預設路徑 + 加密 (production)
    python scripts/backup_db.py --no-encrypt     # dev mode 不加密
    python scripts/backup_db.py --retain-days 60 # 自訂保留天數
    python scripts/backup_db.py --verify-only    # 只驗證最新 backup，不新建

⭐ #41 Backup DR Drill: --encrypt default true (E-1, NIST SC-28)
   key 從 BACKUP_ENCRYPTION_KEY env var 讀取 (E-3)
   無 key 時 default 加密路徑會 fail-fast — 避免靜默 plain backup
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# 加入 src 到 path（讓 systemd 從 command-dashboard/ 啟動時可 import）
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from services.backup_service import (  # noqa: E402
    DEFAULT_RETAIN_DAYS,
    cleanup_old_backups,
    create_backup,
    encrypt_file,
    list_backups,
    verify_backup,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="ICS_DMAS 指揮部 SQLite backup")
    parser.add_argument(
        "--db-path",
        type=Path,
        default=ROOT / "data" / "ics.db",
        help="來源 DB 路徑（預設 data/ics.db）",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=ROOT / "data" / "backups",
        help="backup 輸出目錄（預設 data/backups/）",
    )
    parser.add_argument(
        "--retain-days",
        type=int,
        default=DEFAULT_RETAIN_DAYS,
        help=f"保留天數（預設 {DEFAULT_RETAIN_DAYS} 天）",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="只驗證最新 backup，不新建",
    )
    parser.add_argument(
        "--no-cleanup",
        action="store_true",
        help="跳過過期 backup 清理",
    )
    # #41 Backup DR Drill: 加密 flag (default true, dev 可 --no-encrypt)
    parser.add_argument(
        "--encrypt",
        dest="encrypt",
        action="store_true",
        default=True,
        help="加密 backup (Fernet AES-128-CBC, default true; production 必開)",
    )
    parser.add_argument(
        "--no-encrypt",
        dest="encrypt",
        action="store_false",
        help="關閉加密 (dev mode only, production 必加密)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )
    log = logging.getLogger("ics.backup.cli")

    if args.verify_only:
        backups = list_backups(args.backup_dir)
        if not backups:
            log.error("no backup found in %s", args.backup_dir)
            return 1
        latest = backups[-1]
        ok = verify_backup(latest.path)
        if ok:
            log.info("verify_ok path=%s size=%d", latest.path, latest.size_bytes)
            return 0
        log.error("verify_failed path=%s", latest.path)
        return 2

    try:
        result = create_backup(args.db_path, args.backup_dir)
        log.info(
            "backup_done path=%s size=%d sha256=%s duration_ms=%d encrypted=%s",
            result.path,
            result.size_bytes,
            result.sha256,
            result.duration_ms,
            args.encrypt,
        )
        # #41 加密層 (E-1): create_backup() 完成 .db.gz 後, encrypt_file 包成 .db.gz.enc
        if args.encrypt:
            encrypted_path = result.path.with_suffix(result.path.suffix + ".enc")
            try:
                encrypt_file(result.path, encrypted_path)
                # 加密成功後刪除 plaintext .db.gz (避免 plaintext + ciphertext 並存)
                result.path.unlink(missing_ok=True)
                log.info(
                    "backup_encrypted path=%s plaintext_removed=%s",
                    encrypted_path,
                    result.path,
                )
            except RuntimeError as e:
                # BACKUP_ENCRYPTION_KEY env var 未設 — fail-fast (避免 production 靜默 plain backup)
                log.error("backup_encrypt_failed reason=%s plaintext_kept=%s", e, result.path)
                return 5
    except FileNotFoundError as e:
        log.error("backup_skip reason=db_missing %s", e)
        return 3
    except Exception as e:
        log.exception("backup_failed reason=%s", e)
        return 4

    if not args.no_cleanup:
        deleted = cleanup_old_backups(args.backup_dir, retain_days=args.retain_days)
        if deleted:
            log.info("cleanup_done deleted=%d retain_days=%d", len(deleted), args.retain_days)

    backups = list_backups(args.backup_dir)
    log.info(
        "backup_summary total=%d oldest=%s newest=%s",
        len(backups),
        backups[0].timestamp.isoformat() if backups else None,
        backups[-1].timestamp.isoformat() if backups else None,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
