# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
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

# #419：產品 SBOM（CycloneDX）。release build 由 scripts/gen_release_sbom.sh 產
# command-dashboard/sbom/current.cdx.json + Dockerfile 烤入 /app/sbom/（BASE_DIR=/app）。
# 非 release（dev）build 無此檔 → GET /api/sbom 回 404。不放 static/（prod nginx 服務 static
# 會繞過 RBAC）；走 /api/sbom 經 auth_middleware（READ_ROLES）。
SBOM_PATH: Path = BASE_DIR / "sbom" / "current.cdx.json"

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
# PATCH 2.15.1：安全硬化批次——#354 最後 active sysadmin 不得被降級/停用/封存（防自鎖，後端守門 409）
#               + #345 session 生命週期（prod idle 1h + 批次清理稽核分流 SESSION_REAPED）。
# PATCH 2.15.2：#370 RBAC 兜底 fail-open→fail-closed——allowed_roles_for 未登記路徑改回空
#               frozenset()（→403），前置先把所有現役靠兜底的路由顯式登記（golden 零改＝行為等價）
#               + test_no_route_falls_through_to_deny_fallback 完整性守門。純後端。
# PATCH 2.15.3：演練前硬化批次（PR #373）——#293 DOM-XSS sink escaping（decisions/events/admin/
#               pi-data）+ #295 高權帳號 mTLS 下不硬鎖（防戰時 C2 鎖定-DoS）+ #372 稽核鏈閉環
#               （/api/admin/audit-chain/verify + 開機驗證 + 修 overclaim）。
# PATCH 2.15.4：#369 最後 sysadmin 守門 TOCTOU（module 鎖序列化 check+mutate）+ #285 成功動作
#               異常偵測（audit() 中央 hook → 敏感單筆/批次量 SECURITY_ALERT）。純後端。
# PATCH 2.16.2：#389 修正——online/last_seen 改用 updated_at（最後活動）非 received_at
#               （首見、再廣播不更新→live 裝置誤判離線）。dogfood 抓出。
# PATCH 2.16.1：#389 紅藍分類面板 list_clients 補 online 旗標（_is_online，last_seen 時效近似）+
#               docstring 正名。顯示層支援，分類邏輯不變。
# MINOR 2.16.0：#348-F5 P2a + P2b 帳號初始憑證硬化。P2a＝admin 建帳號首登強制改（is_default_pin
#               + auth_middleware per-account 閘 + cop WS 補閘 + reset_pin 限 first-run）；P2b＝
#               create/reset 改系統產隨機臨時 PIN（generate_temp_pin，admin 不自設、一次性回傳）。
# PATCH 2.16.3：#384 移除死功能 Admin PIN（X-Admin-PIN 無後端驗證）+ 升級殘列清理。淨刪碼。
# MINOR 2.17.0：出向忠實度兩刀——#214 出向 CoT 帶 <__group> 隊伍色/角色（正規化欄位、不偽造遙測）
#               + #216 出向 GeoChat（build_geochat_cot + POST /api/tak/chat，COMMAND_ROLES+audit）。
# PATCH 2.17.1：#216 dogfood——出向 DM 補 <marti><dest callsign> 讓 server 只投遞給該呼號
#               （無 dest 時 server 廣播全發、私訊外洩）。入向 DM→ICS 結構限制另開 #397。
# PATCH 2.17.2：#398 Slice 1——tak_device_certs 加 fingerprint/enroll_status 欄（_m033）+ record
#               存它們，供清單顯示 TAK 同步狀態/比對混用。schema 加欄位、無 API 破壞。
# MINOR 2.18.0：#398 Slice 2——撤銷連動 TAK deregister（usermod -D）+ 對帳端點（reconcile，讀
#               UserAuthenticationFile 比對 ICS vs TAK：殭屍/混用/未同步）。registrar 協定加 op。
# MINOR 2.23.0：#318 Slice 3 part③——按 fingerprint 撤盤點外證：POST /tak/revocations/by-fingerprint
#               （盤點外/非 dashboard 發的證，TAK API 不吐連線證 hash → 操作員自取 fingerprint 貼入）。
#               格式驗證 + 禁撤 infra（按 hash 比對，新 tak_revocation.infra_fingerprints/_cert_sha256_fingerprint）
#               + 強制 audit。part④=手動重啟 SOP（無安全的容器內觸發路徑，reality check 定案）。
# MINOR 2.22.0：#318 Slice 3（[#408](https://github.com/winson3QQ/ICS_COMMAND/issues/408)）——撤銷 backfill：
#               POST /tak/revocations/backfill 把所有 ICS 已撤+有 fingerprint 的證一次推進 TAK certificate
#               表（補 #318/Slice 2 上線前撤的證只設帳面、沒寫 TAK 的洞，dogfood 揭露）。infra 跳過、冪等、
#               無 fp 回 skipped 計數（TAK 撤不掉、需重發）。repo list_revoked_with_fingerprint/count_null。
# MINOR 2.21.0：#318 Slice 2——層2 真撤銷 live enforce：CoreConfig x509checkRevocation=true +
#               TAK_DB_* env 接通 → revoke_in_tak 直寫 TAK certificate 表在 :8089 連線層擋撤銷證。
#               reality check 定案：撤銷=降 __ANON__（非硬斷線，+#404 隔離）；新認證即時、在線已認證
#               證需 TAK 重啟清快取才即時（當網路不穩定處理）；fail-open（無 row 放行）活機實證。
# MINOR 2.20.0：#404——TAK 存取控制層1：reconcile 回 groupList + in_anon + anon_users（偵測 producer
#               卡 __ANON__）+ online_anon（在線匿名連線=被刪帳號/未授權仍掛著，補名冊盲區）；新端點
#               strip-anon（usermod -r -g __ANON__）；registrar reconcile 第 3 欄群清單 + strip-anon op。
#               源碼定讞 TAK 永不拒 CA 證，存取控制=group 隔離。
# MINOR 2.19.0：#401——cert 面板以 TAK server 為 SoT：reconcile enrich（per-row ics_cert_id）+
#               新端點 deregister 任一 TAK callsign（infra 大小寫不敏感擋）。管理非 dashboard 發的殭屍。
# PATCH 2.23.1：#431——enrollment 模式發證補綁 cert fingerprint（usermod -f）→ 修「reconcile 未同步 /
#               證不可撤」；行為改變故 PATCH+1。
# MINOR 2.24.0：#434——容器化 WireGuard + ICS 統管 VPN：發裝置證連帶產 keypair/配 IP/註冊 peer/夾 .conf+QR
#               外層 bundle（一站式）+ 撤證連動撤 peer。完整新功能（實機 E2E 過：發證→自動 WG→裝置同上 TAK）。
# PATCH 2.24.1：#434 review fix——IP 配號並發 IntegrityError 重試 + admin WG backstop（WG 任何例外不擋發證）。
# PATCH 2.24.2：#434 follow-up——GET /api/admin/wg/peers（WG peer 帳本，sysadmin 唯讀）供前端顯示。
# PATCH 2.24.3：#434——WG peer 在線指示（registrar status op 回 wg latest-handshakes + peer_handshakes 合併）。
# MINOR 2.25.0：VPN-gate 儀表板——裝置 .conf 的 AllowedIPs 加公網 IP（WG_EXTRA_ALLOWED_IPS）使瀏覽器經
#               tunnel 用原網址連儀表板（SAN 不變）+ POST /wg/issue（帳號發 WG VPN，label=username，給單獨
#               連 ICS 的人）+ POST /wg/peers/revoke（撤 WG-only peer）。「ICS 也走 VPN」（公網收口待移 :443）。
# MINOR 2.26.0：#344 紅藍分類改綁 cert CN（穩定）非 uid——加 client_identity（m035，uid→CN 快取，從
#               subscriptions/all 寫入）+ ingest faction 解析經快取翻 uid→CN + 面板改列「發證後且在線」
#               （subscriptions ∩ tak_device_certs，CN 鍵）→ 裝置重裝/重 enroll 換 uid 不丟分類。
# PATCH 2.26.1：#344 Slice 2——背景週期（45s）刷新 client_identity（uid→CN），免「裝置換 uid 重連後須
#               先開紅藍分類面板才著色」的窗口（最多一輪詢週期即自動解析 faction）。
# PATCH 2.28.1：#463 公測回報——出向 GeoChat 放寬 operator（role_enum 窄洞 POST /api/tak/chat
#               COMMAND→WRITE，比照 #180 share；audit-first + 發話者身分 server 端決定不變，
#               observer 仍唯讀）。
# MINOR 2.28.0：ICS 登入憑證桌機安裝包——新 fmt=zip（cert_issuance.build_cert_package）打包 .p12 + root-ca.pem
#               + 分平台 README（Windows/macOS/Android 安裝步驟），收斂原本「裸證 + 另抓 root CA + 一大段文字」
#               的多 use-case 複雜度（比照 TAK data package）。密碼走「乙」不入包、仍經 X-P12-Password 顯示。
#               iOS 維持 .mobileconfig（已自成一檔）。
# PATCH 2.27.2：TAK 裝置證發證 callsign 驗證——callsign(=TAK managed-user 帳號)發前套 TAK new-user 規則
#               (≥4 字、僅 [A-Za-z0-9._-])，**僅當線上 enrollment 配置時**(#429 路徑),回乾淨 422 取代
#               TAK 400→502(BB/GGW 短英數、空格、@、中文皆中招)；offline(#344) 沿用較寬規則保留 #324 中文證。
# PATCH 2.27.1：#267 部署後兩 bug 修——(1) 演習刪不掉：prod audit_log append-only(#348) × audit→exercises
#               FK RESTRICT 死結 → m039 拆 FK + 移出 delete cascade（audit 不可變、刪場不抹）；client_faction
#               /cop_entity_tracks 補進清單。(2) iTAK 演習結束後從地圖消失：entity 凍結在 archived 場、待命
#               視圖濾掉 → archive/activate 自動 restamp_all_tak_entities 釋放回 NULL；軌跡 denormalize
#               exercise_id（m038）使 entity 改 scope 不破 AAR；m040 一次性收斂既有殘留。
# MINOR 2.27.0：#267 感測層 scope 重構——entity 屬某場 = 唯一 active 場 × producer CN 在 roster ×
#               ts 在活躍窗（純乙：空 roster=沒人，不 auto-capture），取代舊「insert-time current_exercise_id
#               蓋死」。ingest 接線 _resolve_exercise_scope（覆寫 normalize 預設、在 faction 前）；roster 變動/
#               「加入全部連線」即時重 stamp live 點。新端點 POST /api/admin/exercises/{id}/roster/add-connected。
# PATCH 2.26.2：security 收緊——#393 通用 /api/config/{key} GET/POST 收成 SYSADMIN_ONLY（原 observer 可讀/
#               commander 可寫任意 key）；#375 suspend-all 納 _SYSADMIN_GUARD_LOCK + re-assert 發起者仍
#               active sysadmin（並發 demote 發起者→拒，防達零 sysadmin 自鎖）。
# MINOR 2.29.0：#467 節點推 TAK——出向分享時依 node_type 套 per-type 2525 CoT 符號（tak_downlink
#               _NODE_COT_TYPE 字典；實作 mil_symbol 預留的 cot_type 字典擴充），現場端可辨指揮部/
#               醫療組/安全組/前進組/收容組。存儲 type 不動、出向才換（server-authoritative）。設施延後。
APP_VERSION = "2.29.0"

