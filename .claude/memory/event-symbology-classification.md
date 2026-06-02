# 事件符號 / 分類體系決策（2026-06-02）

> 「為何這樣做」的索引；細節在設計文件（見末尾 pointers）。

## 核心決策
- **體系對照（不重發明）**：NAPSG / 我們的事件 / CoT-2525 / 台灣 NCDR-NFA 是**四種不同切法**，不合成
  單一樹；做法＝「**我們的事件 taxonomy 為主幹 + 掛各標準座標（Rosetta 對照）**」。
- **視覺 = affiliation-aware（依使用情境）**：敵我 → **2525 框**、事件類型 → **NAPSG 象形**、
  severity → **halo/色**、對接 → **`cot_type`**。情境 A–E 由 `cot_type` 前綴自動分流（`a-h`/`a-f`/`a-u`
  走 2525 框；`b-*` 民事走 NAPSG ◆/▲）。**原「全 NAPSG ◆+severity」(Path 1) 已修正** —— 它砍掉敵我，
  「民防含軍事支援任務」不行（敵無人機 vs 友 QRF 會長一樣）。
- **✅ 渲染模型 LOCKED（2026-06-02，三模型×極端案例實渲染對照後）**：**民事 D/E = 現狀「單色＋剝框」**
  （我們的 ◆/▲ 框＋severity 色＋框內白色象形）；**敵我 A/B/C = 2525 框**（milsymbol，P2-05）。
  **NAPSG = 象形「來源」非渲染模型**——只借框內象形、剝原生框與色。**剝框＝轉接頭非妥協**：被剝的
  （原生框＋色）正是我們不要的（框＝敵我、色＝severity 自控）。**不採 verbatim**：互通靠 `cot_type`
  非像素 → verbatim 視覺零互通貢獻，且打掉 severity 色通道＋混兩套文法。**唯一取捨＝靠色才成立的符號退 abbr**。
  **glyph 擴充 audit 合格標準**：🟢線稿可單色 / 🟡待視覺QA / 🔴色依賴 or 單位職位圖(NIMS/Resources) or 無對應。
- **NAPSG 完整庫 = 1301 unique 符號**（自 14356 PNG 去重，11 類別；遠超 v4.0 Guideline PDF 的範例）。
  瀏覽器工具 `command-dashboard/static/napsg_browser.html`（線上引用 S3，CC BY 4.0）= audit 逐型別對照的眼睛。
  ⚠ 原 crosswalk §3「🔴×6」是對 PDF 評的、低估了；待用此標準＋瀏覽器重評。
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

## 待決策 / 可接續（crosswalk §7；渲染模型本身已 LOCKED）
- 殘餘子細項：哪些型別「敵我可變」（需 affiliation segment）；是否新增 `tw_ref` 欄 + 抓 NFA 疏散避難圖例；
  severity 是否補 Purple=Extreme。（milsymbol→P2-05 已隨 LOCKED 確認。）
- **下一步（模型已 lock 後的直球）**：用 LOCKED 的 audit 標準（🟢/🟡/🔴）＋ `napsg_browser.html`
  跑 **NAPSG×22 型別 audit** → 修正 crosswalk §3、給「可加 N 個 glyph」真實數字 → 再定 glyph 擴充 / #66 C2 範圍。
- 其他可做：**#66 C2-新增**（後端已放行、純前端）/ **P1-10h CSP**（含 #64-3）/ **P1-16** placement UI（#40）。

## 環境 quirk（此 Windows 開發機；Mac 為主力機則多無此問題）
- `doc_sync_check` 要 `PYTHONUTF8=1 python scripts/doc_sync_check.py`（否則 cp950 編 ✓ U+2713 → exit1 假失敗）。
- repo 巢狀在 `…/ICS_COMMAND/ICS_COMMAND`；瀏覽器預覽工具的 `.claude/launch.json` 要放**外層** cwd。
- `pdftoppm` 缺 → Read 無法 render PDF；用 `pypdf` 抽文字。

## Pointers
- [`command-dashboard/docs/design/classification-crosswalk.md`](../../command-dashboard/docs/design/classification-crosswalk.md)
  — 四軸對照 + Rosetta 表 + 業界/擴充模型 + **§6 視覺顯示模型(情境 A-E)+ 建立流程** + §7 待決策。
- [`command-dashboard/docs/design/event-symbology-mapping.md`](../../command-dashboard/docs/design/event-symbology-mapping.md)
  — 符號設計 + 三軸資料模型 + FEMA/IPAWS/NIMS 查證 + #66 PR-A/C1 落地紀錄。
