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

mTLS 登入要求「PIN ＋ **綁定本帳號的裝置憑證**」雙因子。但憑證綁定要先登入才能在面板做 → 第一個 admin 在 `ICS_MTLS_REQUIRED=true` 下**進不去**。目前唯一解是**手動翻 env 兩次**（改良見 [#306](https://github.com/winson3QQ/ICS_COMMAND/issues/306)）：

1. 部署時 `.env` 先設 **`ICS_MTLS_REQUIRED=false`**（nginx 仍 `ssl_verify_client on`，要有任一 CA-signed client cert 才過；但 app 不卡綁定）。
2. 讀首次 PIN：`docker compose logs ics-command | grep first_run_token`，或容器內 `/home/ics/.ics/first_run_token`（fresh DB 才會產；is_default_pin=1）。
3. admin 登入 → **強制改 PIN** → 帳號管理 → admin → 「裝置憑證」→ **僅綁定**，CN 填**本機憑證的 Common Name**（不是標籤！綁錯 CN→翻 true 後登入失敗）。
4. `.env` 改回 **`ICS_MTLS_REQUIRED=true`** → `docker compose up -d --force-recreate ics-command` → 雙因子上鎖。

> 砍 `ics-data` 卷＝清 DB（含**所有憑證綁定**）→ 回到雞生蛋，要重走本節。砍 `ca-data` 卷＝step-ca 換新 CA → **所有已發裝置憑證失效**，全部重發。正常進版（重 build image + recreate、**保留兩卷**）則憑證照常有效。

## 裝置憑證（ICS 登入用）

**發證一律在桌機 console 做，不要在 iOS**：iOS Safari 下載 `.p12` 會被系統攔去「裝到本機」，無法存檔轉給別台。面板「發憑證」也一樣 —— iPad/iPhone 按了拿不到檔。

兩條發證路徑：
```
# A. CLI（host）— 產 ./out/<CN>/<CN>.p12 + root_ca.crt；p12 密碼 = ICS_CLIENT_P12_PASS（.env 那串）。不自動綁定 → 再到面板「僅綁定」CN。
docker compose run --rm -e CERT_CN=<CN> issue-client
# B. 面板「發憑證」— step-ca 線上簽 + 自動綁定 + 下載 p12。p12 密碼 = STEP_CLIENT_CERT_P12_PASS（預設 icsclient，非上面那串；面板目前不顯示密碼，見 #307）。
```

裝置安裝：
- **桌機（Windows/Mac/Linux）**：直接匯入 `.p12`（個人憑證）+ `root_ca.crt`（受信任根）。瀏覽器連 → 選憑證 → 登入。
- **iOS（iPhone/iPad）**：必 **Safari**（Chrome 不支援 client cert mTLS）。p12 須 **legacy 格式**（PBE+SHA1+3DES）才裝得了 —— 面板發證已修為 `--legacy`（[#307](https://github.com/winson3QQ/ICS_COMMAND/issues/307) / `cert_issuance.py`）；CLI 用 `openssl pkcs12 -export -legacy` 重打包。最穩是包成 `.mobileconfig`（`ics-validation/mtls/make-ios-profile.py`，內嵌密碼 + root CA），用 **AirDrop / 內建郵件** 開（Files/Chrome/Gmail 只會預覽純文字、不觸發安裝）。

## 內網 / LAN 存取

對外只認 `ICS_SERVER_SANS` 的位址。內網機器若用 host 的 LAN IP（如 `https://10.0.1.16/`）連，**該 LAN IP 必須在 `ICS_SERVER_SANS`**，否則 nginx server cert 名稱不符。加法：`.env` 的 `ICS_SERVER_SANS` 補上 LAN IP（空白分隔）→ `docker compose up -d --force-recreate ca-bootstrap` 重簽 → `restart nginx`。（公網 IP 走 hairpin NAT 多數家用路由器不支援，故內網建議直接用 LAN IP + SAN。）

## 憑證效期

`STEP_CLIENT_CERT_DURATION` 預設 **23h**（step-ca provisioner `maxTLSCertDuration` 預設 24h 上限）。長放（90 天，公測用）須先 `step ca provisioner update ics --x509-max-dur=2160h --x509-default-dur=2160h` 放寬 claims 再調此值 → [#279](https://github.com/winson3QQ/ICS_COMMAND/issues/279)。nginx server cert 的 `ICS_SERVER_CERT_DURATION` 同理。

## 驗證（乾淨環境跑起來後）
- `docker compose ps` 全 healthy；`tak`（若啟）`tak-database` healthy、`takserver` :8089 listen。
- 操作端（同網路/Tailscale 後）`https://<公網IP>/static/commander_dashboard.html` → 憑證 + PIN。
- TAK delivery：ICS `/api/tak/status` 回 connected；COP 收到 ATAK 推的 CoT。

## 之後的縱深（非本棧範圍）
- 周邊 VPN 前置（Headscale/Tailscale，#280）把 443/8089 也收進私網——供應鏈已查（見 `../perimeter/README.md`）；單一公網 IP 下需自架控制面端點，另議。
- image 釘 digest（step-ca/nginx/tak `:latest` → 釘版，供應鏈）。
