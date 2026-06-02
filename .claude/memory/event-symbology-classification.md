# 事件符號 / 分類體系決策（2026-06-02）

> 「為何這樣做」的索引；細節在設計文件（見末尾 pointers）。

## 核心決策
- **體系對照（不重發明）**：NAPSG / 我們的事件 / CoT-2525 / 台灣 NCDR-NFA 是**四種不同切法**，不合成
  單一樹；做法＝「**我們的事件 taxonomy 為主幹 + 掛各標準座標（Rosetta 對照）**」。
- **視覺 = affiliation-aware（依使用情境）**：敵我 → **2525 框**、事件類型 → **NAPSG 象形**、
  severity → **halo**、對接 → **`cot_type`**。情境 A–E 由 `cot_type` 前綴自動分流（`a-h`/`a-f`/`a-u`
  走 2525 框；`b-*` 民事走 NAPSG ◆/▲）。**原「全 NAPSG ◆+severity」(Path 1) 已修正** —— 它砍掉敵我，
  「民防含軍事支援任務」不行（敵無人機 vs 友 QRF 會長一樣）。
- **建立流程 = type-first**（非 scenario-first）：operator 選「事件型別」（高壓下最快）；**敵我是正交屬性**，
  多由型別自動帶，少數「會變」型別（drone / unknown_person / 可疑載具）才問 友/敵/不明。**不重組 taxonomy。**
- **字典可擴充**：2525（SIDC + 通用框架）/ NAPSG（形狀框架、內部象形可換）皆可本地擴充；taxonomy
  `source`（napsg=標準 / ics=本地擴充）+ 全員 `cot_type`（自訂項 degrade 到最近標準碼）保對接退路。
- **業界做法 = Dictionary Renderer**（資料帶代碼 → 字典渲染；Esri / TAK / milsymbol / NICS / OCHA）。
  我們的 Rosetta 表 = 那本字典的雛形。

## 已落地（2026-06-02，全 merged 進 main）
- **P1-10d 事件符號**：◆ diamond + NAPSG severity token + critical pulse + 型別 abbr（#68/#69/#70）
  + **NAPSG 象形 6 強配**（explosive/comm_fail/hazard/evacuation/facility/rescue，#75）。
- **#66 編輯器**：PR-A 後端守門（schema/superset/soft-delete，#76）/ PR-B 解撞名（event 用
  `event_group` 不借 node_type，#77）/ PR-C1 編輯既有+soft-delete+defaultAssigned 預填（#78）/
  來源標註 + `source` 定義欄（NAPSG/ICS 唯讀 badge + GET 回填，#79）。**剩 C2：新增事件/群組 + icon picker。**
- **#64 硬化 1+2+4**（dead exempt 移除 / pmtiles 穿越 resolve() 縱深 / vendored lib SHA CI 驗，#80）；
  **#64-3 CSP enforce → 留 P1-10h**。

## 待決策 / 可接續（crosswalk §7）
- 哪些型別屬「敵我可變」（需 affiliation segment）；**milsymbol（2525 框）整合時機 → P2-05**；
  是否新增 `tw_ref` 欄 + 抓 NFA 疏散避難圖例；severity 是否補 Purple=Extreme。
- 下一個可做：**C2 編輯器** / **P1-10h CSP**（含 #64-3）/ milsymbol affiliation 框。

## 環境 quirk（此 Windows 開發機；Mac 為主力機則多無此問題）
- `doc_sync_check` 要 `PYTHONUTF8=1 python scripts/doc_sync_check.py`（否則 cp950 編 ✓ U+2713 → exit1 假失敗）。
- repo 巢狀在 `…/ICS_COMMAND/ICS_COMMAND`；瀏覽器預覽工具的 `.claude/launch.json` 要放**外層** cwd。
- `pdftoppm` 缺 → Read 無法 render PDF；用 `pypdf` 抽文字。

## Pointers
- [`command-dashboard/docs/design/classification-crosswalk.md`](../../command-dashboard/docs/design/classification-crosswalk.md)
  — 四軸對照 + Rosetta 表 + 業界/擴充模型 + **§6 視覺顯示模型(情境 A-E)+ 建立流程** + §7 待決策。
- [`command-dashboard/docs/design/event-symbology-mapping.md`](../../command-dashboard/docs/design/event-symbology-mapping.md)
  — 符號設計 + 三軸資料模型 + FEMA/IPAWS/NIMS 查證 + #66 PR-A/C1 落地紀錄。
