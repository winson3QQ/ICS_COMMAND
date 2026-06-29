# WG 層連線監測（wg-monitor）— #447

獨立小系統，貼著容器化 WireGuard（[`ics-wg`](../wireguard/container/) / #434）跑，定時 `wg show wg0 dump`
撈 peer 狀態 → 分析連線異常 → 告警。**零 import ICS code、stdlib only、自己的 SQLite 與投遞。**

> 監測對象 = [#280 B](https://github.com/winson3QQ/ICS_COMMAND/issues/280) 已落地的公網入口（單一 UDP 埠 → ics-wg → DNAT）。
> 互補 ICS 內建 `services/security_monitor.py`（應用層）——本系統補**傳輸/連線層**。

## 為什麼是這個形狀

公網入口收斂成一個 WG UDP 埠後，WireGuard 密碼學上**只收登記過的 key**、對未授權封包完全靜默。
殘留的真實威脅 = **盜鑰（valid key, wrong hands）**——key 是真的，tunnel 的密碼學認 key 不認人。
WG 層唯一的破綻是 **endpoint 行為異常**，本系統就是抓這個。

WireGuard **沒有可遠端呼叫的 API**；狀態只能在持有 `wg0` 的 netns 內用 `wg` 工具（netlink）讀。
故本容器以 `network_mode: container:ics-wg` **站進 ics-wg 的 netns** 直接讀，免 docker.sock。

## 偵測規則（v1，按精準度）

| kind | 抓什麼 | 嚴重度 |
|---|---|---|
| `endpoint_oscillation` | **主菜**：同 key 在振盪窗內由 ≥2 來源連入 = key 被複製 | **critical** |
| `dormant_reactivation` | 沉睡 key 由新來源復活 | warning |
| `volume_spike` | 流量 vs 該 peer 基線暴量（學習期後）| warning |
| `offline` / `online` | liveness 轉換 | info |
| `endpoint_change` | 單純漫遊換 IP（手機日常）| **info**（刻意不升級，免淹沒）|

- **並存如何偵測**：`wg show` 每個 peer 只顯「最後來源」，單張快照看不到兩個 endpoint；被複製的 key
  會讓兩台裝置輪流握手 → endpoint 在短窗內**在 ≥2 IP 間擺盪**。故以「振盪窗內 distinct IP 數」推斷。
- **GeoIP/ASN 跳變偵測（#447 3b）未實作**：需 MaxMind GeoLite2，供應鏈未核准。核准後再補。

## 告警與投遞

- **一律先落 `alerts` 表**（append-only、權威真相），再投遞；投遞失敗不丟告警。
- 去抖（同 `(pubkey, kind)` cooldown 內一次）+ 升級（窗內反覆 → critical）。
- 投遞走 `WGMON_NTFY_URL`（自架 ntfy 的完整 topic URL）。**預設自架 ntfy 經 WG**（資料主權）；
  Slack/Telegram 等第三方僅在接受資料出境時當選配。自架 ntfy 部署 + 手機訂閱見 [`ntfy/`](ntfy/)。
- **iOS 背景鎖屏喚醒繞不過 Apple APNs**（平台天花板）：app 前景可全程經 WG 即時收；背景靠 ntfy
  喚醒（wake 借 APNs、內容留自架 server）。Android（F-Droid ntfy）可全程常駐連線走 WG。
- **訊息不會丟**：ntfy server cache + 本系統 SQLite 雙重留存；開 app 或查 DB 一定看得到。

## 部署

### 前置：ics-wg 要有穩定名稱
本容器靠名稱接進 ics-wg 的 netns。ics-prod 的 `ics-wg` 服務預設無 `container_name`（產生名
`ics-prod-ics-wg-1`）。二選一：
- **(建議)** 在 ics-prod 的 `ics-wg` 服務加一行 `container_name: ics-wg`；或
- 設 `WGMON_WG_CONTAINER=ics-prod-ics-wg-1`（用產生名）。

### 啟動
```bash
cd deploy/perimeter/wg-monitor
cp .env.example .env          # 填 WGMON_NTFY_URL（空=只落 log）、必要時 WGMON_WG_CONTAINER
docker compose --env-file .env up -d --build
docker compose logs -f wg-monitor
```

### 先看一眼真實資料（不用本系統也能驗）
```bash
docker exec ics-wg wg show wg0 dump   # 每 peer：pubkey / endpoint(真實公網IP) / 握手 / rx / tx
```

## 設定（環境變數；預設見 `src/config.py`）

| 變數 | 預設 | 說明 |
|---|---|---|
| `WGMON_IFACE` | `wg0` | WG 介面 |
| `WGMON_POLL_S` | `20` | 輪詢秒數（要小於振盪窗才抓得到擺盪）|
| `WGMON_OSC_WINDOW_S` | `180` | 振盪窗（並存判定）|
| `WGMON_OFFLINE_AFTER_S` | `180` | 握手老於此 = 離線 |
| `WGMON_DORMANT_AFTER_S` | `86400` | 沉睡門檻 |
| `WGMON_VOL_FLOOR` / `_FACTOR` / `_LEARN` | `10MiB` / `8` / `20` | 流量暴量地板 / 倍率 / 學習樣本 |
| `WGMON_COOLDOWN_S` | `600` | 告警冷卻 |
| `WGMON_ESCALATE_WINDOW_S` / `_COUNT` | `3600` / `3` | 升級窗 / 次數 |
| `WGMON_NTFY_URL` / `_TOKEN` | — | 自架 ntfy topic URL（空=只 log）|
| `WGMON_PEERMAP` | — | pubkey→callsign 扁平檔路徑（空=只顯 pubkey）。見〈識別豐富化〉|
| `WGMON_RETENTION_DAYS` | `30` | history/events 保留（alerts 永久）|

## 識別豐富化：pubkey → callsign（B 方案，輕耦合）

告警/viewer 預設只顯 pubkey。要顯示人話 callsign 又**不碰 ICS** 的做法：

- callsign 在發證時已被 ICS 當 label 送進 WG 佇列 → **wg-peer-registrar 落成扁平檔**
  `peer-map.tsv`（每行 `pubkey\tcallsign`，預設寫在其 queue 卷 `$WG_QUEUE/peer-map.tsv`）。
- 監測（collector + viewer）**唯讀**讀這個檔（設 `WGMON_PEERMAP` 指過去 + 把該卷唯讀掛進來）→
  在 viewer「識別」欄、告警 detail、ntfy 內容補上 callsign。
- **檔不在/缺某 pubkey → 退回只顯 pubkey**（核心偵測零依賴；單向；無 runtime 依賴）。

> 契約 = 一個扁平檔，**不讀 ICS 的 DB、不呼叫 ICS 的 API、不需 ICS 活著**。資料源自 WG 平面本身
> （registrar 已有 callsign）。要 cert CN（非 callsign）才需動 ICS（發證時把 CN 也塞進 label），屬選配。

## Prod 部署（與 ICS 並排）

監測與 ICS **同機、各自獨立 compose**（不合併進 `deploy/prod`）。獨立生命週期 = ICS 掛了監測還活、互不波及。

```bash
# 0. 前置：ICS prod 棧已起且含 WG（ics-wg 在線）
docker ps --filter name=ics-wg                 # 確認 ics-wg 在跑
docker volume ls | grep wg-queue               # 記下卷實名（多半 ics-prod_wg-queue）

# 1. 設定
cd deploy/perimeter/wg-monitor
cp .env.example .env
#   .env 填：WGMON_WG_CONTAINER（ics-wg 容器名）、WGMON_PEERMAP_VOL（上面查到的卷名）、
#           WGMON_NTFY_URL（要推播才填）、WGMON_VIEWER_PORT

# 2. 起監測（疊加 peermap 啟用 callsign）
docker compose -f docker-compose.yml -f docker-compose.peermap.yml --env-file .env up -d --build

# 3.（選配）自架 ntfy
cd ntfy && cp .env.example .env   # 設 ICS_NET=<icsnet 實名>、NTFY_BASE_URL
docker compose --env-file .env up -d
docker exec -it ics-ntfy ntfy user add wgmon && docker exec -it ics-ntfy ntfy access wgmon wg-alerts rw

# 4. 真機驗證
bash verify.sh                    # 監測讀到的 peer 數 = 真機 live 數 → PASS

# 5. 看網頁：http://localhost:8088（prod 預設只綁 127.0.0.1）
#    要在 iPad/手機看 → 走 WG tunnel 進來（in-perimeter），別曝 LAN（endpoint 是真實 IP=PII）
```

- **不要把 viewer raw 曝公網/LAN**（顯示真實公網 IP=PII）。遠端看走 WG tunnel。
- ntfy 投遞是 **egress** → 對齊 [#450](https://github.com/winson3QQ/ICS_COMMAND/issues/450) 白名單。
- 埠：8088（viewer）/ 8080（ntfy）勿與 host 既有服務撞（ICS 443、TAK 8089/8446 不撞）。

## 資料 / 安全

- `wg show dump` **第一行含 server 私鑰** → parser 只取 8 欄的 peer 行，**私鑰永不解析/落帳**（有測試守）。
- `endpoint` = 裝置**真實公網 IP**（PII，[#388](https://github.com/winson3QQ/ICS_COMMAND/issues/388)）；DB 卷請納入 retention/存取控管。
- 告警投遞是 **egress**，須在 [#450](https://github.com/winson3QQ/ICS_COMMAND/issues/450)（egress 白名單）內。

## 測試
```bash
cd deploy/perimeter/wg-monitor
python -m pytest tests/ -q     # 純邏輯（parser/偵測/告警/端到端），無需容器

bash test-stack/run.sh         # 整合：真 WireGuard + cloned-key → 偵測 endpoint_oscillation（見 test-stack/README）
```

## 範圍

- **v1（本批）**：collector + 偵測 + ntfy 告警。
- **v2（#447 Phase 5）**：viewer 唯讀網頁（拆第二支容器、自己的埠、限 localhost/WG-gated，不曝公網）。
