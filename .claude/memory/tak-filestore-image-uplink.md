---
name: tak-filestore-image-uplink
description: "TAK file store 圖片上下傳（#503）——讀側契約/data 鍵/faction 綁 marker/上傳待真機；live 庫空、端到端待 ATAK dogfood"
metadata:
  node_type: memory
  type: reference
---

**#503 地點型雙向照片 = TAK Enterprise Sync file store 圖片上下傳**（兩軸共用底層；訊息軸見 #505）。**上行（TAK→ICS 下載）讀側已做+測+prod 連通實證；上傳（下行）與端到端顯示卡真機。**

**端點契約（官方 5.7 OpenAPI `docs/reference/takserver-5.7-openapispec.json`，別猜）**：
- 搜尋 `GET /Marti/api/sync/search`（filter：uid/keyword/mimetype/mission/filename/box/circle/startTime/endTime/tool…）→ **回 `ApiResponseNavigableSetResource`＝結果在 `data` 陣列**（**不是 `results`**，2026-07-05 live prod 實證：`{"version":"3","type":"Files","data":[...],"nodeId":...}`）。
- 下載 `GET /Marti/api/files/{hash}` → 原始 bytes（`*/*`，binary）。metadata `GET /Marti/api/files/{hash}/metadata`。刪除 `DELETE /Marti/api/files/{hash}`。
- **上傳不在 `/Marti/api` spec 內**（整份唯一 POST 是 `/files/api/config`）→ 走**未進 OpenAPI 的 legacy servlet `/Marti/sync/upload`**，multipart 欄位/必填 param 需**真機 ATAK 傳圖抓封包**定死（先前手打 400＝格式未定）。故 `tak_files.upload_file` **刻意留樁不猜格式**。
- `Resource` 欄位：`hash/filename/name/mimeType/uid/creatorUid/latitude/longitude/groups/size/submitter/submissionTime/keywords`。**照片自帶經緯度**（可不靠 marker 自錨定）+ `creatorUid` 疑似連 marker + `groups`＝TAK 群落。

**認證/實作**：讀走 `TAK_MARTI_READ_CERT/KEY`（truststore 信任即通、不卡 write gate，同 [[tak-marti-authz-model]] resync）。`services/tak_files.py`（client）+ `tak_rest_client.get_bytes`（binary+max_bytes 防灌爆）。hash 一律驗 SHA-256 hex 擋 path 注入。

**faction 邊界（關鍵）**：ICS 是指揮站、**單一 READ cert 跨群落全見** → TAK group 層**不**替 ICS 分陣營，faction **只能 ICS 這側施加**。故上行端點 `GET /api/tak/files/for-entity/{uid}` **綁「本 session 看得到的 cop_entity」繼承 marker 可見度**（紅方/未分類對 commander 404 不洩存在），不做全庫通搜（會跨陣營洩圖）。下載端點 `GET /api/tak/files/{hash}`：hash 不可猜 + 僅經 faction-gated 列表取得 + 每次 audit；嚴格 per-hash faction gating 待 linkage 確認後補。RBAC 走中央 gate（GET /api/tak/* → READ_ROLES）。

**前端**：`static/js/map/tak_photos.js`（可測子模組，authFetch/doc 注入）——marker 詳情面板顯 72×72 縮圖。**顯示不用 `<img src=API>`**，改 authFetch 取 bytes → blob objectURL（兼 dev header / prod cookie 兩套 auth；CSP `img-src blob:` 允許）→ 純 img property set、無 inline handler（CSP 安全）；檔名走 title property；revoke 舊 blob 免洩漏。

**🧱 [2026-07-05 真機 dogfood 定讞——方向大修正]**：部署 branch 上 prod（`07e8666`+`424284d`，image `37cedada`，**未 merge**）後真機實測，**前端 UI 端到端正常**（區塊/faction/faction 404 全對），但**照片永遠上不了 server**。逐層挖到根：
- **① ATAK marker 照片不進 Enterprise Sync**：附圖+「送 server」後 file store 仍 0 筆、missions 0、CoT detail 無 attachment 參照。裝置 log 只見 `GET /Marti/sync/missionquery`（查 Data Sync feed）、**從無 `POST /Marti/sync/upload`**。⇒ **ATAK 分享 marker 照片走 Data Sync/Mission 機制，非單純 Enterprise Sync 上傳；無 mission 存在則附件無處同步、留手機**。故本檔「`search?uid=` 搜 Enterprise Sync」＝**錯假設**；**正解＝讀 mission contents**（→ 移交 [[tak-mission-datasync-plane]] #506 M2）。
- **② 網路根因**：TAK **8443（Marti API）當初未轉發給 WG 裝置**（compose 誤註「8443 admin 不對外減攻擊面」，實為 mission/Data Sync/Enterprise Sync 上傳的 client 面埠）→ 裝置一切需 Marti API 的功能全滅（建 feed「Searching for server channels…」卡死、上傳失敗）。**已修**：WG entrypoint 加 `dnat 8443`（-i wg0 只給裝置、mTLS 保護，實測 8443 拒無 cert `TLSV13_ALERT_CERTIFICATE_REQUIRED`；TAK 標準做法）→ 見 [[tak-wg-containerized-vpn]]。開後裝置**打到 8443 了**（log `https-jsse-nio-8443-exec`），但 feed 仍建不起（ATAK 端頻道發現卡死，未解）。
- **③ 下行上傳格式副產**：`/Marti/sync/upload` 400 log 揭露坑——**不能帶多餘 `hash` 參數、檔名不能有重音/標點/非 ASCII（CJK 檔名被擋）**。留給 #506 M3。
已驗綠：ICS READ cert→live file store 連通(200)、`data` 鍵、端點邏輯（backend 58 測、frontend 11 測）、前端渲染（真瀏覽器 harness 假圖解碼）。**版號/tag、merge、照片連結封頂 全待 [[tak-mission-datasync-plane]] 地基。** 相關 [[tak-outbound-geochat-dm]]、[[event-symbology-classification]]。
