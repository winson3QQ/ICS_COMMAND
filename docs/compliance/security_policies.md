# ICS_Command Security Policies（承自 ICS_DMAS）

> # ⚠️ 狀態：未完稿骨架（DRAFT SKELETON）— 不得作為合規證據
> 本文件多數小節仍為 `_Session X 填_` **未填佔位**（見下）。**請勿據此主張任何 control family
> 的 compliance**——尤其 **AC-1 / AU-1 / IR-1 / CP-1 / PT** 對應的 policy 內文尚未撰寫，依本文件
> 開頭「未寫 policy 等於該 family 不能主張 compliance」的自訂規則，這些 family **目前不可主張**。
> 已落地的**技術控制**（RBAC default-deny、cert-bound session、PBKDF2 600k、audit hash chain +
> runtime 驗證、PII TTL…）證據在 **code + 測試 + `threat_model.md` + ROADMAP《Compliance
> touchpoints》**；本文件是「政策層」骨架，與技術落地有落差，補實前不等於 ISMS 合規。
> （#348-F11 誠實化：移除「完稿」overclaim、修死連結、明示主張限制。補實 ISMS 內文為獨立大工。）

> **依據**：NIST SP 800-53 每個 control family（xx-1）均要求對應 policy 文件；未寫 policy 等於該 family 不能主張 compliance。
> **組織化**：6 份政策併一檔（原本分 6 檔會碎裂），各為獨立章節，各含 Purpose / Scope / Policy Statements / Procedures / References / Review。
> **狀態**：**0.1 草稿骨架，未完稿**（多數小節為未填佔位；補實前不得引為合規證據）。
> **最後更新**：2026-06-24（#348-F11 誠實化；內文骨架仍 2026-04-25）
> **擁有者**：ICS_Command 專案（承自 ICS_DMAS）
> **Review 週期**：每年一次，或重大架構變更時 re-review
>
> **重要**：本文件為自我聲明（self-attestation）policy；若需通過 ISO 27001 第三方驗證，需由認證 auditor 覆核並加厚程序細節。

---

## 文件元資料

| 項目 | 值 |
|---|---|
| 版本 | 0.1（草稿） |
| 生效日期 | 2026-04-25（草稿） |
| 下次 review | 2027-04-25 |
| 核准 | 待 |

---

## 1. Information Security Policy（資訊安全總政策）

> 對應：NIST 800-53 PM-1 / PL-1；ISO 27001 5.2；附表十防護基準 §1

### 1.1 Purpose
_Session D 填：說明組織對資訊安全的總原則承諾_

### 1.2 Scope
_Session D 填：涵蓋 ICS_DMAS 全系統（command + pi + pwa + 相關基礎設施）_

### 1.3 Policy Statements
_Session D 填。核心聲明草稿：_
- 採 defense-in-depth
- 最小權限
- 失敗即關閉（fail-secure）
- 依據 NIST SP 800-53 Moderate baseline 實作控制項

### 1.4 Roles and Responsibilities
_Session D 填：系統管理員 / 指揮官 / 操作員 / 觀察員 各自資安責任_

### 1.5 Compliance
_Session D 填：對應法規 + 違反處置_

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
_Session A/D 填：規範身份識別、授權、最小權限實施_

### 2.2 Scope
_全系統所有帳號與 API endpoint_

### 2.3 Policy Statements
_Session A 填。草稿：_
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
_Session A 填：建立 / 修改 / 停用帳號流程_

### 2.6 Audit
_每次 role 變更、帳號建立 / 刪除均寫 audit log_

### 2.7 Review

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
- **強度策略（P1 已落地）**：`core/pin_policy.validate_pin_strength` 套四出口（create/reset/change-initial/admin-PIN）——**長度 6–128、拒全同/連續/常見/==帳號名、無組成規則、開放長密語**（NIST 800-63B 對齊；本地 blocklist、無外部 API）。**不溯及**（登入只驗 hash）。
- **待續（分期）**：P2 = 新帳號隨機臨時 PIN + **首登強制改全帳號**（需與系統 first-run gate/#306 bootstrap 解耦，獨立 PR）；P3 = 前端輸入欄放寬 + show-password。本節為「評估後記錄接受風險 + 界定前提」；P1 已把下限/可預測值補上，但**完全提升熵仍取決於使用者選長密語**，**不主張一律達 800-63B memorized-secret 強度**。

