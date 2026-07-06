---
name: tak-server-data-lifecycle-unmanaged
description: "TAK server 側資料（Enterprise Sync resource 表 / postgres）無生命週期管理，現場照片/包越積越多；刪除若要刪到 server DB 須系統化（retention/GC）非逐張手動"
metadata:
  node_type: memory
  type: project
---

**使用者拍板（2026-07-06）：若刪除要刪到 TAK server DB，應「系統化」進行，不是逐張手動。** 目前 TAK server 側資料**無任何生命週期管理**，東西只進不出、越積越多。

**現況（dogfood 觀測）**：Enterprise Sync `cot.resource` 表短時間就累積 48+ 筆（現場每次分享/重試都新增一筆、同名多份），且每筆 `EXPIRATION = -1`（＝**永不過期**）。ICS 的 #509-P2 主動輪詢只「讀」不「清」，TAK 自身也沒設 retention → 檔庫單調增長。ICS 端 `data/tak_attachments/` 同理跟著長。

**為何不能靠逐張手動刪**：
- **輪詢會復活**：#509-P2 主動輪詢 + in-memory `_seen_hashes`（重啟清空）→ 只刪本地、不立墓碑，下一輪/重啟就把同 hash 照片重新橋回來（同 COP marker 的 `deleted` 墓碑 vs CoT 復活問題，見 [[cop-marker-event-decoupling]] / cop_service resurrect 邏輯）。故本地刪除要有用，得配 **hash 墓碑**（輪詢看到也跳過）。
- **累積是系統性問題**：手動逐張刪解決不了「只進不出」；要的是 **retention/GC policy**（TTL / 配額 / 去重 / 依演習結束清場），server 側與 ICS 側對齊。

**方向（記著、未實作）**：
- **A（本地移除）= COP 刪附件連結+檔 + hash 墓碑**（可逆性最好、不碰 TAK；輪詢靠墓碑不復活）。
- **B（連 server 檔庫 DELETE）**＝ A + `DELETE /Marti/api/files/{hash}`（write cert）→ 天然免墓碑（server 沒了輪詢自然看不到），但不可逆、動 TAK。
- **系統化 retention**：TAK `resource.EXPIRATION` 目前 -1（永久）→ 可設 TTL；或 ICS 主導「演習結束一鍵清場」；或去重（同名多份只留最新）。**這才是使用者要的「系統化」**，非逐張。
- 權限：刪除限**指揮層（sysadmin + commander）**（使用者 2026-07-06 拍板），對齊 [[cop-collaborative-edit-shared-truth]] 的「可信變動者」+ audit 問責。
- **誠實紅線**：任何刪除都**不會**讓照片從現場裝置本機地圖消失（TAK client 硬限制，見 [[tak-streaming-archive-stale-vs-mission]]）；UI 要明講「從 COP 移除，現場裝置仍有」。

相關：[[tak-filestore-image-uplink]]（上行照片橋 #509/#509-P2 已通）、[[tak-mission-datasync-plane]]（第三平面）。
