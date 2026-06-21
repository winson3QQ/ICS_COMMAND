# deploy/prod/ — 單機 delivery 棧（ICS + 可選 TAK）

同一份 `docker-compose.yml`，用 **Docker profile + `.env` toggle** 服務不同交付形態（商用化考量：相同佈署、不同 delivery）。ICS 與 TAK 在**同一張 `icsnet`**，TAK on 時 ics-command 以容器名 `takserver:8089`/`:8443` 內網直連，**不經公網 IP**。

> ✅ **狀態：2026-06-21 公網 prod 端到端實證通過**（[#305](https://github.com/winson3QQ/ICS_COMMAND/issues/305)）—— ICS+TAK 同機 build、mTLS、ICS↔TAK CoT、多裝置（iPhone 公網 / Windows LAN）登入全綠。#300（OneDrive reparse build 阻擋）已解。**首次 onboarding 與裝置憑證有幾個實機踩坑，務必看下方〈首次 onboarding〉與〈裝置憑證〉兩節再操作。**

## 兩種 delivery

| 形態 | 啟動 | `.env` |
|---|---|---|
| 純 ICS | `docker compose --env-file .env up -d` | `TAK_ENABLED=false`、**不**加 `--profile tak`（TAK 容器不存在） |
| ICS + TAK | `docker compose --env-file .env --profile tak up -d` | `TAK_ENABLED=true` + 填 TAK 區段 |
| dev 單機全上 | 同 ICS+TAK | 同上 |

## 前置（按序）

1. **乾淨 build 環境**：repo 在非同步路徑（見 #300）。
2. **TAK release**（僅 TAK delivery）：官方包解壓到 `../tak-server/release/`（見 [`../tak-server/README.md`](../tak-server/README.md)），並設好 `../tak-server/.env`。
3. **build image**：
   ```
   docker build -t ics-command:dev ../../command-dashboard
   # TAK image 由 compose --profile tak 首次 up 時 build（context=../tak-server/release）
   ```
4. **`.env`**：`cp .env.example .env`，填密鑰（`openssl rand -hex 24`）、`ICS_SERVER_SANS`（含公網 IP）、TAK toggle。
5. **憑證**：
   - nginx server cert：`ca-bootstrap` 自動（step-ca 簽，SAN 取 `ICS_SERVER_SANS`）。
   - TAK server cert + ICS→TAK client cert（僅 TAK delivery）：跑 `../tak-server/pki/issue-tak-certs.sh`（同一條 step-ca），把 ICS 端的 fullchain/key 放進 `./tak-certs/`（檔名對齊 `.env` 的 `TAK_*` 路徑）。
     ⚠ TAK server cert 的 SAN 必含現場連的位址（公網 IP）——否則嚴格 ATAK client `IP mismatch` 斷線。

## 對外（單一公網 IP，多 port）
家用路由器 forward → 這台：
```
443  → nginx（ICS 儀表板 mTLS）            操作員
8089 → takserver（CoT 串流）               現場 ATAK
8446 → takserver（憑證註冊；managed cert 才需）
8443 → admin web：不轉發，留內網/本機       （減攻擊面）
```

## 首次 onboarding（mTLS bootstrap）— ⚠ 雞生蛋，照順序走

mTLS 登入要求「PIN ＋ **綁定本帳號的裝置憑證**」雙因子。但憑證綁定要先登入才能在面板做 → 第一個 admin 在 `ICS_MTLS_REQUIRED=true` 下會撞雞生蛋。**[#306](https://github.com/winson3QQ/ICS_COMMAND/issues/306) 已解：bootstrap 窗口自動放行，免翻 env。**

**前置（nginx 層，無法省）**：nginx `ssl_verify_client on` → admin 瀏覽器**必須先有一張 CA-signed client cert** 才連得進來。fresh deploy 第一張只能由 **CLI 簽**（app 還進不去）：
```bash
docker compose run --rm -e CERT_CN=<admin-CN> issue-client   # 產 ./out/<CN>/<CN>.p12 + root_ca.crt
```
裝上瀏覽器（含 root_ca.crt 受信任根）。

**bootstrap（`ICS_MTLS_REQUIRED=true` 全程不變、不必 restart）**：
1. 讀首次 PIN：`docker compose logs ics-command | grep first_run_token`（或容器內 `/home/ics/.ics/first_run_token`；fresh DB 才產）。
2. 瀏覽器（已裝 CLI 證）連 `https://<公網IP>/` → admin 登入（**#306 bootstrap 窗口**：唯一帳號且 `account_certs` 尚無任何 row → 用該 CA 已驗的證放行登入，不要求預先綁定）。
3. **強制改 PIN** → 帳號管理 → admin →「裝置憑證」→ **僅綁定**，CN 填**剛裝那張證的 Common Name**（`<admin-CN>`，不是標籤！）。
4. 綁定完成 → bootstrap 窗口**自動關閉**（`account_certs` 有 row 了，單向閂）→ 雙因子即刻生效，**無需翻 env、無需 restart**。

> **安全邊界**：bootstrap 窗口僅在「首位 admin 尚未綁過任何證」時開（持久旗標 `mtls_bootstrap_done`，綁第一張即永久關、`purge` 清不掉），且仍需 (a) first-run PIN（機密）、(b) nginx 已 CA 驗證的證。建立第二個帳號亦永久關窗。**前提：`ICS_PROXY_SHARED_SECRET` 必須設**（compose 已 `${...:?}` 強制）——否則後端會採信偽造的 `X-Client-Cert-*` header，bootstrap（與整個 mTLS 第二因子）失效。
> **舊路徑（手動翻 env）** 仍可用（`false`→綁→`true`+recreate），但 #306 後不再需要。
> 砍 `ics-data` 卷＝清 DB → 回 bootstrap 起點，重走本節。砍 `ca-data` 卷＝step-ca 換新 CA → 已發證全失效。正常進版（重 build + recreate、**保留兩卷**）憑證照常有效。

## 裝置憑證（ICS 登入用）

**發證一律在桌機 console 做，不要在 iOS**：iOS Safari 下載 `.p12` 會被系統攔去「裝到本機」，無法存檔轉給別台。面板「發憑證」也一樣 —— iPad/iPhone 按了拿不到檔。

兩條發證路徑：
```
# A. CLI（host）— 產 ./out/<CN>/<CN>.p12 + root_ca.crt；p12 密碼 = ICS_CLIENT_P12_PASS（.env 那串）。不自動綁定 → 再到面板「僅綁定」CN。
docker compose run --rm -e CERT_CN=<CN> issue-client
# B. 面板「發憑證」— step-ca 線上簽 + 自動綁定 + 下載 p12。p12 密碼預設「每張隨機」，發證後面板會常駐顯示（可複製，請記下轉交持證人）；關閉訊息後無法再取得。需固定密碼才設 STEP_CLIENT_CERT_P12_PASS env（runbook 相容）。#307 已落地。
```

裝置安裝：
- **桌機（Windows/Mac/Linux）**：直接匯入 `.p12`（個人憑證）+ `root_ca.crt`（受信任根）。瀏覽器連 → 選憑證 → 登入。
- **iOS（iPhone/iPad）**：必 **Safari**（Chrome 不支援 client cert mTLS）。
  - **最穩 = 面板發證時「格式」選「iOS 描述檔」**（[#312](https://github.com/winson3QQ/ICS_COMMAND/issues/312)）→ 後端直接產 `.mobileconfig`（root CA + p12 + **內嵌密碼**一包）→ 開啟即裝、**免打憑證密碼**。把檔案弄到目標機（面板「分享」AirDrop，或 AirDrop / 內建郵件；Files/Chrome/Gmail 只會預覽純文字、不觸發安裝）。
  - ⚠ 安裝描述檔時 iOS 會要求「**解鎖此裝置的密碼**」＝該機螢幕鎖密碼（裝置層授權），**不是憑證密碼**；「簽署者 未簽署」屬正常（自建描述檔未簽章）。裝好後連網站登入仍需 PIN。
  - 裸 `.p12` 路徑（不走描述檔）：p12 須 **legacy 格式**（PBE+SHA1+3DES）才裝得了 —— 面板發證已 `--legacy`（[#307](https://github.com/winson3QQ/ICS_COMMAND/issues/307)）；CLI 用 `openssl pkcs12 -export -legacy`。但裸 p12 安裝要**手打憑證密碼**（隨機密碼在 iOS 鍵盤易卡）→ 建議走描述檔。

## 內網 / LAN 存取

對外只認 `ICS_SERVER_SANS` 的位址。內網機器若用 host 的 LAN IP（如 `https://10.0.1.16/`）連，**該 LAN IP 必須在 `ICS_SERVER_SANS`**，否則 nginx server cert 名稱不符。加法：`.env` 的 `ICS_SERVER_SANS` 補上 LAN IP（空白分隔）→ `docker compose up -d --force-recreate ca-bootstrap` 重簽 → `restart nginx`。（公網 IP 走 hairpin NAT 多數家用路由器不支援，故內網建議直接用 LAN IP + SAN。）

## 憑證效期（23h → 90 天）

`STEP_CLIENT_CERT_DURATION`（client）/ `ICS_SERVER_CERT_DURATION`（nginx server）預設 **23h**。
**fresh step-ca 的 provisioner `maxTLSCertDuration` 預設 24h** → 超過會被簽發拒絕，故預設留 23h 確保乾淨佈署不卡。

90 天公測長放（[#279](https://github.com/winson3QQ/ICS_COMMAND/issues/279)），**三步缺一不可**：

```bash
# 1. 放寬 CA provisioner claims（一次性；改的是 ca.json，存在 ca-data 卷、recreate 不丟，
#    但「砍 ca-data 重建 CA」會回 24h 預設 → 重跑本步）。改後 step-ca 需 reload。
docker compose exec step-ca step ca provisioner update ics \
  --x509-max-dur=2160h --x509-default-dur=2160h
docker compose restart step-ca

# 2. .env 設效期為 90 天（解註 .env.example 那兩行）
#    STEP_CLIENT_CERT_DURATION=2160h
#    ICS_SERVER_CERT_DURATION=2160h

# 3. 重簽 nginx server cert（ca-bootstrap 讀新 .env）+ 套用 + 重發既有 client 證
docker compose --env-file .env up -d --force-recreate ca-bootstrap
docker compose --env-file .env up -d --force-recreate ics-command nginx
#    既裝裝置（iPhone/iPad/桌機）的 23h 舊證仍會過期 → 面板「發憑證」重發 + 重裝。

# 驗：server cert notAfter 應為 +90 天
echo | openssl s_client -connect <公網IP>:443 2>/dev/null | openssl x509 -noout -dates
```

> reality check（2026-06-21，#279）：現役 prod 的 provisioner claims **已**手動放寬為 2160h（存於 ca-data 卷），但**未隨 committed config 走** → fresh deploy 仍是 24h，故上面步驟 1 不可略。把 claims 寫進 step-ca init（reproducible）= 待辦（[#279](https://github.com/winson3QQ/ICS_COMMAND/issues/279) 追蹤）。

## 驗證（乾淨環境跑起來後）
- `docker compose ps` 全 healthy；`tak`（若啟）`tak-database` healthy、`takserver` :8089 listen。
- 操作端（同網路/Tailscale 後）`https://<公網IP>/static/commander_dashboard.html` → 憑證 + PIN。
- TAK delivery：ICS `/api/tak/status` 回 connected；COP 收到 ATAK 推的 CoT。

## 之後的縱深（非本棧範圍）
- 周邊 VPN 前置（Headscale/Tailscale，#280）把 443/8089 也收進私網——供應鏈已查（見 `../perimeter/README.md`）；單一公網 IP 下需自架控制面端點，另議。
- image 釘 digest（step-ca/nginx/tak `:latest` → 釘版，供應鏈）。
