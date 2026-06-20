# ICS_DMAS Threat Model

> **依據**：NIST SP 800-53 RA-3 要求之威脅與風險分析；採 STRIDE 方法論。
> **用途**：稽核證據、投標安全架構說明、開發者資安訓練教材。
> **狀態**：骨架建立（Session D 完稿）。
> **最後更新**：2026-04-25

---

## 1. 系統概述（Scope）

### 1.1 組件

| 組件 | 技術棧 | 部署位置 | 信任等級 |
|---|---|---|---|
| Command Dashboard | FastAPI + SQLite + nginx | 指揮部主機（N100 或雲端 VM）| 高（資料主控）|
| Pi Server | Node.js + better-sqlite3 + WS | Raspberry Pi 500（現場）| 中（現場裝置）|
| Shelter PWA | HTML/JS + Dexie + Service Worker | iPad / Android 瀏覽器 | 中（iOS/Android 受作業系統保護）|
| Medical PWA | HTML/JS + Dexie + Service Worker | iPad / Android 瀏覽器 | 中（含傷患個資）|
| step-ca | Go + SQLite | 每客戶獨立 instance | 高（PKI 根信任）|

### 1.2 資料分類

_Session B 細化；當前粗分_：

| 分類 | 舉例 | 處理要求 |
|---|---|---|
| PII 敏感 | 病患姓名 / 年齡 / 症狀 / 過敏史 | 加密儲存 + 存取稽核 + 72h 通報 |
| PII 一般 | 帳號 username / display_name | 存取稽核 |
| 演練資料 | event / decision / snapshot | 完整性（hash chain）+ 保存 6 個月 |
| 系統 config | API keys / admin PIN / TLS 私鑰 | 檔案權限 600 + 靜態加密 |
| 公開 | 版號 / health endpoint | 無特別要求 |

---

## 2. 信任邊界圖（Trust Boundaries）

_Session D 用 ASCII 或 diagram 工具產出。骨架如下：_

```
  ┌─────────────────┐   ① Browser ↔ Command（HTTPS TLS1.2+）
  │ 指揮部人員       │────────────────────────┐
  │ (browser)       │                         │
  └─────────────────┘                         ▼
                                    ┌──────────────────────┐
                                    │  Command Dashboard   │
                                    │  (FastAPI + SQLite)  │
                                    └──────────────────────┘
                                              ▲
                                              │ ④ Pi push（HTTPS POST）
                                              │
  ┌─────────────────┐   ② PWA ↔ Pi      ┌──────────────────┐
  │ 志工 iPad /      │───────────────── │  Pi Server       │
  │ Android (PWA)   │   HTTPS + WSS     │  (Node.js + WS)  │
  └─────────────────┘                   └──────────────────┘
                                              ▲
                                              │ ③ nginx ↔ FastAPI
                                              │   loopback HTTP（零風險）
                                              │
                                    ┌──────────────────────┐
                                    │   step-ca (PKI)      │
                                    │   per-customer       │
                                    └──────────────────────┘
```

**4 條鏈路 + 1 個 PKI 根信任**，每條都需要在 matrix §SC（Comms Protection）對應。

---

## 3. STRIDE 威脅清單

> **S**poofing / **T**ampering / **R**epudiation / **I**nformation Disclosure / **D**enial of Service / **E**levation of Privilege

### 3.1 Spoofing（身份偽冒）

_Session D 填入。初步清單：_

- Pi 偽冒 Command（假 API key）
- Browser 偽冒另一個 user（session token 竊取）
- PWA 端人員互相冒用（單機多人共用 device）

### 3.2 Tampering（資料竄改）

_Session D 填入。初步清單：_

