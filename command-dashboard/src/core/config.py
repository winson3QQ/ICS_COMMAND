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

# ── Live DB at-rest 加密（P1-12c / #229）──────────────────────────────────
# ICS_DB_ENCRYPTED=1 → live DB 走 SQLCipher driver，連線後立即 PRAGMA key。
#   金鑰來源 = DB_KEY env（P1-12a unlock_key.py 提供 = HKDF child[1] db-v1，
#   child.hex() → 剛好 64 hex 字元 = 256-bit raw key，PRAGMA key 用 x'..' 形式
#   直接當 raw key、不再經 SQLCipher KDF）。
# 未設（預設）→ 原生 sqlite3（dev / CI / Windows 無 wheel / 漸進部署）。
# ⚠ Windows 無 sqlcipher3 wheel（已實測）→ 本機只能跑明文，加密路徑由 CI（ubuntu）驗。
DB_ENCRYPTED: bool = os.getenv("ICS_DB_ENCRYPTED", "false").lower() in ("1", "true", "yes")
DB_KEY_ENV = "DB_KEY"

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
# MINOR：#267 納編/退編 —— POST /api/exercises/{id}/enroll（限 COMMAND）改 cop_entity exercise_id
# 進當前 active 場 / 退回 NULL；雙廣播（舊 scope delete / 新 scope create）+ CAS 重試 + audit。
# PATCH 2.7.1：紅隊公網曝面修補（#286 任意檔寫入 / #287 TTX 授權 / #288 events·decisions 跨場
# IDOR / #289 nginx headers / #290 部署 fail-closed），行為變更（授權收緊 + 上傳清洗）。
# PATCH 2.7.2：線上發證 p12 改 --legacy（PBE+SHA1+3DES/RC2）—— iOS 不吃 openssl3/step 預設
# PBES2/AES-256 p12（誤報密碼錯），dogfood 公網實機定位；桌機相容不變（#307）。
# PATCH 2.7.3：p12 匯入密碼改每張隨機（廢弱默認 icsclient，對齊 threat_model H6）+ 經
# X-P12-Password header 回前端顯示（行為變更，非新介面）（#307 衍生子缺口）。
# MINOR 2.8.0：線上發證新增 iOS .mobileconfig 格式（/certs/issue?fmt=mobileconfig；root CA +
# p12 + 內嵌密碼一包，iOS 安裝免打憑證密碼）+ build_mobileconfig/fetch_root_ca_pem（#312）。
# MINOR 2.9.0：mTLS bootstrap 窗口（#306）—— 全新部署（唯一帳號 + account_certs 零 row 單向閂）
# 下首位 admin 用 CA 已驗的證即可登入+綁第一張證，免手動翻 ICS_MTLS_REQUIRED；綁定後自動關窗。
# MINOR 2.10.0：TAK 裝置證自助發放（#315 P2-26 L2）—— POST /api/admin/tak/device-cert：
# 由 ICS-TAK-SVC-CA（offline，TAK 自己的 CA）簽 + 組 ATAK/iTAK data package（p12+truststore+pref，
# 密碼內嵌）；sysadmin+audit。reality check：TAK 只信此 CA、非 step-ca（與 ICS 登入證隔離）。
# MINOR 2.11.0：TAK 裝置證盤點（#317）—— 自建 tak_device_certs 表（記 serial，#318 CRL 前置）+
# GET /tak/device-certs + POST .../revoke（撤銷=帳面 flag，不 enforce；真撤銷 CRL 見 #318）。
# MINOR 2.12.0：DELETE /tak/device-certs/{id}（#325，刪已撤銷盤點紀錄；僅 revoked，active 拒）
# + 修中文 callsign 發證 500（#324，Content-Disposition 非 latin-1 檔名 → RFC5987 filename*）。
# MINOR 2.13.0：GET /api/admin/ca/root（#327，下載 step-ca root CA PEM 供桌機信任 server）。
# PATCH 2.13.1：ATAK 裝置證 pref 補 deviceProfileEnableOnConnect（#329，對齊實機成功包）。
# PATCH 2.13.2：reset-db/reset-exercise 補清 chats 表（#237，AAR 乾淨起點 + PII 隨 reset 清）。
# PATCH 2.13.3：AAR timeline track payload 多帶 cot_type（#335）+ _record_track 濾 (0,0) 壞點（#3）。
# MINOR 2.14.0：AAR timeline 全源完整——polygon/route 區域生命週期（#338）+ 事件帶 marker/位置歷史
#               + 敵我接觸（kind=contact）source（#339）；audit detail 記區域整包 attributes 快照。
# MINOR 2.15.0：紅藍陣營隔離（#343/P2-37）——client_faction 分類 + cop_entities.faction（v31）+
#               chats.faction（v32）+ admin /api/admin/factions/* + 三層 server-side 強制點 + AAR 互斥閘。
APP_VERSION = "2.15.0"

