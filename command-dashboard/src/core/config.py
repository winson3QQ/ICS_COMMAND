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

# 事件分類 taxonomy（P1-10d 地基，issue #60/#66）：同 seed/runtime 模式。
EVENT_TAXONOMY_SEED: Path = STATIC_DIR / "event_taxonomy.seed.json"
EVENT_TAXONOMY_PATH: Path = DATA_DIR / "event_taxonomy.json"

# P1-17（issue #88）永久設施公開資料底圖層：唯讀基準層，**只讀 static seed**，
# 無 runtime/data 副本、非 user-data、不受 exercise scoping / reset 影響。
# 資料由維護者腳本 scripts/import_facilities.py 從台灣政府開放資料產生（非中國）。
FACILITIES_SEED: Path = STATIC_DIR / "facilities.seed.json"

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
# MINOR：P2-30 part 3（#180）—— 廣播放寬 operator+（role_enum 窄洞 /api/tak/share/→WRITE_ROLES）
# + 廣播後即時同步（shared_tak json_set + move 重推 CoT）+ CoT remarks 標 source: ICS。
# PATCH：#265 —— 切換 active 演習後 WS scope 凍結修正（cop_hub.rescope_active 就地重綁
# follows_active 連線、不靠 client 重連）；行為改變（新場 entity 不再需硬重整才 render）。
# MINOR：#267 常駐層疊看後端 —— WS `?standing=1`（限 COMMAND_ROLES）讓 active 場連線也收 NULL
# 常駐 entity（`_Conn.include_standing` + `wants()` NULL-union）；證明不跨演習（仍精確擋別場）。
# PATCH：#267 REST 對等 —— `GET /api/cop/entities?include_standing`（限 COMMAND）疊加 NULL 常駐，
# 與 WS 對等，補掉 resync 抹掉常駐單位的鬼影（前端疊看 slice 的後端半）。
# PATCH：GeoChat 冪等（#248 衍生）—— chat ingest 查 sender_uid（含訊息 GUID、唯一）跳過重播，
# 修「TAK 重訂閱/resync 重播持久化 GeoChat → 同訊息每次重連多一筆」（dogfood 實證 42 筆）。
APP_VERSION = "2.6.2"

# CMD_VERSION：前端 UI 功能版本（不同於後端 SemVer APP_VERSION；規則見 CLAUDE.md 版號規則）
# 兩軌版本命名，不可混用。由 /api/version 提供給前端，是唯一 source-of-truth；release 時更新此值。
# v1.0.0：拆分自 ICS_DMAS 後首個完整可用形態（MapLibre 地圖引擎全換 P1-10b + PWA 移除 P1-11
#         + 演習/放置/稽核 UI P1-13/14/16/#93 + 分類編輯器 #66）→ 0.x 畢業為 MAJOR 紀元。
CMD_VERSION: str = os.getenv(
    "CMD_VERSION", "v1.6.0"
)  # MINOR：P2-24 前端尾（#164）TAK runtime 控制 UI 功能組 —— 系統 tab sysadmin 開/關 toggle
# + 唯讀連線狀態行 + header 燈號認實化（running/configured 區分，消除「沒 task 卻顯斷線重連」謊報）
# PATCH(v1.4.1)：#265 —— 切換演習後不再需硬重整即即時 render（cop_stream onclose identity guard +
# stop() backoff 重置 + _refreshAfterExerciseSwitch 改就地 resync 不清快取，消雙 socket race）。
# MINOR(v1.5.0)：#269/#267 切片1 —— 右欄四-tab 重構（事件追蹤｜通聯｜隊伍｜待裁示，各整欄高 + 紅圈計數
# + per-session tab 記憶）+ 新 TAK 隊伍名冊（按 team_color 分組、只列友軍、敵情接觸排除）。納編/定址/編組
# 動作佔位，後端分批接。
# MINOR(v1.6.0)：#267 常駐層疊看前端 —— 演習中疊顯 NULL 常駐單位（地圖圖層 toggle 限指揮層、預設關、
# 無 active 演習時常駐恆顯）+ roster 標「常駐」候選 + cop_stream 帶 standing/include_standing。
# v1.3.0：P2-30 part 3（#180）敵情標記 UI —— 2525 渲染 + callsign + 右鍵廣播 + 拖曳 + detail modal

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
# P1-10h：觀察期（report-only）已驗證乾淨（commander 無 inline、MapLibre/PMTiles directive 已備），
# 預設翻為 enforce —— 僅 ENFORCE_PATHS（commander_dashboard.html）實際 enforce，其餘路徑
# 仍 report-only（見 security_headers.py 雙白名單 fallback）。dev 可用 CSP_MODE=report-only 退回觀察。
CSP_MODE: str = os.getenv("CSP_MODE", "enforce")  # "report-only" | "enforce"
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
# 註：tile 路由是 /tiles/...（非 /api/ 底下），auth_middleware 只 gate /api/* → tiles 本就不需
# exempt。舊有 "/api/map/tiles/" 條目 match 不到任何路由（dead/誤導），已移除（#64-1）。
AUTH_EXEMPT_PREFIXES: tuple[str, ...] = ("/static/",)