- audit_log 事後竄改（→ C1-D structured log + PII mask 部分緩解 ✅ PR#20；hash chain 防篡改另立 task，C1-D Step A Q6 凍結移出）
- Pi push 路徑中間人竄改（→ C1-B mTLS 緩解）
- PWA 本機 DB（Dexie）篡改（→ 存取稽核 + 不信任客端）
- **PWA → Pi WS 寫入路徑訊息篡改** → ✅ **緩解 — W-C1-G #35 (PR#37) + W-C1-A #36 (PR#38) HMAC-SHA256 + nonce + timestamp 三因子驗章**；篡改任一 byte → server close 4406 ws_signature_invalid + ERROR audit log（Track B real PWA hands-on demo 已驗）
- ⚠️ **Pi → PWA WS 廣播方向訊息篡改（known limitation）** → 反向方向 (`delta` / `catchup_resp` / `network_recovery_push` 等) 仍以純 JSON 廣播，PWA 無 incoming verify。Production 環境靠 C1-B step-ca WSS 防 MITM；dev `ws://` 環境同網段攻擊者可注假 broadcast 誤導 PWA UI（不影響 server DB integrity）。追蹤 → **Issue #39 (W-C1-A-rev follow-up)**

### 3.3 Repudiation（否認）

_Session D 填入。初步清單：_

- 指揮官否認下令（→ duty_log + audit chain 緩解）
- 操作員否認修改事件（→ every write 帶 account_id + timestamp）

### 3.4 Information Disclosure（資訊揭露）

_Session D 填入。初步清單：_

- 傷患姓名外洩（→ C1-C Fernet 加密）
- 錯誤訊息洩漏內部路徑 / stack trace（→ C2-F 生產模式）
- Log 檔案含敏感資訊（→ structlog 過濾規則）
- Browser cache 殘留敏感資料（→ Cache-Control: no-store）
- **At-rest 加密的邊界（P1-12，2026-06-04 釐清）**：SQLCipher live-DB 加密 + Fernet 備份加密**只防「資料靜止」洩漏** —— 硬碟被偷 / 備份檔外洩 / 系統關機時的離線檔案複製。**不防 runtime 存取**：系統運行時 DB 已解密（金鑰在 process memory，FIDO2 啟動時解出、app 持有已解密連線），任何能碰到運行中 process / app API / service 帳號 / root 的主體都讀得到甚至改得到**明文**。**勿把 at-rest 加密誤當「進到系統也看不到」** —— 那是兩個不同層的防護（at-rest vs in-use）。

### 3.5 Denial of Service

_Session D 填入。初步清單：_

- 未限速的非 login endpoint 被暴力攻擊（→ 全域 rate limit，C2-F 新增）
- 大 payload 癱瘓（→ payload size limit，C2-F）
- push_queue 無限累積（→ MAX_QUEUE_AGE=24h，已實作）
- SQLite single-writer lock 競爭（→ WAL mode，C3-E）

### 3.6 Elevation of Privilege

_Session D 填入。初步清單：_

- 操作員 role 繞過前端 gate 呼叫指揮官 API（→ C1-A Phase 2 後端 require_role）
- Admin PIN 暴露 → 提權為 SYSTEM_ADMIN（→ lockout 已做，break-glass 使用規範）
- Pi 被實體奪取 → 本機 admin 提權（→ Pi SSH 金鑰 + 物理安全政策）

---

## 4. 攻擊樹（Attack Trees）

_Session D 產出 3-5 個最高風險攻擊樹。候選：_

1. 攻擊者取得指揮官 session → 下假命令
2. 攻擊者篡改 audit log → 湮滅證據
3. 攻擊者實體奪取 Pi → 本機資料外洩
4. 攻擊者 DoS Command → 演練期間無 COP

---

## 5. 威脅 → 控制項對應

_Session D 填入，每個威脅列出 NIST / CIS / ASVS 對應控制項_

---

## 6. 風險矩陣（Likelihood × Impact）

_Session D 填入 5×5 矩陣，評估 §3 清單項目_

---

## 7. Residual Risks（緩解後剩餘風險）

_Session D 填入。例：Pi 實體被盜之資料外洩風險無法完全消除，靠政策 + 保險轉移_