# CMD_VERSION：前端 UI 功能版本（不同於後端 SemVer APP_VERSION；規則見 CLAUDE.md 版號規則）
# 兩軌版本命名，不可混用。由 /api/version 提供給前端，是唯一 source-of-truth；release 時更新此值。
# v1.0.0：拆分自 ICS_DMAS 後首個完整可用形態（MapLibre 地圖引擎全換 P1-10b + PWA 移除 P1-11
#         + 演習/放置/稽核 UI P1-13/14/16/#93 + 分類編輯器 #66）→ 0.x 畢業為 MAJOR 紀元。
CMD_VERSION: str = os.getenv(
    "CMD_VERSION", "v1.11.0"
)  # MINOR v1.11.0：admin 後台「紅藍」分類 tab（#343/P2-37）——列連線 client + 🔵🔴⚪ 分類 + 單物件 override。
# MINOR v1.10.0：AAR 回放上圖功能組完整——區域（#338）+ 事件/敵我接觸（#339，重用 live milsymbol/
#  NAPSG 符號、隨 T 移動）；B1-B3 含 iPad/iPhone 觸控 human verify 全齊（#201），故進 frontend MINOR。
# PATCH v1.9.3：桌機憑證 UX（#327）——「下載 root CA」鈕 + .p12 標 Windows/iMac/Android + 信任提示。
# PATCH v1.9.2：TAK tab 已撤銷裝置證列加「刪除」鈕（#325；刪紀錄≠撤證）。
# PATCH v1.9.1：TAK tab 加「已發裝置證」盤點列表 + 撤銷-flag（#317；標明不 enforce）。
# v1.9.0：admin TAK tab（#315 P2-26 L2）—— TAK 連線開關搬入 + TAK 裝置證自助發放 UI
# （callsign + ATAK/iTAK 平台 + 下載/分享 data package）。
# v1.8.0：iOS 零打憑證密碼接入（#312）—— 發證格式選單 + .mobileconfig 流程（下載/分享，
# 密碼內嵌、安裝免打）。v1.7.1：裝置憑證面板 UX（#307）發證顯密碼/下載/分享、revoked 摺疊+清除。
# MINOR：P2-24 前端尾（#164）TAK runtime
# 控制 UI 功能組 —— 系統 tab sysadmin 開/關 toggle
# + 唯讀連線狀態行 + header 燈號認實化（running/configured 區分，消除「沒 task 卻顯斷線重連」謊報）
# PATCH(v1.4.1)：#265 —— 切換演習後不再需硬重整即即時 render（cop_stream onclose identity guard +
# stop() backoff 重置 + _refreshAfterExerciseSwitch 改就地 resync 不清快取，消雙 socket race）。
# MINOR(v1.5.0)：#269/#267 切片1 —— 右欄四-tab 重構（事件追蹤｜通聯｜隊伍｜待裁示，各整欄高 + 紅圈計數
# + per-session tab 記憶）+ 新 TAK 隊伍名冊（按 team_color 分組、只列友軍、敵情接觸排除）。納編/定址/編組
# 動作佔位，後端分批接。
# MINOR(v1.6.0)：#267 常駐層疊看前端 —— 演習中疊顯 NULL 常駐單位（地圖圖層 toggle 限指揮層、預設關、
# 無 active 演習時常駐恆顯）+ roster 標「常駐」候選 + cop_stream 帶 standing/include_standing。
# MINOR(v1.7.0)：#267 納編/退編 UI —— roster per-unit「納編」（常駐→active 場）/「退編」（→NULL）鈕
# （限指揮層、演習中現），接 POST /api/exercises/{id}/enroll；雙廣播後 cop_stream 就地過渡。
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

# ── 部署環境 dev / prod ───────────────────────────────────────────────────────
# 同一份程式碼靠環境變數切行為。
#   dev（預設，開發機）：/docs、ReDoc、OpenAPI、根 dev 導覽頁全開 → 開發方便。
#   prod（佈署容器設 ICS_ENV=prod）：上述一律關閉，app 自身不對外吐出 API 探索面
#                                    與 admin/docs 導覽，不依賴反代遮蔽（縱深防禦）。
# DOCS_ENABLED 可單獨覆寫（如 staging 想開文件）：ICS_DOCS_ENABLED=true/false。
ICS_ENV: str = os.getenv("ICS_ENV", "dev").lower()  # "dev" | "prod"
IS_PROD: bool = ICS_ENV == "prod"
DOCS_ENABLED: bool = os.getenv("ICS_DOCS_ENABLED", "false" if IS_PROD else "true").lower() == "true"

