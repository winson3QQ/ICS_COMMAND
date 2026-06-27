# ICS_Command Security Policies（承自 ICS_DMAS）

> # 狀態：內文已補實（self-attestation；尚未經第三方驗證）
> §1–§6 各政策章節 + 附錄 A 對照已撰寫**實質內文**，取自本系統實際落地之控制（#348-F11 補實）。
> 本文件為**自我聲明（self-attestation）**——可作投標/自評/稽核前置之政策層證據；但通過
> **ISO 27001 第三方驗證仍需認證 auditor 覆核並加厚程序細節**。
> **仍有技術缺口（inline 誠實標註、不主張達標）**：at-rest 加密 prod 預設未開（#348 GAP1）、
> 稽核完整性 AU-9(3) 驗證器未接（#348 GAP2）、events/chats/session PII 無 TTL（#348-F10）、
> 無 HA / DR 演練未跑（§5.5–5.6）。**控制狀態總覽（✅/🟡/❌ + 證據 + owner）見
> [`audit-status.md`](audit-status.md)**。

> **依據**：NIST SP 800-53 每個 control family（xx-1）均要求對應 policy 文件；未寫 policy 等於該 family 不能主張 compliance。
> **組織化**：6 份政策併一檔（原本分 6 檔會碎裂），各為獨立章節，各含 Purpose / Scope / Policy Statements / Procedures / References / Review。
> **狀態**：**0.2 內文已補實（self-attestation）**；技術缺口 inline 標註，第三方驗證待 auditor。
> **最後更新**：2026-06-27（#348-F11：§1–§6 + 附錄 A 補實內文）
> **擁有者**：ICS_Command 專案（承自 ICS_DMAS）
> **Review 週期**：每年一次，或重大架構變更時 re-review
>
> **重要**：本文件為自我聲明（self-attestation）policy；若需通過 ISO 27001 第三方驗證，需由認證 auditor 覆核並加厚程序細節。

---

## 文件元資料

| 項目 | 值 |
|---|---|
| 版本 | 0.2（內文補實，self-attestation） |
| 生效日期 | 2026-04-25（草稿） |
| 下次 review | 2027-04-25 |
| 核准 | 待 |

---

## 1. Information Security Policy（資訊安全總政策）

> 對應：NIST 800-53 PM-1 / PL-1；ISO 27001 5.2；附表十防護基準 §1

### 1.1 Purpose
本系統為民防/應變指揮的共同作戰圖（COP），承載人員位置、事件、決策與傷患等敏感資料，且對外經公網存取。本政策宣示專案對資訊安全的總原則承諾：在「韌性（可用性）優先於機密性」的民防定位下，以縱深防禦保護資料之機密性、完整性與可用性，使系統在實戰/演訓中可信賴運作、事後可課責。

### 1.2 Scope
涵蓋 ICS_Command 全系統：指揮儀表板後端（FastAPI + SQLite）、儀表板前端、Node.js relay（`server/`）、上游節點 ingress（Pi/PWA 回流介面）、TAK 整合層，及其部署基礎設施（nginx 反代、step-ca、容器/單機棧）。原 ICS_DMAS 三組件之共用元件亦適用。

### 1.3 Policy Statements
- **縱深防禦（defense-in-depth）**：網路層（mTLS）、應用層（RBAC、輸入驗證）、資料層（at-rest 加密能力）、課責層（audit）多層獨立防線。
- **最小權限**：角色（sysadmin/commander/operator/observer）按職責授予；未明確授權者一律拒絕。
- **失敗即關閉（fail-secure）**：授權預設 deny（#370）；組態危險時 fail-fast 拒啟動（#290）。
- **基準**：以 NIST SP 800-53 Moderate baseline 為目標控制集；對齊 OWASP ASVS L2、ISO 27001、台灣《資通安全管理法》與《個資法》。
- **可課責**：寫入類操作留 audit 痕；安全事件可追溯。
- **供應鏈紅線**：禁用任何與中國相關之軟體/函式庫/服務（見 CLAUDE.md）。

### 1.4 Roles and Responsibilities
| 角色 | 資安責任 |
|---|---|
| **sysadmin** | 系統組態、帳號/憑證發放與撤銷、備份還原、安全事件處置；最高權限，操作全程強制 audit。 |
| **commander** | 指揮決策、帳號管理（受限）；對其指揮範圍之資料正確性與保密負責。 |
| **operator** | 前線感知標記/事件回報；對其輸入資料之真實性負責，不得越權跨演習場。 |
| **observer** | 唯讀稽核/觀察；不得寫入；對所見敏感資料保密。 |
| **專案維護者** | 維護本政策、執行 review、把關供應鏈紅線與 code-review 安全檢查點。 |

### 1.5 Compliance
適用法規：台灣《資通安全管理法》（責任等級對應之 ISMS/通報義務）、《個人資料保護法》（蒐集/處理/利用/通報）、《災害防救法》（應變場景）。違反處置：依嚴重度由專案維護者啟動 §4 事件應變；涉個資外洩觸發 §6.5 通報；涉人員違規依組織規章處理。

