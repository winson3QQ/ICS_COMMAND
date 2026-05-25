"""
main.py — ICS 指揮部後端 API（C0 重構版）

啟動方式：
  uvicorn main:app --host 0.0.0.0 --port 8000 --reload

版本：command-v2.0.0（C0 架構重構）
"""

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
    dashboard,
    decisions,
    events,
    exercises,
    manual,
    map,
    pi_push,
    security,
    snapshots,
    sync,
    tak,
    ttx,
)


# ─────────────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # C1-A：首次啟動產生隨機 PIN（取代舊的預設 1234），印 console + 寫 ~/.ics/first_run_token
    ensure_initial_admin_token()
    ensure_default_admin_pin()
    cleanup_expired_sessions()  # 清除上次遺留的過期 session
    yield


init_logging()   # C1-D：structlog 初始化，在 app 建立前呼叫

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
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ── 路由 ──────────────────────────────────────────────────────────────────────
for router in (
    auth.router, auth.session_router, snapshots.router, events.router, decisions.router,
    admin.router, backups.router, pi_push.router, sync.router, manual.router,
    dashboard.router, config_router.router, map.router,
    exercises.router, ttx.router, ai.router, tak.router, security.router,
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


