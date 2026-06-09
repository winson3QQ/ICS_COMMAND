---
name: tak-marti-authz-model
description: 官方 TAK 5.7 Marti 授權模型實測——讀=truststore 信任即通/寫=mission defaultRole 決定(owner 不附著 stateless REST)；內容增刪不需 admin 但刪 mission 需 admin；UserAuthenticationFile 非 Marti 讀寫 gate；docker 不 hot-reload
metadata:
  type: project
---

# TAK 5.7 Marti REST 授權模型（2026-06-09 活 docker 實測，#176/#177 衍生）

#177 L1 端到端實測（活 TAK 5.7-RELEASE-43，HTTP + api log 證據）盤出的授權真相，**推翻 #176 cert-role 定案數個前提**。延伸 [[tak-server-marti-cert-not-oauth]]（認證=mTLS cert）的**授權**面。

## 事實（皆有證據）
- **讀取對任何 truststore（step-ca CA）信任的 cert 開放**，與 `UserAuthenticationFile.xml` 註冊**無關**。對照組鐵證：已註冊 ROLE_USER 的 read cert vs 未註冊的 write cert，打 `/cot/sa`、`/missions`、`/clientEndPoints`、`/groups/all` **行為全同（200）**。未註冊 cert 連線後**不會**被自動加入名冊。
- **寫入權完全由 mission `defaultRole` 決定**（非 cert、非 fingerprint、非 server role、非 creatorUid）。深挖修正初版「寫=mission-role/owner 把關」：
  - **`MISSION_OWNER`/`MISSION_WRITE` 角色不附著到無狀態 REST 請求**——連 mission **建立者**新連線 `GET .../role` 都只拿 defaultRole；訂閱匹配 uid、一致 creatorUid、`PUT role` 自設皆無法取得 owner（api log `MissionPermissionEvaluator: currentRole MISSION_READONLY_SUBSCRIBER, requested MISSION_WRITE → 403`）。owner 是**持久 streaming 訂閱(ATAK)或 admin** 的屬性。
  - **不設/寬鬆 defaultRole（MISSION_SUBSCRIBER）→ 任何 truststore-trusted cert 都能 PUT/DELETE contents**（read cert 用隨機/無 creatorUid 皆 200）。`defaultRole=MISSION_READONLY_SUBSCRIBER` → **沒有任何 REST client 寫得了，含 creator**。→ 初版「creatorUid 偽造擋得住」**錯**：creatorUid 非寫入 gate。
  - **內容增刪（ADD/REMOVE_CONTENT）在寬鬆 mission 上可行、不需 admin**：`PUT/DELETE contents` 200、`GET .../changes` 出 `ADD_CONTENT`/`REMOVE_CONTENT`+`contentUid` → **#173 增量同步 + #161 可靠刪除達成路徑**。**DELETE 整個 mission 需 ROLE_ADMIN**（ROLE_USER 恆 403）。
- **`/cot/sa?start=&end=&{left,bottom,right,top}` 回真 CoT** → #173 reconnect resync 路徑端到端成立。缺 start/end 或用 secago → generic「resource unavailable **or** not allowed」頁（**參數錯與授權拒共用此頁**，極易誤判成 auth）。
- **docker/Mac 不 hot-reload `UserAuthenticationFile.xml`**：改檔（含容器內 touch）零 reload，`FileAuthenticator - adding new file-based user` 僅啟動時出現 → **須 restart**。#176「hot-reload 免 restart」是 Windows 觀察，不適用 docker（macOS bind-mount inotify 不跨界）。重啟後 TAK 自行 re-marshal 名冊（`role="ROLE_USER"` 當預設省略）。
- fingerprint 格式（File backend）= **冒號分隔大寫**（`openssl x509 -fingerprint -sha256` 原樣，即 `register-tak-fingerprint.sh` 的 `FP_FORMAT=raw`）。

## How to apply
- **P2-14 resync（讀）**：只需一張 step-ca 簽的 CA-trusted cert，`/cot/sa` 即可用。**毋須** register fingerprint。
- **P2-13 downlink（下達指令）— 機制實測定案**：官方 Marti **無 REST 廣播端點**（`injectors/cot/uid` 是注入 detail 非廣播）。兩條路：
  - **(A) :8089 雙向 streaming 廣播** ✅ 實測：ICS 寫 CoT 到 :8089（`tak_service` 現有連線）→ server 廣播給同 group(__ANON__) 所有現場端（活 iTAK 實收）；`t-x-d-d` 刪除亦廣播。**不需 mission/owner-role/admin，只要 truststore-trusted cert**。= 最簡下行路（同 ATAK 送 CoT）。限制：live、無持久/可靠刪除（reconnect 不重播）。現有 :8089 連線唯讀，P2-13=加寫能力（pytak `writer` 重用待驗，socket 層已證雙向）。
  - **(B) Mission contents**（持久/權威指令）：survives reconnect + `changes` 可靠刪除，但 owner-role REST 取不到 → 需寬鬆 mission（任何受信 cert 可寫，無身分邊界）或 admin。
  - → **即時指令走 (A)，持久/權威指令走 (B)**。指令=標準 CoT + COP `source='command'`/`planned=true`。**open：planned→actual ack 回流機制未定**。讀/寫 cert 分流對寫入**無安全價值**（寫權=truststore，與 cert 身分無關）。
- `register-tak-fingerprint.sh`（server ROLE_USER/ADMIN）**非** Marti 讀寫 gate，只在 server-admin 級操作有意義；保留為 L2 admin console CLI 前身，勿當讀寫前置。
- 改 `UserAuthenticationFile` 後**務必 restart** TAK（docker），別等 hot-reload。
- 相關：[[tak-server-marti-cert-not-oauth]]、[[tak-streaming-archive-stale-vs-mission]]、[[exercise-scope-server-authoritative]]。
