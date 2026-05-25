"""
core/logging.py — ICS_DMAS Command 結構化日誌 — C1-D
規格：logging_architecture_decision_v1.1.md

APP_ENV=production → INFO+，mask PII，不輸出 traceback 路徑，寫 /var/log/ics/command.log
APP_ENV=development（預設）→ DEBUG+，完整輸出，寫 /var/log/ics/command.log
寫入失敗 → fallback stderr，每 60s 最多警告一次（Q11）
"""
import contextvars
import logging
import os
import sys
import time
import uuid
from pathlib import Path

import structlog
from starlette.requests import Request

# ── Correlation ID context var（Q10, D-12）────────────────────────
_correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default=""
)


def get_correlation_id() -> str:
    return _correlation_id.get()


# ── Constants ─────────────────────────────────────────────────────
IS_PROD   = os.getenv("APP_ENV", "development") == "production"
LOG_DIR   = Path("/var/log/ics")
LOG_FILE  = LOG_DIR / "command.log"
COMPONENT = "command"
VERSION   = os.getenv("APP_VERSION", "v2.1.0")

# ── /var/log/ics/ 自建（D-6）──────────────────────────────────────
def _ensure_log_dir() -> bool:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        return True
    except OSError:
        return False


# ── Stderr fallback — 每 60s 最多一次（Q11）────────────────────────
_fallback_warned_at: float = 0.0


def _stderr_fallback(reason: str) -> None:
    global _fallback_warned_at
    now = time.monotonic()
    if now - _fallback_warned_at >= 60.0:
        print(
            f"[ICS-LOG-FALLBACK] log file unavailable, falling back to stderr: {reason}",
            file=sys.stderr, flush=True,
        )
        _fallback_warned_at = now


# ── structlog processors ──────────────────────────────────────────

def _inject_meta(logger, method, event_dict):
    event_dict.setdefault("component", COMPONENT)
    event_dict.setdefault("version",   VERSION)
    return event_dict


def _inject_correlation_id(logger, method, event_dict):
    cid = get_correlation_id()
    if cid:
        event_dict["correlation_id"] = cid
    return event_dict


def _mask_pii(logger, method, event_dict):
    """PROD only: mask user、session_id、detail.ip（Q9, v1.1 §2.2）"""
    if not IS_PROD:
        return event_dict
    # user: 留前 2 字元 + "**"
    if event_dict.get("user"):
        v = str(event_dict["user"])
        event_dict["user"] = (v[:2] + "**") if len(v) > 2 else "**"
    # session_id: 留前 4 字元 + "..."
    if event_dict.get("session_id"):
        v = str(event_dict["session_id"])
        event_dict["session_id"] = (v[:4] + "...") if len(v) > 4 else "..."
    # detail.ip: 留 /24 subnet
    detail = event_dict.get("detail", {})
    if isinstance(detail, dict) and detail.get("ip"):
        parts = str(detail["ip"]).split(".")
        if len(parts) == 4:
            detail["ip"] = ".".join(parts[:3]) + ".x"
    return event_dict


def _strip_traceback_in_prod(logger, method, event_dict):
    """PROD：移除 exc_info 防止 stack trace 路徑洩漏（SI-11）"""
    if IS_PROD:
        event_dict.pop("exc_info",  None)
        event_dict.pop("exception", None)
    return event_dict


# ── init ──────────────────────────────────────────────────────────

def init_logging() -> None:
    """應用程式啟動時呼叫一次（main.py）。"""
    _ensure_log_dir()
    try:
        file_handler  = logging.FileHandler(LOG_FILE, encoding="utf-8")
        output_stream = file_handler.stream
    except OSError as exc:
        _stderr_fallback(str(exc))
        file_handler  = logging.StreamHandler(sys.stderr)
        output_stream = sys.stderr

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(file_handler)
    root.setLevel(logging.DEBUG if not IS_PROD else logging.INFO)

    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _inject_meta,
            _inject_correlation_id,
            _mask_pii,
            _strip_traceback_in_prod,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.DEBUG if not IS_PROD else logging.INFO
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=output_stream),
    )


# ── HTTP correlation middleware（Q10, D-12）───────────────────────
# main.py 以 LAST app.middleware("http") 加入 → 最外層先執行（LIFO）

def _is_valid_uuid4(value: str) -> bool:
    try:
        val = uuid.UUID(value, version=4)
        return str(val) == value.lower()
    except (ValueError, AttributeError):
        return False


async def correlation_middleware(request: Request, call_next):
    incoming = request.headers.get("X-Correlation-ID", "").strip()
    cid = incoming if _is_valid_uuid4(incoming) else str(uuid.uuid4())
    token = _correlation_id.set(cid)
    try:
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = cid
        return response
    finally:
        _correlation_id.reset(token)   # 避免 context 在 worker 間洩漏


# module-level convenience
log = structlog.get_logger()