# ── Trusted Ingest（TI-01）────────────────────────────────────────────────
# HMAC 時間戳記容差（ms）。超出此窗口的請求一律拒絕。
HMAC_TIMESTAMP_SKEW_MS: int = int(os.getenv("HMAC_TIMESTAMP_SKEW_MS", "300000"))
# Nonce TTL（ms）：Lazy Expiry 清理週期。應 ≥ HMAC_TIMESTAMP_SKEW_MS。
NONCE_TTL_MS: int = int(os.getenv("NONCE_TTL_MS", "600000"))

# ── TAK CoT 訂閱（P2-03 / #107）────────────────────────────────────────────
# 預設 OFF：未設定 TAK 時 app 不嘗試連線（dev/CI 安全）。設了才在 lifespan launch 背景 task。
TAK_ENABLED: bool = os.getenv("TAK_ENABLED", "false").lower() == "true"
TAK_COT_URL: str = os.getenv("TAK_COT_URL", "")  # tls://<host>:8089（TAK CoT streaming）
TAK_CLIENT_CERT: str = os.getenv("TAK_CLIENT_CERT", "")  # client 憑證 PEM，須含完整鏈（leaf+intermediate）
TAK_CLIENT_KEY: str = os.getenv("TAK_CLIENT_KEY", "")  # client 私鑰 PEM
TAK_CAFILE: str | None = os.getenv("TAK_CAFILE") or None  # 驗 server 憑證的 CA（step-ca root）；正式部署必填
# 顯式允許「無 cafile → 完全不驗 server」（僅 dev/PoC，有 MITM 風險，build_subscribe_config 會 warn）
TAK_ALLOW_INSECURE_TLS: bool = os.getenv("TAK_ALLOW_INSECURE_TLS", "false").lower() == "true"

# ── TAK Marti REST API（P2-11 / #138）──────────────────────────────────────
# 指揮部「主動查」TAK Server :8443 Marti REST（vs :8089 被動收串流）。M2M 認證 =
# **client cert（mTLS）**，**複用上方 TAK_CLIENT_CERT/KEY/CAFILE**（與 :8089 同一套 step-ca）。
# 規格更正：官方 TAK Server 5.7 Marti 走 cert 非 OAuth2（見 memory tak-server-marti-cert-not-oauth）。
# 空 → REST 功能停用（P2-12+ 消費方各自判斷）。
TAK_MARTI_URL: str = os.getenv("TAK_MARTI_URL", "")  # https://<host>:8443
# 單一 client 自保 rate-limit（兩次請求最短間隔，秒）+ 暫時性錯誤重試上限
TAK_MARTI_MIN_INTERVAL_S: float = float(os.getenv("TAK_MARTI_MIN_INTERVAL_S", "1.0"))
TAK_MARTI_MAX_RETRIES: int = int(os.getenv("TAK_MARTI_MAX_RETRIES", "3"))

