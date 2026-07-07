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

**[2026-07-08 #518 已上 prod · backend-v2.42.0 / frontend-v1.31.0 · PR #521 merge 403ad4b]**：L1+L2、逐張手動刪落地（migration 41）。
- **復活機制定讞（grounded in code）**：ICS 是 TAK 下游鏡像，三源會拉回——① live :8089 SA beacon（`TAK_PRESENCE_INTERVAL_S` 預設 60s）② `resync_on_connect` upsert-only ③ filestore poll。最關鍵：`cop_service.ingest_cot_event` #161 設計，既有 `deleted=1` 墓碑收到更新 CoT 就 auto-resurrect → 現行 `soft_delete_tak_entity` 墓碑擋不住 beacon（=「刪了幾秒又回來」marker 層主因）。
- **L2 端點確認**：`DELETE /Marti/api/files/{hash}` 存在（OpenAPI 實證）。**🔴 granularity gap**：ICS attachment `target_uid=sha256(image_bytes)`（影像內容 hash），但 server resource=**mission-package zip hash**（影像包 zip 裡非獨立 resource）→ 刪除目標須是 zip hash，故 migration 41 在 link 記 `pkg_hash`。
- **實作**：`cop_entity_links` 加 `direction`/`pkg_hash`；`tak_pkg_tombstones` 持久墓碑表；墓碑守門置於**共用 `_bridge_package_by_hash`** → 同時擋主動輪詢＋被動 `handle_fileshare`（跨重啟不復活）；`delete_attachment(sha, marker_uid, purge_server)`＝L1 一律（刪連結+墓碑+無他 marker 連才刪檔）、L2 選配 best-effort 狀態分開回報；`marker_uid` 限定＝faction 邊界；`DELETE /api/tak/files/{hash}` COMMAND_ROLES（role_enum 顯式收緊）+ faction 反查 + **audit-first**；`reset-db` 一併清 tombstones。前端方向徽記+刪除鈕+方向感知確認框（下行預設連 server 清、上行預設只清本地）+ 誠實紅線。
- **方向感知決策**：持有照片的現場裝置永遠不因 ICS 清而看不到（client 硬限制，與方向無關）；ICS 清只影響指揮台視圖+server 分發點+未同步者。下行（owner=ICS）有資格連 server 清；上行（owner=現場）動 L2 是抹別人共享情資、後果較重 → 預設只 L1。
- **🔴 真機待驗（dogfood）**：① `DELETE /Marti/api/files/{zip_hash}` 是否真能刪走 `/sync/content` 的 mission-package（兩端點是否同 resource；否則 L2 靜默 404、tombstone 遮住看似成功）；② ATAK 分享是否曾多照片打一包（多照片 zip 時「刪單張」over-delete 整包）。③ 舊資料（pkg_hash NULL，migration 41 前）刪除無墓碑仍可能復活。
- **仍未做**：系統化 retention/TTL/演習結束一鍵清場（本版只逐張手動）。

相關：[[tak-filestore-image-uplink]]（上行照片橋 #509/#509-P2 已通）、[[tak-mission-datasync-plane]]（第三平面）、[[cop-collaborative-edit-shared-truth]]（可信變動者可刪）、[[cop-marker-event-decoupling]]（#161 resurrect）。