- **特權 runtime 存取（含自動化 / AI agent）— 2026-06-04 記錄**：能 shell 進指揮部主機、或以 service 帳號 / root 運行的主體（**含被授權的 AI agent / 自動化工具**），可繞過 app RBAC 直接讀 / 改 DB（runtime 已解密、金鑰在記憶體）。**at-rest 加密（P1-12）無法緩解此風險** —— 加密只擋「拿走檔案 / 備份」那條路。緩解屬「OS 層 + 政策」而非加解密層：
  - ① **OS 存取控制 + 最小權限**：限制誰能登入主機 / 用 service 帳號；systemd 硬化（ProtectHome / 受限能力）。
  - ② **防竄改稽核**：簽章式 append-only audit（hash chain，§3.2 已列 task），至少讓「偷改」留下無法抹除的證據（偵測，非預防）。
  - ③ **對 AI / 自動化的存取邊界**：**不給 prod 機器 shell / DB 金鑰 / service 憑證**；在 sandbox / 受限環境運行；一旦 agent 取得 box 特權，加密與 RBAC 皆失效，**唯一防線是「一開始就不給存取」**。
  - 殘餘：上述為政策 + OS 層緩解，**無法以應用層或加密完全消除**；high-trust 部署需依賴實體 / OS / 人員管控。

---

## 8. TAK 介面信任邊界（P2 整合）

> 2026-06-07 建立。對應 ROADMAP P2-17（TAK 信任邊界文件化）與紅隊 TAK-A~F 審查處理。
> COP 第一個外部資料源是 TAK Server，引入兩條新鏈路（上行收 / 下行送），信任假設與既有 Pi/PWA 不同，獨立列管。

### 8.1 新增鏈路與傳輸加密

| 方向 | 鏈路 | 加密 / 認證 | 狀態 |
|---|---|---|---|
| **上行** | TAK Server → Command（CoT 串流 `tls://:8089`）| **TLS**；Command 驗 server 憑證（對 step-ca 信任鏈），**fail-closed**——無 cafile 且未顯式 `allow_insecure_tls` → 拒連，不裸奔（`tak_service.build_subscribe_config`）| ✅ 已實作（P2-02/03）|
| **下行** | Command → TAK Server Marti REST（`https://:8443`）| **mTLS（雙向憑證）**；Command 自證 client cert + 驗 server，同 fail-closed（`tak_rest_client.build_marti_ssl_context`）| 🔧 傳輸層已建（P2-11）；查詢消費（P2-12）/ Mission 下令（P2-13）**功能未實作** → 現階段下行無實際流量 |

**傳輸加密殘餘**：上行 `check_hostname=False`（`tak_service.py`）—— 驗「憑證由我方 CA 簽發」但**不驗「憑證簽給此連線位址」**。在全自簽、單一內網 PKI（所有 cert 由同一 step-ca 發放且皆受控）下可接受；嚴格收緊需開啟 hostname 檢查（程式已寫明此取捨原因）。

**At-rest（落地）**：CoT 收進後存 `cop_entities` 為**明文**（同 §3.4 at-rest 邊界）。硬碟被盜 / 備份外洩 / 主機被入侵時可讀。緩解 = P1-12c SQLCipher（**尚未實作**）。見 TAK-F。

### 8.2 STRIDE — TAK 介面（紅隊 2026-06-05 審查 → 2026-06-07 處理）