# CMD_VERSION：前端 UI 功能版本（不同於後端 SemVer APP_VERSION；規則見 CLAUDE.md 版號規則）
# 兩軌版本命名，不可混用。由 /api/version 提供給前端，是唯一 source-of-truth；release 時更新此值。
# v1.0.0：拆分自 ICS_DMAS 後首個完整可用形態（MapLibre 地圖引擎全換 P1-10b + PWA 移除 P1-11
#         + 演習/放置/稽核 UI P1-13/14/16/#93 + 分類編輯器 #66）→ 0.x 畢業為 MAJOR 紀元。
CMD_VERSION: str = os.getenv(
    "CMD_VERSION",
    "v1.22.0",  # MINOR：#467 節點手動廣播到 TAK——節點 modal 加「📡 廣播」鈕（指揮層 + TAK 啟用）；
    # 現場端依 node_type 顯示不同 2525 符號。設施延後（#467 follow-up）。
    # PATCH v1.21.2：#260 D1——route 頂點編輯同步 attributes.link（geoLinks⟷vertices lockstep）→
    # 修 reshape 後形狀不進 attributes.link、廣播送舊形狀、回灌打回原狀的 round-trip bug（真機 dogfood 定位）。
    # PATCH v1.21.1：#463——通聯 compose 送出框對 operator 顯示（後端同步放寬 WRITE_ROLES）。
    # MINOR v1.21.0：route 命名 waypoint（SP/CP/TGT）顯示成 map marker+label（#260 C2 / #464）——
    # 圓點+白字標出戰術 checkpoint（`attributes.link` 的 b-m-p-w+callsign），control point 不畫、
    # 純自建 route 無 link 不顯示（命名/編輯=#260 D）；移動時 waypoint 隨 link 同步位移。真機 4-route dogfood PASS。
    # v1.20.0：發證面板桌機改發「安裝包 .zip」（含 root CA + 分平台 README）——一個檔到位、瘦身
    # 原本落落長的多平台說明文字；密碼仍只在面板顯示（乙）。iOS 維持 .mobileconfig。
    # PATCH v1.19.2：ICS .p12 憑證「分享 / 存檔」在 Mac 桌機 navigator.share 丟 Permission denied(NotAllowedError)
    # → 對齊 #330 退回下載(原其餘 3 個 share 鈕已有、唯 .p12 漏 → 只它 alert「分享失敗」)；Windows share 成功時行為不變
    # PATCH v1.19.1：TAK 發證面板修——① 憑證時間改顯本地時區(fmtLocalDT,原顯 UTC 差 8h)；
    # ② callsign 提示照 TAK 帳號規則(≥4 字/英數._-)發前硬擋 + 講清「中文名設在 App 顯示 callsign」
    # MINOR v1.19.0：#267 演習 roster 可用性 UI——隊伍名冊頭顯「X 連線 / Y 在場」計數 + 空場紅字警示橫幅
    # （在場數==0 → 顯，鍵在結果故對「逐一納編」與「一鍵全加」兩套機制都正確）+「加入全部連線」一鍵鈕
    # PATCH v1.18.1：#344 紅藍分類面板文案——改「發證後且在線（CN 為準，不受 callsign/uid 變動）」
    # MINOR v1.18.0：VPN-gate 儀表板——帳號管理裝置憑證面板加「📶 發 VPN」（label=username）+ WG 帳本列加撤除鈕
    # PATCH v1.17.1：#434——WG peer 帳本段每列加 🟢/⚪ 在線指示（近期握手≈在線）
    # MINOR v1.17.0：#434 follow-up——發證結果面板顯 WG 配置狀態（X-WG-Status）+ TAK 面板附 WG peer 帳本段
    # PATCH v1.16.3：#318 Slice 3 part③——TAK 面板「撤銷盤點外的證（按 fingerprint）」輸入 + 撤在線證重啟 SOP 提示
    # PATCH v1.16.2：#318 Slice 3——TAK 面板「↑ 撤銷補登 TAK」鈕（backfill）+ 已撤無 fp 證標「⚠ TAK 撤不掉」
    # PATCH v1.16.1：#318——撤銷文案誠實化（降 __ANON__ 隔離、非硬斷線、在線證需重啟）+ 回饋 tak_revoke 結果
    # MINOR v1.16.0：#404——TAK 面板示警卡 __ANON__（面板級+per-row ⚠）+ 一鍵「移出匿名群」+「在線匿名連線」段
)  # MINOR v1.15.0：#401——TAK 裝置證面板改以 TAK server 為準（列全部帳號）+ 直接移除殭屍/從 TAK 刪
# PATCH v1.13.2：#398 Slice 1——清單顯示同步狀態/fingerprint + 發證前防呆（同名重發/中文 callsign）。
# PATCH v1.13.1：#216 dogfood——通聯時間改本地時區顯示（對齊 header 時鐘，原顯示 UTC 慢 8 小時）。
# MINOR v1.13.0：#216 出向 GeoChat compose 面板（通聯面板收+發、目標跟隨脈絡、COMMAND_ROLES）。
# PATCH v1.12.3：#384 移除「Admin PIN」驗證畫面 + 子分頁（死功能）。
# PATCH v1.12.2：#389 紅藍分類面板——「實戰池」正名為「待命池」+「含已離線」文案 + 每行 🟢/⚪ 在線 badge。
# PATCH v1.12.1：#348-F5 P2b hotfix——臨時 PIN modal 被帳號管理面板(z300)蓋住看不到 → 改自帶 z10000 overlay。
# MINOR v1.12.0：#348-F5 P3 密碼欄放寬接受密語（登入/首登/PinLock 解鎖，6→128）+ show-password 眼睛。
# PATCH v1.11.2：#293 前端 DOM-XSS sink escaping（純前端硬化、非視覺）。
# PATCH v1.11.1：#354 帳號管理 UI 鎖定最後一個 active sysadmin 的降權控制（角色 select + 停用鈕禁用 + 提示）。
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

