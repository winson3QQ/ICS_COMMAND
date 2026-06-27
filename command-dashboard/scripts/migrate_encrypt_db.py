#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
migrate_encrypt_db.py — 明文 SQLite → SQLCipher 加密 DB 一次性轉換（P1-12c #229）

把現有明文 `data/ics.db` 轉成 SQLCipher 加密 DB（in-place swap，原檔留 .pre-encrypt 備援）。
邏輯收口在 src/core/database.py `encrypt_db`（sqlcipher_export）。

使用方式：
    # 1) 先停服務（避免並發寫入）
    systemctl stop ics-command            # 或對應啟動方式

    # 2) 提供 DB_KEY（HKDF child[1] db-v1，64 hex）——由 P1-12a unlock_key.py 產：
    #    python scripts/keymgmt/unlock_key.py --output /run/ics/keys.env && source /run/ics/keys.env
    #    （dev：ICS_MASTER_KEY=<64hex> 走 fallback）

    # 3) 轉換（預設對 core.config.DB_PATH；可 --db 覆寫）
    DB_KEY=<64hex> python scripts/migrate_encrypt_db.py

    # 4) 啟服務時設 ICS_DB_ENCRYPTED=1 + DB_KEY，驗 schema_migrations 後再清 .pre-encrypt 備援

性質：
- **idempotent**：DB 已是 SQLCipher（無 SQLite 明文 magic header）→ 跳過、return 0
- **fail-safe**：先產加密暫存 + 驗證可解可讀，才 atomic swap；原明文留 `<db>.pre-encrypt-<ts>`
- Windows 無 sqlcipher3 wheel（已實測）→ 本機跑不了；於 Linux 部署機 / CI（ubuntu）執行
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from core.config import DB_PATH  # noqa: E402
from core.database import _db_key, encrypt_db  # noqa: E402

# 明文 SQLite 檔頭 magic（SQLCipher 預設連檔頭都加密 → 缺此 magic 視為已加密）
_SQLITE_MAGIC = b"SQLite format 3\x00"


def _is_plaintext_sqlite(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            return f.read(16) == _SQLITE_MAGIC
    except OSError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="明文 SQLite → SQLCipher 加密一次性轉換（P1-12c）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"目標 DB（預設 {DB_PATH}）")
    parser.add_argument(
        "--keep-plaintext",
        action="store_true",
        help="保留原明文備援 .pre-encrypt-<ts>（預設保留；此旗標僅為語意明示，不自動刪）",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
    log = logging.getLogger("ics.migrate.encrypt")

    db: Path = args.db
    if not db.exists():
        log.error("DB 不存在：%s", db)
        return 2

    # DB_KEY 必須先備好（早失敗，免做半套）
    try:
        _db_key()
    except RuntimeError as e:
        log.error("%s", e)
        return 4

    if not _is_plaintext_sqlite(db):
        log.info("DB 非明文 SQLite（無 magic header）—— 視為已加密，跳過（idempotent）：%s", db)
        return 0

    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    tmp_enc = db.with_name(f"{db.name}.enc-tmp-{ts}")
    backup_plain = db.with_name(f"{db.name}.pre-encrypt-{ts}")

    if tmp_enc.exists():
        tmp_enc.unlink()

    try:
        log.info("加密轉換中：%s → %s", db, tmp_enc)
        encrypt_db(db, tmp_enc)

        # 驗證：加密暫存可用 key 開且讀得到 schema_migrations（轉換完整性）
        _verify_encrypted(tmp_enc, log)

        # atomic swap：原明文先改名留備援，再把加密暫存就位
        db.replace(backup_plain)
        tmp_enc.replace(db)
        # 明文 WAL/SHM 屬舊檔，加密 DB 為全新單檔；清掉避免誤用
        for suffix in ("-wal", "-shm"):
            sidecar = db.with_name(db.name + suffix)
            sidecar.unlink(missing_ok=True)
    except Exception as e:
        tmp_enc.unlink(missing_ok=True)
        # backup_plain 若已建立代表 swap 後段失敗 → 還原原檔，保證 current 不毀
        if backup_plain.exists() and not db.exists():
            backup_plain.replace(db)
        log.exception("加密轉換失敗，current DB 未動：%s", e)
        return 3

    log.info("✓ 加密完成：%s", db)
    log.info("  原明文備援：%s（驗證服務正常起、schema 對得上後再安全刪除）", backup_plain)
    log.info("  啟動需設：ICS_DB_ENCRYPTED=1 + DB_KEY")
    return 0


def _verify_encrypted(enc_path: Path, log: logging.Logger) -> None:
    """以 DB_KEY 開加密暫存並讀 schema_migrations，證明轉換可逆可讀。"""
    from core.database import _apply_key, _import_sqlcipher

    sqlcipher3 = _import_sqlcipher()
    conn = sqlcipher3.connect(str(enc_path))
    try:
        _apply_key(conn, _db_key())
        row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone()
        if row is None:
            raise RuntimeError("加密暫存缺 schema_migrations 表 — 轉換不完整")
        ver = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0]
        log.info("  驗證 OK：加密 DB 可解、schema_migrations max version=%s", ver)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
