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

---

## 8. 審查歷程

| 日期 | Version | 變更 |
|---|---|---|
| 2026-04-25 | 0.1 | 骨架建立（Session D 完稿）|