---

## 3. Audit and Accountability Policy（稽核與課責政策）

> 對應：NIST 800-53 AU-1；ASVS V7

### 3.1 Purpose
_Session B/D 填：規範 log 內容、保存、存取、完整性保護_

### 3.2 Scope
_所有 application log + audit log + access log_

### 3.3 Policy Statements
_Session B 填。草稿：_
- 寫入類操作必須 audit log
- ✅ Audit log 有結構化 JSON + PII mask 6 類 + logrotate 7 天（C1-D PR#20 2026-04-28）
- Audit log hash chain 防篡改：另立 task（Step A Q6 凍結，未含於 C1-D）
- 保存期間 6 個月（一般）/ 依法規要求（個資存取 5 年）
- Log 不得含明文 PII / 密碼 / token

### 3.4 Logged Events
_未填（草稿）：完整 logged-events 清單（AU-2）。實際 audit action_type 清單見 code（`repositories`
各 audit() 呼叫）+ `static/js/auth.js` `_AUDIT_BADGE`；本節待補成正式對照。原「matrix AU-2」對照已廢
（matrix.md 不存在），改以 ROADMAP《Compliance touchpoints》＋ code 為準。_

### 3.5 Review

---

## 4. Incident Response Policy（事件應變政策）

> 對應：NIST 800-53 IR-1；附表十 §7 事件通報

### 4.1 Purpose
_Session C/D 填：資安事件偵測、分類、應變、通報流程_

### 4.2 Scope

### 4.3 Incident Classification
_Session C 填。草稿：_
- Level 1：可疑活動（告警）
- Level 2：有限資料外洩
- Level 3：大規模外洩 / 服務中斷
- Level 4：個資外洩（觸發 72h 通報義務）

### 4.4 Response Steps
_Session C 填：Detect → Contain → Eradicate → Recover → Lessons Learned_

### 4.5 Notification
_Session C 填：內部 / PDPC / 司法機關（若涉及犯罪）_

### 4.6 Review

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
_Session B/D 填：個人資料的蒐集、處理、利用原則_

### 6.2 Scope
_傷患姓名 / 年齡 / 症狀 / 過敏史；志工帳號；演練參與者資料_

### 6.3 Principles
_Session B 填：_
- Purpose limitation（蒐集目的明確）
- Data minimization（必要資料才蒐集）
- Storage limitation（超過保存期刪除）
- Integrity（加密儲存、存取稽核）
- Transparency（告知當事人）

### 6.4 Data Subject Rights
_Session B 填：查閱 / 更正 / 停止利用 / 刪除 的程序_

### 6.5 Breach Notification
_Session B 填：72h PDPC 通報流程（個資法 §12）_

### 6.6 Cross-Border Transfer
_Session B 填：演練資料不出境；雲端 AI 僅用匿名化資料_

### 6.7 Review

---

## 附錄 A：Policy 與程式碼 / 設定的對應

_未填（草稿）：每個 policy statement → 實作檔案 / 設定的對照表。原規劃的 `matrix.md` control
對照已廢；現行 compliance↔實作對照 inline 於 [`docs/ROADMAP.md`](../ROADMAP.md) 各 phase 的
《Compliance touchpoints》。本附錄待補成 policy-statement 粒度的對照。_

---

## 附錄 B：修訂歷史

| 日期 | Version | 變更 |
|---|---|---|
| 2026-04-25 | 0.1 | 骨架建立（多數小節未填佔位） |
| 2026-06-24 | 0.1.1 | #348-F11 誠實化：加未完稿/不可作合規證據警語、明示 AC-1/AU-1/IR-1/CP-1/PT 暫不可主張、修死連結 `matrix.md`（已廢→指 ROADMAP Compliance touchpoints）、擁有者改 ICS_Command |
| 2026-06-24 | 0.1.2 | #348-F5：§2.8.1 PIN 熵「已評估接受風險」doctrine（PIN=mTLS 後本地次因子，前提/殘留界定，不主張達 800-63B 強度）。#348-F9：§5 Contingency Plan 補真實機制 runbook（加密 data/ 備份 + 應用層/卷層還原 + 金鑰前提），誠實標註 DR 演練未跑、無 HA = 已記錄缺口（不主張 BC/DR 達標） |
