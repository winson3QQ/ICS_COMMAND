# 事件分類體系與標準對照（Classification Crosswalk）

> 記錄於 2026-06-02。動機：釐清「NAPSG icon 分類 / 我們的事件分類 / TAK(CoT) / 台灣標準」之間
> 的關係，建一個**共同對照框架**，讓使用者清楚、且不重新發明。
> 與 [`event-symbology-mapping.md`](event-symbology-mapping.md) 互補：該檔講「符號怎麼畫 + 三軸資料模型」，
> 本檔講「分類體系怎麼對齊各標準」。

## 1. 為什麼它們看起來不一樣 —— 四種不同的「切法」

同一批現實（火災、爆裂物、撤離、收容…），四套體系用**不同維度**切，不是同一棵樹。硬塞成一棵會痛。

| 體系 | 按什麼分類 | 本質 / 用途 |
|---|---|---|
| **NAPSG Incident Symbology** | 領域 / 用途（Hazard、Public Alert、Infrastructure、Resources、NIMS Positions、USAR、Lifelines…）| 一個**符號庫目錄**（「畫什麼」）；US DHS/FEMA-aligned |
| **我們的事件 taxonomy** | 營運分組（安全威脅 / 搜救 / 醫療 / 收容 / 基礎設施 / 行動管理）| **指揮台**如何歸類事件，給人看 + 篩選 |
| **CoT / MIL-STD-2525** | 敵我（affiliation）× 維度（dimension）× 功能（function）| 對接 **TAK** 的資料語言（共同語言的橋）|
| **台灣 NCDR / NFA** | 災害類型 + 疏散避難元素（收容處所 / 避難路線 / 危險潛勢區）| **民防疏散**導向，範圍較窄 |

**關鍵理解**：四者是同一批現實的不同投影。**不要**逼它們合成單一分類樹。

## 2. 共同平台的做法：統一「對照」，不是統一「分類」

不重新發明 = **以「我們的事件 taxonomy」為主幹，每個事件掛上各標準的座標**（Rosetta 對照）。
使用者只看我們的分類；對接系統各取所需座標：

- 地圖視覺 → 讀 **NAPSG**（icon / 形狀 / severity 色）
- ATAK / 外部系統 → 讀 **`cot_type`**（CoT，已內建於 taxonomy）
- 台灣在地場景 → 讀 **台灣對應**（NFA/NCDR 圖例，收容/疏散類）
- 嚴重度 → **severity**（NAPSG 標準 hex，3 級）

> taxonomy 既有的 `cot_type` 與 `source`（napsg/ics）欄位即是這張對照表的雛形；本檔把它擴充成
> 完整 crosswalk，並評估是否新增 `tw_ref`（台灣對應）欄。

## 3. Rosetta 對照表（22 事件 × 各標準）

> **NAPSG icon 欄為 draft**：標註信心（🟢 強配＝有乾淨單色象形 / 🟡 勉強＝單位圖或彩色或語意鬆 /
> 🔴 無乾淨對應）。是否放寬「事件 vs 單位」潔癖、把 🟡 也納入，見 §6 視覺模型 / §7 待決策。
> cot_type / source 來自現行 seed；台灣欄僅在民防疏散/收容類有對應，其餘留空。
>
> **✅ 2026-06-03 已重 audit**：下表 NAPSG 欄為原始 **draft（對 v4.0 Guideline PDF 評估，已知低估）**。
> 經對**完整 NAPSG 庫（1301 unique 符號；工具 [`static/napsg_browser.html`](../../static/napsg_browser.html)）**重評，
> **以表後〈audit 修正〉為準**（個別 row cell 未逐一改寫，保留 draft 供對照）。