# Build 戳記：build 時由 `--build-arg ICS_BUILD_ID`（git short sha + dirty + 時間）注入,dev 預設 "dev"。
# /api/version 回傳、登入頁顯示 → 可辨識「實際跑的是哪個 build」(版號常數無法分辨每次 rebuild)。
BUILD_ID: str = os.getenv("ICS_BUILD_ID", "dev")

# #275 mTLS：是否強制 client 憑證（prod/演練 on、dev/demo 預設 off）。
# on 時 login + middleware 驗 X-Client-Cert-Verify=SUCCESS 且 cert CN 綁定帳號
# （cert = MFA「持有」第二因子，與 PIN 構成 AAL2）。見 security_policies §2.8。
ICS_MTLS_REQUIRED: bool = os.getenv("ICS_MTLS_REQUIRED", "false").lower() == "true"

# #275 wave 4：反代信任。on 時信任 nginx 設的 X-Real-IP（= 真實 $remote_addr）取 client IP；
# off（直連/dev）時忽略可偽造的 X-Forwarded-For，改用 request.client.host（防 §8.6 XFF 偽造
# 削弱 IP 限速）。容器/正式部署經 nginx → 設 true。
ICS_BEHIND_PROXY: bool = os.getenv("ICS_BEHIND_PROXY", "false").lower() == "true"

# #280 紅隊修補：nginx ↔ 後端共享密鑰。設了之後，後端只在請求帶相符 X-Proxy-Auth 時
# 才信任 nginx 注入的 X-Client-Cert-*（mTLS 第二因子）。防「內網直打後端 :8000 偽造
# cert header 繞過 mTLS」（紅隊實證可拿 sysadmin）。空＝back-compat（信任，舊行為）。
# 容器/正式部署務必設（與 nginx 同值；nginx 用 envsubst 注入）。
ICS_PROXY_SHARED_SECRET: str = os.getenv("ICS_PROXY_SHARED_SECRET", "")

# #280 H：安全告警 webhook（選配）。設了才推；空＝只進結構化 log（SECURITY_ALERT）。
# 任意 HTTP endpoint（Slack/Discord/自架收集器…）；背景緒推、失敗不影響鑑權。
ICS_SECURITY_WEBHOOK_URL: str = os.getenv("ICS_SECURITY_WEBHOOK_URL", "")

# #275 wave B-2：面板「發憑證」線上簽發（選項 i 安全版）。後端**不持 CA 鑰**，改呼叫
# step-ca daemon（provisioner token）請它簽 → 回傳 p12。CA 鑰始終只在 daemon。
# 未配置（STEP_CA_URL 空）時 /certs/issue 回 503，面板僅保留「手動綁定」（離線簽 fallback）。
STEP_CA_URL: str = os.getenv("STEP_CA_URL", "")  # 如 https://step-ca:9000；空=未配置
STEP_CA_PROVISIONER: str = os.getenv("STEP_CA_PROVISIONER", "ics")
# provisioner 密碼以檔案提供（不進 log / 不進 env dump）；root 用 fingerprint 驗
STEP_CA_PROVISIONER_PASSWORD_FILE: str = os.getenv("STEP_CA_PROVISIONER_PASSWORD_FILE", "")
STEP_CA_FINGERPRINT: str = os.getenv("STEP_CA_FINGERPRINT", "")  # root_ca.crt 指紋（直給）
# 指紋每次 CA init 變動，容器棧難寫死 → 也支援從檔讀（bootstrap 寫進共享 volume）
STEP_CA_FINGERPRINT_FILE: str = os.getenv("STEP_CA_FINGERPRINT_FILE", "")
# #307：未設＝每張發證隨機產 p12 密碼（廢除弱默認 icsclient，對齊 threat_model H6）；
# 顯式設了才用固定值（runbook / 自動化相容）。實際採用值見 cert_issuance._p12_password()。
STEP_CLIENT_CERT_P12_PASS: str | None = os.getenv("STEP_CLIENT_CERT_P12_PASS") or None
STEP_CLIENT_CERT_DURATION: str = os.getenv("STEP_CLIENT_CERT_DURATION", "2160h")  # 90 天