### 1.6 References
- ~~compliance/matrix.md~~（**已廢**：不再維護獨立 matrix.md；Compliance 對照已 inline 於
  [`docs/ROADMAP.md`](../ROADMAP.md) 各 phase 的《Compliance touchpoints》區塊，見 CLAUDE.md）
- [compliance/threat_model.md](threat_model.md)

### 1.7 Review
_每年 / 重大事件後 re-review_

---

## 2. Access Control Policy（存取控制政策）

> 對應：NIST 800-53 AC-1；ASVS V4；CIS Control 5/6

### 2.1 Purpose
規範身份識別、鑑權、授權與最小權限之實施，確保僅經授權之主體能存取對應資源，並使越權嘗試被擋下且留痕。

### 2.2 Scope
全系統所有帳號、session 與 `/api/*` endpoint；含 WebSocket（`/ws/updates`）與上游 ingress（HMAC 機器對機器路徑）。

### 2.3 Policy Statements
核心聲明：
- 所有 API endpoint 預設拒絕，明確授權後才開放
- 使用 RBAC：系統管理員 / 指揮官 / 操作員 / 觀察員
- Admin PIN 為 break-glass，非日常使用
- Session timeout 依 role 風險級別（SYSTEM_ADMIN 30 min、其他 14 hours）
- Account lockout：5 失敗 / 15 min（一般）或 5 失敗 / 30 min（admin PIN）

**授權實施紅線（2026-06-20 紅隊 #287/#288 衍生；威脅依據 `threat_model` §8.7）**：

- **中央 gate 必須登記每個 router**：授權由 `auth/role_enum.allowed_roles_for` 前綴比對集中決定；**未登記的 path 會落寬鬆預設（GET=READ / POST=WRITE）**＝靜默 broken access control（#287 TTX 即此患）。新增 router **必須**在此函式加明示 case；視為 code-review 檢查點（理想上加「每 router prefix 必有 case」測試斷言）。
- **寫入路徑與讀取路徑授權須對稱**：凡讀取端點以 `resolve_scope` 做演習場隔離者，其對應**寫入端點亦須套同一 scope**（不可只認資源 id）；否則低權角色可跨演習場 IDOR 越權（#288 events/decisions 即「讀有 scope、寫沒有」）。scope 比對應在 repo 層與資料異動同一查詢內原子完成（跨場 row 視同不存在 → 404，不洩漏存在性）。
- **使用者可控檔名/路徑不得直接用於檔案系統操作**：上傳/寫檔一律 server 端生成安全名（如 uuid）+ `resolve()`/`is_relative_to(base)` 二次守門；runtime 寫入物落 `data/` 不落 tracked 的 `static/`（#286 + User Data 紅線）。

### 2.4 AC-14 Permitted Actions Without Authentication（明確定義免驗清單）

> 對應：NIST 800-53 AC-14；Evidence：`server/routes.js` `_FIRST_RUN_WHITELIST`、`server/ws_handler.js` `_STATE_CHANGING`

#### Pi Server HTTP First-run Allowlist（first-run gate 解除前）

| Method | Path | 放行理由 |
|---|---|---|
| `GET` | `/admin/status` | 狀態查詢，無敏感資料，無寫入 |
| `GET` | `/cert` | CA 憑證下載，無敏感資料 |
| `GET` | `/cert/install` | 憑證安裝說明，靜態內容 |
| `GET` | `/` | PWA 入口頁，setup 前需可存取 |
| `POST` | `/admin/setup` | First-run setup，內部另驗 token + 密碼複雜度 |

靜態檔案（`express.static`）在 gate 之前處理，自動繞過 gate。所有其他路徑回 423 Locked。

#### Pi Server WebSocket Gate（first-run gate 解除前）

寫入類（`_STATE_CHANGING`）回 `FIRST_RUN_REQUIRED`：`delta`、`sync_push`、`session_restore`、`audit_event`、`clear_table`

放行（只讀 / 心跳）：`auth`、`debug_ping`、`catchup_req`、`time_sync_req`、`ping`

#### Token 產生時機與防 SD 卡 clone（L2）

First-run token 在 Pi Server **首次啟動時**寫入 `~/.ics/first_run_token`（chmod 600）。此路徑位於 `/home/ics/` 目錄，屬 home partition，**不在 SD 卡系統分區**。SD 卡 clone 只複製系統分區映像，不含 home partition 內容，因此 clone 後的 Pi 不帶 token，無法通過 first-run gate，必須重新完成 setup 流程。