# ── #344：TAK 裝置 enrollment registrar（發證即註冊 managed user + 初始群）────────────
# #315 發證原本只簽證、不註冊 → 裝置一連落匿名 __ANON__ → PR #363 的 REST update-groups 改不動
# （faction 分類對它靜默跳過）= 系統性缺口。本機制在發證時把裝置證 fingerprint 註冊成 TAK managed
# user + 初始群 neutral（fail-closed），之後 admin 紅藍分類走 REST update-groups 即生效。
# 為何要 registrar 而非 ICS 自己跑：usermod/certmod 是 **server-coupled**（連 takserver 本機 IPC 熱
# 套用），ICS（獨立 netns、無 Java）跑會 timeout；裸寫 UserAuthenticationFile.xml 跑著的 server 不認
# 且會被 re-marshal 清掉（2026-06-23 PoC 實證，memory tak-faction-group-identifier）。解法＝與 takserver
# **共享 network namespace** 的 registrar sidecar，經**共享卷檔佇列**收 ICS 請求後本機跑 usermod。
# 空 queue dir → enrollment 停用（發證仍出證、但落匿名待 roster 補；不影響 #343 視圖層）。
TAK_ENROLL_QUEUE_DIR: str = os.getenv("TAK_ENROLL_QUEUE_DIR", "")  # 與 registrar 共享的卷掛載點，如 /registrar-queue
TAK_ENROLL_DEFAULT_GROUP: str = os.getenv("TAK_ENROLL_DEFAULT_GROUP", "neutral")  # 初始群（fail-closed，未分類即孤立）
TAK_ENROLL_TIMEOUT_S: float = float(os.getenv("TAK_ENROLL_TIMEOUT_S", "8.0"))  # 等 registrar 結果逾時（best-effort）

