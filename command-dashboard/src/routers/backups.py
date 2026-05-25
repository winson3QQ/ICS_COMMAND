"""
routers/backups.py — Admin Backup 管理 API

對應 ROADMAP C3-D 第二階段（admin UI 配套）；NIST CP-9/CP-10、CIS §11。

設計：
- 所有 endpoint 需 Admin PIN（per `_check_admin_pin`）
- 不提供 production 還原 API（必須停服務 → CLI 操作；見 disaster_recovery.md）
- 只提供 read-only inspect（preview）+ 列表 + 觸發 + 驗證 + 還原指令產生
- 全部操作寫 audit_log
- License 解耦（per Decision E）：backup 是法規必要功能，全 tier 必開

Endpoints：
- GET  /api/admin/backups              列表 + 狀態
- POST /api/admin/backups              手動觸發 backup
- POST /api/admin/backups/{name}/verify   驗證 backup 完整性
- GET  /api/admin/backups/{name}/preview  read-only inspect（schema + table counts）
- GET  /api/admin/backups/{name}/restore-cmd  回傳 CLI 還原指令（複製給 admin 在 SSH 跑）
"""

from __future__ import annotations

import gzip
import shutil
import sqlite3
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from core.config import DB_PATH
from repositories._helpers import audit
from routers.admin import _check_admin_pin
from services.backup_service import (
    DEFAULT_RETAIN_DAYS,
    cleanup_old_backups,
    create_backup,
    list_backups,
    verify_backup,
)

router = APIRouter(prefix="/api/admin", tags=["備份"])

DB_PATH_PATH = Path(DB_PATH)
BACKUP_DIR = DB_PATH_PATH.parent / "backups"


def _resolve_backup(name: str) -> Path:
    """從 timestamp name (e.g. '2026-04-25T03-00-00Z') 解出 backup file。

    #41 Sync v3: auto-detect .db.gz vs .db.gz.enc (encrypted)
    """
    if "/" in name or ".." in name or "\\" in name:
        raise HTTPException(400, "name 含非法字元")
    # 優先嘗試 .enc (#41 加密 backup); fallback .db.gz (legacy / dev plain)
    enc = BACKUP_DIR / f"ics-{name}.db.gz.enc"
    plain = BACKUP_DIR / f"ics-{name}.db.gz"
    if enc.exists():
        return enc
    if plain.exists():
        return plain
    raise HTTPException(404, f"backup 不存在：{name}")


@router.get("/backups")
def list_all_backups(request: Request):
    """列出所有 backup（最新在前）+ 系統 backup 狀態。

    #41 Sync v3: 每個 backup item 含 encrypted flag (UI 顯示加密 indicator)
    """
    _check_admin_pin(request)
    backups = list_backups(BACKUP_DIR)
    items = []
    for b in reversed(backups):  # 最新在前
        # #41: 從 filename 判斷 encrypted (.db.gz.enc vs .db.gz)
        encrypted = b.path.name.endswith(".db.gz.enc")
        # name = timestamp 部分 (剝離 ics- prefix + .db.gz / .db.gz.enc suffix)
        name = b.path.name
        if name.startswith("ics-"):
            name = name[4:]
        if name.endswith(".db.gz.enc"):
            name = name[: -len(".db.gz.enc")]
        elif name.endswith(".db.gz"):
            name = name[: -len(".db.gz")]
        items.append(
            {
                "name": name,
                "filename": b.path.name,
                "timestamp": b.timestamp.isoformat(),
                "size_bytes": b.size_bytes,
                "encrypted": encrypted,  # #41: UI 顯示 encryption indicator
            }
        )
    return {
        "backups": items,
        "total": len(items),
        "retain_days": DEFAULT_RETAIN_DAYS,
        "backup_dir": str(BACKUP_DIR),
        "db_path": str(DB_PATH_PATH),
        "encryption_enabled": True,  # #41: feature flag for UI
    }


@router.post("/backups")
def trigger_backup(request: Request):
    """立即觸發 backup（不等 systemd timer）。"""
    _check_admin_pin(request)
    if not DB_PATH_PATH.exists():
        raise HTTPException(404, f"DB 不存在：{DB_PATH_PATH}")
    try:
        result = create_backup(DB_PATH_PATH, BACKUP_DIR)
    except Exception as e:
        audit(
            "admin",
            None,
            "backup_failed",
            "system",
            "backup",
            {"error": str(e), "db_path": str(DB_PATH_PATH)},
        )
        raise HTTPException(500, f"備份失敗：{e}") from e

    audit(
        "admin",
        None,
        "backup_created",
        "system",
        result.path.name,
        {
            "size_bytes": result.size_bytes,
            "sha256": result.sha256,
            "duration_ms": result.duration_ms,
            "trigger": "manual",
        },
    )

    # 順便 cleanup 過期 backup
    deleted = cleanup_old_backups(BACKUP_DIR)
    if deleted:
        audit(
            "admin",
            None,
            "backup_cleanup",
            "system",
            "rolling",
            {"deleted_count": len(deleted), "retain_days": DEFAULT_RETAIN_DAYS},
        )

    return {
        "name": result.path.stem.replace("ics-", "").replace(".db", ""),
        "filename": result.path.name,
        "size_bytes": result.size_bytes,
        "sha256": result.sha256,
        "duration_ms": result.duration_ms,
        "timestamp": result.timestamp.isoformat(),
        "cleanup_deleted": len(deleted),
    }


