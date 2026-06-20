# #275 全角色 mTLS — Windows/Docker 端到端驗證棧

在**真正連公網的 Windows/Docker** 上驗：持證裝置可登入、不持證握手即擋、撤銷即時生效。
與同層 `docker-compose.yml`（非 mTLS 自簽快測）並存；本棧才開 `ssl_verify_client on`。

> 決策依據：per-device 裝置憑證、App 層撤銷、**(ii) step-ca 離線簽（CA 鑰不進 ICS 後端）**。
> 見 `docs/compliance/security_policies.md` §2.8。

## 架構

```
瀏覽器/裝置(p12) ──TLS+client cert──▶ nginx :443 (ssl_verify_client on)
                                        │  無有效證 → 400（網路層擋）
                                        │  有效證 → 注入 X-Client-Cert-CN/Verify
                                        ▼
                                   ics-command :8000（只經 nginx 可達）
                                   ICS_MTLS_REQUIRED=true：CN 須為帳號 active 綁定
   pki volume ◀── pki-init (step-ca 離線建 CA + 簽 server 證；CA 鑰只在此)
```

## 前置

- Docker Desktop（Windows）。
- `ics-command:dev` image 為**現行分支** build（含 #275 後端）：
  ```bash
  cd command-dashboard
  docker build --build-arg ICS_BUILD_ID="$(git rev-parse --short HEAD)-mtls" -t ics-command:dev .
  ```
- external volume `ics-data`、`ics-tiles` 已存在（沿用既有驗證棧；無則先 `docker volume create`）。

## 步驟（Git Bash；PowerShell 同理，去掉 `MSYS_NO_PATHCONV`）

```bash
cd deploy/ics-validation
export COMPOSE=docker-compose.mtls.yml

# 1. 建 CA + 簽 nginx server 憑證（一次性，idempotent）
docker compose -f $COMPOSE up pki-init

# 2. 簽一張 per-device client 憑證 → ./out/<CN>/<CN>.p12（匯入密碼預設 icsclient）
CERT_CN=commander-phone-01 docker compose -f $COMPOSE run --rm issue-client

# 3. 起棧（ics-command + nginx）
docker compose -f $COMPOSE up -d ics-command nginx

# 4. 綁定 CN ↔ 帳號（= App 層第二因子授權）
#    正式走 admin 面板（B：admin console「裝置憑證」分頁）。
#    首次 bootstrap（雞生蛋：MTLS on 時登入需綁定）可先以 exec 種一筆：
docker compose -f $COMPOSE exec -T ics-command python -c "
import sys; sys.path.insert(0,'/app/src')
from repositories.account_cert_repo import bind_cert
bind_cert(1,'commander-phone-01','指揮官手機','system')"

# 5. 把 ./out/commander-phone-01/commander-phone-01.p12 匯入瀏覽器/OS keychain
#    （Windows：憑證管理員 → 個人 → 匯入；瀏覽器會在 TLS 握手自動出示）
#    開 https://localhost/ → 選憑證 → PIN 登入
```

## 驗證矩陣（2026-06-20 本棧實測通過）

| 情境 | 結果 |
|---|---|
| 綁定證 + 對 PIN | **200**（登入成功）|
| 綁定證 + 錯 PIN | **401** |
| 不持證 | **400**（nginx `No required SSL certificate was sent`）|
| CA-有效但 CN 未綁定 + 對 PIN | **401**（第二因子擋）|
| 撤銷該證後原證 + 對 PIN | **401**（App 層撤銷即時生效，不必等 token 過期）|

curl 快驗（容器內，掛 pki 卷取 client 證）：
```bash
NET=ics-validation_icsnet; PKIVOL=ics-validation_pki
# 不持證 → 400
docker run --rm --network $NET curlimages/curl -sk -o /dev/null -w "%{http_code}\n" https://nginx:443/api/version
# 持證 → 200 + JSON
docker run --rm --network $NET -v $PKIVOL:/pki:ro curlimages/curl \
  -sk --cert /pki/clients/<CN>/fc.crt --key /pki/clients/<CN>/c.key https://nginx:443/api/version
```

## 撤銷（裝置遺失）

admin 面板「裝置憑證」分頁按撤銷，或：
```bash
docker compose -f $COMPOSE exec -T ics-command python -c "
import sys; sys.path.insert(0,'/app/src')
from repositories.account_cert_repo import list_certs, revoke_cert
revoke_cert([c['id'] for c in list_certs(1) if c['status']=='active'][0],'system')"
```
撤銷後該裝置**下一個 request 即失效**（活躍 session 一併失效）。

## 收尾

```bash
docker compose -f docker-compose.mtls.yml down
docker volume rm ics-validation_pki   # 清掉驗證用 CA（保留 ics-data）
```

## 注意

- `ICS_MTLS_REQUIRED` 預設 `true`；bootstrap 綁定期可先 `ICS_MTLS_REQUIRED=false docker compose ... up -d ics-command` 用 UI 綁好再翻 `true`。
- 本棧 CA 為**驗證用自家 CA**；正式交付每場域獨立 step-ca instance（per-customer 隔離，見 `deploy/step-ca/README.md`）。
- `./out/` 與 `pki` volume 含私鑰，**勿入版控**（已 gitignore）。
