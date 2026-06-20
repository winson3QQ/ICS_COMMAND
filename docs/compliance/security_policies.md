# ICS_DMAS Security Policies

> **依據**：NIST SP 800-53 每個 control family（xx-1）均要求對應 policy 文件；未寫 policy 等於該 family 不能主張 compliance。
> **組織化**：6 份政策併一檔（原本分 6 檔會碎裂），各為獨立章節，各含 Purpose / Scope / Policy Statements / Procedures / References / Review。
> **狀態**：骨架建立（Session D 完稿；但 C1-A Phase 4 會把草稿升為正式 v1）。
> **最後更新**：2026-04-25
> **擁有者**：ICS_DMAS 專案
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
- compliance/matrix.md
- compliance/threat_model.md

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
- **✅ trusted-header 剝除（wave 2 已落地）**：非 mTLS block（`command.conf` 443、`deploy/ics-validation/nginx.conf`）以 `proxy_set_header X-Client-Cert-CN "";`／`X-Client-Cert-Verify "";` **剝除** client 自帶值；mTLS block 改以 `$ics_client_cn`（map 從 `$ssl_client_s_dn` 抽 CN；stock nginx 無單欄位 CN 變數，直用會 emerg）／`$ssl_client_verify` **覆寫**注入真值。後端（uvicorn）**只經 nginx 可達**：bare-metal systemd 已改 bind `127.0.0.1`、容器 compose 不 publish 後端埠。否則攻擊者直連後端偽造 cert header 可繞第二因子（AAL2→AAL1）。後端側 `X-Client-Cert-*` 信任已 env-gated（`ICS_MTLS_REQUIRED`）。
- **✅ PKI（wave 3 已落地）**：**per-device** 裝置憑證（一帳號可綁多台，`account_certs` 表 = SoT，migration v29）。簽發：`deploy/step-ca/issue-client-cert.sh`（step-ca offline 簽 client cert + p12，複用 TAK `gen-device-dp.sh` pattern）。綁定/撤銷管理面：`/api/admin/accounts/{username}/certs`（GET/POST/DELETE，sysadmin only）。**撤銷採 App 層綁定撤銷**（`status='revoked'`）：login 查 active 綁定、`check_session` 每 request 查 `is_cert_active` → 撤銷後活躍 session **下一個 request 即失效**（不依賴 CRL 分發）。網路層 CRL（`ssl_crl` 握手即擋）留作後續客戶威脅模型加固。
- PIN KDF：PBKDF2-HMAC-SHA256 現 100k → 升 OWASP 2023 建議 600k。
- IP 信任：`_client_ip` 改信任反代設定的可信 hop（非 `X-Forwarded-For` 最左值），防偽造繞限速。
- 鎖定-DoS：帳號鎖定（§2.3，5/15min）保留，加 per-source / admin 復原路徑，避免攻擊者鎖死單一 admin。

> **過渡**：mTLS 未全面佈署前，公網存取為**臨時驗證**狀態（`threat_model` §8.6），正式上線前 #275 必須完成。

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
_Session B 填：完整事件清單（對照 matrix AU-2）_

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

### 5.1 Purpose
_Session C/D 填：系統失效時的備援 + 資料還原 + 演練照常運作_

### 5.2 Scope

### 5.3 Backup Strategy
_Session C 填（對應 C3-D）。草稿：_
- SQLite WAL（即時）
- Daily gzip（保留 30 天）
- NAS rsync（可選）
- RTO: 4 hours / RPO: 1 hour

### 5.4 Recovery Procedures
_Session C 填：step-by-step recovery playbook_

### 5.5 Testing
_Session C 填：每 6 個月至少一次 recovery drill_

### 5.6 Review

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

_Session D 填：每個 policy statement 對應的 matrix control + 實作檔案_

---

## 附錄 B：修訂歷史

| 日期 | Version | 變更 |
|---|---|---|
| 2026-04-25 | 0.1 | 骨架建立（Session D 完稿） |