### 2.5 Procedures
- **建立帳號**：sysadmin/commander 建帳號 → 後端產系統隨機臨時 PIN（`pin_policy.generate_temp_pin`，不落明文）→ `is_default_pin=1` → 首次登入 `must_change_pin` 強制改（server-side 閘，§2.8.1 P2a）。
- **發/綁裝置憑證**：mTLS 下，sysadmin 經面板線上發證（step-ca，後端不持 CA 鑰）或僅綁定既有 CN；一帳號可綁多裝置（`account_certs`）。
- **修改角色/狀態**：經 `/api/admin/accounts/*`（ACCOUNT_MANAGER_ROLES）；變更即寫 audit（`account_role_updated`/`account_status_updated`）。
- **停用/封存**：`status='suspended'/'archived'` → login 即拒（403）；憑證撤銷（`status='revoked'`）→ 下一個 request 即失效。
- **PIN 重設**：產新臨時 PIN + 強制首登再改；不洩漏舊值。

### 2.6 Audit
每次 role 變更、帳號建立/刪除/停用、憑證綁定/撤銷、登入（成功/失敗）、授權拒絕均寫 audit log（見 §3.4）。

### 2.7 Review
每年或重大鑑權/授權架構變更（如 #275 mTLS、#370 default-deny）後 re-review；RBAC 路由分類由 `test_rbac_route_matrix`（golden + default-deny fallthrough）每次測試自動守門。

### 2.8 Authentication Assurance（鑑權強度分級 / NIST 800-63B AAL）

