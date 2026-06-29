# 自架 ntfy（wg-monitor 的 prod 告警投遞目標）— #447

讓 wg-monitor 的告警推到你手機,且**告警內容留在 perimeter 內**(資料主權,不過第三方雲)。

## 跑

```bash
cd deploy/perimeter/wg-monitor/ntfy
cp .env.example .env        # 設 NTFY_BASE_URL / NTFY_PORT / ICS_NET（見下）
docker compose --env-file .env up -d

# 開一個有 wg-alerts 讀寫權的帳號（deny-all 預設下必做）
docker exec -it ics-ntfy ntfy user add wgmon            # 設密碼
docker exec -it ics-ntfy ntfy access wgmon wg-alerts rw
```

接 wg-monitor：在 `../.env` 設 `WGMON_NTFY_URL=http://ics-ntfy/wg-alerts`（+ `WGMON_NTFY_TOKEN` 若用 token），recreate wg-monitor。monitor 共享 ics-wg netns、與 ntfy 同在 icsnet → 以容器名直連。

> `ICS_NET`：ntfy 要 join prod 的 icsnet。實名用 `docker network ls | grep icsnet` 查（多半 `ics-prod_icsnet`），填進 `.env`。

## 手機怎麼收到（可達性三選一）

ntfy server 在 perimeter 內,手機要連得到它才收得到。依你的拓樸選：

| 方式 | 適用 | 備註 |
|---|---|---|
| **經 WG tunnel**（最符合 doctrine） | 手機本就是 WG peer | 需 ics-wg 對 ntfy 加一條 DNAT（wg0:<port>→ics-ntfy:80）。＝最主權，告警全程不出 perimeter |
| **LAN / Tailscale** | 手機與主機同網段 | 訂閱 `http://<主機IP>:8080/wg-alerts`,最省事 |
| ntfy.sh 公雲 | 不想自架 | ❌ 告警內容出境（#447 已否決為預設）|

訂閱：ntfy app →「+」→ 輸入上面的 server URL + topic `wg-alerts` + 帳號。

## iOS 的硬限制（誠實標註）

- **Android（F-Droid ntfy）**：可用常駐連線直連你的自架 server（經 WG）→ 全程不碰第三方。
- **iOS**：背景鎖屏喚醒**必經 Apple APNs**,自架 server 沒有 ntfy app 的 APNs 憑證 → ntfy 的 iOS 做法是
  **喚醒繞 ntfy.sh → APNs,但訊息內容可留你自架 server**(手機醒來回拉)。＝「有事發生」這個事實過了 Apple
  一下,告警**內容**(IP/callsign)留在你家。這是 iOS 平台天花板,非設定問題。

## 安全

- 預設 `NTFY_AUTH_DEFAULT_ACCESS=deny-all` → 只有開的帳號能讀寫 `wg-alerts`,不被任意人訂閱告警。
- 投遞是 **egress**,須在 [#450](https://github.com/winson3QQ/ICS_COMMAND/issues/450) egress 白名單內。
- 告警含 endpoint 真實公網 IP（PII,#388）→ ntfy 帳號/topic 存取要鎖好。

## 先驗一遍（不必動 prod）

`../test-stack/run.sh` 已內建一台 ntfy（published :8080）。跑它 → 手機在 ntfy app 訂閱
`http://<本機LAN-IP>:8080/wg-alerts` → 觸發盜鑰偵測時手機即時響。先用它確認「手機收得到」,再上 prod。
