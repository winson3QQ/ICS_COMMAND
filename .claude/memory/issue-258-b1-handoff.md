---
name: issue-258-b1-handoff
description: COP 雙向協作編輯 α/β epic 已完成（#257/#258/#265 全 CLOSED）；保留演習隔離與 TTX 外部幾何編輯的耐久驗證事實
metadata:
  node_type: memory
  type: project
  originSessionId: d79bcb7a-8115-4f66-80ce-9445f4a0613e
---

**COP 雙向協作編輯（[[cop-collaborative-edit-shared-truth]] doctrine）— ✅ epic 完成（2026-06-15）**

- **#257 α 全系列 merge + CLOSED**：α-1 整體移動 #262 / α-2 reshape·α-2b 插入刪除 #263 / α-3 marker 敵我態 #264。ICS 自建 polygon/route/marker 可原地編輯、即時上 TAK。
- **#258 β CLOSED**：β-1 經 [PR #266](https://github.com/winson3QQ/ICS_COMMAND/pull/266)（squash 12f894b）merge——TTX 模式可編輯**外部 TAK 來源幾何**（polygon/route）。前端編輯閘 mode-aware（`_isReadonlySource` 外部來源僅 active exercise type='ttx' 可編；`setExerciseMode` 走 main.js 橋接守 module boundary——map.js 只 import ./ws.js + ./map/*）。後端零改動（TTX 早放行、已測 `test_put_tak_allowed_in_ttx`）。編輯不自動回推 TAK、走明示「📡 廣播」鈕。
- **#265（WS scope blocker）✅ 已修 CLOSED**（[PR #268](https://github.com/winson3QQ/ICS_COMMAND/pull/268) `1dcd9e0`）：切換 active 演習後 cop WS scope 不再凍結。正解＝server 端切換時直接更新各 `_Conn.exercise_id` + cop_stream onclose identity guard + `_refreshAfterExerciseSwitch` in-place resync 不清快取。**這解除了原本卡 β live 驗證的 race，切場後不再需要硬重整。**
- **後續演進（已 merge）**：#267 常駐層疊看（WS `?standing=1` 限 COMMAND 疊收 NULL 常駐 entity，後端 #271 + 前端 #272）、納編/退編（#274，把單位移進 active 場 / 退回 NULL 常駐）、#269 右欄四-tab 重構 + TAK 隊伍名冊（#270）。

**耐久驗證事實（演習隔離語意，跨機器仍適用）**：
- entity 的 `exercise_id` **create 後凍結不改**：切到 TTX 前已存在的真實 PLI 綁 `exercise_id=NULL` → 在 TTX 場永不出現（刻意演習隔離，非 bug；同家族 #125）。TTX 可編的外部物件 = 演習 active 期間 ATAK 新畫、綁該 TTX exercise_id 的幾何。常駐（NULL）單位要在演習場看見須走 #267 疊看 / #274 納編。
- ATAK 入向幾何**會**正確解析成 kind=polygon + vertices（實證 u-d-r 矩形→4 頂點），故外部幾何 reshape/move 可用。

相關：[[worktree-basemap-pmtiles-junction]]、[[issue-275-mtls-waves]]