# #429 ICS 代理 enrollment（dashboard signClient 發證）：TAK 憑證註冊埠 :8446 的對內 URL。
# 空 → 代理發證停用（dashboard 回退 offline 簽 / 或回 503）。容器內網用 https://takserver:8446。
TAK_ENROLL_URL: str = os.getenv("TAK_ENROLL_URL", "")

# #434 容器化 WireGuard：ICS 經共享卷檔佇列驅動 ics-wg 容器加/刪 peer（services/wg_provision）。
# 空 queue dir → WG peer 控制停用（發證仍出 TAK 證、VPN peer 待手動/重試；不影響證流程）。
WG_QUEUE_DIR: str = os.getenv("WG_QUEUE_DIR", "")  # 與 ics-wg 容器共享的卷掛載點，如 /wg-queue
WG_SUBNET_PREFIX: str = os.getenv("WG_SUBNET_PREFIX", "10.13.13.")  # VPN 子網前綴（peer /32 須落此段，fail-closed）
WG_PEER_TIMEOUT_S: float = float(os.getenv("WG_PEER_TIMEOUT_S", "8.0"))  # 等 ics-wg 容器結果逾時（best-effort）
# 組裝裝置端 .conf 用：server 公鑰（ics-wg 容器開機產，寫在 /wg-queue/server.pub，部署時填此）+ 對外端點。
WG_SERVER_PUBKEY: str = os.getenv("WG_SERVER_PUBKEY", "")  # ics-wg 的 server pubkey（裝置 .conf 的 [Peer] PublicKey）
WG_ENDPOINT: str = os.getenv("WG_ENDPOINT", "")  # 對外 WG 端點 公網IP:port（裝置 .conf 的 Endpoint），如 1.2.3.4:51820
# 額外導進隧道的 AllowedIPs（逗號分隔 CIDR，併在 VPN 子網之後）。用途＝VPN-gate 儀表板：填儀表板公網 IP/32
# （= WG_ENDPOINT 的 host）→ 瀏覽器照用原網址 https://公網IP，封包改走 tunnel → DNAT :443 → nginx（server
# cert SAN 不必含 WG 私網 IP）。WG 自身 transport 封包(往 Endpoint:port)由 fwmark 排除在隧道外，不成迴圈。
WG_EXTRA_ALLOWED_IPS: str = os.getenv("WG_EXTRA_ALLOWED_IPS", "")  # 如 "1.34.230.218/32"