def step_ca_fingerprint() -> str:
    """root 指紋：優先 env，否則讀 *_FILE（容器棧由 bootstrap 寫入共享 volume）。"""
    if STEP_CA_FINGERPRINT:
        return STEP_CA_FINGERPRINT.strip()
    if STEP_CA_FINGERPRINT_FILE and os.path.isfile(STEP_CA_FINGERPRINT_FILE):
        try:
            with open(STEP_CA_FINGERPRINT_FILE, encoding="ascii") as f:
                return f.read().strip()
        except OSError:
            return ""
    return ""


def step_ca_configured() -> bool:
    """線上發證地基是否齊備（URL + provisioner 密碼檔 + root 指紋）。"""
    return bool(STEP_CA_URL and STEP_CA_PROVISIONER_PASSWORD_FILE and step_ca_fingerprint())


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
# #315 P2-26 L2：TAK 裝置證 data package 的 connectString 用「對外可達 TAK 位址」——
# 不是容器內網 TAK_COT_URL（takserver:8089）；裝置（ATAK/iTAK）連的是公網/LAN IP。
# 空 → 發證端點回 503（部署層未設對外位址）。port 預設 8089（TAK CoT streaming）。
TAK_DEVICE_CONNECT_HOST: str = os.getenv("TAK_DEVICE_CONNECT_HOST", "")  # 對外 TAK IP/網域
TAK_DEVICE_CONNECT_PORT: int = int(os.getenv("TAK_DEVICE_CONNECT_PORT", "8089"))
# #315 reality check：TAK 裝置證**必須由 TAK 自己的 CA（ICS-TAK-SVC-CA）簽**——TAK truststore
# 只信它、不信 step-ca（step-ca 證被 peer not verified）。此 dir 含 tak-ca.pem + tak-ca.key
# （offline 簽，同 deploy/.../gen-device-pkg.sh）。空 → 發 TAK 裝置證回 503。與 ICS 登入證的
# step-ca **刻意隔離**（#305：儀表板 cert 碰不到 TAK）。
TAK_DEVICE_CA_DIR: str = os.getenv("TAK_DEVICE_CA_DIR", "")  # 如 /tak-certs/_ca

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
# #344/#357：管理級 cert（ROLE_ADMIN，certmod -A）—— user-management API（group 管理）需 admin，
# read/write cert 不夠。空 → faction 分類不同步 TAK group（純 ICS 視圖層，#343 仍運作）。
TAK_MARTI_ADMIN_CERT: str = os.getenv("TAK_MARTI_ADMIN_CERT", "")  # admin cert PEM（fullchain）
TAK_MARTI_ADMIN_KEY: str = os.getenv("TAK_MARTI_ADMIN_KEY", "")  # admin cert 私鑰 PEM

# ── Marti 權威 resync（P2-14 (C) / #194 / #173）────────────────────────────────
# :8089 串流不對重連者重播既有靜態標記 → ICS 重啟/斷線會漏 server 已持久化的 marker。
# 解法：拉 Marti `GET /cot/sa?start=&end=` 權威快照逐筆補進 cop_entities（只 upsert 不刪）。
# 回看窗（秒）：每次 resync 抓「now - lookback ~ now」的 SA 快照。
#   ⚠ /cot/sa 大時間窗回 BAD_REQUEST(400)（活 5.7 實測：2h 通、40 天掛；且 400 與 auth 拒
#   共用同一頁，易誤判）→ 預設 2h（known-good 上限），需更長請自行驗證該 server 的窗上限。
TAK_RESYNC_LOOKBACK_S: float = float(os.getenv("TAK_RESYNC_LOOKBACK_S", "7200"))  # 預設 2h（known-good）
# (重)連線後是否自動跑一次 resync 補回 streaming 漏掉的靜態標記（#173 主訴求）。
TAK_RESYNC_ON_CONNECT: bool = os.getenv("TAK_RESYNC_ON_CONNECT", "true").lower() == "true"

# ── 紅藍陣營隔離（#343）──────────────────────────────────────────────────────
# 強制過濾總開關。預設 OFF：上線即 fail-closed（未分類 tak entity 對 commander 全部消失），
# 故須演習前先把藍軍 roster 分類完、再開此旗標（避免「藍軍單位在分類前集體隱形」）。
# OFF 時 list/WS 完全不帶 visible_factions → 行為與隔離前完全一致（零風險漸進啟用）。
FACTION_ISOLATION_ENABLED: bool = os.getenv("ICS_FACTION_ISOLATION", "false").lower() == "true"

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
