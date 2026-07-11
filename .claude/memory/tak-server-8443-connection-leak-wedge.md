---
name: tak-server-8443-connection-leak-wedge
description: "TAK :8443 每 1-2 天卡死的真根因＝ICS 每輪輪詢新開 REST 連線、TAK 不關→CLOSE_WAIT 累積吃爆 heap；解＝ICS 連線重用(非 heap 調大)"
metadata:
  node_type: memory
  type: project
---

**TAK server 每跑 1~2 天 :8443 Marti REST 就卡死(:8089 串流照常)——真根因是 ICS 這邊的連線洩漏,不是 TAK OOM 本身。**

## 症狀(反覆)
- `:8443` 所有 `/Marti/api/*`(連 `/version`)timeout「重試耗盡」;ICS 端 `#509-P2 file store 列舉失敗` 洗版;推照片回 **502**(上傳走 :8443 掛)、b-f-t-r 從沒送出→現場沒收到;紅藍分類/憑證/照片附件面板全空。`:8089` CoT 串流正常(所以地圖/隊伍還活)。
- **兩種死法、同一個根**:2026-07-07=messaging JVM(1GB)丟 `OutOfMemoryError`→殭屍;2026-07-11=api JVM(1GB)Old gen 94%+並發 GC 4675 次+80s GCT=**GC 死亡螺旋**(還沒 OOM 先凍結),250 條請求執行緒全堆在 Spring Security filter chain。

## 真根因(2026-07-11 定讞,逐層證據)
1. `jmap -histo` api tier:`[B` byte 陣列 **≈511MB / 30 萬個**(佔 1GB heap 一半)+ `NioSocketWrapper`/`SocketChannelImpl` **各約 1 萬個**。
2. `ss -tan | grep :8443`:**7145 條連線、7142 是 CLOSE_WAIT、其中 7119 來自 ICS 容器 IP(172.19.0.6)**。
3. CLOSE_WAIT = ICS 關了自己這端、**TAK api tier 不關它那端** → socket 永久卡住,每條佔 socket + Tomcat NIO 緩衝(那堆 byte array)→ 塞爆 heap。
4. 來源:ICS **每個背景輪詢迴圈、每一輪都 `tak_files._build_read_client()` 造新 `aiohttp.ClientSession`(新連線)、查完 `close()`**——`tak_attachments.filestore_poll_loop`→`poll_filestore_once`(每輪新 client)、mission poll、`tak_resync`、`handle_fileshare`(每事件)、per-request endpoint(for-entity/download)都是。跑幾天累積幾千條。`tak_rest_client._ensure_session` 每個 client 自建 `TCPConnector`+`ClientSession`,client 間不共用。

## 解(堵源頭,非加 heap)
- **✅ 正解＝ICS 連線重用**:全 app 共用長命 client(read/write/admin 各一,lazy 建、關機才收),所有 TAK REST 走它 → 連線數幾千→幾條 → TAK 沒東西可洩、heap 穩定。
- **輪詢間隔(15s/30s/60s)與洩漏無關**——重用後是同一條連線,頻率不影響 socket 數(2026-07-11 現行 15s;曾誤提「回 30s 保險」是自打臉、已收回。頻繁反而讓那條連線保持熱、不閒置斷)。
- **heap 調大 = 純延後、非解**(無界洩漏,再大都死;使用者當場戳破)。`-XX:+ExitOnOOM`+healthcheck 自動重啟只當保險絲(萬一還有別的漏,乾脆重啟別變殭屍),不是主角。
- **立即恢復**:`docker restart takserver`(~95s、現場自動重連)清掉 CLOSE_WAIT + un-wedge :8443。

## 現況(2026-07-11 換 session 時)
- **連線重用修 = 尚未實作**(下個 session 第一優先)。TAK 目前**卡死中**(:8443 down),需先 restart 才能恢復 + 才能驗其他東西。
- 併入 [[tak-server-data-lifecycle-unmanaged]] 家族;歸屬 #523(C2 維運納管)的 P0——但 #523 原本寫「heap right-size」是錯的方向,真 P0 是**這條連線重用**。

相關:[[deployment-topology-windows-docker]]、[[tak-marti-authz-model]]、[[p2-tak-deploy-issue101]](#101 也是 :8443 tier 起不來,不同因)。
