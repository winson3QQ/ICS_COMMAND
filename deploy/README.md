# deploy/ — 部署設定與腳本

C1-B 階段建立。所有與「程式以外」的部署相關內容集中於此：TLS 憑證管理、nginx 反代、未來 systemd unit / install.sh（C3-B）。

## 目錄

| 子目錄 | 用途 | 階段 |
|---|---|---|
| `step-ca/` | 內網 PKI（憑證簽發 + 續期）| C1-B |
| `nginx/` | HTTPS 反向代理設定 | C1-B |
| ~~`systemd/`~~ | （規劃中）service unit | C3-B |
| ~~`install/`~~ | （規劃中）一鍵安裝腳本 | C3-B |

## C1-B 完整 HTTPS Stack 啟動（Mac dev）

一次性前置（每台機器只做一次）：

```bash
brew install step nginx

# 1. 初始化 dev CA
deploy/step-ca/init-mac-dev.sh

# 2. 把 root CA 加進 macOS 系統信任
deploy/step-ca/trust-root-mac.sh

# 3. 啟動 CA daemon（背景）
nohup deploy/step-ca/start-ca.sh > ~/.step/ca.log 2>&1 &

# 4. 簽發各服務憑證
deploy/step-ca/issue-cert.sh command.ics.local 127.0.0.1 localhost
deploy/step-ca/issue-cert.sh shelter.ics.local 127.0.0.1
deploy/step-ca/issue-cert.sh medical.ics.local 127.0.0.1

# 5. /etc/hosts
echo '127.0.0.1 command.ics.local shelter.ics.local medical.ics.local' | sudo tee -a /etc/hosts
```

日常啟動（HTTPS full stack）：

```bash
./start_mac_https.sh
```

或保留純 HTTP fast path（dev iteration）：

```bash
./start_mac.sh   # 不走 nginx，無 TLS
```

## 與既有 mkcert 憑證的關係

`certs/192.168.100.10+2.pem` 等 mkcert 產物**保留**為 fallback：

- `start_pi.sh` 會優先找 step-ca 憑證，找不到才用 mkcert
- 演練成功一次後可考慮淘汰 mkcert（時間點：C3-B 一鍵安裝腳本完成）

## 22 縣市部署（C3-B 才做，C1-B 先設計）

每個客戶交付獨立 step-ca instance（per-customer CA 隔離原則）：

- 客戶 A 的 CA 私鑰外洩 → 只影響客戶 A
- 對應 ISO 27001「資安事件影響範圍最小化」（commercialization §C6-A）
- step-ca 跑在客戶的 Command Console（同一台 N100），不是廠商雲端

C3-B `install.sh` 會包含 step-ca 自動初始化、systemd unit、ACME renew timer、nginx reload hook。

---

## PKI 管理政策（資安稽查 / ISO 27001 用）

> 此節作為 commercialization §C6-A ISMS 文件集的「密碼/PKI 管理政策」實質內容。
> 法源：附表十資通系統防護基準「通訊傳輸保護」項；NIST SP 800-57 Part 1 Rev. 5。

### 1. CA 來源策略（雙軌）

| 選項 | 適用場景 | 預設 |
|---|---|---|
| **A. step-ca 內建** | 純內網部署（Pi ↔ Command 不對外曝露）| ✅ 預設 |
| **B. GTLSCA 政府憑證** | 客戶 RFP 明文要求 / 對外 portal | 可選 |
| **C. 客戶自備（BYO）** | 客戶已有企業 PKI（有 root CA 想沿用）| 可選 |

法規依據：附表十「通訊傳輸保護」項規定加密演算法須「公開、國際驗證、未被破解」，**未指定 CA 來源**。GTLSCA 為政府對外服務之官方推薦，非強制。

### 2. 演算法基線

| 用途 | 演算法 | 金鑰長度 |
|---|---|---|
| Root CA | ECDSA P-256 或 RSA 4096 | — |
| Intermediate CA | ECDSA P-256 | — |
| Server cert | ECDSA P-256（預設） | — |
| TLS 協定 | TLS 1.2 + 1.3 | — |
| Cipher suite | Mozilla Intermediate（見 `nginx/conf.d/ssl-common.conf`）| — |

禁用：MD5、SHA-1、RC4、3DES、TLS 1.0/1.1、RSA < 2048。

### 3. 金鑰管理

**Root CA 私鑰**
- 儲存：`~/.step/secrets/root_ca_key`（dev）/ `/etc/step-ca/secrets/`（prod）
- 權限：0600，owner=`ics` 系統帳號
- 備份：客戶部署時，廠商**不持有**客戶 root CA 私鑰；備份由客戶自行加密儲存於離線媒體
- 災難復原：root CA 損毀 → 重建新 CA + 重發所有 server cert（記錄於 incident log）

