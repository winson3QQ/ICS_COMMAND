---
name: tak-outbound-geochat-dm
description: "ICS→TAK 出向 GeoChat（#216）+ DM 靠 marti dest（streaming 認）+ 入向 DM→ICS 結構不通"
metadata:
  node_type: memory
  type: reference
  originSessionId: 0ce2986b-a122-4dc9-a757-37f02a55d890
---

ICS→TAK 出向文字通聯（GeoChat `b-t-f`）= `services/tak_downlink.build_geochat_cot` + `POST /api/tak/chat`（COMMAND_ROLES + audit-first + 內容白名單）。站台身分 `ICS_SELF_UID="ICS-CMD"`（ICS 非 GPS 裝置、wire 無自身 uid，需穩定 sender 才能組 chatgrp/link/uid）；發話者由 senderCallsign 帶出。回送 ICS 自身靠 uid 的 msg_id GUID 冪等去重（`chat_repo.chat_exists`），且 `ingest_chat` 把 sender==`ICS_SELF_UID` 歸 `faction='blue'`（否則 fail-closed 對藍方隱藏、連發話 commander 自己都看不到）。

**🔑 出向 DM 必須帶 `<marti><dest callsign="..."/>`（2026-06-25 公網雙實機 dogfood 實證）**：
- 沒帶 dest → TAK server 對 GeoChat **廣播全發**，要不要顯示交給各 client 過濾，而 **ATAK 過濾比 iTAK 鬆 → 私訊外洩**（私訊給 itak、atak 也收到；私訊給 atak 只 atak 收到，不對稱）。
- 帶 `<marti><dest callsign>` → **server 只投遞給該呼號**（dogfood 驗：itak 收、atak 不收）。
- **關鍵且先前不確定的事實：TAK :8089 streaming-write 進來的訊息「照 marti 路由」**（不只 REST/federation 才認 marti）。廣播/命名聊天室**不**帶 dest（群發語意）。

**入向 DM → ICS 結構性不通（→ [#397](https://github.com/winson3QQ/ICS_COMMAND/issues/397)）**：ICS 是被動 :8089 訂閱者、**不自報 SA/presence**（非 GPS 裝置 doctrine，見 [[event-symbology-classification]] 的反偽造遙測）→ ① 現場端聯絡人清單裡沒 ICS、選不到當收件人；② ICS 連線 cert 身分（CN `ics-cot`）≠ 出向掛的 senderCallsign（operator display_name）→ server 找不到對應 client 投不到。**廣播進得來**（ICS 從串流收所有 b-t-f），uid/callsign 定向 DM 進不來。要通 = ICS 得 announce 可定址 contact endpoint（牴觸非 GPS doctrine，待決）。

通聯時間顯示：`chat_panel._shortTime` 原直接切 ISO 字串 → 顯示 UTC、比 header 時鐘（`toTimeString`=本地）慢一個時區；改 `new Date(t).toTimeString()`（CoT/received_at 皆帶 Z=UTC）對齊本地。

版本落地：backend-v2.17.0（#214 出向 `<__group>` + #216 GeoChat）/ v2.17.1（DM marti）；frontend-v1.13.0（compose 面板）/ v1.13.1（時間本地化）。真機抓包基準 `tests/fixtures/cot/geochat_btf.xml`。相關 [[tak-marti-authz-model]]、[[tak-streaming-archive-stale-vs-mission]]。