# ── TAK Marti 服務 cert：讀/寫身分分離（#177 L1；cert-role 見 #176）──────────────
# 兩張 step-ca 簽的 Marti REST cert，由 deploy/tak-server/pki/issue-tak-certs.sh 產出。
#   讀 cert → P2-14 DataSync / resync（/cot/sa、/cot、/changes）
#   寫 cert → P2-13 下行（DELETE/PUT .../contents 權威增刪）
# ⚠ 2026-06-09 活 TAK 5.7 實測（memory tak-marti-authz-model）修正前述「mission-role 靠註冊」：
#   · 讀取：truststore 信任即通，兩張都能讀；register fingerprint 對讀寫 gating 無作用。
#   · 寫入：由 mission role（MISSION_WRITE/owner）把關；寫 cert 怎麼取得 owner role = P2-13 待解。
# 兩張分離主要為**身分/審計分離**，非能力 gate。空 → 對應功能停用（消費方各自判斷）。
TAK_MARTI_READ_CERT: str = os.getenv("TAK_MARTI_READ_CERT", "")  # 讀 cert PEM（fullchain：leaf+intermediate）
TAK_MARTI_READ_KEY: str = os.getenv("TAK_MARTI_READ_KEY", "")  # 讀 cert 私鑰 PEM
TAK_MARTI_WRITE_CERT: str = os.getenv("TAK_MARTI_WRITE_CERT", "")  # 寫 cert PEM（fullchain）
TAK_MARTI_WRITE_KEY: str = os.getenv("TAK_MARTI_WRITE_KEY", "")  # 寫 cert 私鑰 PEM

# ── Marti 權威 resync（P2-14 (C) / #194 / #173）────────────────────────────────
# :8089 串流不對重連者重播既有靜態標記 → ICS 重啟/斷線會漏 server 已持久化的 marker。
# 解法：拉 Marti `GET /cot/sa?start=&end=` 權威快照逐筆補進 cop_entities（只 upsert 不刪）。
# 回看窗（秒）：每次 resync 抓「now - lookback ~ now」的 SA 快照。
#   ⚠ /cot/sa 大時間窗回 BAD_REQUEST(400)（活 5.7 實測：2h 通、40 天掛；且 400 與 auth 拒
#   共用同一頁，易誤判）→ 預設 2h（known-good 上限），需更長請自行驗證該 server 的窗上限。
TAK_RESYNC_LOOKBACK_S: float = float(os.getenv("TAK_RESYNC_LOOKBACK_S", "7200"))  # 預設 2h（known-good）
# (重)連線後是否自動跑一次 resync 補回 streaming 漏掉的靜態標記（#173 主訴求）。
TAK_RESYNC_ON_CONNECT: bool = os.getenv("TAK_RESYNC_ON_CONNECT", "true").lower() == "true"

# ── COP 軌跡抽樣（P2-06a / #120）──────────────────────────────────────────────
# cop_entity_tracks per-uid 最短寫入間隔（秒）。ATAK 可 >0.5Hz，不節流則每筆位置更新
# 都落一筆軌跡 → 表爆量。抽樣基準 = CoT event time（非 wall-clock）。不同演習場景
# （高速載具 vs 步兵）可經此覆寫，免改 code 重部署。
TRACK_MIN_INTERVAL_S: float = float(os.getenv("TRACK_MIN_INTERVAL_S", "5.0"))

# ── TAK :8089 串流流量管制（TAK-E / #151）────────────────────────────────────
# 全域 token bucket：ingest 每秒最多處理幾筆 CoT（含全部 uid）。防多 uid 高頻 burst
# 持續壓 DB write + WS broadcast（50 ATAK @ 1Hz = 50/s）。超量丟棄該筆、節流 warning、
# 不中斷串流。0 或負值 = 關閉限速（不建議）。預設 60（典型演習人車數 × 1Hz 充裕）。
TAK_INGEST_MAX_EVENTS_PER_SEC: float = float(os.getenv("TAK_INGEST_MAX_EVENTS_PER_SEC", "60"))

# ── 軌跡 PII retention（P2-20 收尾 / #207，threat_model §8.4 政策乙案）─────────
# cop_entity_tracks 超過此天數自動清除（人員行蹤個資不無限保存；90 天前演習將不可 AAR 回放）。
# runtime 開關（Admin）持久化於 config 表 retention.tracks_ttl_enabled，此處為天數參數。
TRACKS_TTL_DAYS: int = int(os.getenv("TRACKS_TTL_DAYS", "90"))

# 註：舊 COP_STALE_REMOVE_WINDOW_S（#160/#161 WIP last-heard 時窗）已於 #161 reality check
# 退場——改對齊 TAK 原生 honor `<archive/>` + honor `stale`（見 cop_entity_repo.list_cop_entities
# 與 memory tak-streaming-archive-stale-vs-mission）。env knob 一併移除避免 dead config 誤導。