# ── #318 層2 真撤銷：ICS 直連 TAK Server postgres ──────────────────────────────
# reality check（2026-06-26，#318）：TAK 對 CA 信任的證 TLS 不拒、deregister 只降匿名(__ANON__)；
# 唯一「連都連不進」= 撤銷＝`certificate` 表有該證 hash+revocation_date + CoreConfig x509checkRevocation=true
# → X509Authenticator findOneByHash 命中 RevokedException（**CRL 只擋 :8443 不擋 :8089 串流**，故走 DB）。
# certadmin REST 無「補登 offline 證」端點 → 只能直寫 postgres（ICS 與 tak-database 同 docker net 可達 :5432）。
# 空 TAK_DB_HOST/PASSWORD → 撤銷僅 ICS 帳面 + deregister（不寫 TAK，#318 enforce 停用）。
TAK_DB_HOST: str = os.getenv("TAK_DB_HOST", "")  # 如 tak-database
TAK_DB_PORT: int = int(os.getenv("TAK_DB_PORT", "5432"))
TAK_DB_NAME: str = os.getenv("TAK_DB_NAME", "cot")
TAK_DB_USER: str = os.getenv("TAK_DB_USER", "martiuser")
TAK_DB_PASSWORD: str = os.getenv("TAK_DB_PASSWORD", "")  # ⚠ 汰 dev `takdevpass123`（threat_model §8.4 at-rest）

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
# #348-F10：chats（通聯）超過此天數自動清除。chats 含 message/callsign/lat-lon 個資，且**既不在
# exercise-cascade、又無 TTL** → 連刪演習都清不掉、永久累積。與 tracks 同政策窗（90 天後該場通聯
# 不可再 AAR 回放），共用同一 retention 開關（retention.tracks_ttl_enabled）。
CHATS_TTL_DAYS: int = int(os.getenv("CHATS_TTL_DAYS", "90"))

# 註：舊 COP_STALE_REMOVE_WINDOW_S（#160/#161 WIP last-heard 時窗）已於 #161 reality check
# 退場——改對齊 TAK 原生 honor `<archive/>` + honor `stale`（見 cop_entity_repo.list_cop_entities
# 與 memory tak-streaming-archive-stale-vs-mission）。env knob 一併移除避免 dead config 誤導。
