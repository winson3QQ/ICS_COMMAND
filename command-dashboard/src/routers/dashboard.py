
import os
import shutil
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Request

from core.config import (
    APP_VERSION, BUILD_ID, CMD_VERSION, DB_PATH,
    HEALTH_DISK_DEGRADED_PCT_THRESHOLD, HEALTH_DB_LATENCY_DEGRADED_MS,
)
from core.database import get_health_schema_version
from repositories.audit_repo import get_audit_log
from repositories.snapshot_repo import get_latest_snapshot
from services.dashboard_service import build_dashboard
from services.exercise_service import resolve_scope
from auth.service import check_session

router = APIRouter(tags=["儀表板"])


@router.get("/api/dashboard")
def get_dashboard(request: Request, exercise_id: int | None = None):
    """前端每 10 秒 polling 的主要端點。
    P1-14：預設只回當前 active 場；commander 顯式帶 exercise_id 才看歷史（resolve_scope 守門）。"""
    return build_dashboard(resolve_scope(request.state.session, exercise_id))


@router.get("/api/staff", tags=["人員"])
def get_staff():
    result = {}
    for node_type in ("shelter", "medical", "forward", "security"):
        snap = get_latest_snapshot(node_type)
        if snap:
            extra = snap.get("extra") or {}
            result[node_type] = {
                "staff":         extra.get("staff_list", []),
                "staff_on_duty": snap.get("staff_on_duty"),
                "snapshot_time": snap.get("snapshot_time"),
            }
        else:
            result[node_type] = {"staff": [], "staff_on_duty": None, "snapshot_time": None}
    return result


@router.get("/api/audit_log", tags=["系統"])
def audit_log_endpoint(request: Request, limit: int = 100, exercise_id: int | None = None):
    # P1-14（MED-5）：稽核軌跡預設只回當前 active 場；commander 顯式帶才看歷史。
    return get_audit_log(limit, resolve_scope(request.state.session, exercise_id))


@router.get("/api/version", tags=["系統"])
def version():
    """
    前端啟動時 fetch 取版本號（C1-F Q1）。
    - cmd_version：前端 UI 功能版本（Wave 里程碑）
    - server_version：後端 API SemVer
    無需認證（版號非敏感資訊）。
    """
    return {
        "cmd_version":    CMD_VERSION,
        "server_version": APP_VERSION,
        "build":          BUILD_ID,
    }


@router.get("/api/health", tags=["系統"])
def health(request: Request):
    """伺服器健康（狀態燈用，未登入亦可呼叫）。
    §8.6 收口：**未登入只回粗略狀態**(status + db_writable，燈點判斷所需)；
    **db_path / 磁碟 / DB 延遲 / schema 等運維細節只給已登入者**，避免對外洩漏部署資訊。
    （前端燈輪詢 cop.js 有 session 即帶 X-Session-Token → 取得完整 tooltip。）"""
    db_path = Path(DB_PATH)
    db_writable = _db_writable(db_path)
    first_run_required = _first_run_required()
    disk_free_pct = _disk_free_pct(db_path.parent)
    # initializing 狀態跳過 DB 延遲與 schema 查詢，避免並發 DB 連線造成 health 超時
    db_lat_ms = None if first_run_required else _db_latency_ms(db_path)

    status = "ok"
    if first_run_required:
        status = "initializing"
    elif not db_writable:
        status = "degraded"
    elif disk_free_pct < HEALTH_DISK_DEGRADED_PCT_THRESHOLD:
        status = "degraded"
    elif db_lat_ms is not None and db_lat_ms > HEALTH_DB_LATENCY_DEGRADED_MS:
        status = "degraded"

    # 未登入：燈點所需的最小集合（不洩漏運維細節）
    resp = {
        "status": status,
        "db_writable": db_writable,
        "version": APP_VERSION,
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    # 已登入（帶有效 session token）才附詳細運維數據
    token = request.headers.get("X-Session-Token")
    if token:
        sess, failure = check_session(token, touch=False)
        if sess and not failure:
            resp.update({
                "db_path":       str(db_path),
                "schema_version": None if first_run_required else _schema_version(db_path),
                "disk_free_mb":  _disk_free_mb(db_path.parent),
                "disk_free_pct": disk_free_pct,
                "db_latency_ms": db_lat_ms,
            })
    return resp


@router.get("/api/status", tags=["系統"])
def status():
    return {"status": "ok", "version": APP_VERSION}


def _disk_free_mb(path: Path) -> int:
    try:
        usage = shutil.disk_usage(path)
    except FileNotFoundError:
        usage = shutil.disk_usage(path.parent)
    return int(usage.free // (1024 * 1024))


def _disk_free_pct(path: Path) -> float:
    """磁碟剩餘百分比（0.0–100.0），供 degraded 判斷用。"""
    try:
        usage = shutil.disk_usage(path)
    except FileNotFoundError:
        usage = shutil.disk_usage(path.parent)
    return round(usage.free / usage.total * 100, 1)


def _db_latency_ms(path: Path) -> float | None:
    """對 DB 執行 SELECT 1 的往返時間（ms）。DB 不存在時回傳 None。"""
    if not path.exists():
        return None
    try:
        t0 = time.monotonic()
        uri = f"file:{path}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=1) as conn:
            conn.execute("SELECT 1")
        return round((time.monotonic() - t0) * 1000, 1)
    except sqlite3.Error:
        return None


def _db_writable(path: Path) -> bool:
    if path.exists():
        return os.access(path, os.W_OK)
    return os.access(path.parent, os.W_OK)


def _schema_version(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        uri = f"file:{path}?mode=ro"
        with sqlite3.connect(uri, uri=True) as conn:
            return get_health_schema_version(conn)
    except sqlite3.Error:
        return None


def _first_run_required() -> bool:
    try:
        from repositories.account_repo import is_first_run_required
        return is_first_run_required()
    except Exception:
        return False
