---
name: tak-mission-datasync-plane
description: "TAK 第三資料平面 Mission/Data Sync：ICS 零消費（#506）；ATAK 附件走此非 Enterprise Sync；M0-M4 建置計畫"
metadata:
  node_type: memory
  type: project
---

**TAK 有三個資料平面，ICS 只接了兩個。** Mission/Data Sync 是缺的第三個，[#506](https://github.com/winson3QQ/ICS_COMMAND/issues/506) 追蹤。

**三平面分工（心智模型）**：
- **Stream(:8089)** = 即時·廣播·易逝（stale/archive）。ICS **成熟**（tak_service 訂閱/ingest、tak_resync /cot/sa 補漏、tak_downlink 出向 CoT/GeoChat/DM）。用於：活動位置 PLI、即時敵情。
- **Enterprise Sync** = hash 定址檔案 blob 庫。ICS 讀側 ✅（tak_files search/metadata/download）、上傳留樁。用於：照片/data package 的**底層儲存**（單獨無語意，要 mission/CoT 引用）。
- **Mission/Data Sync** = **具名·持久·可訂閱·帶附件·有變更史·可靠刪除**。ICS **零程式碼**（grep 全 src 無 mission 消費）。用於：作戰圖/管制措施、**照片/情境注入**、跨隊協作、AAR 問責、離線 client 重連補全。**= TAK 版的 COP「共享真實」**（同構 [[cop-collaborative-edit-shared-truth]]）。

**Mission 動作機制**：`PUT /Marti/api/missions/{name}` 建 feed（owner + `defaultRole` 決定寫權，見 [[tak-marti-authz-model]]）→ client `PUT .../subscription` 訂閱（拿當前完整快照，不像 :8089 只給上線後的）→ 加 marker 進 feed（**帶照片則 bytes 上 Enterprise Sync 取 hash、feed 只存 hash 參照**）→ server 推變更給訂閱者、靠 hash 去 Enterprise Sync 下載。

**🔑 為何非 Mission 不可（2026-07-05 真機實證）**：ATAK 分享 marker 照片**走 Data Sync/Mission，非單純 Enterprise Sync 上傳**——無 mission 則附件留手機（file store 恆 0）。故 #503「按 uid 搜 Enterprise Sync」錯；**正解＝讀 mission contents（marker↔附件 hash 明寫）**。詳見 [[tak-filestore-image-uplink]]。

**🧱🧱 [2026-07-06 二輪 dogfood 推翻上一段——#507 已上 prod]**：上面「照片非 Mission 不可／file store 恆 0」＝**誤判**。**直查 prod `cot.resource` 表**證實現場照片**確實在 Enterprise Sync**（atak4 17 檔/itak3 10 檔），走 fileshare `b-f-t-r` → `/Marti/sync/content?hash=`。當初「file store 恆 0」的真因＝**ICS `ics-marti-read` 不在 `blue` group（不在 UserAuthenticationFile）只讀 public → 看不到現場檔**，非「照片沒上 server」。→ **M2「讀 mission contents 拿照片」前提垮掉；照片正解＝group 權限修（#507 已上 prod：backend-v2.38.1，read cert 補進三群、實測抓現場照 200）+ 後續 #508/#509 附件顯示線**。**mission 平面本身仍有值**（作戰圖/管制/協作/AAR），但**不是照片路**——#506 降為「非照片的 mission 消費」；M2 作廢。詳見 [[tak-filestore-image-uplink]]。

**建置計畫 M0-M4（先建不吃真機、server 端自驗的地基；mission 讀走內網 takserver:8443 已通，不依賴裝置端 WG 8443）**：
- **M0** Mission 讀取地基 `services/tak_missions.py`（架 tak_rest_client）：GET /missions、/{name}、/{name}/contents、/changes。驗：server PUT 測試 mission → 讀回。不吃真機。
- **M1** 訂閱 + 消費進 COP：poll contents → 走既有 `cop_service.ingest_cot_event`；抽附件 hash 掛 entity（可靠連結）。不吃真機。
- **M2** 照片重接 mission（#503 pivot）：前端已成（顯示 entity 附件），只換後端資料源。
- **M3** Enterprise Sync 上傳（下行）：真機定 `/Marti/sync/upload` 格式（坑：無多餘 hash 參數、檔名純 ASCII）。要真機。
- **M4** 可靠刪除（P2-14 收尾）：Mission REMOVE_CONTENT。出向支線：ICS 建/寫 mission（TTX 情境注入正解）。

**約 P2-14 等級大工**（新平面）。前身 P2-14 當初走 (C) resync 迂迴（tak_resync），本平面為正解。
