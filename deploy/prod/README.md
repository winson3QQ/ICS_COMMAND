# deploy/prod/ — 單機 delivery 棧（ICS + 可選 TAK）

同一份 `docker-compose.yml`，用 **Docker profile + `.env` toggle** 服務不同交付形態（商用化考量：相同佈署、不同 delivery）。ICS 與 TAK 在**同一張 `icsnet`**，TAK on 時 ics-command 以容器名 `takserver:8089`/`:8443` 內網直連，**不經公網 IP**。

> ⚠ **狀態：scaffold，尚未在乾淨環境實證**。本機 build 受阻於 [issue #300](https://github.com/winson3QQ/ICS_COMMAND/issues/300)（OneDrive 殘留 reparse point，見 [`../build-env.md`](../build-env.md)）→ 先把 repo 弄到非同步路徑（`C:\dev`）再操作。

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

## 發裝置憑證（ICS 登入用）
```
docker compose run --rm -e CERT_CN=commander-phone-01 issue-client
# 產物在 ./out/<CN>/<CN>.p12，交付給該裝置匯入（iOS 用 .mobileconfig，見 ics-validation/mtls）
```

## 驗證（乾淨環境跑起來後）
- `docker compose ps` 全 healthy；`tak`（若啟）`tak-database` healthy、`takserver` :8089 listen。
- 操作端（同網路/Tailscale 後）`https://<公網IP>/static/commander_dashboard.html` → 憑證 + PIN。
- TAK delivery：ICS `/api/tak/status` 回 connected；COP 收到 ATAK 推的 CoT。

## 之後的縱深（非本棧範圍）
- 周邊 VPN 前置（Headscale/Tailscale，#280）把 443/8089 也收進私網——供應鏈已查（見 `../perimeter/README.md`）；單一公網 IP 下需自架控制面端點，另議。
- image 釘 digest（step-ca/nginx/tak `:latest` → 釘版，供應鏈）。
