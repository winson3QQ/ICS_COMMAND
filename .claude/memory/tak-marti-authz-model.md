---
name: tak-marti-authz-model
description: 官方 TAK 5.7 Marti 授權模型實測——讀=truststore 信任即通/寫=mission-role 把關；UserAuthenticationFile 非 Marti 讀寫 gate；docker 不 hot-reload
metadata:
  type: project
---

# TAK 5.7 Marti REST 授權模型（2026-06-09 活 docker 實測，#176/#177 衍生）

#177 L1 端到端實測（活 TAK 5.7-RELEASE-43，HTTP + api log 證據）盤出的授權真相，**推翻 #176 cert-role 定案數個前提**。延伸 [[tak-server-marti-cert-not-oauth]]（認證=mTLS cert）的**授權**面。

## 事實（皆有證據）
- **讀取對任何 truststore（step-ca CA）信任的 cert 開放**，與 `UserAuthenticationFile.xml` 註冊**無關**。對照組鐵證：已註冊 ROLE_USER 的 read cert vs 未註冊的 write cert，打 `/cot/sa`、`/missions`、`/clientEndPoints`、`/groups/all` **行為全同（200）**。未註冊 cert 連線後**不會**被自動加入名冊。
- **寫入由 per-mission role 把關**，非 cert／非 fingerprint／非 server role。api log 鐵證：
  `MissionPermissionEvaluator - hasPermission denied! currentRole: MISSION_READONLY_SUBSCRIBER, requested: MISSION_WRITE → 403`。
  偽造 `creatorUid` query 參數**無法繞過**（綁 mission-role 不綁 query，安全）。無狀態 REST 的 mission creator **不會**自動取得 `MISSION_WRITE`（落到 mission 的 `defaultRole`）。
- **`/cot/sa?start=&end=&{left,bottom,right,top}` 回真 CoT** → #173 reconnect resync 路徑端到端成立。缺 start/end 或用 secago → generic「resource unavailable **or** not allowed」頁（**參數錯與授權拒共用此頁**，極易誤判成 auth）。
- **docker/Mac 不 hot-reload `UserAuthenticationFile.xml`**：改檔（含容器內 touch）零 reload，`FileAuthenticator - adding new file-based user` 僅啟動時出現 → **須 restart**。#176「hot-reload 免 restart」是 Windows 觀察，不適用 docker（macOS bind-mount inotify 不跨界）。重啟後 TAK 自行 re-marshal 名冊（`role="ROLE_USER"` 當預設省略）。
- fingerprint 格式（File backend）= **冒號分隔大寫**（`openssl x509 -fingerprint -sha256` 原樣，即 `register-tak-fingerprint.sh` 的 `FP_FORMAT=raw`）。

## How to apply
- **P2-14 resync（讀）**：只需一張 step-ca 簽的 CA-trusted cert，`/cot/sa` 即可用。**毋須** register fingerprint。
- **P2-13 downlink（寫）**：必須讓 ICS 寫 cert 取得 mission `MISSION_WRITE`/owner role——**怎麼可靠取得仍待解**（無狀態 REST creator 沒拿到；可能要持久 subscription／建 mission 時身分綁定／admin 指派）。**這是 P2-13 動工第一個要解的**。
- `register-tak-fingerprint.sh`（server ROLE_USER/ADMIN）**非** Marti 讀寫 gate，只在 server-admin 級操作有意義；保留為 L2 admin console CLI 前身，勿當讀寫前置。
- 改 `UserAuthenticationFile` 後**務必 restart** TAK（docker），別等 hot-reload。
- 相關：[[tak-server-marti-cert-not-oauth]]、[[tak-streaming-archive-stale-vs-mission]]、[[exercise-scope-server-authoritative]]。
