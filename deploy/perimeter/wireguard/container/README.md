# 容器化 WireGuard（#434）— ICS 可驅動的 VPN

把 WG 從 Windows-native 改為 **Linux 容器**，讓 ICS 像驅動 TAK registrar 一樣，**發裝置證時連帶自動配 WG peer**，達成「ICS 完整管理 TAK client」的另一半（證 + 連線）。

> **為何容器化**（reality-check #434）：Windows-native WG 跨 OS 邊界，容器內的 ICS 驅動不了（`wg.exe` 要 host admin、無 netns 捷徑）。Linux 容器後 ICS 經共享卷佇列即時加/刪 peer。kernel WG（WSL2 6.6 原生支援，已實測）。代價：對外 UDP 經 Docker NAT，漫遊恢復 ~60–90s（已實測可接受）。

## 組成

| 檔 | 角色 |
|---|---|
| `Dockerfile` | alpine + 上游 wireguard-tools/iptables/iproute2（供應鏈乾淨） |
| `entrypoint.sh` | wg0 bring-up（server 私鑰持久化 + 導出 `server.pub`）+ wg0→TAK/dashboard DNAT 路由 + 啟動 watcher |
| `wg-peer-registrar.sh` | peer 控制面（ICS 寫 queue → `wg set` 加/刪 + 持久化），injection-safe |
| `wg-persist.sh` | `wg showconf` 落檔，容器重啟 peer 不丟 |

ICS 端：`services/wg_provision`（產 keypair/配 IP/加 peer/組 .conf/QR）+ `repositories/wg_peer_repo`（帳本 + IP pool，m034）+ `admin.issue_tak_device_cert`（發證連帶 bundle）。

## 階段 2：並排 E2E 測試（**不碰 live Windows WG**）

容器開 host **51822**（live Windows WG 用 51820，不撞）。需 `--profile tak`（DNAT 要 takserver 在）。

```bash
# 1. .env 設（並排）：
#    WG_QUEUE_DIR=/wg-queue
#    WG_HOST_PORT=51822
#    WG_ENDPOINT=<公網IP>:51822
# 2. 起 ics-wg（建映像 + 啟動）
docker compose -f deploy/prod/docker-compose.yml --env-file deploy/prod/.env --profile tak --profile wg up -d --build ics-wg
# 3. 讀 server.pub → 填回 .env 的 WG_SERVER_PUBKEY，再 recreate ics-command（吃 WG env）
docker compose ... exec ics-wg cat /wg-queue/server.pub      # 複製這個 pubkey
#    .env: WG_SERVER_PUBKEY=<上面那串>
docker compose ... --profile tak --profile wg up -d --no-deps --force-recreate ics-command
# 4. 路由器暫時 forward UDP 51822 → 主機
# 5. 儀表板發一張裝置證 → 下載 bundle.zip → 解壓：
#    - 掃 wireguard-qr.png（或匯 wireguard.conf）進 WireGuard app → 啟用
#    - 匯 <callsign>-TAK.zip 進 iTAK/ATAK → 開 app
#    裝置即經容器 WG 連上 TAK。
```

驗證：`docker compose ... exec ics-wg wg show wg0`（該裝置有 handshake）；TAK `subscriptions/all` 出現該 callsign。

## 階段 3：切換（live cutover）

並排驗過後：① 停 Windows WG tunnel；② `.env` `WG_HOST_PORT=51820`、`WG_ENDPOINT=<公網IP>:51820`；③ recreate ics-wg；④ 路由器 51820 forward 維持指向主機（現在轉到容器）。**回退**：停容器 ics-wg、重啟 Windows WG tunnel。

> 既有 Windows WG 的裝置 config（itak-1/2…）用的是舊 server key，切換後需經儀表板**重發**（新 server key + 新 peer）。故切換 = 全體測試者換新 bundle。
