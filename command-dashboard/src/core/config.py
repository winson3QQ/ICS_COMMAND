"""
core/config.py — 環境變數與路徑設定
"""

import os
from pathlib import Path

# ── 路徑 ──────────────────────────────────
SRC_DIR = Path(__file__).parent.parent
BASE_DIR = SRC_DIR.parent
DATA_DIR = BASE_DIR / "data"
STATIC_DIR = BASE_DIR / "static"
MBTILES_DIR = STATIC_DIR / "tiles"

_ics_db_path_env = os.getenv("ICS_DB_PATH")
DB_PATH: Path = Path(_ics_db_path_env) if _ics_db_path_env else DATA_DIR / "ics.db"

# ── map_config（P1-13 seed/runtime 分離）──────────────────────────────
# SEED：tracked，factory default（issue/PR snapshot 期間維持版本控管）
# PATH：gitignored，runtime 實檔（user data 邊界，未來進 P1-12b backup + P1-12c SQLCipher 加密邊界）
# Startup 時 ensure()：PATH 不存在 → copy SEED 過去；POST 寫 PATH（atomic write）
MAP_CONFIG_SEED: Path = STATIC_DIR / "map_config.seed.json"
MAP_CONFIG_PATH: Path = DATA_DIR / "map_config.json"

# β（issue #29）Phase 1：map_config ETag 樂觀鎖嚴格模式。
#   "0"（預設）→ 寬鬆：POST 不送 If-Match 仍接受（用 current version 寫，舊 client 相容）
#   "1"        → 嚴格：POST 必送 If-Match，缺 → 428；version 不符 → 409
# 漸進上線：Phase 1 預設 off（純觀察 version 是否正確遞增），Phase 2 client 改好後翻 1。
MAP_CONFIG_STRICT_ETAG: bool = os.getenv("MAP_CONFIG_STRICT_ETAG", "0") == "1"

# 磁碟剩餘百分比低於此值 → degraded（黃燈）
HEALTH_DISK_DEGRADED_PCT_THRESHOLD: float = float(os.getenv("HEALTH_DISK_DEGRADED_PCT_THRESHOLD", "20"))
# DB 查詢延遲超過此值（ms）→ degraded（黃燈）
HEALTH_DB_LATENCY_DEGRADED_MS: int = int(os.getenv("HEALTH_DB_LATENCY_DEGRADED_MS", "500"))

# ── Session ───────────────────────────────
SESSION_TIMEOUT: int = int(os.getenv("SESSION_TIMEOUT", "50400"))
IDLE_TIMEOUT: int = int(os.getenv("ICS_IDLE_TIMEOUT_SECONDS", "900"))
WARNING_THRESHOLD_SECONDS: int = int(os.getenv("ICS_WARNING_THRESHOLD_SECONDS", "120"))
# 注意：PinLock（UI 層 idle 鎖定）是獨立機制，與此 server-side timeout 無關

# ── App ───────────────────────────────────
APP_VERSION = "2.1.0"  # Issue #24 + #26：deploy/setup.sh + ics service + /api/health 增強 + 4-role RBAC；PR#25 + PR#27

# CMD_VERSION：前端 UI 功能版本（Wave 里程碑，不同於後端 SemVer APP_VERSION）
# 兩軌版本命名，不可混用（見 CLAUDE.md 版號規則）
# 由 /api/version 提供給前端，是唯一 source-of-truth；release 時更新此值
CMD_VERSION: str = os.getenv("CMD_VERSION", "v0.12.14")

# ── CORS（C1-B）──────────────────────────
# 架構備忘：PWA→Pi→Command 為 hub-and-spoke，瀏覽器無跨源呼叫，CORS 在主流程中無作用。
# 保留 middleware 是為未來 TTX Orchestrator（C5-A 獨立服務）與 Tier 3 開放 API（C5-E）預留。
# 部署時由 /etc/ics/command.env 的 ALLOWED_ORIGINS 覆寫；預設只開本機（dev）。
_default_origins = "http://localhost:8000,http://127.0.0.1:8000,https://localhost,https://127.0.0.1"
ALLOWED_ORIGINS: list[str] = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", _default_origins).split(",") if o.strip()]

# ── 安全標頭（C1-B）────────────────────────
# CSP_MODE: "report-only"（觀察期）→ "enforce"（正式擋）
# 由 nginx 反代終結 TLS 並注入 HSTS（避免 FastAPI 在 HTTP dev 環境也送 HSTS 鎖死瀏覽器），
# CSP / X-Frame-Options / X-Content-Type-Options 由 FastAPI middleware 負責（與應用語義耦合）。
CSP_MODE: str = os.getenv("CSP_MODE", "report-only")  # "report-only" | "enforce"
CSP_REPORT_URI: str = os.getenv("CSP_REPORT_URI", "/api/security/csp-report")
ENABLE_SECURITY_HEADERS: bool = os.getenv("ENABLE_SECURITY_HEADERS", "true").lower() == "true"

# ── 認證豁免路由 ──────────────────────────
# (method, path) 完整匹配
AUTH_EXEMPT_EXACT: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/api/auth/login"),
        ("GET", "/api/status"),
        ("GET", "/docs"),
        ("GET", "/openapi.json"),
        ("GET", "/"),
        # CSP violation report：瀏覽器自動 POST，不帶 session token
        ("POST", "/api/security/csp-report"),
        # 版本資訊：前端啟動時 fetch，不含敏感資訊，無需認證（C1-F Q1）
        ("GET", "/api/version"),
    }
)

# path 前綴匹配（任何 method）
AUTH_EXEMPT_PREFIXES: tuple[str, ...] = (
    "/static/",
    "/api/map/tiles/",
)

# ── Trusted Ingest（TI-01）────────────────────────────────────────────────
# HMAC 時間戳記容差（ms）。超出此窗口的請求一律拒絕。
HMAC_TIMESTAMP_SKEW_MS: int = int(os.getenv("HMAC_TIMESTAMP_SKEW_MS", "300000"))
# Nonce TTL（ms）：Lazy Expiry 清理週期。應 ≥ HMAC_TIMESTAMP_SKEW_MS。
NONCE_TTL_MS: int = int(os.getenv("NONCE_TTL_MS", "600000"))