| 事件 key | 群組 | severity | source | NAPSG（類別 / 候選 icon · 信心）| cot_type | 台灣（NFA/NCDR）|
|---|---|---|---|---|---|---|
| explosive 疑似爆裂物 | security | critical | napsg | Human-Caused / Explosion `FAA` 🟢 | a-h-G | — |
| drone 無人機威脅 | security | critical | napsg | （DHS 集無乾淨 UAS；2525 a-h-A 空中）🔴 | a-h-A | — |
| violent 暴力事件 | security | critical | napsg | Public Alert / Law Enf Warning `GABC` 🟡 | a-h-G | — |
| unknown_person 不明人士 | security | warning | ics | Incident Intel / Reporting Party `HAAJ` 🟡 | a-u-G | — |
| perimeter 管制區異常 | security | warning | ics | Access Hazards / Blocked Access `BAAD` 🟡 | b-a-g | — |
| crowd 秩序問題 | security | warning | ics | Resources / Crowd Control Team `DABV`（單位）🟡 | a-n-G | — |
| rescue 受困救援 | rescue | warning | napsg | USAR / Victim `AAE`（「V」）🟢 | a-f-G | — |
| qrf QRF 出動 | rescue | warning | ics | Resources / SWAT `DABX`（單位）🟡 | a-f-G | — |
| mci 大量傷亡 | medical | critical | napsg | Resources / MCI 支援車 `DAAU`（單位）🟡 | a-u-G-I | — |
| emergency 緊急病症 | medical | critical | napsg | Lifelines / Medical（彩色含字）🟡 | a-f-G-U-U-M | — |
| infectious 傳染疑慮 | medical | warning | napsg | HazMat / Class 6 Infectious `JAAY`（placard 含字）🟡 | a-u-G-I | — |
| capacity 量能超載 | care | warning | ics | （抽象，無）🔴 | b-r | NFA 收容處所（狀態）|
| isolation 隔離事件 | care | warning | ics | HazMat 生物危害 / 檢疫 🟡 | a-u-G-I | （防疫場所）|
| person_need 人員狀況 | care | info | ics | （person，泛用）🔴 | a-f-G | NFA 收容相關 |
| comm_fail 通訊異常 | infra | warning | napsg | Infrastructure/Comms / Tower `LEK` 🟢 | b-r | — |
| facility 設施異常 | infra | info | napsg | USAR / Structure `AAB` 🟢 | a-u-G-I | — |
| equipment 設備故障 | infra | info | ics | （無乾淨）🔴 | b-r | — |
| evacuation 撤離 | ops | warning | napsg | Public Alert ▲ / Evacuation Immediate `GAAN` 🟢 | b-a | **NFA 避難方向/路線** |
| resource 資源調度 | ops | info | ics | Resources（泛用）🟡 | t | — |
| situation 現場變化 | ops | info | ics | （泛用，無）🔴 | a-u-G-I | — |
| hazard 危害回報 | ops | info | napsg | Hazard / General Hazards `MAAN`（!）🟢 | a-u-G-I | NCDR 災害潛勢（依類）|
| other 其他 | ops | info | ics | Human-Caused / Other `FAI` 🟢 | a-u-G | — |

### ✅ audit 修正（2026-06-03，對完整 NAPSG 庫；取代上方 draft 標記與舊統計）

> 套 §6 LOCKED 判準：先分軸（A/B/C 敵我 → 2525 milsymbol P2-05，**NAPSG 象形不適用**；D/E 民事 → NAPSG）；
> D/E 再判 🟢線稿可單色 / 🟡待視覺QA / 🔴色依賴 or 單位職位圖 or 無對應。
> ⚠ 本次為**名稱+類別層級**判定（未逐圖目視）；🟡 待用瀏覽器視覺 QA。

- **走 P2-05（非 NAPSG 象形）**：`drone`(A 敵)、`violent`(A 敵)、`unknown_person`(B 不明)、`qrf`(C 友/單位)
  —— 敵我/單位本質，框內用 2525 entity 符號（milsymbol 原生），不配 NAPSG glyph。修正舊表把這些當「NAPSG 待配」。
- **已落地 🟢（6，#75）**：explosive · comm_fail · hazard · evacuation · facility · rescue
- **ready 🟢（+2，本次新確認）**：
  - `perimeter` → `Incident/Barrier__No_Access`、`Access_Hazards/No_Access__Blocked`（barrier/禁入 線稿）
  - `infectious` → `Hazard/Biological_Hazard`（無字 biohazard trefoil；**非** `Class_6_2` 含字 placard，那個是 🔴）
