---
name: tak-marti-authz-model
description: 官方 TAK 5.7 Marti 授權模型實測——讀=truststore 信任即通/寫=mission defaultRole 決定(owner 不附著 stateless REST)；內容增刪不需 admin、刪 mission 對 tool=public+creator 也 200(非 admin 限定)；mission 成員≠repository 刪除；/cot/sa resync 已敲定(.000Z + 小窗，大窗 400 是 gotcha 非格式)；OpenAPI spec=docs/reference/takserver-5.7-openapispec.json；UserAuthenticationFile 非 Marti gate；docker 不 hot-reload
metadata:
  type: project
---

# TAK 5.7 Marti REST 授權模型（2026-06-09 活 docker 實測，#176/#177 衍生）

#177 L1 端到端實測（活 TAK 5.7-RELEASE-43，HTTP + api log 證據）盤出的授權真相，**推翻 #176 cert-role 定案數個前提**。延伸 [[tak-server-marti-cert-not-oauth]]（認證=mTLS cert）的**授權**面。

## 🔑 權威 API 參考（動 TAK REST 前先查，別猜）
**`docs/reference/takserver-5.7-openapispec.json`** —— TAK Server 5.7 官方 OpenAPI spec，**301 個 Marti path** 全契約（cot/sa、missions/*、federatecertificates、groups、datafeeds…）。query 參數 / request body / 型別都在裡面。CLAUDE.md「先找先例再 reinvent」的金礦；P2-14（Mission/DataSync/resync）、P2-26（admin console cert/role/group）動工**第一步查它**。本檔多條 endpoint 事實即由此 spec + 活 server 實測交叉敲定。

## 事實（皆有證據）
- **讀取對任何 truststore（step-ca CA）信任的 cert 開放**，與 `UserAuthenticationFile.xml` 註冊**無關**。對照組鐵證：已註冊 ROLE_USER 的 read cert vs 未註冊的 write cert，打 `/cot/sa`、`/missions`、`/clientEndPoints`、`/groups/all` **行為全同（200）**。未註冊 cert 連線後**不會**被自動加入名冊。
- **寫入權完全由 mission `defaultRole` 決定**（非 cert、非 fingerprint、非 server role、非 creatorUid）。深挖修正初版「寫=mission-role/owner 把關」：
  - **`MISSION_OWNER`/`MISSION_WRITE` 角色不附著到無狀態 REST 請求**——連 mission **建立者**新連線 `GET .../role` 都只拿 defaultRole；訂閱匹配 uid、一致 creatorUid、`PUT role` 自設皆無法取得 owner（api log `MissionPermissionEvaluator: currentRole MISSION_READONLY_SUBSCRIBER, requested MISSION_WRITE → 403`）。owner 是**持久 streaming 訂閱(ATAK)或 admin** 的屬性。
  - **不設/寬鬆 defaultRole（MISSION_SUBSCRIBER）→ 任何 truststore-trusted cert 都能 PUT/DELETE contents**（read cert 用隨機/無 creatorUid 皆 200）。`defaultRole=MISSION_READONLY_SUBSCRIBER` → **沒有任何 REST client 寫得了，含 creator**。→ 初版「creatorUid 偽造擋得住」**錯**：creatorUid 非寫入 gate。
  - **內容增刪（ADD/REMOVE_CONTENT）在寬鬆 mission 上可行、不需 admin**：`PUT/DELETE contents` 200、`GET .../changes` 出 `ADD_CONTENT`/`REMOVE_CONTENT`+`contentUid` → **#173 增量同步 + #161 可靠刪除達成路徑**。**DELETE 整個 mission 需 ROLE_ADMIN**（ROLE_USER 恆 403）。
- **`/cot/sa?start=&end=&{left,bottom,right,top}` 回真 CoT** → #173 reconnect resync 路徑端到端成立。缺 start/end 或用 secago → generic「resource unavailable **or** not allowed」頁（**參數錯與授權拒共用此頁**，極易誤判成 auth）。
- **docker/Mac 不 hot-reload `UserAuthenticationFile.xml`**：改檔（含容器內 touch）零 reload，`FileAuthenticator - adding new file-based user` 僅啟動時出現 → **須 restart**。#176「hot-reload 免 restart」是 Windows 觀察，不適用 docker（macOS bind-mount inotify 不跨界）。重啟後 TAK 自行 re-marshal 名冊（`role="ROLE_USER"` 當預設省略）。
- fingerprint 格式（File backend）= **冒號分隔大寫**（`openssl x509 -fingerprint -sha256` 原樣，即 `register-tak-fingerprint.sh` 的 `FP_FORMAT=raw`）。

## [2026-06-10 P2-14 前置 reality check（cop-subscriber cert 對活 5.7-RELEASE-43，#194 衍生）]
- **Mission CRUD 全鏈用 cop-subscriber cert（非 admin）皆 200**：`PUT /missions/{n}?creatorUid=&tool=public`→201、`PUT/DELETE .../contents`→200、**`DELETE /missions/{n}`→200**。**精煉上文 line 17「DELETE mission 需 ROLE_ADMIN」**：對 **`tool=public` 且本 cert 為 creator** 的 mission，DELETE 整個 mission **可行（200）**；ROLE_ADMIN-403 應是**非 public / 非 creator** 情境 → 設計 P2-14 admin console 時須**釐清 tool/creator 維度**，別一律假設刪 mission 要 admin。
- **★ mission 成員資格 ≠ repository 刪除**：把既有 repository CoT（:8089 streaming 來、archived）`PUT .../contents` 加進 mission、再 `DELETE .../contents` 移除後，**該 CoT 仍在一般 repository**（`GET /Marti/api/cot/xml/{uid}` 200）。→ **REMOVE_CONTENT 只對 mission 訂閱者廣播移除事件**（上文 line 17 的「可靠刪除」是這個語意）；**一般 SA repository / 非 mission streaming client 仍看得到**。**P2-14 可靠刪除的真正前提：標記必須 mission-native（透過 mission 通道發布）＋ 現場 iTAK 訂閱該 mission**；part 3 的 :8089+archive 是**另一條通道**，兩者不互通。
- **★ `/cot/sa` resync 契約敲定（OpenAPI spec + 實測，#194 解鎖）**：權威來源 = **`docs/reference/takserver-5.7-openapispec.json`**（301 paths，TAK 5.7 全 Marti 契約；P2-14/P2-26 動工先查它別猜）。`GET /Marti/api/cot/sa?start=&end=`：
  - **date 格式 = `yyyy-MM-dd'T'HH:mm:ss.SSS'Z'`（`.000Z`）**；`epoch`/`+0000` → `Failed to convert ... java.util.Date`（start=Spring `@DateTimeFormat`）。
  - **start/end 必填；`left/bottom/right/top`(bbox)、`isFiltered` 選填**。
  - **★ 真正 gotcha = 時間窗有上限**：窄窗（2h）→ **200 回真 CoT**（123 events 含活 iTAK 裝置）；大窗（40 天）→ generic `BAD_REQUEST Invalid Request`（**與參數錯共用此頁，極易誤判成格式/auth 壞**）。→ #194 resync 用 **since-last-seen 小窗** 天然避開。end<start→400、空窗→404。
  - cop-subscriber cert 即可（無需 admin/register），對齊上文「讀=truststore 信任即通」。
- 相關：[[tak-streaming-archive-stale-vs-mission]]（為何 streaming 刪不掉）、P2-14 / #194。

## How to apply
- **P2-14 resync（讀）**：只需一張 step-ca 簽的 CA-trusted cert，`/cot/sa` 即可用。**毋須** register fingerprint。
- **P2-13 downlink（下達指令）— 機制實測定案**：官方 Marti **無 REST 廣播端點**（`injectors/cot/uid` 是注入 detail 非廣播）。兩條路：
  - **(A) :8089 雙向 streaming 廣播** ✅ 實測：ICS 寫 CoT 到 :8089（`tak_service` 現有連線）→ server 廣播給同 group(__ANON__) 所有現場端（活 iTAK 實收）；`t-x-d-d` 刪除亦廣播。**不需 mission/owner-role/admin，只要 truststore-trusted cert**。= 最簡下行路（同 ATAK 送 CoT）。限制：live、無持久/可靠刪除（reconnect 不重播）。現有 :8089 連線唯讀，P2-13=加寫能力（pytak `writer` 重用待驗，socket 層已證雙向）。
  - **(B) Mission contents**（持久/權威指令）：survives reconnect + `changes` 可靠刪除，但 owner-role REST 取不到 → 需寬鬆 mission（任何受信 cert 可寫，無身分邊界）或 admin。
  - → **即時指令走 (A)，持久/權威指令走 (B)**。指令=標準 CoT + COP `source='command'`/`planned=true`。**open：planned→actual ack 回流機制未定**。讀/寫 cert 分流對寫入**無安全價值**（寫權=truststore，與 cert 身分無關）。
- `register-tak-fingerprint.sh`（server ROLE_USER/ADMIN）**非** Marti 讀寫 gate，只在 server-admin 級操作有意義；保留為 L2 admin console CLI 前身，勿當讀寫前置。
- 改 `UserAuthenticationFile` 後**務必 restart** TAK（docker），別等 hot-reload。
- 相關：[[tak-server-marti-cert-not-oauth]]、[[tak-streaming-archive-stale-vs-mission]]、[[exercise-scope-server-authoritative]]。
