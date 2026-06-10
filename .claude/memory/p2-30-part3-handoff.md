---
name: p2-30-part3-handoff
description: P2-30 part 3（手動感知/敵情標記 UI）交接狀態——WIP 未 merge，驗到哪/剩什麼/怎麼跑
metadata:
  type: project
---

# P2-30 part 3 交接（手動感知/敵情標記 UI，#180）

**狀態（2026-06-10）**：功能基本完成、**WIP commit 在 branch `feat/issue-180-part3-contact`**（未 review、未 merge）。承前：[[tak-marti-authz-model]]（下行機制）、[`cop-event-layering.md`](../../docs/design/cop-event-layering.md)。

## 做了什麼（前端為主，後端沿用既有）
- **放置**：圖層面板「📍 敵情/感知標記」→ picker 選敵我（敵/不明/中立/友）→ 點地圖建 `cop_entity`（`source=manual`、`attributes.kind='contact'`、`type=a-{h/u/n/f}-G`）。RBAC=operator+（`canAccessMapObjects`，非指揮層——無線電回報是一線職責）。
- **渲染**：`_contactLayer`（**仿 infra：circle+abbr，同步、無 async bake-race**；不走 `_takLayer`——那是最初的架構錯誤、會「要 refresh 才出」）。affiliation 色（紅敵/黃?/綠中/藍友），`_CONTACT_AFF`。
- **注記**：點標記 → detail modal 可編輯 標籤/呼號 + 備註 → 💾儲存（`updateEntity` PUT+If-Match）。
- **分享到 TAK**（指揮層）：detail modal「📡 分享到 TAK」→ `POST /api/tak/share/{uid}`（part 2 已 merged 的 endpoint）→ 寫 CoT 進 :8089 → 現場 iTAK 顯示。
- **刪除**（operator+）：detail modal「🗑 刪除標記」→ `deleteEntity`。
- 改的檔（全前端）：`static/js/map.js`、`main.js`、`auth.js`、`map/mil_symbol.js`（+ `affiliationToCotType`）、`tests/js/mil_symbol.test.js`。

## 驗證狀態
- ✅ 放置→**即時**色標記、✅ 注記儲存、✅ 刪除（ICS）、✅ 4 敵我色——**真機 dogfood 驗過**。
- ✅ share→iTAK 顯示：**已證**（API 測試 `a-h-G` 在活 iTAK 顯示；使用者親見）。
- ⏳ **UI 分享鈕→iTAK 最終再驗**：使用者最初「沒看到」是因 dashboard 啟動漏帶 TAK env（503）；**已重啟帶 env、connected=true**，待用 UI 鈕再確認一次。

## 已知缺口（part 3 後續 / 標清楚別當 bug）
- **拖曳移動（#3）**：未做（infra 拖曳機制可後加）= part 3c。
- **刪除不傳 TAK**：ICS 刪 contact 只軟刪 ICS，**不送 t-x-d-d** → 已分享標記留在現場端到 stale。可靠刪除到 TAK = P2-14 領域。
- **contact 圖層無獨立 toggle**：`_layerVis.contact=true` 恆顯示，面板沒 toggle row（可後加）。
- type 用 generic `a-h-G`（實測 iTAK 顯示正常；「接觸」比 combat-unit `-U-C` 貼切）。

## 怎麼跑（接手即可驗）
- dashboard：`./start_mac.sh`（worktree 根）；**帶 TAK env** 才能分享：
  `export TAK_ENABLED=true TAK_COT_URL=tls://localhost:8089 TAK_CLIENT_CERT=deploy/step-ca/certs/cop-subscriber/client-fullchain.crt TAK_CLIENT_KEY=deploy/step-ca/certs/cop-subscriber/client.key TAK_ALLOW_INSECURE_TLS=true`
- 登入 `admin` / PIN `1234`（dev DB 已重設；正式是隨機 6 位在 `~/.ics/admin_pin_token`）。
- docker TAK（鯨魚）+ 活 iTAK 需在跑（`docker ps` 看 takserver / tak-database）。
- 硬刷新（Cmd+Shift+R）載最新 static/js。

## 下一步
1. UI 分享鈕→iTAK 最終驗。
2. `/code-review` + `/security-review`（前端 + entity→CoT；security 面小）。
3. 收尾：先把 WIP commit squash 成正式 commit → PR → merge #180（part 3）；ROADMAP P2-30 加 part 3 落地註記（步驟 8.5）。