@router.post("/backups/{name}/verify")
def verify(name: str, request: Request):
    """驗證指定 backup 完整性（SQLite 可開 + schema_migrations 表存在）。"""
    _check_admin_pin(request)
    backup_path = _resolve_backup(name)
    ok = verify_backup(backup_path)
    audit(
        "admin",
        None,
        "backup_verified" if ok else "backup_verify_failed",
        "system",
        backup_path.name,
        {"ok": ok},
    )
    return {"name": name, "ok": ok}


@router.get("/backups/{name}/preview")
def preview_contents(name: str, request: Request):
    """Read-only 預覽 backup 內容：schema_migrations + 每個表的筆數（不解碼資料內容）。

    用途：稽核 / forensic — 看「2 個月前資料筆數」不接觸 PII 明文。
    """
    _check_admin_pin(request)
    backup_path = _resolve_backup(name)
    if not verify_backup(backup_path):
        raise HTTPException(400, "backup 無效或損毀，請先 verify")

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        with gzip.open(backup_path, "rb") as fin, tmp_path.open("wb") as fout:
            shutil.copyfileobj(fin, fout)

        conn = sqlite3.connect(str(tmp_path))
        try:
            tables = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ).fetchall()
            ]
            counts = {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}  # nosec B608 — t 來自 sqlite_master（系統 table 名稱），非使用者輸入
            migrations = [
                {"version": r[0], "name": r[1], "applied_at": r[2]}
                for r in conn.execute(
                    "SELECT version, name, applied_at FROM schema_migrations ORDER BY version"
                ).fetchall()
            ]
        finally:
            conn.close()
    finally:
        tmp_path.unlink(missing_ok=True)

    audit(
        "admin",
        None,
        "backup_previewed",
        "system",
        backup_path.name,
        {"tables": len(tables), "migrations": len(migrations)},
    )

    return {
        "name": name,
        "schema_migrations": migrations,
        "table_counts": counts,
        "total_rows": sum(counts.values()),
    }


@router.get("/backups/{name}/restore-cmd")
def restore_command(name: str, request: Request):
    """產生 production 還原 CLI 指令給 admin 複製（不直接執行 — 需停服務後手動跑）。"""
    _check_admin_pin(request)
    backup_path = _resolve_backup(name)
    audit(
        "admin",
        None,
        "backup_restore_cmd_issued",
        "system",
        backup_path.name,
        {"target_db": str(DB_PATH_PATH)},
    )

    cmd_lines = [
        "# === Production 還原步驟（複製到 SSH 跑；切勿直接執行）===",
        "# 1. 停指揮部服務（避免新寫入覆蓋）",
        "sudo systemctl stop ics-command",
        "",
        "# 2. 把當前 DB 保留為 forensic 副本",
        f"cp {DB_PATH_PATH} {DB_PATH_PATH}.suspect.$(date +%Y%m%dT%H%M%S)",
        "",
        "# 3. 還原 backup",
        f"cd {DB_PATH_PATH.parent.parent}  # 切到 command-dashboard",
        "PYTHONPATH=src python scripts/restore_db.py \\",
        f"    --backup {backup_path} \\",
        f"    --target {DB_PATH_PATH} \\",
        "    --overwrite",
        "",
        "# 4. 驗證 schema",
        f'sqlite3 {DB_PATH_PATH} "SELECT version, name FROM schema_migrations ORDER BY version;"',  # nosec B608 — 純說明文字字串，不被 Python 執行為 SQL
        "",
        "# 5. 啟服務",
        "sudo systemctl start ics-command",
        "curl -k https://localhost:8000/api/health",
        "",
        "# 6. 完成後在 admin 介面記一筆 audit（注明還原來源 + 原因）",
    ]

    return {
        "name": name,
        "backup_path": str(backup_path),
        "target_db": str(DB_PATH_PATH),
        "cli_command": "\n".join(cmd_lines),
        "playbook_ref": "docs/ops/disaster_recovery.md",
    }