- **🟡 待視覺 QA**：`isolation`(`Incident/Decontamination`)、`mci`(`Incident/Triage`；另受 §7-1「mci 是否屬 C 友軍」影響)、
  `facility` 變體(`Structural_Collapse`/`Do_Not_Enter_Structure`)、`capacity`(`Hazard/High_Occupancy_Numbers`)、`emergency`(醫療，多為單位圖)
- **🔴 維持 abbr**：`crowd`(單位圖)、`situation`(抽象/指揮)、`resource`(`Staging__*` 屬「who/資源」非 WHAT)、
  `other`(與 hazard 撞驚嘆號)、`equipment`(preplan 設施符號、細節多、40px 易糊)

**真實數字**：已 6 ＋ 穩 2 ＝ **8 個 🟢**；🟡 過 QA 上看 **~12**。舊表「剩 0–2 可加」**修正為 +2（穩）~ +6（含 QA 過關）**
—— 確比 0–2 多（完整庫覆蓋面廣），但非「+16」：近半型別本質是敵我(→2525)或單位/抽象(→abbr)，此為 LOCKED 分層必然。

> **本次只修文件**：glyph 本身（vendor SVG + 剝框上白 + 測試）待 **[#66](https://github.com/winson3QQ/ICS_COMMAND/issues/66) C2** 真做時一併處理；
> perimeter / infectious 為屆時最先可上的 🟢。

## 4. 台灣現況（2026-06-02 查證）

有平台、**無公開的統一 incident icon 標準**：
- [NCDR 3D災害潛勢地圖](https://dmap.ncdr.nat.gov.tw/)、[災防資料服務平台](https://datahub.ncdr.nat.gov.tw/)
- [內政部消防署 簡易疏散避難地圖](https://www.nfa.gov.tw/cht/index.php?code=list&ids=82)（含繪製教學/圖例）
- [全民防災 e 點通 防災圖台](https://bear.emic.gov.tw/MY2/map)

最接近的是 **NFA/NCDR「疏散避難地圖圖例」**（收容處所 / 避難方向 / 危險潛勢區）——
**偏民防疏散，非全面 incident 符號學**。

**啟示**：
- **收容 / 疏散類**事件（evacuation、capacity、person_need、isolation）→ 可對齊台灣 NFA 圖例（在地使用者更熟）。
- **操作型 incident**（安全威脅 / 搜救 / 行動）→ 台灣無對應，仍以 **NAPSG / CoT** 為主幹。
- 台灣標準偏「自然災害 + 疏散」，與我們偏「指揮操作」的 scope 互補而非重疊。

## 5. 業界做法 / Prior Art —— Dictionary Renderer 模式（2026-06-02 查證）

> CLAUDE.md：一定有先例，先找成功做法再 reinvent。查證結論：業界**不手畫、不逐筆指定符號**，
> 而是「採標準集 + 資料帶代碼 + 字典渲染」。我們的 crosswalk 表正是那本字典的雛形 → 方向正確。

### 核心模式
1. **資料 feature 帶「標準代碼」**（2525 的 SIDC、或 TAK 的 CoT type）+ 屬性。
2. **rule engine / 字典（如 Esri `stylx`）** 依代碼從**標準符號集**組裝符號（"symbol primitives + rule engine, assembled from attributes"）。
3. **採用現成標準集**（MIL-STD-2525 / APP-6 / NAPSG / OCHA），不自繪。
4. **互通靠代碼傳遞**（CoT / SIDC 在系統間流動），各端用自己的字典 render。

### 具體軟體
| 軟體 | 做法 |
|---|---|
| **Esri ArcGIS**（標竿）| **Dictionary Renderer** + 內建 2525B/C/D/E、APP-6、NAPSG style；資料填 `identity`/`symbolset` 或單一 SIDC → 自動 render |
| **TAK（ATAK/WinTAK）** | CoT 事件；`type`（如 `a-h-G`）→ 2525 符號；互通＝CoT XML 在網路傳 |
| **milsymbol**（JS, MIT；本專案 roadmap P2-05）| 吃 SIDC/CoT → 畫 2525/APP-6，**與 ATAK 同 type 畫同款符號（像素級一致）** |
| **NICS / NextGen ICS**（美國民事指揮）| 採 **NAPSG** 符號集 |
| **OCHA Humanitarian Icons** | 人道領域免費標準集（OSM-humanitarian / Sahana 採用）＝民間版 NAPSG |

### 對我們的意義
- 「taxonomy 主幹 + cot_type + source + Rosetta 對照」**就是 Dictionary Renderer 模式**，沒在 reinvent；`cot_type`/`source` 即字典 key。
- 業界**整套採用**標準集當字典、缺則擴充字典、不用文字 → 我們的中文 abbr 是權宜，應逐步以標準象形取代。
- **視覺要對齊哪個標準？**（已於 §6 落定為 affiliation-aware：A/B/C 走 2525 框、D/E 走 NAPSG）
  - **Path 1 — NAPSG 字典**：民事緊急視覺（FEMA/NICS 路線），key = 事件→NAPSG code。
  - **Path 2 — 2525 via milsymbol（key = `cot_type`）**：**與 ATAK 像素級一致**，對 TAK 整合最強；milsymbol 已在 P2-05。
  - 兩者皆 dictionary 模式，可**並存切換**（平時 NAPSG 民事、對接 TAK 時 2525）。

> 來源：[Esri Dictionary Renderer Toolkit](https://github.com/Esri/dictionary-renderer-toolkit)、[Esri Military Symbology Styles](https://developers.arcgis.com/documentation/mapping-and-location-services/data-visualization/resources/military-symbology-styles/)、[milsymbol](https://github.com/spatialillusions/milsymbol)（瑞典 MIT，非中國）。

### 5.1 字典擴充模型（標準 / 擴充 + degrade）

> 結論：2525 與 NAPSG **都可擴充**，機制不同；我們的 ICS 自訂事件＝合法的本地擴充。

- **2525 / APP-6**：SIDC 結構碼 + 通用框架（敵我框、維度套用任何 entity）；entity 目錄**可加自訂**
  （Esri dictionary-renderer-toolkit「add a new symbol set to MIL-STD-2525D」明示）；CoT type 可帶自訂 suffix。
- **NAPSG**：保留形狀框架（◆/▲）+「Symbols are variable other than shape」→ **自畫內部象形塞進保留形狀仍合規**；官方 set 不可改但本地可擴充。
- **我們的模型即此**：`source`（napsg=標準 / ics=擴充）標示「標準 vs 本地擴充」；字典 = {標準符號} ∪ {ICS 自訂}。
- **⚠️ 擴充代價**：自訂符號**只在本系統有完整語意**（外部無我們字典看不懂）→ 故每個事件（含 ics）都掛
  **最近的標準 `cot_type`**，外部系統至少能 degrade 畫出近似符號（如 `a-u-G` 不明地面）。

## 6. 視覺顯示模型（依使用情境）+ 事件建立流程（2026-06-02 ✅ LOCKED）

> **修正**：原 §5 的「Path 1（全 NAPSG ◆ + severity）」**砍掉了敵我/維度** —— 對「民防含軍事支援任務」不行
> （敵無人機 vs 友 QRF 在地圖上會長一樣，敵我只活在 `cot_type`、要開 ATAK 才看得到）。故修正為
> **affiliation-aware**：敵我用 2525 框、類型用 NAPSG 象形、severity 用 halo。

### ✅ LOCKED 決策（2026-06-02，三模型×極端案例實渲染對照後定案）

經「現狀（單色+剝框）/ NAPSG verbatim（原框原色）/ 2525 affiliation」三模型 × 極端案例（敵我區分、顏色失義、撞號、混場一致性）本地實渲染對照後**定案**。後續 audit / glyph 擴充 / [#66](https://github.com/winson3QQ/ICS_COMMAND/issues/66) C2 一律以此為準：

1. **民事 D/E → 現狀模型「單色 + 剝框」**：我們的 ◆(hazard) / ▲(public-alert) 框 + severity 單一色 + 框內**白色**象形。**NAPSG = 象形來源，非渲染模型**——只借框內象形，**剝掉**其原生框與原生色。
2. **敵我 A/B/C → 2525 affiliation 框**（milsymbol 吃 `cot_type` 生，**P2-05**）；落地前 A/B/C 可暫用 NAPSG + severity 過渡。
3. **剝框是「轉接頭」不是妥協**：被剝的（NAPSG 原生框＋原生色）正是我們不要的——框＝敵我、色＝severity，由我們權威控制；剝框讓 NAPSG 象形服貼 2525 文法底盤、與 A/B/C 視覺一致（同一套「框＋色＋框內象形」文法）。

**為何不採 verbatim**：對接靠 `cot_type`（資料）不靠像素 → verbatim 視覺對互通**零貢獻**；且會打掉「severity 獨佔顏色通道」、與 2525 框混場（兩套文法、要學兩套圖例）。**唯一真實取捨 = 靠顏色才成立的符號 → 退 abbr**。

**NAPSG 象形擴充 audit 合格標準**（本模型直接推論；供 §3 重評與 glyph 擴充判定）：
- 🟢 **線稿 / 剪影型**，剝框上白在 ~40px 仍可辨（爆炸 / 結構 / 塔 / 火 / 水 / 生物 trefoil…）→ 可配 glyph
- 🟡 細節多或半依賴色 → 標「待視覺 QA」
- 🔴 **靠色才成立**（hazmat 色碼牌）/ **單位·職位圖**（NIMS_Positions、Resources，違反「符號只講 WHAT」）/ 無乾淨對應 → 維持 abbr

> 參考工具：完整 NAPSG 庫（1301 unique 符號）瀏覽器見 `command-dashboard/static/napsg_browser.html`，audit 逐型別對照時用它當眼睛。

### 統一原則（三通道不互搶）
- **敵我（affiliation）→ 外框形狀**（2525：友=矩形 / 敵=菱形 / 不明=四葉 / 中立=方或圓）
- **事件類型 → 內部象形**（NAPSG glyph）
- **嚴重度 → halo / 角標**（NAPSG 色 ramp，當 secondary modifier，不跟敵我框搶顏色）
- **對接 → `cot_type`**（全程帶，與視覺無關）

### 顯示情境表

| # | 情境 | 範例事件 | 敵我 | 地圖顯示（框 + 象形 + 嚴重度）| cot_type | 標準 |
|---|---|---|---|---|---|---|
| A | 敵對威脅 | 敵無人機、爆裂物、暴力 | 敵 | 2525 敵框（菱形）+ 象形 + critical halo | `a-h-*` | 2525/APP-6 |
| B | 不明目標 | 不明人士、可疑載具 | 不明 | 2525 不明框（四葉）+ 象形 + severity halo | `a-u-*` | 2525/APP-6 |
| C | 己方 / 友軍應處 | QRF、受困救援、醫療出動 | 友 | 2525 友框（矩形）+ 象形 + severity halo | `a-f-*` | 2525/APP-6 |
| D | 公眾警報 | 撤離、就地避難 | （對民眾）| ▲ 三角（IPAWS/NAPSG）+ 象形 + severity | `b-a-*` | NAPSG/IPAWS |
| E | 民事危害 / 事故 | 設施、通訊、危害回報、傳染、收容超載 | 中立 | ◆ 危害菱形（NAPSG）+ 象形 + severity 色 | `b-r`/`a-u-G-I` | NAPSG incident |

A/B/C（有敵我）走 2525 框（milsymbol 吃 `cot_type` 生，P2-05）；D/E（民事）走 NAPSG。情境由 `cot_type`
前綴（`a-h`/`a-f`/`a-u` vs `b-*`）**自動分流**。

### ⚠️ 內部象形（框內那個圖）有兩個來源，別混
- **民事 D/E（NAPSG ◆/▲）→ 內部用 NAPSG 象形** ←「NAPSG 定義好的 symbol」即此。工作 = **NAPSG 象形字典擴充
  + admin icon picker**，**歸 [#66](https://github.com/winson3QQ/ICS_COMMAND/issues/66) C2**（不是孤兒 follow-up）；P1-10d 已落地 6 個強配。
- **軍事 A/B/C（2525 框）→ 內部用 2525 entity 符號**（milsymbol 原生）←**非** NAPSG 象形。工作 = **P2-05**。
- 兩套 icon 來源不同：NAPSG 服務民事、2525/milsymbol 服務軍事；切換由情境（cot_type 前綴）決定。

### 事件建立流程（type-first，不是 scenario-first）

> 分類主軸＝**事件型別**（operator 想「是什麼事」，高壓下最快）；**敵我是正交屬性**，多由型別自動帶、
> 少數現場可改。**不重組 taxonomy、不改操作員心智模型。**（2525/ATAK 之所以敵我優先，是軍事戰場 COP；
> 我們是民事 ICS，incident-first 才對。）

1. 長按地圖 → EventPopup
2. **選分類 → 選事件型別**（不變）
3. 型別自動帶：severity、`cot_type`、**敵我預設**、象形
4. **僅「敵我會變」的型別**（無人機、不明人士、可疑載具）→ 多一條快速 **友 / 敵 / 不明** segment
   （預設已帶，通常不動）；其餘型別不問
5. 回報組（自動可改）、位置（長按點）→ 建立
6. 地圖依上表 render（敵我→框、型別→象形、severity→halo）

UI：EventPopup 維持「分類 → 型別」下鑽，僅對「敵我可變」型別多一條 affiliation segment；**95% 事件步驟不變**。

## 7. 待決策（殘餘子細項；渲染模型本身已於 §6 ✅ LOCKED）

1. **哪些型別屬「敵我可變」**（需 affiliation segment）：初判 drone / unknown_person /（可疑載具）；其餘固定。待逐一確認。
2. ✅ **milsymbol（2525 框）整合時機 = P2-05**（§6 LOCKED 確認）：A/B/C 的實作主力；落地前 A/B/C 暫用 NAPSG + severity 過渡。
3. **是否新增 `tw_ref` 欄** + 是否抓 NFA 疏散避難圖例（收容/疏散在地對齊）。
4. **severity 是否補 Purple=Extreme**（NAPSG 7 級我們用 3）。

## 8. route / polygon（線 / 面）符號 — 現況 ad-hoc，標準對齊屬 P2（2026-06-03 釐清）

> §6 LOCKED 渲染模型**只管「事件點」**（框＋象形＋severity）。route（線）/ polygon（面）是另一類，
> 既不在 §6 範圍、也不對 NAPSG（NAPSG 是點事件符號學，不規範線/面）。本節記錄現況與未來標準。

### 現況：app 自訂、寫死 hex、無標準對齊
- `static/js/map.js` `POLY_TYPES`（5：管制區 / 疏散範圍 / 集結點 / 危險區域 / 作業區）、
  `ROUTE_TYPES`（3：主要 / 次要 / 緊急）顏色為**寫死 hex**（`#e05555` 等 GitHub-dark 系），
  dash 與否逐型別硬編。
- **不對任何外部標準**（非 NAPSG、非 2525）。
- **違反 POLICY doctrine**：寫死 hex + 非 token；POLICY「唯一 saturated 色 = MIL affiliation
  + severity token」對線/面**尚未落實**——pre-existing 缺口，早於 token 紀律。

### 對應的外部標準 = MIL-STD-2525 Tactical Graphics（control measures）
- **route ≈ axis of advance / direction**（帶箭頭的軸線）；**polygon ≈ area control**
  （集結區 / 危險區 / 管制區…）。配色依 affiliation、線型有規定。
- 走 TAK CoT（`g-*` graphic types）互通——與事件點靠 `cot_type` 對接同理。
- **歸 P2（P2-05）**：線/面對齊 2525 tactical graphics + 走 design token 的正規化，留待 TAK
  整合時做。**P1 階段維持 app 自訂、可自由調整**（線寬 / 箭頭等純美術 tweak 不受標準綁）。

> 本檔記錄框架與落定方向；§3 Rosetta 的 NAPSG icon 欄、§6 的 milsymbol 實作與 §7 細項待後續定案更新。
