# Security Policy

## 漏洞通報（Vulnerability Disclosure）

我們重視 ICS_DMAS 系統的安全性。若您發現任何資安漏洞，請依下列流程通報。

### 通報方式

請**不要**在 GitHub Issues / Pull Requests / Discussions 公開揭露漏洞細節。

請將漏洞報告**私下**寄至：
- **Email**：[資安通報窗口待補]
- 或使用 GitHub 的 [Private Vulnerability Reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability) 功能

### 通報內容

請盡可能提供以下資訊：
- 漏洞描述與影響範圍（Impact）
- 重現步驟（含 PoC，如有）
- 受影響的版本（如 `cmd-v2.0.5`、`server-v1.3.0`、`shelter-v2.2.53`、`medical-v0.6.7-alpha`）
- 受影響的組件（command-dashboard / Pi server / shelter PWA / medical PWA）
- 您建議的修補方式（選填）

### 回應 SLA

| 嚴重度 | 確認 | 修補目標 |
|---|---|---|
| 🔴 Critical（資料外洩 / RCE / 認證繞過）| 24 小時內 | 7 天內 |
| 🟡 High（權限提升 / DoS）| 72 小時內 | 30 天內 |
| 🟠 Medium（資訊揭露 / 設定漏洞）| 7 天內 | 90 天內 |
| ⚪ Low（最佳實務 / 加固建議）| 14 天內 | 下個 release |

### 揭露政策

- 我們採 **Coordinated Disclosure** 原則
- 修補完成 + release 後 30 天，再公開漏洞細節（給用戶升級時間）
- 通報者若同意，我們會在 changelog / release notes 致謝

## 涵蓋範圍（Scope）

### In Scope
- `command-dashboard/` 後端（FastAPI + SQLite）
- `server/` Pi server（Node.js + WebSocket）
- `shelter-pwa/` 收容組 PWA
- `medical-pwa/` 醫療組 PWA
- `deploy/nginx/` + `deploy/step-ca/` 部署基礎設施

### Out of Scope
- 第三方依賴漏洞（請通報原專案；我們會跟進升級）
- 客戶部署環境的 OS / 網路設定問題
- 社交工程 / 釣魚 / 實體入侵

## 已知議題（公開）

詳見 [docs/compliance/matrix.md § Gap Register](docs/compliance/matrix.md) — 我們公開維護合規缺口清單，採透明原則。

正在處理的高優先項目（v2.1.0 投標版本前完成）：
- C1-A Phase 2：4-role RBAC + 後端 endpoint-level gate
- C1-C：個資三層加密（Fernet / SQLCipher / LUKS）
- ✅ C1-D：結構化日誌 + correlation_id 全鏈路（PR#20 · 2026-04-28 完成）
- audit hash chain：audit_log 防篡改（另立 task，C1-D 未含）
- C3-D：自動備份 + DR playbook
- C2-E：SBOM + SLSA L2 + dep scan

## 個資外洩 72h 通報（個資法 §12）

若漏洞涉及個人資料外洩（特別是醫療 PWA 的病患資料），我們將於發現後 **72 小時內**：
1. 通報「個人資料保護委員會」（PDPC）
2. 通知受影響的當事人
3. 公開外洩範圍與已採取的補救措施

詳見 [docs/compliance/security_policies.md § 6 Privacy Policy](docs/compliance/security_policies.md)。

## 參考標準

本系統參照下列標準制定資安要求：
- NIST SP 800-53 Rev. 5（Moderate baseline）
- NIST SP 800-218 SSDF
- OWASP ASVS 4.0 Level 2
- CIS Controls v8
- 中華民國個人資料保護法
- 中華民國資通安全管理法 + 附表十防護基準

詳見 [docs/compliance/](docs/compliance/) 完整對照。

---

**最後更新**：2026-04-25
**下次 review**：2027-04-25 或重大事件後
