"""
routers/backup_restore.py — P1-12b 整包 data/ 加密備份 + GUI 還原（#228）

與既有 routers/backups.py（DB-only gzip + systemd timer 相容入口）並存，
本 router 是 admin GUI 的整包 user-data 備份 / 還原主入口。

Endpoints（全 sysadmin-only + audit）：
- POST /api/admin/user-data-backups          手動觸發整包備份（L1）
- GET  /api/admin/user-data-backups          列表（userdata-* + pre-restore-*）
- GET  /api/admin/user-data-backups/{name}/manifest  解密預覽 manifest
- POST /api/admin/restore                    上傳 .tar.gz.enc 還原（active 演習拒 409）

restore 安全：解密驗 manifest（失敗不動 current）→ 自動 pre-restore-{ts} →
替換 data/ → 回 restart_required（替換熱 DB 需重啟）。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from core.config import DATA_DIR
from repositories._helpers import audit
from repositories.exercise_repo import get_active_exercise
from routers.admin import _check_system_admin
from services import user_data_backup_service as uds

router = APIRouter(prefix="/api/admin", tags=["備份"])

BACKUP_DIR = DATA_DIR / "backups"
_VALID_SUFFIX = ".tar.gz.enc"


def _resolve(name: str) -> Path:
    """name = 檔名（不含路徑）→ BACKUP_DIR 下的 .tar.gz.enc。"""
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(400, "name 含非法字元")
    if not name.endswith(_VALID_SUFFIX):
        raise HTTPException(400, "非整包備份檔（需 .tar.gz.enc）")
    path = BACKUP_DIR / name
    if not path.exists():
        raise HTTPException(404, f"備份不存在：{name}")
    return path


@router.post("/user-data-backups")
def trigger_user_data_backup(request: Request):
    """L1 手動：整包 data/ 加密備份。"""
    sess = _check_system_admin(request)
    if not DATA_DIR.exists():
        raise HTTPException(404, f"data 目錄不存在：{DATA_DIR}")
    try:
        res = uds.create_backup(DATA_DIR, BACKUP_DIR, trigger="manual")
    except Exception as e:
        audit(sess["username"], None, "user_data_backup_failed", "system", "data", {"error": str(e)})
        raise HTTPException(500, f"備份失敗：{e}") from e
    audit(
        sess["username"], None, "user_data_backup_created", "system", res.path.name,
        {"size_bytes": res.size_bytes, "sha256": res.sha256, "trigger": "manual", "files": len(res.manifest["files"])},
    )
    # 滾動保留已移入 create_backup（單一點，涵蓋全部觸發路徑，非只手動）
    return {
        "name": res.path.name,
        "size_bytes": res.size_bytes,
        "sha256": res.sha256,
        "duration_ms": res.duration_ms,
        "timestamp": res.timestamp.isoformat(),
        "manifest": res.manifest,
    }


@router.get("/user-data-backups")
def list_user_data_backups(request: Request):
    """列出整包備份（最新在前）+ 解密讀 manifest 帶出 trigger / 演習（清單直接顯示，
    免逐筆點 manifest）。演習名加密藏在 manifest，僅 sysadmin 已登入時即時解密讀出，
    不落明文於磁碟（保 at-rest）。單筆解密失敗（外來/損毀檔）→ 仍列出，trigger=None。"""
    _check_system_admin(request)
    items = []
    if BACKUP_DIR.exists():
        for p in BACKUP_DIR.iterdir():
            if not (p.is_file() and p.name.endswith(_VALID_SUFFIX)):
                continue
            item = {
                "name": p.name,
                "size_bytes": p.stat().st_size,
                "is_pre_restore": p.name.startswith("pre-restore-"),
                "trigger": None,
                "exercise": None,
            }
            try:
                m = uds.read_manifest(p)
                item["trigger"] = m.get("trigger")
                ex = m.get("exercise")
                item["exercise"] = ex.get("name") if isinstance(ex, dict) else None
            except Exception:  # 損毀/外來/舊格式檔仍列出，只是無 trigger/演習
                pass
            items.append(item)
    items.sort(key=lambda x: x["name"], reverse=True)
    return {"backups": items, "total": len(items), "backup_dir": str(BACKUP_DIR)}


@router.get("/user-data-backups/{name}/download")
def download_backup(name: str, request: Request):
    """下載加密備份檔（.tar.gz.enc）給 admin 存到 USB / 異地保存。

    檔案本身已 Fernet 加密；下載者仍需 BACKUP_KEY 才能解，外洩風險受加密保護。
    """
    from fastapi.responses import FileResponse

    sess = _check_system_admin(request)
    path = _resolve(name)
    audit(sess["username"], None, "user_data_backup_downloaded", "system", name, {"size_bytes": path.stat().st_size})
    return FileResponse(path, media_type="application/octet-stream", filename=path.name)


@router.get("/user-data-backups/{name}/manifest")
def preview_manifest(name: str, request: Request):
    """解密讀 MANIFEST.json（restore 前預覽：演習 metadata / 檔案清單 / 大小）。"""
    sess = _check_system_admin(request)
    path = _resolve(name)
    try:
        manifest = uds.read_manifest(path)
    except ValueError as e:
        raise HTTPException(422, f"無法讀取 manifest：{e}") from e
    audit(sess["username"], None, "user_data_backup_manifest_read", "system", name, {"schema": manifest.get("schema")})
    return {"name": name, "manifest": manifest}


def _require_no_active_exercise() -> None:
    """還原防呆：有進行中演習 → 409（避免覆寫進行中場次，須先歸檔）。"""
    active = get_active_exercise()
    if active is not None:
        raise HTTPException(
            409, f"有進行中演習「{active.get('name')}」（id={active.get('id')}）— 還原前請先歸檔"
        )


def _restore_from_path(path: Path, sess: dict, label: str) -> dict:
    """共用還原：解密驗 manifest 失敗不動 current（422）；wipe 後中斷 → 500（pre-restore 可救）。"""
    try:
        result = uds.restore_backup(path, DATA_DIR, BACKUP_DIR)
    except ValueError as e:
        audit(sess["username"], None, "user_data_restore_failed", "system", label, {"error": str(e)})
        raise HTTPException(422, f"還原失敗（current 未變動）：{e}") from e
    except RuntimeError as e:
        audit(sess["username"], None, "user_data_restore_interrupted", "system", label, {"error": str(e)})
        raise HTTPException(500, str(e)) from e
    audit(
        sess["username"], None, "user_data_restored", "system", label,
        {"pre_restore": result["pre_restore"], "manifest_created_at": result["manifest"].get("created_at")},
    )
    return {
        "ok": True,
        "manifest": result["manifest"],
        "pre_restore": result["pre_restore"],
        "restart_required": result["restart_required"],
    }


@router.post("/user-data-backups/{name}/restore")
def restore_from_list(name: str, request: Request):
    """還原伺服器上已存在的備份（清單「還原此筆」）——免下載再上傳。"""
    sess = _check_system_admin(request)
    _require_no_active_exercise()
    path = _resolve(name)
    return _restore_from_path(path, sess, name)


@router.post("/restore")
async def restore_user_data(request: Request, file: UploadFile = File(...)):
    """上傳外部 .tar.gz.enc（USB / 異地拿回）還原整包 data/。

    防呆：① active 演習 → 409；② 解密/manifest 失敗 → 不動 current；③ 自動 pre-restore。
    """
    sess = _check_system_admin(request)
    _require_no_active_exercise()

    filename = file.filename or ""
    if not filename.endswith(_VALID_SUFFIX):
        raise HTTPException(400, "僅接受 .tar.gz.enc 整包備份檔")

    content = await file.read()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    # 寫到暫存檔（restore_backup 以路徑操作；用 tempfile 避免污染 BACKUP_DIR）
    with tempfile.NamedTemporaryFile(prefix="ics-upload-", suffix=_VALID_SUFFIX, dir=BACKUP_DIR, delete=False) as tf:
        tmp_path = Path(tf.name)
        tf.write(content)
    try:
        return _restore_from_path(tmp_path, sess, filename)
    finally:
        tmp_path.unlink(missing_ok=True)
