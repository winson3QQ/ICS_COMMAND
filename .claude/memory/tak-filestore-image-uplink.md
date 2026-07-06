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

**🧱🧱🧱 [2026-07-06 二輪 dogfood 定讞——推翻上面 07-05 的「P2P/mission」判斷；#507 修復並上 prod]**（教訓：**只看 ErrorController log〔僅記 4xx〕漏成功上傳；只用 ICS read cert 抓 404 不能證明檔案不在——要直查 prod DB**）：
- **① 上面「照片不進 Enterprise Sync／走 mission」＝誤判，已推翻**。直查 prod `cot.resource` 表：**現場照片確實上了 Enterprise Sync**（3QQ-atak4 17 檔／3Q-itak3 10 檔，含先前一直 404 的 hash）。ICS 抓 404 的真因＝**TAK group 隔離**：`ics-marti-read` **不在任何 group**（不在 UserAuthenticationFile）→ 只讀 `public` → 現場檔（在 `blue` 群）404。對照 `ics-cot`（在 blue）實測抓得到 10.6MB JPG＝坐實。⇒ **非架構牆、非 mission 問題，是可修的 group 權限**。「照片走 fileshare `b-f-t-r` → `/Marti/sync/content?hash=`（Enterprise Sync）」才是實情（senderUrl 指 server，不是 P2P）。
- **② #507 落地「兩面一軸」消費半身並上 prod**（PR #510，backend-v2.38.0 + hotfix v2.38.1 / frontend-v1.29.0）：`services/tak_identity` infra 證群歸屬**宣告式 SoT**（cot/read/write=全群、admin=空）+ `check_drift` 五態 + registrar **多群 register**（`enroll_infra_groups`，逗號展開）+ 編排 `reconcile_infra_groups`（開機 best-effort 警示 + `POST /tak/infra-groups/reconcile` sysadmin 一鍵對帳 + 面板消費半身漂移顯示）。**hotfix v2.38.1**：`tak_revocation._cert_sha256_fingerprint` 對 fullchain（leaf+intermediate）改**只切第一張證**再解（原 `ssl.PEM_cert_to_DER_cert` 整檔多張 base64 併解、長度非 4 倍數即拋 → write `no-fingerprint`；read 僥倖；連帶修 `infra_fingerprints` 撤銷保護漏 write）。
- **③ prod 實機驗過**：一鍵對帳 → 四證群對齊（read/write/cot 三群、admin 空）；**`ics-marti-read` 抓現場照 → HTTP 200, 906KB, zip 內 `1000004604.jpg`**（就是先前 404 那類檔）＝**照片存取權限的牆打通**。⚠ **#507 修的是「權限」**；現場照片**自動畫上 COP marker** 還差 #508（CoT 分流器接 `b-f-t-r`）+ #509（附件模型 收→抓→解壓→掛 marker）。
- **④ presence beacon（#507 Phase4）**：ICS 自報 SA（`a-f-G-U-C`，誠實不送偽造遙測 #214）現身現場 Contacts＝下行定址；**預設 OFF**（`TAK_PRESENCE_ENABLED`，opt-in）。
- 連帶更正 [[tak-mission-datasync-plane]]（M2「讀 mission 拿照片」前提作廢）。

**🧱🧱🧱🧱🧱 [2026-07-06 下行「顯示到現場」配方真機定讞——ICS→field 照片推送通]**（教訓：**推裸 jpg 現場 client 不吃、靜默無反應；要推 mission-package zip；senderUrl/transport 早就對，錯在 payload**）：
- **真機抓包定案（廣播 b-f-t-r 的真實格式，DEBUG log 於 dispatcher 捕獲）**：`<event type='b-f-t-r' how='h-e'><point/><detail><fileshare filename='<name>.zip' senderUrl='https://10.13.13.1:8443/Marti/sync/content?hash=<ZIP-hash>' sizeInBytes='<zip 大小>' sha256='<ZIP-hash>' senderUid='...' senderCallsign='...' name='<name>'/></detail></event>`。**廣播就是純 `<fileshare>`——無 `peerHosted`、無 `<ackrequest>`、無 `<marti><dest>`**。senderUrl 用**裝置面對的位址**（WG `10.13.13.1:8443`，非容器內網名）。
- **下行推送三步配方（真機驗證：現場 ATAK/iTAK 收到檔案通知 + marker 上圖 + 照片附件）**：① ICS 打包 **mission-package zip**（`MANIFEST/manifest.xml` + `<uid>/<uid>.cot` 一個 a-u-G marker CoT 帶 `<archive/>` + `photo/<name>.jpg`；結構=`parse_mission_package` 的反向）② 上傳 zip 到 Enterprise Sync（write cert，`/Marti/sync/upload?name=X.zip&creatorUid=&keywords=missionpackage`）③ 用 **cot cert** 經 :8089 廣播上面那則 b-f-t-r 指向 zip（senderUrl/sha256 = **zip 的** hash，非照片的）。
- **收訊定址真相（解上一輪「ICS 收不到」的過度推論）**：**廣播 b-f-t-r 到得了 ICS subscriber**（在 group 內即收）；**addressed 給 `ICS-Command` 的到不了**（路由到 presence beacon 那條一次性死連線＝beacon/subscriber 分裂）。故：ICS 收現場照片=**#509-P2 輪詢**（與通告解耦，穩定）；要抓入向 b-f-t-r 格式須請 iTAK **廣播**分享（別選 ICS-Command）。beacon-over-subscriber 修復只有「ICS 要收 addressed DM/fileshare」才需要，**下行廣播推送不需要它**。
- **transport 已證**：raw ssl（cot cert）送 CoT 到 :8089，TAK 廣播到 group 內裝置（送普通 a-u-G marker 實測現身裝置地圖）。**待建 #509-P3**：`build_mission_package` + `build_fileshare_cot` + upload 後 send_cot（recipient targeting：group 廣播 / 點對點 addressed 兩者，指揮官每次選——使用者 2026-07-06 拍板）。nginx `client_max_body_size 32m` 已補（原 1MB 擋多 MB 照片上傳 413，backend 無涉、deploy config hotfix）。

**🧱🧱🧱🧱 [2026-07-06 三輪 dogfood 定讞——#508/#509/#509-P2 全上 prod，現場照片端到端通]**（教訓：**iTAK 與 ATAK 的照片分享 wire 行為不同，別一概而論；成功路徑不印原始 CoT type 會誤判「沒收到」；先查 DB／Enterprise Sync 再下結論**）：
- **#508（分流器）+ #509（附件模型）上 prod**（PR #511，backend-v2.39.0）：`b-f-t-r` fileshare → 抓 mission-package zip（`/Marti/sync/content?hash=`）→ `parse_mission_package`（zip-slip/magic/size 縱深）→ ingest 內含 marker CoT → 抽照片存本地（檔名=sha256）掛 marker，經 #503 面板顯示。denylist 分流（攔 `b-f-t-*`/`t-x-*` 非實體家族，不誤存成 marker）。
- **關鍵發現：iTAK 照片能到、ATAK 照片到不了**。同組（team White）、位置 CoT 兩家都到得了 ICS，但：**iTAK 分享照片會廣播 `b-f-t-r` 通告 → #509 被動接到 → 成功掛 marker；ATAK 不廣播 `b-f-t-r`（定址因 client 而異）→ ICS 等不到 → 照片躺在 Enterprise Sync 沒人接**。直查 `cot.resource` 證實**兩家照片都在 Enterprise Sync**（ATAK=`1000004677.jpg.zip`、iTAK=`3QQ-iTAK.06.162214.zip`），包結構相同（MANIFEST + marker CoT + jpg，#509 解析器完全解得動），ICS read cert 抓得到（200），連結的 marker 也在 ICS——**唯一缺的是「通告」這一步**。⇒ 只靠被動等 `b-f-t-r` 會漏 ATAK。
- **#509-P2 主動輪詢橋上 prod**（PR #513，backend-v2.40.0）：不再只等通告，**週期打 `/Marti/api/sync/search` → 濾 `Keywords=["missionpackage"]`（避開 ICS 自傳的 #503 檔，其 keyword=marker_uid）→ 對未見過的 hash 走 #509 同一套**。announce/poll 共用 `_bridge_package_by_hash` + `_seen_hashes`。**時序韌性**：不倚賴 `ingest_cot_event` 回傳（poll 時 package 內 CoT 常比 live marker 舊 → ingest no-op 回 None），改查「marker 存在且未刪」才掛照片。開關 `TAK_FILESTORE_POLL_ENABLED`（預設 OFF）。**prod 實機驗過：開機首輪即回填 7 張歷史現場照片，含先前「無照片」的 ATAK marker R.6.161339（掛上 `1000004677.jpg`）。ATAK 照片端到端通。**
- **下行上傳 + DELETE 已 live 驗證（2026-07-06，推翻舊「上傳卡真機/留樁」狀態）**：write cert 往返實測全通——`POST /Marti/sync/upload?name=<ascii>&creatorUid=&uid=<marker>&keywords=<marker>` + raw body → **200 回 Hash**；`search?uid=<marker>`（read cert）撈得到（for-entity 路徑）；`/Marti/sync/content?hash=` 抓回 bytes 完全一致；**`DELETE /Marti/api/files/{hash}`（write cert）→ 200 真刪、search remaining=0**。ICS 端點 `POST /api/tak/files/upload`（routers/tak.py:478，指揮層守門 + faction 綁 marker 可見度 + magic-byte 驗真圖 + audit）外層 unit-tested。**⚠ 仍待真機 dogfood**：① 走真正 ICS UI（指揮層 marker 面板「📤上傳照片」）整條；② 推上去的照片會不會顯示在現場裝置（受 TAK client 顯示行為影響）。**DELETE 200 = [[tak-server-data-lifecycle-unmanaged]] 選項 B（刪到 server 檔庫）+ 系統化 retention 的底層動作已證實可行。**
- **另兩個現場觀測**：① faction=NULL——現場裝置未在 ICS 分類（#344）→ 所有 TAK 物件 faction=NULL；`ICS_FACTION_ISOLATION=true` 時對非 sysadmin fail-closed 全藏（連位置點都看不到），要 sysadmin 看、或分類裝置到 blue+restamp、或關隔離。② presence beacon（#507 P4）真機開起來：ICS-Command 穩定現身現場 Contacts（write cert 一次性連線，takserver 每輪 WARN「can't find subscription」但 Contacts 顯示穩定；座標暫用現場區 24.7877/121.0148，正式應改真指揮部座標）。compose 透傳 `TAK_PRESENCE_*`/`TAK_FILESTORE_POLL_*` 已補（先前 beacon 部署過但漏 commit compose）。
