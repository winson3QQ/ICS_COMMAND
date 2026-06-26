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

**At-rest（落地）**：CoT 收進後存 `cop_entities` 為**明文**（同 §3.4 at-rest 邊界）。硬碟被盜 / 備份外洩 / 主機被入侵時可讀。緩解 = P1-12c SQLCipher（**code 落地 [#229](https://github.com/winson3QQ/ICS_COMMAND/issues/229)**：`get_conn` driver 抽象 + 明文→加密 migration + backup/restore 鏈；**預設 off**，加密整合測試走 CI ubuntu，硬體驗收 [#230](https://github.com/winson3QQ/ICS_COMMAND/issues/230)；**待 merge/CI-green**）。**同機部署完整 at-rest 仍需 LUKS [#231](https://github.com/winson3QQ/ICS_COMMAND/issues/231)**（§8.4：SQLCipher 為內層縱深，TAK PG 同碟明文不受其保護）。見 TAK-F。

### 8.2 STRIDE — TAK 介面（紅隊 2026-06-05 審查 → 2026-06-07 處理）

| 代號 | STRIDE | 威脅（事實）| 處理 / 決策 |
|---|---|---|---|
| **TAK-A** | Spoofing / EoP | inbound `POST /api/tak/events` 無機器間認證；外部 TAK/federation 拿不到 session token | **決策（2026-06-07）：選 HMAC inbound，延後實作**。production 上行走 :8089 pull、federation server↔server 落 :8089，皆不經此端點；真有「外部系統 REST 推 CoT 進 Command」需求時才接 HMAC inbound（對齊 Pi-node `verify_hmac`）。**[2026-06-07 補強 — #146 衍生]**：原述「session-gated 本就擋外部」**漏想內部威脅** —— 已認證 operator 自己有 session，能經此端點注入/竄改 tak 物件（繞過 cop 編輯守門）。故 [#146](https://github.com/winson3QQ/ICS_COMMAND/issues/146) 順手把 POST /api/tak/* 由 WRITE_ROLES 收緊到 **COMMAND_ROLES**（operator 注入面已關；:8089 串流不經 HTTP RBAC、無生產影響）。HMAC inbound 仍為機器對機器正解，延後不變 |
| **TAK-B** | Tampering | TAK CoT uid 直通，可偽造/碰撞 `manual:*` 等本地他源 uid → `ingest_cot_event` CAS 覆寫本地 entity | **✅ 已修（[#145](https://github.com/winson3QQ/ICS_COMMAND/issues/145)，2026-06-07）：來源所有權守門**。CoT 標準要求轉傳保留 uid 不改寫（供 P2-13 下行對位）→ 不前綴改名；改為 TAK 事件只准動 `source='tak'`，撞本地他源一律拒絕覆寫、回 None 記 log（`cop_service.ingest_cot_event` + 負向測試）。**✅ 反向亦已修（[#146](https://github.com/winson3QQ/ICS_COMMAND/issues/146)，2026-06-07）**：cop `PUT`/`DELETE` 加來源所有權 + 情境守門（`_require_editable_source`）—— `manual`/`command` 永遠可編輯；`tak`/pi-node/waveink **僅演習(TTX)模式可編輯**（server 權威，依 active exercise type），**實戰模式鎖死**（保護真實位置不被造假/誤刪；下令走 `source='command'` 新物件）。並順手關注入後門：POST /api/tak/* 收緊 COMMAND_ROLES（見 TAK-A） |
| **TAK-C** | — | `opex` 決定演習/實戰歸屬 | **❌ 不採**：違反 server-authoritative；模式由指揮部 active exercise 決定，不信 client `opex`（見 commit `74db9e8`）|
| **TAK-D** | Info Disclosure | `visible_to` 預設 `["all"]`，TAK entity 全可見（含 observer）；`cop_hub` 無分級過濾 | **🔶 重歸屬 P2-12**：UI 分層時建 CoT `access`→`visible_to` 映射；現況 observer=read-only、無多分類部署下無害 |
| **TAK-E** | DoS | :8089 訂閱無流量管制，多 uid 高頻 burst（單 uid 已有 1s 保護）| **✅ 已修（[#151](https://github.com/winson3QQ/ICS_COMMAND/issues/151)，2026-06-07）**：全域 token bucket（`TAK_INGEST_MAX_EVENTS_PER_SEC`，預設 60）套 `_consume_cot`，超量丟棄該筆 + 節流 warning、不中斷串流；跨重連共用同桶。**per-exercise uid cap descope**（受信任 mTLS 源價值低、每筆 create 多一 COUNT query；速率桶已涵蓋持續高率 DoS）|
| **TAK-F** | Info Disclosure | CoT `<detail>`（含 MEDEVAC 9-line）明文存 attributes + 廣播 | **🔶 部分處理**：9-line 釐清為**聚合後送態勢非個資**（P2-09 設計 B，不開 medical_records 表）；殘餘(1) 明文 at-rest 邊界 —— **P1-12c（[#229](https://github.com/winson3QQ/ICS_COMMAND/issues/229)）緩解 code 落地、待 merge/CI**（ICS DB SQLCipher；預設 off，完整 at-rest 仍需 LUKS [#231](https://github.com/winson3QQ/ICS_COMMAND/issues/231)，見 §8.4）；殘餘(2) ingest 無 server-side XSS escape（→ [#136](https://github.com/winson3QQ/ICS_COMMAND/issues/136)）|

### 8.3 裝置准入信任假設（缺口 #7）

Command **信任 TAK Server 轉發的所有 CoT** —— 即使傳輸加密（§8.1）且 server 憑證已驗，**資料內容的可信度只等同 TAK Server 的裝置准入控管**（cert enrollment 是 TAK 管理員責任，非 Command 可驗）。任何被 TAK 接納的 ATAK 裝置都能推 CoT 進 COP。

兩層信任，勿混淆：
- **連到對的 server**（§8.1 憑證驗證）≠ **server 送的資料可信**（本節）。
- Command 端對「資料內容」的最後防線 = **P2-10 內容層白名單**（座標越界拒絕 / callsign 字元白名單 / type prefix 白名單）+ TAK-B 來源所有權守門。

**[2026-06-21 #315 — ICS 成為 TAK 裝置發證方（doctrine 拍板）]**：P2-26 L2 讓 admin 可從 dashboard 線上發 TAK 裝置證 data package（`POST /api/admin/tak/device-cert`）。此舉**把「裝置准入」部分責任從 TAK 管理員移到 ICS sysadmin**——ICS 發的證即被 TAK 信任 → 持證裝置能推 CoT 進共享 COP（**COP poisoning，本介面最大 blast radius**）。

**🔑 CA 拓撲（reality check 2026-06-21，dogfood 實證）**：TAK truststore **只信 `ICS-TAK-SVC-CA`、不信 step-ca**（step-ca 簽的 client 證 TAK 回 `peer not verified`）。故 TAK 裝置證**必須由 ICS-TAK-SVC-CA（offline，`tak-ca.key` @ `TAK_DEVICE_CA_DIR`）簽**，**與 ICS 登入證的 step-ca 刻意隔離**（#305「儀表板 cert 碰不到 TAK」）。**這推翻本段初稿「CA 鑰仍在 step-ca daemon、後端不持鑰」**：TAK 裝置證簽發**後端確實用到 offline ICS-TAK-SVC-CA 私鑰**（該鑰本就掛載 `/tak-certs/_ca` 供 TAK 整合用、`gen-device-pkg.sh` 已在用，#315 非新增暴露）。ICS 登入證（step-ca daemon、後端不持鑰）不受影響、兩 CA 不相通。

**緩解（皆已落地）**：(1) 端點 **sysadmin-only**（`_check_system_admin`）；(2) **每張強制 audit**（`tak_device_cert_issue`，不得 best-effort）——誰發了哪個 callsign 留痕，事後可究責/撤；(3) device 私鑰隨 package **即產即交、不落 DB**（守 #255 紅線，cert/key 內容入 DB 仍卡 P1-12）；(4) CA 隔離：ICS 登入證（step-ca）即使外洩也**連不上 TAK**（TAK 不信 step-ca），反之亦然。**殘餘**：(a) 發證是 sysadmin 蓄意決定，無法防「被盜 sysadmin session」濫發——與其他 sysadmin 破壞性動作同級，靠 audit + mTLS 第二因子 + PIN 緩解；(b) **offline ICS-TAK-SVC-CA 私鑰落地檔案系統**（同 §8.4 at-rest 邊界，偷碟可冒充發 TAK 裝置證）→ 緩解走 LUKS [#231](https://github.com/winson3QQ/ICS_COMMAND/issues/231)；(c) **發出的 TAK 裝置證無 app 層撤銷**（不像 ICS 登入證可即時撤）——撤銷須走 step-ca/TAK CRL 或重簽 CA，P2-26 cert lifecycle 未做。推翻 #255 原「UI 不簽證」對裝置證的部分，理由見 [#315](https://github.com/winson3QQ/ICS_COMMAND/issues/315)。

**[2026-06-26 #403/#404 — TAK 存取控制機制定讞（源碼 + 對活機端到端實證）]**：釐清「ICS 如何真正管控誰能連進 TAK」。**源碼定讞**（官方 `X509Authenticator.java`，`TAK-Product-Center/Server`）：TAK 對 **CA 信任的證架構上永不拒絕**——非名冊證無群即無條件落 `__ANON__`（`doAnonAssignment`）；我們翻過的 `x509addAnonymous="false"` 只在 LDAP 分支生效、對 file-auth 是死碼（對活機實測非名冊證仍落 `__ANON__`）。**唯一 TLS/auth 層真拒絕 = 撤銷**（`x509checkRevocation` + cert 在 TAK `certificate` 表有 `revocationDate` → `RevokedException`）。故「改 CoreConfig 白名單擋連」與 TAK 架構衝突、**死路**；存取控制只能靠 **group membership**，落為兩層：
- **層1（地基）= `__ANON__` 死群隔離**（[#404](https://github.com/winson3QQ/ICS_COMMAND/issues/404)）：非名冊證落 `__ANON__` 擋不掉，但只要**所有 ICS producer（含 `ics-cot`）不掛 `__ANON__`**（走 red/blue/neutral），`__ANON__` 即無資料 → 不明證落進去看不到、也注不進。**端到端實證**：`ics-cot` 在 `__ANON__` 時非名冊證注入 → ICS `cop_entities` 真 ingest（漏洞）；`usermod -r -g __ANON__ ics-cot` 移除後同注入 → ICS 收不到（關閉）。`register-tak-fingerprint.sh` 改為**不再預設 `__ANON__`**（須顯式 named group，`__ANON__` 須 `--allow-anon`）。`ics-tak-admin` 仍 `__ANON__`（REST-only 不 stream、無 streaming 洩漏；移除其唯一群會 bounce 回 `__ANON__`）。
- **層2（點名封殺）= 撤銷**（[#318](https://github.com/winson3QQ/ICS_COMMAND/issues/318)）：ICS **離線簽**的證 TAK `certificate` 表**查無** → 預設**撤不掉**（`findOneByHash` 撲空）；須 ICS 發證後**把證補登進 TAK DB** + 開 `x509checkRevocation`。**實證**：手插 `certificate`(hash+revocation_date) + 開旗標 + restart → 該證連 :8089 立刻被踢（recv 0）、REST 500，未撤的 `ics-cot` 正常。

> **架構含義**：離線簽＝握發證權，但「繞過 TAK 不登記」正是撤不掉的根源；**補登 TAK DB 可兼得發證權與撤銷力**。**更新本節 §8.3(c) 殘餘**「發出的 TAK 裝置證無 app 層撤銷」→ 路徑已明（層2，#318），非無解。

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

**[2026-06-12 拍板（P1-12 動工前決議，使用者核定）]**：採本節控制策略——
- **L1 主控 = LUKS 整碟**，立為獨立 item（[issue #231](https://github.com/winson3QQ/ICS_COMMAND/issues/231)），P1-12 不含；**L2 縱深 = SQLCipher**（P1-12c，[#229](https://github.com/winson3QQ/ICS_COMMAND/issues/229)）。對外安全邊界陳述應寫明「at-rest 防護完整需 LUKS 到位」，**P1-12c 完成不等於偷碟免疫**（TAK PG 同碟明文仍在，見上）。
- P1-12a key 階層**含 `child[3] = disk-v1`**（[#227](https://github.com/winson3QQ/ICS_COMMAND/issues/227)），LUKS 統一 FIDO2 unlock 預留位。
- 部署形態定調 **manned C2**：接受開機需人持 FIDO2 token；無人值守（unattended Pi）的 escrow / TPM / 降級後路於 #231 內評估，不阻塞 P1-12。

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
- **[2026-06-08 admin UI 實證]**：TAK Server admin GUI（`Administrative → Client Certificates`）**有內建撤銷功能**（`Revoke Selected` + `Show Revoked` 過濾）→ 撤銷機制存在。**但關鍵限制**：該清單對本部署顯示 **"No Certificates Found"**——現行 `icscop`/`admin`/`itak` 等 cert 由 **`makeCert.sh` 離線簽發（CA 信任鏈通，但未經 TAK enrollment 註冊）** → **TAK 不視為 managed cert、此 GUI 撤銷不到它們**。**意涵**：被擄裝置 cert 的撤銷，現行離線 cert 模型下**只能靠 CA 層 CRL 或改 truststore**（非 GUI 一鍵）。**修正方向**：場端裝置 cert 應走 **TAK enrollment（:8446）發行**（才進 managed 清單、可 GUI 撤銷 + `Show Revoked` 稽核），或建 CA CRL 並確認 ICS/TAK mTLS 驗證會 honor。**[2026-06-12 升優先]**：本缺口已開 [issue #232](https://github.com/winson3QQ/ICS_COMMAND/issues/232) 進工作佇列（SC7）。

### 8.6 公網直曝的對外存取信任邊界（驗證部署實測 2026-06-20）

驗證部署把 cmd dashboard 經 AirPort 443 port-forward **直接暴露公網**（`https://<公網IP>/`）實測,暴露面與認證強度的缺口:

- **未登入即可取 `/static` 全目錄（資訊揭露）**：`AUTH_EXEMPT_PREFIXES = ("/static/",)` 把整個 `/static` 免認證,但該目錄**混了 UI 資源與伺服端資料檔**。實測未登入可下載 **`facilities.seed.json`(2.4MB,設施資料)**、`event_taxonomy.seed.json`、`map_config.seed.json`,以及**全部前端 JS**(→ API 端點結構/客戶端邏輯全攤開)。前端取 facilities/taxonomy 實走 `/api/facilities`、`/api/event_taxonomy`(auth-gated),**seed 檔純 factory 預設,前端從不直取** → 不該對外 serve。**修補方向**：/static 服務層擋 `*.seed.json`(#3,修中)。COP 實體/演習/tracks/底圖 pmtiles 已正確 gate(423),未洩。
- **`/api/version`、`/api/health` 未登入可取(#4 已修)**：原洩版本(利於針對性攻擊)與系統健康(磁碟/DB 狀態)。**修補**：`/api/health` 未登入只回 `status`/`db_writable`/`version`(狀態燈所需),`db_path`/磁碟/DB 延遲/`schema_version` 改為**僅帶有效 session token 才附**;狀態燈輪詢(cop.js)有 session 即帶 token→tooltip 完整,無則 plain(登入頁仍顯示粗略狀態)。`/api/version` **維持公開**(build 戳記顯示於登入頁,版本揭露為刻意取捨)。
- **6 位數字 PIN — 線上爆破已被帳號鎖定擋住(原評估有誤,更正)**：`accounts` 表有 `failed_login_count` + `locked_until`,**連錯 5 次鎖 15 分**(`LOCKOUT_THRESHOLD=5` / `LOCKOUT_DURATION_MIN=15`,`unlock_account()` 解)→ **線上分散式爆破不可行**(不論攻擊者 IP,帳號自身會鎖)。另有 `/api/auth/login` IP 滑窗限速(60s/10 次→429)為輔。**先前「無帳號鎖定」評估錯誤,在此更正。** **殘留**：(a)**鎖定反成 DoS 向量** —— 每 15 分丟 5 次失敗即可**持續鎖死合法 admin**(單一 admin 的 C2 在事件中失去存取 = 可用性風險;本次驗證即意外自鎖,靠 `unlock_account` 救回);(b)`_client_ip` 取 `X-Forwarded-For` **最左值**可偽造,削弱 IP 限速層(惟真正防線是帳號鎖定);(c)6 位 PIN 實際弱點轉為**離線**(若 `pin_hash` 外洩,6 位數空間離線可秒破 → 須確認 hash 為慢 KDF)。**PIN 非設計失誤**(戰術平板 UX,LAN/VPN 後合理)。**修補方向(#2)**：修 XFF 信任、緩解鎖定-DoS(per-source 鎖定 / admin 復原路徑)、確認 PIN hash KDF 強度。
- **✅ 正解（#1，已落地 — #275，2026-06-20）**：強制 **全角色 mTLS client 憑證**——只有持證裝置連得到登入（「PIN + 裝置 cert」= AAL2）。落地：nginx `ssl_verify_client on`（單埠 443，`command-mtls.conf.disabled` / `deploy/ics-validation/mtls/`）+ trusted-header 剝除 + 後端 loopback 收口 + per-device 簽發/撤銷（`account_certs`，App 層即時撤銷）+ 面板線上發證（step-ca daemon，CA 鑰不進後端）。**對外實機驗證通過**（iPhone Safari 4G + mTLS → dashboard/COP/WebSocket 全通；不持證 → nginx 400）。**iOS 限制**：第三方瀏覽器（Chrome/Firefox=WebKit 殼）拿不到 client 憑證 → 須用原生 Safari。詳 `security_policies.md` §2.8。自簽憑證僅限驗證；正式交付每場域獨立 step-ca + 真 CA + HSTS。
- **✅ #2 機制面硬化（已落地 — #275 wave 4）**：XFF 信任修正（反代信任 `X-Real-IP`、直連忽略可偽造 XFF，`ICS_BEHIND_PROXY`）；鎖定-DoS 緩解（持綁定裝置證者鎖定不擋、錯 PIN 不上鎖 → 攻擊者無證可鎖、本人持裝置永不被鎖死）；PBKDF2 100k→600k（透明 rehash）。
- **#3 seed 擋除（已修）、#4 gate version/health（已修）**。
- **本次（早上）驗證範圍**：原始實測係**臨時驗證**(throwaway box,空 DB、無正式敏資料)；該次完成後移除 forward。**正式對外現已具備 #1+#2**；mTLS 公網實證另見 #275（曝 1h 內即遭掃描 bot 打 `/config/.env` 等，全被 mTLS 擋 400）。

### 8.7 應用層紅隊批次（白箱源碼審查 2026-06-20，#286–290 已修）

§8.6 收的是「公網周邊 / 部署」面；本節為同日**白箱靜態源碼審查**（6 路並行讀 code，非黑箱實打）在**應用層**找到的破口。完整稽核見 `docs/security-audit/redteam-2026-06-20.md`；皆已修 + 測試（PR #291，backend 2.7.1）。

| 代號 | STRIDE | 事實 | 修補 |
|---|---|---|---|
| H1 (#286) | Tampering / EoP | `routers/map.py upload_map_image` 直接用 client `file.filename` 拼 `STATIC_DIR` → path traversal / 覆寫前端 JS（儲存型 XSS → 全站接管，含污染 sysadmin session 提權）；允許 svg | server uuid 命名（client 對路徑零控制）+ `resolve()`/`is_relative_to` 守門 + 移除 svg；`.gitignore` 擋 runtime 上傳物 |
| H2 (#287) | EoP | `auth/role_enum.allowed_roles_for` 無 `/api/ttx/` case → 落寬鬆預設（GET=READ/POST=WRITE）；observer 讀任意場 inject 腳本、operator 注入任意演習場（含已歸檔）→ broken access control + 跨場越權 | 比照 `/api/exercises/` 鎖 COMMAND_ROLES |
| H3 (#288) | Tampering / Info Disclosure | `routers/events.py`·`decisions.py` 寫入端點（patch/status/notes/deadline/decide）只認 id、**無 `resolve_scope`**（讀取卻有）→ operator 可改/裁示**別演習場（含歷史場）**之事件與決策（IDOR + 跨場 PII 越權） | 寫入與讀取對稱套 `resolve_scope`；抽 `_helpers.scope_clause` 把目標 row 限 scope 內（跨場視同不存在 → 404） |
| H6 (#290) | Spoofing | `docker-compose.mtls.yml` 寫死公開預設密碼（CA provisioner `icsprov` / proxy secret `…change-me` / p12 `icsclient`）→ 知 repo 者可簽任意憑證繞 mTLS / 偽造 cert header | compose 改 `${VAR:?}` fail-closed + `.env.example`；腳本同收緊 |
| M1 (#290) | Spoofing | `IS_PROD && ICS_MTLS_REQUIRED && !ICS_PROXY_SHARED_SECRET` 時 `_proxy_trusted` fail-open 無條件信任 cert header（重開 #280 的內網偽造洞），且 silent | `main._assert_safe_mtls_config()` 啟動即拒此危險組合 |

> **方法論限制**：本批為**白箱 SAST**，未做黑箱實打。runtime 面（H4 nginx 實際 header/cipher、prod env 是否真設 proxy secret、IDOR/traversal 對活靶）仍待 Windows/Docker prod 黑箱驗證（見稽核 log「需 runtime 確認」）。
> **正面（已查無虞）**：SQLi（全參數化 + 白名單動態欄位/表名）、XXE（defusedxml 三關）、SSRF（`_join_url` 拒絕絕對 URL）、CoT 跨源 uid 覆寫（已守門）、機密無入 git、供應鏈無中國套件。

#### 8.7.1 前端駭客視角 + perimeter（mTLS/VPN/真實IP）防禦對照（2026-06-20）

攻擊者讀「暴露的前端」（mTLS 下＝持證者/被擄裝置/外洩 build）能推出的攻法，與 perimeter 控制的覆蓋對照。**結論：perimeter 關掉「網路層/外部」整面；殘留＝圈內人 + 資料毒化，須 app 層縱深。皆為已知類別、有標準解（無新題）。**

| 攻法 | perimeter（mTLS + VPN前置 + 真實IP還原）擋多少 | 殘留（app 層）→ issue | 業界類別 / 標準解 |
|---|---|---|---|
| ① 直打 API 掃 IDOR/越權 | 外部全擋（無證連不到）；縮到持證內部人 | 內部人/被擄裝置仍可掃 → 後端 authz（#286–288 已修）+ 回歸矩陣 #296 | OWASP A01；deny-by-default + 每端點授權 |
| ② client 翻角色 | 無關（後端認 session，已守） | — | "never trust client"（ASVS V1/V4）|
| ③ XSS 偷 token | 偷到難重放（cert-bound + IP/24，真實IP還原使 IP 綁定生效）；當場操作不擋 | 輸出編碼 #292 + token httpOnly #293 + CSP #294 | OWASP A03/CWE-79；脈絡編碼 + CSP + httpOnly |
| ④ CoT 注入毒化（無需帳號） | **擋不住**（毒源與受害者皆圈內） | input_safety + #292 + #294 | A03；輸出編碼 + CSP |
| ⑤ 鎖死指揮官（DoS） | **大幅改善**：真實IP還原 → 鎖來源不鎖帳號；mTLS → 無證打不到 login | per-source / 高權不硬鎖 #295 | ASVS V2.2 / NIST 800-63B |
| ⑥ WS scope 探測 | 外部全擋；縮到內部人 | 後端 gate（已守：standing=COMMAND + resolve_scope）| A01 |

> **doctrine（NIST 800-207 zero-trust）**：perimeter 必要但不充分。C2 威脅模型**必含「被擄的合法裝置」**（前線平板被繳獲＝合法憑證落敵手）→ 圈內不可預設信任，① 內部掃與 ③/④ 毒化只有 app 層縱深擋得住。**最高 CP＝先落地 IP 整理（Tailscale 前置 + 真實IP還原，#280），其餘殘留循 #292–296 修。皆標準解，無未解難題。**

### 8.8 Live-host vs at-rest 區分 + 安全交付 checklist（#301 黑箱後架構討論 2026-06-22）

#301 公網黑箱證明**周邊極硬**（mTLS 雙 port 強制、Marti/後端/DB 對外全 filtered、header/cipher 達標、匿名攻擊者止步 TLS 握手）。但「最強堡壘由內部攻破」——以下釐清 perimeter 蓋不到的層 + 交付分階段紀律。

**關鍵區分：at-rest（冷）vs live-host（熱）——兩者防法不同，勿混為一談**

| 威脅狀態 | §8.4 LUKS/BitLocker 全碟加密 | 防得住嗎 |
|---|---|---|
| **冷**：關機被偷 / 拔硬碟 | ✅ 看到密文 | 是 |
| **熱**：開機中的主機被 OS 層攻陷（root code exec / host 上的惡意 AI/agent） | ❌ volume 已解密掛載，CA 鑰/DB 在記憶體與掛載點是**明文**，跑在 host 上的程式照樣讀 | **否** |

→ **全碟加密只防「冷」。** 「活著的被攻陷主機讀走 CA 鑰 → 簽任意憑證 → mTLS 信任根全破」是 at-rest 蓋不到的層（[issue #323](https://github.com/winson3QQ/ICS_COMMAND/issues/323)）。**事實**：現 `step-ca` CA 私鑰與 ICS/nginx/DB 同跑單機 `ca-data` volume。

**選定交付架構 = manned C2 密封盒（sealed appliance，2026-06-22 使用者拍板）**

交付物 = **加密硬碟 + 單一 container（ICS 全棧含 step-ca）+ 一把實體 FIDO2 金鑰**。此模型**用「縮小攻擊面 + 實體鑰閘」取代「CA 離機」**來處理 live-host 威脅：

- **「host 上的 AI/agent」威脅在交付盒上不存在** —— 盒子**只跑那一個密封 container，不跑通用 agent**（該威脅只在 dev 機，靠圍籬擋）。攻擊面從「通用主機」縮成「單一用途密封盒」。
- **實體金鑰閘** = at-rest 解鎖需 FIDO2（CTAP2 hmac-secret + **PIN + touch**）→ 偷碟無鑰無 PIN 開不了（碟 + 鑰 + PIN ＝ 3 因子）。

**模型成立的前提（強制，否則加密形同虛設）**：
1. FIDO2 須 **PIN + touch**，不可只靠持有（CTAP2 PIN，#226/#227 已含）。
2. **碟與鑰分開運送/保管**（一起被拿＝鑰解碟）。
3. **不用即關機；運行中需人看管** —— ⚠ 加密/金鑰**只防冷/開機邊界**：開機解鎖後系統在記憶體是明文，此時拔鑰、整碟加密皆無效，「**開著被搶**＝加密失效」。manned C2 假設運行時人在鑰在、閒置斷電回密文。
4. **多把金鑰 + 救援路徑**（enroll 2+ token + 助記詞 rescue；#230「失 token 演練」）。

**殘留（可接受、記明）**：container escape / 盒內 app RCE 仍可摸到盒內 CA 鑰 → 緩解＝盒子單一用途、最小服務、container 硬化。對 manned-C2 單盒為可接受殘留（非無人值守高保證場景）。

**高保證替代（非 manned-C2 必需）**：無人值守 / 高威脅場景才升級為 **CA 離機**（離線 root CA / HSM / 分機分網段），ICS host 不持簽證 CA 鑰。

**單機（尤其單 Windows dev/delivery 機）能做什麼 — reality check**

| 防線 | 單機能做？ | 機制 |
|---|---|---|
| at-rest（冷竊） | ✅ | BitLocker（Win，= §8.4 #231 Windows 軌）/ LUKS（Linux）+ FIDO2 鑰閘 |
| live-host AI/agent（**dev 機**） | ⚠ 部分 | **agent 能力圍籬**（最小權限、禁 prod-exec/secret-read/self-escalation；#301 session 實證有效）+ egress 控管 + hash 鏈稽核 |
| live-host（**交付密封盒**） | ✅ 縮面解 | 盒子只跑單一 container、不跑 agent → 該威脅不存在；殘留＝container escape（盒硬化緩解） |
| CA 鑰離機（高保證選項） | ❌ 單機做不到 | manned-C2 **不需要**；無人值守/高威脅才升級拓樸（#323） |

> **manned-C2 殘留邊界**：密封盒 + 實體鑰閘下，「冷竊」「偷碟」「dev 機 AI」皆解。**唯一不可逆殘留＝「盒子開機運行中被實體奪取」**（記憶體明文，加密無效）→ 靠運行時人看管 + 閒置斷電 + tamper 反應緩解，非技術絕對解。**這是 manned C2 形態本質接受的風險，記明於此。**

**安全交付 checklist（公測 vs 正式交付）**

| 項 | 公測階段（無真資料，風險可接受） | 正式交付（強制） |
|---|---|---|
| 交付形態（#323） | dev 機單棧 — **記明 CA 同機為已知殘留** | **manned C2 密封盒**：加密碟 + 單 container + 實體金鑰（CA 同機可接受，靠密封 + 鑰閘 + 看管）。高保證場景才升 CA 離機 |
| at-rest + 實體鑰閘（§8.4 #231 / #226） | 建議開 | **BitLocker/LUKS 整碟 + FIDO2（PIN+touch）解鎖必開**；碟與鑰分開保管、閒置斷電 |
| FIDO2 統一 unlock（#226/#227/#230） | 可延後（mock 驗證即可） | 真 token 整合 + 失 token 演練（最少硬體：Pi 500 + 2 把 FIDO2，免買量產機，見 #230） |
| agent/process 圍籬 | **必在**（host 上跑 agent 時） | **必在** |
| 真實 client IP 還原（#280） | 可接受 per-IP 防護失效 | **Tailscale/host-net 還原**（per-IP 限速/fail2ban 才生效） |
| 裝置證撤銷（§8.5 #232/#318） | 管控發證 + 別濫發 | **CRL/OCSP 撤銷能力 + 遺失即撤 SOP** |
| prod secret（#290） | compose `:?` 強制非空（已驗） | 確認非預設、強隨機 |
| server cert SAN（#321） | — | **不含 RFC1918 內網 IP**（ca-bootstrap 預設過濾） |

> **紅線**：上述「正式交付」欄各項，**不可帶著公測階段的風險接受值出貨**。密封盒模型下 CA 同機可接受，但**整碟加密 + FIDO2 鑰閘（PIN+touch）+ 碟鑰分離 + 閒置斷電**為出貨強制，缺一即退回公測風險等級。

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
| 2026-06-20 | 0.7 | 新增 §8.7「應用層紅隊批次」(白箱源碼審查,#286–290 已修,PR #291,backend 2.7.1)：H1 `upload_map_image` 任意檔寫入(→ 儲存型 XSS/全站接管)、H2 TTX router 無授權 gate(broken access control + 跨場)、H3 events/decisions 寫入 IDOR + 跨演習越權(讀有 scope 寫沒有)、H6 compose 公開預設密碼、M1 mTLS-on-但-secret-空 fail-open。標明方法論限制(白箱未黑箱;runtime 面待驗)+ 正面查核(SQLi/XXE/SSRF/CoT 覆寫/供應鏈中國紅線皆過)|
| 2026-06-20 | 0.8 | 新增 §8.7.1「前端駭客視角 + perimeter 防禦對照」：攻法①–⑥ × mTLS/VPN/真實IP 覆蓋 × app 層殘留(#292–296)。結論=perimeter 關「網路層/外部」整面,殘留=圈內人+資料毒化須 app 縱深;皆已知類別有標準解。doctrine：zero-trust(NIST 800-207)——C2 威脅含被擄合法裝置,圈內不可預設信任 |
| 2026-06-21 | 0.9 | §8.4 加「P1-12 動工前決議」拍板（2026-06-12；LUKS 主控 [#231](https://github.com/winson3QQ/ICS_COMMAND/issues/231) / SQLCipher 內層 [#229](https://github.com/winson3QQ/ICS_COMMAND/issues/229) / `disk-v1` child 入 12a [#227](https://github.com/winson3QQ/ICS_COMMAND/issues/227) / manned C2 形態）；§8.5 升優先開 [#232](https://github.com/winson3QQ/ICS_COMMAND/issues/232)（rebase 對齊：原 0.6 與安全批次撞號 → 改 0.9）|
| 2026-06-22 | 1.0 | #301 公網黑箱（周邊強：mTLS 雙 port 強制、Marti/後端/DB 對外 filtered、header/cipher PASS）後新增 §8.8「Live-host vs at-rest 區分 + 安全交付 checklist」：釐清全碟加密只防冷竊、live-host 威脅；**使用者拍板交付形態＝manned C2 密封盒（加密碟 + 單 container + 實體 FIDO2 金鑰）**——用縮面 + 鑰閘取代「CA 離機」，CA 同機於密封盒可接受（[#323](https://github.com/winson3QQ/ICS_COMMAND/issues/323)）；前提＝PIN+touch / 碟鑰分離 / 閒置斷電 / 多鑰救援（#230）；唯一不可逆殘留＝開機運行中被實體奪取。公測 vs 正式交付分階段紀律（at-rest+FIDO2 / 真實IP #280 / 撤銷 #232 / SAN #321 為交付強制）|