> 對應：NIST SP 800-63B（AAL）；OWASP ASVS V2（Authentication）；CJIS §5.6（advanced authentication）。Tracking：[#275](https://github.com/winson3QQ/ICS_COMMAND/issues/275)；威脅依據：`threat_model` §8.6。

**原則**：鑑權強度應匹配角色的權限與影響等級（authentication assurance ∝ privilege / impact）。RBAC（§2.3）規範**授權**；本節規範**鑑權 assurance**（authentication），兩者正交。

**部署決策（2026-06-20）**：本系統所有角色之存取**均經公網**（cmd dashboard 對外），故**所有角色一律要求 mTLS 裝置憑證** —— 「PIN（something you know）+ 裝置 client cert（something you have）」構成雙因子，達 **AAL2**，且無任何角色裸曝公網（憑證即網路層准入）。

| 角色 | 影響 | 目標 AAL | Authenticator |
|---|---|---|---|
| observer | 低（唯讀） | AAL2 | PIN + 裝置 cert |
| operator | 中（可寫） | AAL2 | PIN + 裝置 cert |
| commander | 高（下令 / 管帳號） | AAL2（敏感操作可 step-up） | PIN + 裝置 cert |
| sysadmin | 最高 | AAL2–3 | PIN + 裝置 cert + 強制 audit |

**實作要點（#275 分波）**：
- nginx `ssl_verify_client on`（全角色 mTLS 單埠 443 強制版 `deploy/nginx/conf.d/command-mtls.conf.disabled`，Wave 3 PKI 簽出 client cert 後啟用）→ 無有效 client cert 連不進登入（網路層即擋）。
- 後端驗 `ssl_client_verify=SUCCESS` 並將 `X-Client-Cert-CN` **綁定帳號**（cert ↔ user = 第二因子）。
- **✅ trusted-header 剝除（wave 2 已落地）**：非 mTLS block（`command.conf` 443、`deploy/ics-validation/nginx.conf`）以 `proxy_set_header X-Client-Cert-CN "";`／`X-Client-Cert-Verify "";` **剝除** client 自帶值；mTLS block 改以 `$ics_client_cn`（map 從 `$ssl_client_s_dn` 抽 CN；stock nginx 無單欄位 CN 變數，直用會 emerg）／`$ssl_client_verify` **覆寫**注入真值。後端（uvicorn）**只經 nginx 可達**：bare-metal systemd 已改 bind `127.0.0.1`、容器 compose 不 publish 後端埠。**⚠ 紅隊修補（2026-06-20）**：容器拓樸下後端 bind `0.0.0.0:8000` 在共享網路上，**任何同網路容器可直打後端偽造 `X-Client-Cert-*` 繞 mTLS 拿 sysadmin**（紅隊實證）。修法＝**nginx↔後端共享密鑰**（`ICS_PROXY_SHARED_SECRET`）：後端只在請求帶相符 `X-Proxy-Auth`（nginx envsubst 注入、prod setup.sh 產隨機值）時才採信 cert header → 偽造請求（無密鑰）即使在內網也被拒（重打紅隊：401）。`client_cert_verified`/`client_cert_cn` 中央把關（`_proxy_trusted`）；未配置＝back-compat。
- **✅ PKI（wave 3 已落地）**：**per-device** 裝置憑證（一帳號可綁多台，`account_certs` 表 = SoT，migration v29）。簽發兩路徑：① **線上發證（面板「發憑證」，選項 i 安全版）**——後端**不持 CA 鑰**，呼叫 **step-ca daemon**（可撤銷 provisioner token）請簽 → 回傳 p12 + 自動綁定（`services/cert_issuance.py`、`POST /api/admin/accounts/{username}/certs/issue`）；② **離線簽**（`deploy/step-ca/issue-client-cert.sh` / Docker `issue-client`，複用 TAK `gen-device-dp.sh` pattern）+ 面板「僅綁定」。綁定/撤銷管理面：`/api/admin/accounts/{username}/certs`（GET/POST/DELETE，sysadmin only）。**撤銷採 App 層綁定撤銷**（`status='revoked'`）：login 查 active 綁定、`check_session` 每 request 查 `is_cert_active` → 撤銷後活躍 session **下一個 request 即失效**（不依賴 CRL 分發）。網路層 CRL（`ssl_crl` 握手即擋）留作後續客戶威脅模型加固。
- **✅ PIN KDF（wave 4）**：PBKDF2-HMAC-SHA256 100k → **600k**（OWASP 2023）；迭代數編進 hash 字串（`<iters>$<hex>`），舊裸 hex 相容（legacy 100k），登入成功透明 rehash 升級；驗證改 `hmac.compare_digest`（等時）。
- **✅ IP 信任（wave 4）**：`_client_ip`（`auth/service.py` + `auth/rate_limit.py`）改——反代後信任 nginx 設的 `X-Real-IP`（真實 client），直連時忽略可偽造的 `X-Forwarded-For`、用實際 peer（`config.ICS_BEHIND_PROXY` 切換）→ 防 §8.6 XFF 偽造繞限速。
- **✅ 鎖定-DoS（wave 4）**：帳號鎖定（§2.3，5/15min）對無裝置證者照舊（反爆破）；**出示綁定本帳號之有效 mTLS 裝置證者，鎖定不擋、錯 PIN 不再上鎖** → 攻擊者無證可鎖、合法本人持裝置永不被鎖死（解 §8.6 鎖死單一 admin 之患）。`unlock_account` 仍為 admin 復原路徑。

> **過渡**：mTLS 未全面佈署前，公網存取為**臨時驗證**狀態（`threat_model` §8.6），正式上線前 #275 必須完成。

#### 2.8.1 PIN 熵 — 已評估的接受風險（#348-F5；NIST 800-63B §5.1.1.2）

**現況**：PIN 為 4–6 位純數字（`routers/auth.py` 驗證），knowledge-factor 熵 10⁴–10⁶，**單看**不達 NIST 800-63B memorized-secret 強度，600k PBKDF2 對此空間助益有限。

**定位（doctrine，非疏漏）**：PIN 在本系統是 **mTLS 之後的「本地次因子」**，**非唯一/主要鑑權**。possession factor 是綁定本帳號的 **mTLS 裝置憑證**（something you have）——prod 強制 mTLS（nginx `ssl_verify_client`），**無有效裝置證者在網路層即被擋、根本到不了 PIN 輸入**；且登入成功仍須 cert↔帳號綁定一致才建 session（單純猜中 PIN 不足以登入）。故在「**已具 AAL2 possession**」前提下，PIN 低熵為**刻意接受的風險**（便於現場快速操作），而非鑑權缺口。

**界限 / 殘留**：
- 此接受**僅在 mTLS 強制（prod 預設）成立**；非 mTLS 部署下 PIN 即足以建 session → 低熵成真缺口（與 §2.3 lockout、#295 高權不鎖之 mTLS 前提同源）。
- 線上爆破另由帳號鎖定（§2.3）＋ `/api/auth/login` 限流（10/min/IP）＋ 計時旁路抹平（#348-F15）界定。
- **強度策略（P1 已落地）**：`core/pin_policy.validate_pin_strength` 套四出口——**長度 6–128、拒全同/連續/常見/==帳號名、無組成規則、開放長密語**（NIST 800-63B 對齊；本地 blocklist、無外部 API）。**不溯及**（登入只驗 hash）。**P2b 後**：change-initial / admin-PIN 直驗使用者輸入值；create / reset 改驗 `generate_temp_pin` 的**系統產生值**（內部 retry until 通過同策略）。
- **P2a 已落地**：admin 建帳號的初始 PIN **首登強制改**（`create` 設 `is_default_pin=1` → 登入
  `must_change_pin` → **auth_middleware per-account 閘**限改 PIN 路徑、其餘 423，server-side 真強制、
  非只靠前端）。`is_first_run_required` 收斂為 bootstrap-only（accounts==1 sysadmin default）→ 第一個
  admin 行為不變（#306 不受影響），第 2+ 帳號 default 不再觸發全系統 423。對齊 NIST：admin 給的初始/
  臨時憑證須首次使用即換。**閘覆蓋（review 補強）**：(a) cop `/ws/updates` WebSocket 不跑 HTTP
  middleware → handler 自查 `account_needs_pin_change`，待改帳號不得訂閱 live COP 串流；(b) reset_pin
  （`PUT .../pin`，不驗目前 PIN）僅 first-run bootstrap admin 放行，非 first-run 待改帳號須走
  change-initial-pin（驗目前 PIN）→ 杜絕「持被盜 session 免舊 PIN 自清 default 解閘」。
- **P3 已落地**：前端「使用者長久秘密」輸入欄放寬接受密語 + show-password 眼睛（opt-in，預設遮蔽，
  共用大螢幕防肩窺）。涵蓋 **3 個同一秘密的入口**：登入框、首登強制改 overlay、PinLock 閒置解鎖——
  三者連動（`maxlength 6→128`、去 `inputmode="numeric"`、捨數字正則改長度 6–128；強度/blocklist 仍由
  BE `pin_policy` 把關，FE 只驗長度+一致）。已廢的 Admin PIN 空殼維持不動（→ #384 另案移除）。
- **P2b 已落地**：admin **不再自設** create/reset 的 PIN → 後端 `pin_policy.generate_temp_pin`
  產**系統隨機臨時 PIN**（retry until 過 `validate_pin_strength`），**一次性**回 `temp_pin` 供前端顯示後
  轉交（**不落 plaintext、不寫 audit**；DB 僅存 hash）。`create` 移除 `pin` 欄；`reset_pin` 改不收 body、
  產臨時 PIN 並 `set_default_pin_flag`（**首登強制再改**，對齊 create）。安全性質：admin 無法得知/保留
  使用者初始 PIN，且初始值必為系統隨機（杜絕「admin 設可預測值 / 共用同一 PIN」）。前端建帳號/編輯面板
  移除手動 PIN 欄、改「重設為臨時 PIN」按鈕 + 一次性醒目顯示。
- **待續（分期）**：**第一個 admin 免-CLI 量產 onboarding（#382）**（QR 顯示出廠密碼之安全陷阱已議）。
  本節「評估後記錄接受風險 + 界定前提」；P1 補下限/可預測值、P2a 補初始憑證強制換、P2b 補系統指派初始
  憑證、P3 補可見密碼欄對齊（可輸密語），但**完全提升熵仍取決於使用者選長密語**，**不主張一律達
  800-63B memorized-secret 強度**。

---

## 3. Audit and Accountability Policy（稽核與課責政策）

> 對應：NIST 800-53 AU-1；ASVS V7

### 3.1 Purpose
規範稽核日誌之內容（記錄哪些事件）、保存期限、存取控制與完整性保護，確保安全相關操作可事後追溯、課責（民防 AAR 之命脈），並支援安全事件調查（§4）。

### 3.2 Scope
_所有 application log + audit log + access log_

### 3.3 Policy Statements
- 所有**寫入類/狀態變更**操作必須寫 audit log（`repositories/_helpers.audit`）。
- Audit 結構化欄位：`operator`/`device_id`/`action_type`/`target_table`/`target_id`/`detail`/`correlation_id`/`exercise_id`/`created_at`/`hash_prev`；演習場自動戳記（Model B）。
- Log 不得含明文 PII / 密碼 / token（PII mask；hash 只存雜湊）。
- 保存期間：一般 6 個月；個資存取相關依《個資法》要求（目標 5 年）。
- **完整性（AU-9(3)）— 已知缺口**：已具 SHA-256 hash chain（`core/audit_chain.py`），但 `verify_audit_chain` 尚未接入端點/排程，且為無金鑰鏈（有 DB 寫權者可重算偽造）→ **#348 GAP2**，補「驗證器接線 + HMAC 金鑰錨/簽章」前**不主張 AU-9(3) 達標**。

### 3.4 Logged Events（AU-2）
實際記錄之 `action_type`（SoT = code 各 `audit()` 呼叫；前端 `_AUDIT_BADGE` 為顯示對照）：

| 類別 | action_type |
|---|---|
| **鑑權/Session** | `login`、`logout`/`SESSION_LOGOUT`、`SESSION_EXPIRED`、`SESSION_REAPED`、`IDLE_KICKED`、`BINDING_MISMATCH_IP`、`BINDING_MISMATCH_UA`、`initial_pin_changed` |
| **帳號** | `account_created`、`account_role_updated`、`account_status_updated`、`account_pin_reset`、`account_display_name_updated`、`account_locked`、`account_unlocked`、`ACCOUNT_ARCHIVED`、`all_accounts_suspended` |
| **授權** | RBAC 拒絕（`ROLE_DENIED`，`auth/middleware.py`） |
| **COP/事件/決策** | `cop_entity_created/updated/deleted`、`cop_entity_faction_override`、`event_created`、`event_status_updated`、`event_note_added`、`decision_created`、`decision_made` |
| **演習/TTX** | `exercise_created/status_updated/deleted/reset`、`ttx_inject_fired` |
| **TAK/憑證** | `cert_issue`、`cert_bind`、`cert_revoke`、`cert_purge_revoked`、`tak_device_cert_issue`、`tak_user_deregister`、`tak_user_strip_anon` |
| **系統/資料** | `config_updated`、`db_reset`、`RETENTION_CLEANUP`、`three_pass_sync`、`conflict_resolved`、`manual_input`/`manual_record_synced`、`snapshot_received`、`pi_node_created/deleted/rekeyed` |
| **備份/還原** | `user_data_backup_downloaded`、`user_data_backup_manifest_read`、`user_data_backup_failed`、`user_data_restore_failed`、`user_data_restore_interrupted` |

> 新增寫入類端點時，須同步在此表登記其 action_type（與 §2.3 中央 gate 登記同為 code-review 檢查點）。

### 3.5 Review
每年或新增重大寫入類功能後 re-review；完整性閉環（#348 GAP2）完成後更新本節 AU-9(3) 達標宣告。

---

## 4. Incident Response Policy（事件應變政策）

> 對應：NIST 800-53 IR-1；附表十 §7 事件通報

### 4.1 Purpose
規範資安事件之偵測、分類、應變、復原與通報流程，使事件衝擊最小化並符合法定通報義務。

### 4.2 Scope
涵蓋未授權存取、憑證/PIN 外洩、帳號爆破、資料外洩/竄改、勒索/主機失陷、服務中斷等；含 prod 單機棧與其資料/金鑰。

### 4.3 Incident Classification
- **Level 1 可疑活動（告警）**：偵測訊號如帳號鎖定、cert 第二因子失敗、binding mismatch（`security_monitor` / audit）。
- **Level 2 有限資料外洩**：單一帳號/裝置受限影響。
- **Level 3 大規模外洩 / 服務中斷**：主機失陷、信任根（CA 鑰）疑洩、prod 不可用。
- **Level 4 個資外洩**：涉傷患/人員個資 → 觸發《個資法》§12 通報義務。

### 4.4 Response Steps（Detect → Contain → Eradicate → Recover → Lessons Learned）
1. **Detect**：audit log（§3.4）+ `security_monitor`（認證異常）+ 健康/燈號；人工發現亦循此流程。
2. **Contain**：撤銷受影響裝置憑證（`status='revoked'`，下一 request 即失效）；停用/封存帳號；必要時 `POST /api/tak/connection` 關 TAK；最嚴重者隔離主機/斷網。
3. **Eradicate**：修補根因（patch/組態）；輪替受影響金鑰/secret（`ICS_PROXY_SHARED_SECRET`、憑證、`BACKUP_KEY`）；重簽憑證。
4. **Recover**：由 §5 加密備份還原 `data/`；驗 `/api/version` + 登入 + COP；確認 audit 完整。
5. **Lessons Learned**：事後 AAR；finding 留痕 Issue/PR（對齊 PROCESS〈Post Findings〉），更新本政策與 audit-status 頁。

### 4.5 Notification
- **內部**：第一時間通知專案維護者 / sysadmin。
- **個資外洩（Level 4）**：依《個資法》§12 於知悉後**適當方式通知當事人**並向主管機關通報（PDPC/目的事業主管機關），72 小時為作業目標。
- **資安事件**：依《資通安全管理法》責任等級之通報時限向上級/主管機關通報。
- **涉及犯罪**：報請司法/警察機關。

### 4.6 Review
每次 Level ≥2 事件後 re-review 本流程；每年定期演練（含通報路徑）。

---

## 5. Contingency Plan（應變計畫 / 業務持續）

> 對應：NIST 800-53 CP-1；災害防救法

> #348-F9：本節由佔位草稿補成「真實機制 runbook」。⚠️ **HA 與正式 DR 演練未完**，見 §5.5/§5.6 誠實界定——本節是**復原程序文件**，不等於已驗證的 BC/DR。

### 5.1 Purpose
系統元件失效（DB 損毀、誤刪、主機故障、勒索）時，能在可接受時間內**還原 user data（`data/`）並恢復指揮運作**；並界定本系統「韌性 > 機密性」的民防第一序與其**已知韌性缺口**。

### 5.2 Scope
- **資料**：`data/` 邊界整包（`ics.db` + `map_config.json` + event_taxonomy + uploads；CLAUDE.md User Data 紅線）。
- **不含**：底圖 tiles（`ics-tiles` 卷，可由 `provision_basemap` 重建）、CA 卷（`ca-data`；砍則已發證全失效，屬金鑰生命週期非資料還原）。
- **架構現況**：**單機**（CA + DB + app 同主機，無 HA）——見 §5.5 韌性缺口。

### 5.3 Backup Strategy（實際機制）
- **主備份 = 整包 `data/` 加密備份**（`services/user_data_backup_service.py`）：tar.gz 全 `data/`（排除 `data/backups/` 防遞迴）+ `MANIFEST.json`（含 exercise metadata/trigger/app_version）+ **Fernet 加密**；SQLite 走 online backup 一致快照（排除 -wal/-shm）。產物 `data/backups/userdata-*.tar.gz.enc`（#348-F15 起 chmod 0600）。
- **金鑰**：`BACKUP_KEY`（P1-12a HKDF child[0]；過渡相容 `BACKUP_ENCRYPTION_KEY`）。**無金鑰的 dev 部署不備份**（`key_available()` 略過）。
- **觸發**：① 手動（admin GUI）② 正常關機 best-effort（`lifespan` shutdown）③ 破壞性操作前自動（reset-db/reset-exercise/restore → `pre-restore-*`）④ admin 端點。
- **滾動保留**：`ICS_BACKUP_RETAIN_DAYS=30` / `KEEP_MIN=10`；`archive`（演習里程碑）與 `pre-restore`（還原防呆）不被自動刪。
- **目標**：RPO ≤ 最近一次觸發（關機/手動/破壞前）；RTO ≤ 1 小時（單機重佈署 + restore，未正式計時，見 §5.5）。
- **離站**：異地副本（NAS/物件儲存）**未自動化**（手動複製 `data/backups/*.enc`，加密故可外放）；列韌性缺口。

### 5.4 Recovery Procedures（runbook）

**A. 應用層還原（誤刪/資料損壞，主機仍在）— 主路徑**
1. sysadmin 登入 → 帳號管理 → 備份/還原；或 API `POST /api/admin/user-data-backups/{name}/restore`（既有備份）/ `POST /api/admin/restore`（上傳 `.tar.gz.enc`）。**active 演習 → 409**（先封存）。
2. 後端流程（`routers/backup_restore._restore_from_path` → `user_data_backup_service.restore_backup`）：**解密（試 BACKUP_KEY/legacy）→ 驗 MANIFEST → 先自動備份當前（`pre-restore-{ts}`）→ 替換 `data/`**。
3. 還原後重啟 app（或依提示），驗 `/api/version` + 登入 + COP 正常。失敗可由步驟 2 的 `pre-restore-*` 回滾。

**B. 主機/容器層 DR（主機故障、勒索、遷機）**
1. 乾淨佈署棧（見 `deploy/prod/README`）。**保留或還原卷**：`ca-data`（CA，砍則重走 onboarding + 重發證）、`ics-data`（DB）、`ics-tiles`（底圖，可重 provision）。
2. 還原 `ics-data` 卷（volume-level，已於 #306 測試實證可行）：
   ```bash
   docker run --rm -v ics-data:/data -v "$PWD":/bk alpine tar xzf /bk/<備份>.tgz -C /data
   ```
   或把 `data/backups/userdata-*.tar.gz.enc` 放回後走 §5.4-A 應用層還原。
3. 確認 `BACKUP_KEY` 可得（否則加密備份無法解）→ 還原 → 驗證。

**C. 金鑰前提**：所有加密備份還原**依賴 `BACKUP_KEY`**；金鑰遺失＝備份不可解。金鑰保管屬 P1-12a/#226 範圍。

### 5.5 Testing（誠實界定 — 未完）
- **政策目標**：每 6 個月至少一次完整 recovery drill（乾淨環境從加密備份還原並驗證）。
- ⚠️ **現況：正式 DR 演練未執行**。已有**部分證據**：#306 測試期間做過 `ics-data` 卷層 tar 備份→還原（B 路徑步驟 2）；應用層 restore 有單元/整合測試（`test_user_data_backup`/`test_backup_restore_api`）。但「乾淨環境端到端、計時 RTO/RPO」的正式演練**尚未做** → 在此之前**不得主張 CP/ISO 22301 BC/DR 達標**。

### 5.6 Review / 已知韌性缺口（#348-F9）
- **無 HA**：單機（CA+DB+app 同主機），主機故障即服務中斷，無自動 failover。降階 doctrine（TAK 掛 → 退原生 COP；離線 PMTiles 底圖）是**部分**補償，**不等於** HA/BC。HA（多機/備援）屬 infra 大工、**範圍外**、未排程。
- **離站備份未自動化**、**DR 演練未跑**（§5.5）。
- 上述為**已記錄的接受/待辦缺口**，非主張已解。每年或重大架構變更 re-review。

---

## 6. Privacy Policy（個資保護政策）

> 對應：個資法 PDPA；NIST Privacy Framework；NIST 800-53 PT family

### 6.1 Purpose
規範系統內個人資料之蒐集、處理、利用與刪除原則，符合《個資法》並落實資料最小化。

### 6.2 Scope（本系統實際 PII 盤點）
- **人員/傷患**：`events`（`related_person_name`、`location_desc`、`operator_name`、`reported_by_unit`、`description`、`notes`）。
- **位置軌跡**：`cop_entity_tracks`（lat/lon 時序）、`cop_entities`（callsign、座標）。
- **通聯**：`chats.message`。
- **帳號/連線**：accounts、`sessions`（IP、user-agent、cert CN）。

### 6.3 Principles
- **目的限定**：僅為應變指揮/演訓蒐集。
- **資料最小化**：必要欄位才蒐集。
- **保存限定**：軌跡 PII 90 天 TTL（`retention_service`，預設開、sysadmin 可調、TTL=0 防呆）。⚠ **缺口（#348-F10）**：`events`/`chats`/`sessions.ip` 目前**無 TTL**，待補保存期限。
- **完整性/機密性**：存取經 RBAC + scope 隔離 + audit；at-rest 加密能力具備（#229，prod 啟用為 #348 GAP1）。
- **透明**：對演訓參與者/當事人告知蒐集。

### 6.4 Data Subject Rights
當事人查閱/更正/停止利用/刪除之請求，經 sysadmin/commander 於系統內處理：查閱（依場次/人員撈）、更正（編輯對應記錄）、刪除（刪 cop_entity/事件/通聯，受 archived/propagate 限制）。reset/exercise-delete 級聯清資料（含 chats，PII 衛生）。

### 6.5 Breach Notification
個資外洩依《個資法》§12：知悉後以適當方式通知當事人並向主管機關通報（72h 作業目標）。與 §4.5 一致。

### 6.6 Cross-Border Transfer
演訓/實戰資料**不出境**、不上公有雲。AI 推論若用外部模型，僅以**匿名化/去識別**資料；本系統 AI 路徑現為本地（無雲端 PII 傳輸）。

### 6.7 Review
每年或新增 PII 欄位/資料流時 re-review；補齊 §6.3 缺口（events/chats/session TTL）後更新。

---

## 附錄 A：Policy 與程式碼 / 設定的對應

Policy statement → 實作/設定 → 證據對照（控制狀態總覽見 [`audit-status.md`](audit-status.md)）：

| Policy | 控制 | 實作 / 設定 | 證據 |
|---|---|---|---|
| §1.3 fail-secure | default-deny 授權 | `auth/role_enum.allowed_roles_for` | #370；`test_rbac_route_matrix` |
| §2.3 每 endpoint 預設拒絕 | 中央 RBAC gate | `auth/middleware.py` | #287/#370 |
| §2.3 讀寫授權對稱 | scope 隔離 | `services/exercise_service.resolve_scope` | #288 |
| §2.8 AAL2 | mTLS + PIN 雙因子 | nginx `ssl_verify_client`；`account_certs` | #275 |
| §2.8 proxy 信任 | 共享密鑰 fail-fast | `ICS_PROXY_SHARED_SECRET`；`main.py:95` | #290 |
| §2.8 PIN KDF | PBKDF2 600k + rehash | `repositories/_helpers.py` | wave4 |
| §3.3 寫入留痕 | audit() | `repositories/_helpers.audit` | §3.4 |
| §3.3 完整性 | hash chain（⚠ 驗證未接）| `core/audit_chain.py` | #348 GAP2 |
| §5.3 加密備份 | Fernet `data/` 整包 | `services/user_data_backup_service.py` | #228 |
| §6.3 保存限定 | 軌跡 90d TTL | `services/retention_service.py` | #207 |
| §6 at-rest | SQLCipher（⚠ 預設未開）| `core/database.py` `_connect` | #229；#348 GAP1 |
| §1.3 供應鏈紅線 | 無中國元件 | `requirements.txt` 維護者註記 | CLAUDE.md |

> 控制狀態（✅/🟡/❌）+ 標準對照 + 缺口 owner：見 [`audit-status.md`](audit-status.md)（single-pane）。

---

## 附錄 B：修訂歷史

| 日期 | Version | 變更 |
|---|---|---|
| 2026-04-25 | 0.1 | 骨架建立（多數小節未填佔位） |
| 2026-06-24 | 0.1.1 | #348-F11 誠實化：加未完稿/不可作合規證據警語、明示 AC-1/AU-1/IR-1/CP-1/PT 暫不可主張、修死連結 `matrix.md`（已廢→指 ROADMAP Compliance touchpoints）、擁有者改 ICS_Command |
| 2026-06-24 | 0.1.2 | #348-F5：§2.8.1 PIN 熵「已評估接受風險」doctrine（PIN=mTLS 後本地次因子，前提/殘留界定，不主張達 800-63B 強度）。#348-F9：§5 Contingency Plan 補真實機制 runbook（加密 data/ 備份 + 應用層/卷層還原 + 金鑰前提），誠實標註 DR 演練未跑、無 HA = 已記錄缺口（不主張 BC/DR 達標） |
| 2026-06-27 | 0.2 | #348-F11 補實內文：§1（資安總政策 purpose/scope/statements/roles/compliance）、§2.1/2.2/2.5–2.7、§3.1/3.3/§3.4（AU-2 logged-events 實際清單）/3.5、§4（事件應變全節 detect→notify）、§6（個資全節 + 實際 PII 盤點）、附錄 A（policy↔code 對照）；header 改 self-attestation；GAP1/GAP2/F10/DR 缺口 inline 標註、不主張達標 |