| 代號 | STRIDE | 威脅（事實）| 處理 / 決策 |
|---|---|---|---|
| **TAK-A** | Spoofing / EoP | inbound `POST /api/tak/events` 無機器間認證；外部 TAK/federation 拿不到 session token | **決策（2026-06-07）：選 HMAC inbound，延後實作**。production 上行走 :8089 pull、federation server↔server 落 :8089，皆不經此端點；真有「外部系統 REST 推 CoT 進 Command」需求時才接 HMAC inbound（對齊 Pi-node `verify_hmac`）。**[2026-06-07 補強 — #146 衍生]**：原述「session-gated 本就擋外部」**漏想內部威脅** —— 已認證 operator 自己有 session，能經此端點注入/竄改 tak 物件（繞過 cop 編輯守門）。故 [#146](https://github.com/winson3QQ/ICS_COMMAND/issues/146) 順手把 POST /api/tak/* 由 WRITE_ROLES 收緊到 **COMMAND_ROLES**（operator 注入面已關；:8089 串流不經 HTTP RBAC、無生產影響）。HMAC inbound 仍為機器對機器正解，延後不變 |
| **TAK-B** | Tampering | TAK CoT uid 直通，可偽造/碰撞 `manual:*` 等本地他源 uid → `ingest_cot_event` CAS 覆寫本地 entity | **✅ 已修（[#145](https://github.com/winson3QQ/ICS_COMMAND/issues/145)，2026-06-07）：來源所有權守門**。CoT 標準要求轉傳保留 uid 不改寫（供 P2-13 下行對位）→ 不前綴改名；改為 TAK 事件只准動 `source='tak'`，撞本地他源一律拒絕覆寫、回 None 記 log（`cop_service.ingest_cot_event` + 負向測試）。**✅ 反向亦已修（[#146](https://github.com/winson3QQ/ICS_COMMAND/issues/146)，2026-06-07）**：cop `PUT`/`DELETE` 加來源所有權 + 情境守門（`_require_editable_source`）—— `manual`/`command` 永遠可編輯；`tak`/pi-node/waveink **僅演習(TTX)模式可編輯**（server 權威，依 active exercise type），**實戰模式鎖死**（保護真實位置不被造假/誤刪；下令走 `source='command'` 新物件）。並順手關注入後門：POST /api/tak/* 收緊 COMMAND_ROLES（見 TAK-A） |
| **TAK-C** | — | `opex` 決定演習/實戰歸屬 | **❌ 不採**：違反 server-authoritative；模式由指揮部 active exercise 決定，不信 client `opex`（見 commit `74db9e8`）|
| **TAK-D** | Info Disclosure | `visible_to` 預設 `["all"]`，TAK entity 全可見（含 observer）；`cop_hub` 無分級過濾 | **🔶 重歸屬 P2-12**：UI 分層時建 CoT `access`→`visible_to` 映射；現況 observer=read-only、無多分類部署下無害 |
| **TAK-E** | DoS | :8089 訂閱無流量管制，多 uid 高頻 burst（單 uid 已有 1s 保護）| **✅ 已修（[#151](https://github.com/winson3QQ/ICS_COMMAND/issues/151)，2026-06-07）**：全域 token bucket（`TAK_INGEST_MAX_EVENTS_PER_SEC`，預設 60）套 `_consume_cot`，超量丟棄該筆 + 節流 warning、不中斷串流；跨重連共用同桶。**per-exercise uid cap descope**（受信任 mTLS 源價值低、每筆 create 多一 COUNT query；速率桶已涵蓋持續高率 DoS）|
| **TAK-F** | Info Disclosure | CoT `<detail>`（含 MEDEVAC 9-line）明文存 attributes + 廣播 | **🔶 部分處理**：9-line 釐清為**聚合後送態勢非個資**（P2-09 設計 B，不開 medical_records 表）；殘餘 = 明文 at-rest 邊界（→ P1-12c）+ ingest 無 server-side XSS escape（→ [#136](https://github.com/winson3QQ/ICS_COMMAND/issues/136)）|

### 8.3 裝置准入信任假設（缺口 #7）

Command **信任 TAK Server 轉發的所有 CoT** —— 即使傳輸加密（§8.1）且 server 憑證已驗，**資料內容的可信度只等同 TAK Server 的裝置准入控管**（cert enrollment 是 TAK 管理員責任，非 Command 可驗）。任何被 TAK 接納的 ATAK 裝置都能推 CoT 進 COP。

兩層信任，勿混淆：
- **連到對的 server**（§8.1 憑證驗證）≠ **server 送的資料可信**（本節）。
- Command 端對「資料內容」的最後防線 = **P2-10 內容層白名單**（座標越界拒絕 / callsign 字元白名單 / type prefix 白名單）+ TAK-B 來源所有權守門。

### 8.4 同機部署的 at-rest 與統一金鑰託管（缺口）

**事實**：TAK Server 與 ICS Command **部署在同一台主機**。同一顆碟上同時有：ICS `cop_entities`（SQLite）、**TAK Server 的 PostgreSQL repository**（存每個 uid 最新 CoT、mission、GeoChat）、step-ca / TAK 憑證與**私鑰**、log、map_config、上傳檔。

**威脅 → 為何「只加密 ICS DB」不一致**：
- §8.1 / TAK-F 的緩解寫「P1-12c SQLCipher 加密 `cop_entities`」，但**TAK PostgreSQL 在同碟仍明文** → 偷碟 / 備份外洩 / 主機被入侵時，攻擊者**直接讀 TAK PG 就拿到同一批敵我位置/mission**，繞過 SQLCipher。**per-app 加密在同機部署下給假安全感。**
- **私鑰明文落地**：mTLS client key（`icscop-nopass.key` 為**解密**狀態）、step-ca CA key、TAK keystore 在碟上 → 偷碟即可**冒充 ICS 對 TAK 注入/刪 CoT、讀 Marti**（與 §8.3 裝置信任合流放大）。

**控制策略（分層、機器層為底）**：
- **L1 主控（必備）= 整碟加密 LUKS**（Linux/Pi）/ BitLocker（Windows）——一次涵蓋 ICS SQLite + **TAK PG** + 憑證/私鑰 + log。**這翻轉 P1-12c「SQLCipher 不取代 LUKS」的相對定位**：**同機部署下 LUKS 是主控必備，SQLCipher 退為內層縱深**（非可選）。
- **L2 縱深** = SQLCipher（ICS DB，P1-12c）+ TAK PG 強密碼（汰 dev 的 `takdevpass123`）+ PG 只綁 localhost。
- **統一金鑰託管** = P1-12a 的 FIDO2→HKDF 階層**加一個 `disk-v1` child（如 `child[3]`）= LUKS unlock key** → **一次 FIDO2 unlock 同時開：開機碟 + ICS app + backup**；**勿**讓 LUKS / app / TAK-PG 各持一把獨立 unlock secret（碎裂託管才是真風險）。
- **Trade-off（需拍板）**：FIDO2 開機解 LUKS = **需人在場摸 token 才能開機** → manned C2 可接受；**無人值守 Pi 停電重啟會卡**。依部署形態決定。
- **Blast radius**：同機 = 單一 host root 被穿透即 ICS + TAK **一起爆**。單盒可部署形態**接受並於此文件記明**；高保證場景才考慮 ICS / TAK **分機 + 分網段**。

→ **回饋 P1-12a 設計**：key 階層新增 `disk-v1` child（統一 unlock）。**動工前先訂部署 at-rest 策略，再讓 P1-12 照它做。**

**[2026-06-08 retention 實證，連動 PII / 缺口 #13]**：同一顆碟上**兩個資料域目前都無界成長**（= 更多明文 PII 暴露面 + 磁碟耗盡 DoS）：① **TAK PG**——TAK 有 Data Retention GUI（per-type TTL：Cot/GeoChat/Mission(含 tracks)/Files + 排程），但**現況 TTL 全空、排程 `Never`**（預設留全部）；② **ICS `cop_entity_tracks`**（SQLite，TAK 保留管不到）。緩解：兩域各設 TTL（90 天 / exercise 刪除 cascade）——TAK 側開 GUI、ICS 側自管。

**[2026-06-11 政策定案（P2-20 收尾 / issue #207，使用者拍板乙案）— 軌跡 PII retention policy]**：

| 資料域 | 政策 | 機制 |
|---|---|---|
| **ICS `cop_entity_tracks`**（人員行蹤，PII） | **exercise 刪除 cascade（既有）＋ 90 天 TTL 自動清理（#207 落地）** | `services/retention_service.py` 每日背景清理 `t <` cutoff；天數 env `TRACKS_TTL_DAYS`（預設 90、下限 1 防全清誤設）；**Admin runtime 開關** `POST /api/admin/retention`（SYSADMIN_ONLY、`RETENTION_TOGGLE` audit-first、**預設啟用**——政策出廠生效，可關以支援長保存需求）；每次清理筆數記 `RETENTION_CLEANUP` audit（個資刪除留痕） |
| **TAK PostgreSQL**（同一批行蹤的 TAK 側副本） | 同 90 天基準 | **ops SOP**：TAK admin GUI `Administrative → Data Retention` 設 per-type TTL（Cot/GeoChat/Mission/Files）+ 排程（現況 `Never` 須手動改）——ICS 管不到，部署 checklist 項 |

**已知 trade-off（接受並記明）**：超過 90 天的演習**不可再 AAR 回放**（軌跡已清；events/decisions/chats/audit 不在此政策內、仍保留）。需長保存的場次：Admin 關閉開關、或於 90 天內以 exercise 歸檔備份（P1-12b backup）帶走。

### 8.5 憑證撤銷控制缺口（被擄裝置）

§8.3 描述「任一被 TAK 接納的裝置都能推 CoT」這個**威脅**；對應的**控制缺口**＝**無憑證撤銷機制**。被擄/失竊的場端裝置，其 client cert 在 TLS 有效期內仍在信任邊界內 → 可**注入假敵我位置、或用 `t-x-d-d` 刪 COP 物件**（完整性威脅，對 C2 ≥ 機密性）。

- **現況**：step-ca 曾發 24h 短期 cert（ROADMAP #98 drift），但**無 CRL/OCSP、無被擄裝置撤銷 SOP**。
- **緩解方向**：短 cert TTL + 自動續期 + **撤銷機制（CRL/OCSP）** + 「裝置遺失 → 立即撤銷」操作 SOP。撤銷責任在 TAK 管理員（cert enrollment 端），ICS 為下游消費者。連動 P2-15（federation peer cert profile）+ step-ca 90 天 patch。
- **ICS 端可加的縱深**：來源標註（哪張 cert/裝置推的）+ 異常偵測（同 uid 位置跳變 / 大量刪除），留 P2-12/P2-19。
- **[2026-06-08 admin UI 實證]**：TAK Server admin GUI（`Administrative → Client Certificates`）**有內建撤銷功能**（`Revoke Selected` + `Show Revoked` 過濾）→ 撤銷機制存在。**但關鍵限制**：該清單對本部署顯示 **"No Certificates Found"**——現行 `icscop`/`admin`/`itak` 等 cert 由 **`makeCert.sh` 離線簽發（CA 信任鏈通，但未經 TAK enrollment 註冊）** → **TAK 不視為 managed cert、此 GUI 撤銷不到它們**。**意涵**：被擄裝置 cert 的撤銷，現行離線 cert 模型下**只能靠 CA 層 CRL 或改 truststore**（非 GUI 一鍵）。**修正方向**：場端裝置 cert 應走 **TAK enrollment（:8446）發行**（才進 managed 清單、可 GUI 撤銷 + `Show Revoked` 稽核），或建 CA CRL 並確認 ICS/TAK mTLS 驗證會 honor。

### 8.6 公網直曝的對外存取信任邊界（驗證部署實測 2026-06-20）

驗證部署把 cmd dashboard 經 AirPort 443 port-forward **直接暴露公網**（`https://<公網IP>/`）實測,暴露面與認證強度的缺口:

- **未登入即可取 `/static` 全目錄（資訊揭露）**：`AUTH_EXEMPT_PREFIXES = ("/static/",)` 把整個 `/static` 免認證,但該目錄**混了 UI 資源與伺服端資料檔**。實測未登入可下載 **`facilities.seed.json`(2.4MB,設施資料)**、`event_taxonomy.seed.json`、`map_config.seed.json`,以及**全部前端 JS**(→ API 端點結構/客戶端邏輯全攤開)。前端取 facilities/taxonomy 實走 `/api/facilities`、`/api/event_taxonomy`(auth-gated),**seed 檔純 factory 預設,前端從不直取** → 不該對外 serve。**修補方向**：/static 服務層擋 `*.seed.json`(#3,修中)。COP 實體/演習/tracks/底圖 pmtiles 已正確 gate(423),未洩。
- **`/api/version`、`/api/health` 未登入可取**：洩版本(利於針對性攻擊)與系統健康(磁碟/DB 狀態)。**修補方向**：gate 或最小化(#4)。
- **6 位數字 PIN — 線上爆破已被帳號鎖定擋住(原評估有誤,更正)**：`accounts` 表有 `failed_login_count` + `locked_until`,**連錯 5 次鎖 15 分**(`LOCKOUT_THRESHOLD=5` / `LOCKOUT_DURATION_MIN=15`,`unlock_account()` 解)→ **線上分散式爆破不可行**(不論攻擊者 IP,帳號自身會鎖)。另有 `/api/auth/login` IP 滑窗限速(60s/10 次→429)為輔。**先前「無帳號鎖定」評估錯誤,在此更正。** **殘留**：(a)**鎖定反成 DoS 向量** —— 每 15 分丟 5 次失敗即可**持續鎖死合法 admin**(單一 admin 的 C2 在事件中失去存取 = 可用性風險;本次驗證即意外自鎖,靠 `unlock_account` 救回);(b)`_client_ip` 取 `X-Forwarded-For` **最左值**可偽造,削弱 IP 限速層(惟真正防線是帳號鎖定);(c)6 位 PIN 實際弱點轉為**離線**(若 `pin_hash` 外洩,6 位數空間離線可秒破 → 須確認 hash 為慢 KDF)。**PIN 非設計失誤**(戰術平板 UX,LAN/VPN 後合理)。**修補方向(#2)**：修 XFF 信任、緩解鎖定-DoS(per-source 鎖定 / admin 復原路徑)、確認 PIN hash KDF 強度。
- **正解（#1，正規做法）**：**C2 不該直曝公網**。擺 **VPN(WireGuard) 後**或強制 **mTLS client 憑證**(repo 已預留 `deploy/nginx/conf.d/tier3-mtls.conf.disabled`)→ 只有持證裝置連得到登入,屆時「6 位 PIN + LAN 模型」即站得住。自簽憑證亦僅限驗證,正式需真 CA 憑證 + HSTS。
- **本次驗證範圍**：以上係**臨時驗證**(throwaway box,空 DB、無正式敏資料),完成後應移除 AirPort 443 forward。**正式對外前必須先落地 #1。**

---

## 9. 審查歷程

| 日期 | Version | 變更 |
|---|---|---|
| 2026-04-25 | 0.1 | 骨架建立（Session D 完稿）|
| 2026-06-04 | 0.2 | §3.4 加 at-rest 加密邊界（only 防靜止，不防 runtime）；§7 加「特權 runtime 存取（含 AI agent）」殘餘風險 + OS/政策緩解（dogfood 提問衍生）|
| 2026-06-07 | 0.3 | 新增 §8「TAK 介面信任邊界」：上行/下行傳輸加密（fail-closed cert 驗證 + check_hostname 取捨 + at-rest 明文邊界）、STRIDE TAK-A~F 處理（TAK-B 來源所有權守門已修、TAK-A HMAC inbound 決策延後、C/D/E/F 歸屬）、裝置准入信任假設（缺口 #7）|
| 2026-06-07 | 0.4 | §8.2 TAK-B 反向缺口已修（#146）：cop PUT/DELETE 來源所有權 + 情境守門（實戰鎖死外部來源、演習 TTX 可編輯，server 權威）；TAK-A 補強內部威脅洞見（已認證 operator 可經 POST /api/tak/events 注入 → 順手收緊 COMMAND_ROLES）|
| 2026-06-08 | 0.5 | 新增 §8.4「同機部署 at-rest 與統一金鑰託管」（TAK Server 與 ICS 同機 → TAK PostgreSQL 同碟明文使「只加密 ICS DB」不一致；**LUKS 整碟翻為主控必備、SQLCipher 退內層**；P1-12a key 階層加 `disk-v1` child 統一 FIDO2 unlock；私鑰明文落地；blast radius）+ §8.5「憑證撤銷控制缺口」（被擄裝置 cert 在信任邊界內可注入/刪 COP；無 CRL/OCSP / 撤銷 SOP）。源於 dogfood 安全策略對話 |
| 2026-06-20 | 0.6 | 新增 §8.6「公網直曝對外存取信任邊界」：驗證部署(cmd dashboard 經 AirPort 443 直曝公網)實測 — `/static` 免認證洩資料檔(`facilities.seed.json` 2.4MB)+ 全前端 JS；`/api/version\|health` 洩漏；6 位 PIN：**線上爆破已被帳號鎖定擋(連錯 5 次鎖 15 分;原「無帳號鎖定」評估更正)**,殘留=鎖定-DoS 向量 + XFF 最左值可偽造削弱 IP 限速 + PIN 離線弱點。正解=不直曝(VPN/mTLS，已預留 tier3-mtls)。緩解 #3(seed 擋除,已修)/#4(gate version·health)/#2(XFF·鎖定-DoS·PIN KDF)/#1(mTLS·VPN) |
