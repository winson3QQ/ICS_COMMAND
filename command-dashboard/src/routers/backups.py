"""
routers/backups.py — Admin Backup 管理 API

對應 ROADMAP C3-D 第二階段（admin UI 配套）；NIST CP-9/CP-10、CIS §11。

設計：
- 所有 endpoint 限 sysadmin（per `_check_system_admin`，session 角色把關）
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
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from core.config import DB_PATH
from repositories._helpers import audit
from routers.admin import _check_system_admin
from services.backup_service import (
    BACKUP_ENCRYPTION_KEY_ENV,
    BACKUP_KEY_ENV,
    DEFAULT_RETAIN_DAYS,
    _sha256_file,
    cleanup_old_backups,
    create_backup,
    encrypt_file,
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
    _check_system_admin(request)
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
    _check_system_admin(request)
    if not DB_PATH_PATH.exists():
        raise HTTPException(404, f"DB 不存在：{DB_PATH_PATH}")
    try:
        result = create_backup(DB_PATH_PATH, BACKUP_DIR)
        # P1-12b（#228）drift 修正：API 觸發過去產**明文** .db.gz（CLI 路徑才加密）。
        # 有金鑰時一律加密成 .db.gz.enc 並移除明文，與 backup_db.py CLI 一致。
        # 無金鑰（dev/CI）→ 維持明文（無 secret 可保護、且不阻斷開發）。
        # 用 local 變數（不 mutate dataclass）；加密後 sha256/size 重算對齊「實際落地檔」。
        out_path, out_size, out_sha = result.path, result.size_bytes, result.sha256
        if os.getenv(BACKUP_KEY_ENV) or os.getenv(BACKUP_ENCRYPTION_KEY_ENV):
            enc_path = result.path.with_suffix(result.path.suffix + ".enc")
            encrypt_file(result.path, enc_path)
            result.path.unlink(missing_ok=True)
            out_path = enc_path
            out_size = enc_path.stat().st_size
            out_sha = _sha256_file(enc_path)
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
        out_path.name,
        {
            "size_bytes": out_size,
            "sha256": out_sha,
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

    # name = timestamp 部分（剝 ics- 前綴 + .db.gz[.enc] 後綴，與 list/_resolve_backup 對齊）
    _name = out_path.name
    if _name.startswith("ics-"):
        _name = _name[4:]
    for _suf in (".db.gz.enc", ".db.gz"):
        if _name.endswith(_suf):
            _name = _name[: -len(_suf)]
            break
    return {
        "name": _name,
        "filename": out_path.name,
        "size_bytes": out_size,
        "sha256": out_sha,
        "duration_ms": result.duration_ms,
        "timestamp": result.timestamp.isoformat(),
        "cleanup_deleted": len(deleted),
    }


@router.post("/backups/{name}/verify")
def verify(name: str, request: Request):
    """驗證指定 backup 完整性（SQLite 可開 + schema_migrations 表存在）。"""
    _check_system_admin(request)
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
    _check_system_admin(request)
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
    _check_system_admin(request)
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
        # P1-12b（#228）OP-4：此端點回傳 CLI 還原指令，系統運行中執行 cp 覆蓋熱 DB
        # → WAL 不一致風險。GUI 還原（POST /api/admin/restore，整包 data/ + 自動
        # pre-restore 防呆）已取代此用途；本端點標 deprecated，僅留離線災後相容。
        "warning": "運行中執行此 CLI 會覆蓋熱 DB（WAL 不一致風險）；務必先停服務。GUI 還原（整包 data/）為建議路徑。",
        "deprecated": True,
    }
