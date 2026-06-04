"""
main.py — ICS 指揮部後端 API（C0 重構版）

啟動方式：
  uvicorn main:app --host 0.0.0.0 --port 8000 --reload

版本：command-v2.0.0（C0 架構重構）
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from auth.first_run_gate import first_run_gate_middleware
from auth.middleware import auth_middleware
from auth.rate_limit import auth_rate_limit_middleware
from auth.service import cleanup_expired_sessions
from core.config import ALLOWED_ORIGINS, APP_VERSION, STATIC_DIR
from core.database import init_db
from core.logging import correlation_middleware, init_logging
from core.security_headers import security_headers_middleware
from repositories.account_repo import ensure_initial_admin_token
from repositories.config_repo import ensure_default_admin_pin

# ── Routers ───────────────────────────────────────────────────────────────────
from routers import (
    admin,
    ai,
    auth,
    backups,
    config_router,
    cop,
    dashboard,
    decisions,
    events,
    exercises,
    ingress,
    manual,
    map,
    security,
    snapshots,
    sync,
    tak,
    ttx,
)

# ─────────────────────────────────────────────────────────────────────────────
log = logging.getLogger(__name__)
_SESSION_CLEANUP_INTERVAL = 300  # 秒：週期清理閒置/逾時 session 的間隔


async def _periodic_session_cleanup():
    """#93(b)：週期清理 abandoned（關頁/切帳號 → token 不再被用）的逾時 session。
    cleanup_expired_sessions 內含 audit（逾時登出留痕），故 abandoned 登出 ~閒置逾時內即記，
    不必等下次啟動。best-effort：單次失敗不中斷迴圈。"""
    while True:
        await asyncio.sleep(_SESSION_CLEANUP_INTERVAL)
        try:
            await asyncio.to_thread(cleanup_expired_sessions)
        except Exception:
            log.warning("[session] 週期清理失敗（best-effort）", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # C1-A：首次啟動產生隨機 PIN（取代舊的預設 1234），印 console + 寫 ~/.ics/first_run_token
    ensure_initial_admin_token()
    ensure_default_admin_pin()
    cleanup_expired_sessions()  # 清除上次遺留的過期 session
    # P1-13（issue #27）：首次啟動 / fresh deploy 把 seed 複製到 runtime；
    # 已存在則 no-op。避免 first GET /api/map_config 抓不到檔。
    from services import map_config_store

    map_config_store.ensure()
    # P1-10d 地基（issue #60/#66）：事件分類 taxonomy seed → runtime ensure（同模式）
    from services import event_taxonomy_store

    event_taxonomy_store.ensure()
    # #93(b)：啟動週期 session 清理任務（abandoned 逾時登出及時 audit）
    _cleanup_task = asyncio.create_task(_periodic_session_cleanup())
    yield
    # shutdown：停週期清理 + 關閉所有 COP WS 連線（issue #29 PR-D in-process hub）
    _cleanup_task.cancel()
    from services.realtime_hub import cop_hub

    await cop_hub.close_all()


init_logging()  # C1-D：structlog 初始化，在 app 建立前呼叫

app = FastAPI(
    title="ICS 指揮部 API",
    version=APP_VERSION,
    docs_url="/docs",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Session-Token"],
)
app.middleware("http")(auth_middleware)
app.middleware("http")(first_run_gate_middleware)
app.middleware("http")(auth_rate_limit_middleware)
app.middleware("http")(security_headers_middleware)
# C1-D：correlation_middleware 最後加入 → 最外層執行（LIFO），最先設定 correlation_id
app.middleware("http")(correlation_middleware)


# ── 靜態檔案 ──────────────────────────────────────────────────────────────────
class _NoCacheJsStaticFiles(StaticFiles):
    """對 .js 回應強制 Cache-Control: no-store。

    前端 ES module（尤其動態 import，如 cop_stream.js 在登入後才載）若被瀏覽器
    模組/HTTP 快取住，會出現「新 map.js × 舊 cop_stream.js」的 API 不匹配（issue #29
    dogfood 撞到，連 cmd+shift+R 都繞不掉）。app 無 build/版號 cache-busting，故 JS
    一律 no-store：永遠抓最新、根除 stale-module。CSS/字型/圖片不受影響（沿用預設快取）。
    production 由 nginx（deploy/）服務靜態並自管 header，不走此 mount。
    """

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        if path.endswith(".js"):
            resp.headers["Cache-Control"] = "no-store"
        return resp


if STATIC_DIR.exists():
    app.mount("/static", _NoCacheJsStaticFiles(directory=str(STATIC_DIR)), name="static")

# ── 路由 ──────────────────────────────────────────────────────────────────────
for router in (
    auth.router,
    auth.session_router,
    snapshots.router,
    events.router,
    decisions.router,
    admin.router,
    backups.router,
    ingress.router,
    sync.router,
    manual.router,
    dashboard.router,
    config_router.router,
    map.router,
    cop.router,
    exercises.router,
    ttx.router,
    ai.router,
    tak.router,
    security.router,
):
    app.include_router(router)


# ── 首頁 ──────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def index():
    return f"""<html><head><meta charset="UTF-8"><title>ICS 指揮部</title></head>
    <body style="font-family:monospace;padding:20px;background:#0a0e1a;color:#9ab0c8">
    <h2 style="color:#fff">ICS 指揮部 command-{APP_VERSION}</h2>
    <p><a href="/static/commander_dashboard.html" style="color:#f0883e;font-weight:bold">▶ 儀表板</a></p>
    <p><a href="/static/admin_backups.html" style="color:#f0883e">▶ Backup 管理（admin）</a></p>
    <p><a href="/docs" style="color:#90b8e8">/docs — Swagger UI</a></p>
    <p><a href="/api/health" style="color:#90b8e8">/api/health</a></p>
    </body></html>"""