**Intermediate CA 私鑰**
- 同上，但有 short-lived 設計（每 1 年 rotate）

**Server cert 私鑰**
- 權限：0600，owner=各服務帳號（uvicorn / node）
- 不出 host：私鑰只在簽發機產生，不跨機複製

### 4. 憑證生命週期

| 類別 | 預設有效期 | 續期觸發 |
|---|---|---|
| Root CA | 10 年 | 提前 1 年規劃接班 CA |
| Intermediate CA | 1 年 | 自動續（systemd timer，C3-B）|
| Server cert | 90 天（prod）/ 24h（dev）| 過期前 1/3 時間自動續 |

ACME provider 由 step-ca 提供，協議與 Let's Encrypt 相同。

### 5. 廢止流程（CRL / OCSP）

| 觸發事件 | 行動 |
|---|---|
| Pi 設備失竊 | 廢止該 Pi 的 server cert + 撤銷對應 API key |
| 員工離職 | 廢止該員工 client cert（Tier 3 mTLS 啟用時）|
| 私鑰疑似洩漏 | 廢止 + 重簽 + audit log 記錄 |
| CA 私鑰洩漏 | 全 CA 重建（per-customer 隔離限制影響範圍至該客戶）|

廢止記錄寫入 `audit_log`（C1-D 上線後）。

### 6. 稽核軌跡

每次簽發/廢止憑證時，記錄：
- 操作者（人/系統）
- 時間戳
- 憑證 SAN / fingerprint
- 觸發原因

C1-B 階段由 step-ca 內建 audit log（`~/.step/db/`）。
C1-D 上線後合併進系統 `audit_log` 表（hash chain 防竄改）。

### 7. 客戶交付 checklist（C3-B install.sh 必達）

- [ ] step-ca 已 init，root CA fingerprint 列印給客戶留存
- [ ] root CA 私鑰權限 0600，owner=`ics`
- [ ] Intermediate CA + server cert 自動續期 timer 已啟用
- [ ] PKI 政策中文文件交付（本節列印 PDF）
- [ ] 廢止流程 SOP 文件交付（本節 §5）
- [ ] 客戶資安窗口簽收 root CA fingerprint + PKI 政策

### 8. 與既有規範對應

| 條文 | 本政策對應節 |
|---|---|
| 附表十「通訊傳輸保護」演算法 | §2 演算法基線 |
| 附表十「通訊傳輸保護」金鑰管理 | §3 金鑰管理 + §6 稽核軌跡 |
| 附表十「通訊傳輸保護」定期更換憑證 | §4 憑證生命週期 |
| ISO 27001:2022 A.10 密碼學 | §2 + §3 全節 |
| NIST SP 800-57 Part 1 Rev. 5 金鑰生命週期 | §3 + §4 + §5 |

## C1-B 範圍（已完成）

- ✅ FastAPI CORS：`allow_origins=["*"]` → env-driven 白名單（`ALLOWED_ORIGINS`）
- ✅ FastAPI CSP middleware：basic CSP + report-uri，預設 report-only 模式
- ✅ CSP violation 收集端點：`POST /api/security/csp-report`
- ✅ nginx 模組化反代：HTTP/2 + TLS 1.2/1.3 + HSTS + 安全標頭
- ✅ step-ca 內網 PKI 腳本：init / start / trust / issue / renew / uninstall
- ✅ Pi TLS 啟動腳本（step-ca 優先，mkcert fallback）
- ✅ Pi push 走 HTTPS（`server/sync.js` 已支援，由 env 控制）
- ✅ 演練前 stack 啟動：`start_mac_https.sh`

## C1-B 範圍外（後續 cX 階段）

- 🔲 CSP enforce 模式（觀察 1-2 週後切；本 sprint 暫留 report-only）
- 🔲 strict CSP（nonce-based + strict-dynamic）：依賴 C1-F 前端模組化
- 🔲 mTLS（雙向憑證）：Tier 3 旗艦版功能，nginx config 已預留 `tier3-mtls.conf.disabled`
- 🔲 自動憑證續期（ACME renew timer）：C3-B install.sh
- ✅ Pi server `STRICT_TLS=true` env：沒憑證 fail-fast；C3-B systemd unit 設此 env 強制生產 HTTPS
- 🔲 OCSP stapling：C3-B 客戶部署環境啟用（dev 無 OCSP responder）
