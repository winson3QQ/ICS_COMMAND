# #275 全角色 mTLS — Windows/Docker 端到端驗證棧

在**真正連公網的 Windows/Docker** 上驗：持證裝置可登入、不持證握手即擋、撤銷即時生效、
**面板線上發證**。與同層 `docker-compose.yml`（非 mTLS 自簽快測）並存；本棧才開 `ssl_verify_client on`。

> 決策：per-device 裝置憑證、App 層撤銷、**發證 = 選項 i 安全版**（後端不持 CA 鑰，
> 向 step-ca daemon 請簽）。見 `docs/compliance/security_policies.md` §2.8。

## 架構（單一 CA = step-ca daemon）

```
瀏覽器/裝置(p12) ──TLS+client cert──▶ nginx :443 (ssl_verify_client on)
                                        │  無有效證 → 400（網路層擋）
                                        │  有效證 → map 抽 CN → X-Client-Cert-CN/Verify
                                        ▼
                                   ics-command :8000（只經 nginx 可達；ICS_MTLS_REQUIRED）
                                        │  發憑證：後端 → step-ca daemon 請簽（CA 鑰不進後端）
                                        ▼
   step-ca daemon ◀── ca-bootstrap（一次性）：簽 nginx server 憑證 + 出 truststore + 寫後端憑據
   （唯一 CA，CA 鑰只在此）       nginx 與線上發的 client 證同一個 CA → 互認
```

## 前置

- Docker Desktop（Windows）。
- `ics-command:dev` 為**現行分支** build（含 #275 後端 + step CLI）：
  ```bash
  cd command-dashboard
  docker build --build-arg ICS_BUILD_ID="$(git rev-parse --short HEAD)" -t ics-command:dev .
  ```
- external volume `ics-data`、`ics-tiles` 已存在（無則 `docker volume create`）。

## 啟動（Git Bash；PowerShell 去掉 `MSYS_NO_PATHCONV`）

```bash
cd deploy/ics-validation
# bootstrap 期先關後端 MTLS，方便用 UI 綁第一張證（解雞生蛋）
ICS_MTLS_REQUIRED=false docker compose -f docker-compose.mtls.yml up -d
# 依序起 step-ca → ca-bootstrap(一次性) → ics-command → nginx
```

## 發憑證（兩條路，都同一個 daemon CA）

**A. 面板線上發證（推薦，零指令）** — 適合交付給非技術部署者
1. 瀏覽器開 `https://localhost/static/commander_dashboard.html`（首次先匯 root CA，見下）
2. 登入 admin → 管理員後台 → 帳號卡 → **🔑 裝置憑證** → 填 CN（如 `my-pc`）→ **發憑證**
3. 瀏覽器自動下載 `my-pc.p12`（已自動綁定該帳號）

**B. CLI 發證** — 批次/自動化
```bash
CERT_CN=my-pc docker compose -f docker-compose.mtls.yml run --rm issue-client
# → ./out/my-pc/my-pc.p12，再到面板「僅綁定」CN
```

## 匯入憑證到 Windows（瀏覽器才出示得了）

- **root CA**（免伺服器憑證警告）：`./out/<CN>/root_ca.crt`（CLI 路徑）→ 安裝到
  「受信任的根憑證授權單位」。面板發證時 root 可從 `docker compose exec step-ca cat /home/step/certs/root_ca.crt` 取。
- **client p12**：雙擊 `<CN>.p12` → 匯入精靈 → 「目前使用者 / 個人」→ 密碼 `icsclient`。

## 翻開 mTLS 強制 + 驗證

```bash
ICS_MTLS_REQUIRED=true docker compose -f docker-compose.mtls.yml up -d ics-command
```

| 情境（瀏覽器 / curl） | 結果（本棧 2026-06-20 實測）|
|---|---|
| 綁定證 + 對 PIN | **200** 登入成功 |
| 綁定證 + 錯 PIN | **401** |
| 不持證（無痕視窗）| **400** `No required SSL certificate was sent` |
| CA-有效但 CN 未綁定 | **401**（第二因子擋）|
| 撤銷該證後 | **401**（App 層即時失效）|

## 撤銷（裝置遺失）

面板「裝置憑證」按撤銷（即時失效，活躍 session 一併失效）。

## 收尾

```bash
docker compose -f docker-compose.mtls.yml down
docker volume rm ics-validation_pki ics-validation_ca-data ics-validation_ca-share
```

## 注意

- 驗證棧 CA `maxTLSCertDuration` 預設 24h → client 證效期用 23h；**prod 每場域獨立 step-ca
  instance** 設 2160h（90 天，見 `deploy/step-ca/README.md`）。
- step-ca daemon 持 CA 鑰；ICS 後端只持**可撤銷的 provisioner 憑據**（被打穿賠的不是 CA 鑰）。
- `./out/`、`pki`/`ca-*` volume 含私鑰，**勿入版控**（已 gitignore）。
