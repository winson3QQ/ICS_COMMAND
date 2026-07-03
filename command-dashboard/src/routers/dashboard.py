# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
import os
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from auth.role_enum import visible_factions_for_session
from auth.service import check_session
from core.config import (
    APP_VERSION,
    BUILD_ID,
    CMD_VERSION,
    DB_PATH,
    FACTION_ISOLATION_ENABLED,
    HEALTH_DB_LATENCY_DEGRADED_MS,
    HEALTH_DISK_DEGRADED_PCT_THRESHOLD,
    SBOM_PATH,
)
from core.database import get_health_schema_version, open_readonly_live
from repositories.audit_repo import get_audit_log
from repositories.snapshot_repo import get_latest_snapshot
from services.dashboard_service import build_dashboard
from services.exercise_service import current_exercise_id, resolve_scope

router = APIRouter(tags=["儀表板"])


@router.get("/api/dashboard")
def get_dashboard(request: Request, exercise_id: int | None = None):
    """前端每 10 秒 polling 的主要端點。
    P1-14：events/decisions 等仍走 resolve_scope（跨場隔離）；#472：tak_squads 改 faction 守門。"""
    # #472 安全補漏：dashboard 的 tak_squads 聚合過去無 faction 過濾（紅隊 centroid/兵力洩漏給
    # READ_ROLES）→ 補上 visible_factions（sysadmin→全見；藍→{blue,neutral}）+ 平時放行未編隊。
    vf = visible_factions_for_session(request.state.session) if FACTION_ISOLATION_ENABLED else None
    return build_dashboard(
        resolve_scope(request.state.session, exercise_id),
        visible_factions=vf,
        allow_null_faction=current_exercise_id() is None,
    )


@router.get("/api/staff", tags=["人員"])
def get_staff():
    result = {}
    for node_type in ("shelter", "medical", "forward", "security"):
        snap = get_latest_snapshot(node_type)
        if snap:
            extra = snap.get("extra") or {}
            result[node_type] = {
                "staff": extra.get("staff_list", []),
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
        "cmd_version": CMD_VERSION,
        "server_version": APP_VERSION,
        "build": BUILD_ID,
    }


@router.get("/api/sbom", tags=["系統"])
def sbom():
    """產品 SBOM（CycloneDX）下載（#419）。

    RBAC：READ_ROLES（observer 以上，需登入）——由 auth_middleware 經 allowed_roles_for
    閘控。**不入未認證 allowlist**（不同於 /api/version）：SBOM 列確切相依版本＝對匿名訪客
    的 CVE 偵察面，公網 prod 不主動公布（縱深防禦）。供關於頁下載 / 客戶資安盡職調查。

    檔案於 release build 由 gen_release_sbom.sh 產 + Dockerfile 烤入；dev / 非 release
    build 無此檔 → 404（graceful，非錯誤）。
    """
    if not SBOM_PATH.exists():
        raise HTTPException(404, "此 build 未附 SBOM（僅正式 release build 提供）")
    return FileResponse(
        SBOM_PATH,
        media_type="application/vnd.cyclonedx+json",
        filename=SBOM_PATH.name,
    )


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
            resp.update(
                {
                    "db_path": str(db_path),
                    "schema_version": None if first_run_required else _schema_version(db_path),
                    "disk_free_mb": _disk_free_mb(db_path.parent),
                    "disk_free_pct": disk_free_pct,
                    "db_latency_ms": db_lat_ms,
                }
            )
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
    # 加密模式（P1-12c）：open_readonly_live 走 sqlcipher3，錯誤型別為 sqlcipher3.Error
    # 非 sqlite3.Error；health 探針任何 DB 連線/解密問題一律回 None（degraded 判斷）。
    try:
        t0 = time.monotonic()
        conn = open_readonly_live(path, timeout=1)
        try:
            conn.execute("SELECT 1")
        finally:
            conn.close()
        return round((time.monotonic() - t0) * 1000, 1)
    except Exception:
        return None


def _db_writable(path: Path) -> bool:
    if path.exists():
        return os.access(path, os.W_OK)
    return os.access(path.parent, os.W_OK)


def _schema_version(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        conn = open_readonly_live(path)
        try:
            return get_health_schema_version(conn)
        finally:
            conn.close()
    except Exception:
        return None


def _first_run_required() -> bool:
    try:
        from repositories.account_repo import is_first_run_required

        return is_first_run_required()
    except Exception:
        return False
