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
> 🔴 無乾淨對應）。是否放寬「事件 vs 單位」潔癖、把 🟡 也納入，待決策（見 §6）。
> cot_type / source 來自現行 seed；台灣欄僅在民防疏散/收容類有對應，其餘留空。

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

**現況統計**：🟢 強配 8（已實作 6：explosive/comm_fail/hazard/evacuation/facility/rescue；other/?）、
🟡 勉強 ~8、🔴 無乾淨 ~6。其餘以 abbr 顯示（中間步）。

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
- **視覺要對齊哪個標準？**（與 §6.1 的 A/B/C 連動）
  - **Path 1 — NAPSG 字典**：民事緊急視覺（FEMA/NICS 路線），key = 事件→NAPSG code。
  - **Path 2 — 2525 via milsymbol（key = `cot_type`）**：**與 ATAK 像素級一致**，對 TAK 整合最強；milsymbol 已在 P2-05。
  - 兩者皆 dictionary 模式，可**並存切換**（平時 NAPSG 民事、對接 TAK 時 2525）。

> 來源：[Esri Dictionary Renderer Toolkit](https://github.com/Esri/dictionary-renderer-toolkit)、[Esri Military Symbology Styles](https://developers.arcgis.com/documentation/mapping-and-location-services/data-visualization/resources/military-symbology-styles/)、[milsymbol](https://github.com/spatialillusions/milsymbol)（瑞典 MIT，非中國）。

## 6. 待決策（對照清楚後再定）

1. **NAPSG icon 採用程度**（見 [`event-symbology-mapping.md`] 已列 A/B/C；對照 §5 Path 1/2）：
   - A 維持現渲染（severity 填色菱形）+ 放寬潔癖、把 🟡 也配上 NAPSG 單色象形，減少 abbr。
   - B 直接用 NAPSG 原版 icon（含其色彩/框），失去統一填色。
   - C 維持現狀（6 glyph + abbr）。
2. **是否新增 `tw_ref` 欄**：給收容/疏散類掛台灣 NFA 圖例對應。
3. **是否抓 NFA 疏散避難圖例實際符號**做在地對齊（需另查 NFA 繪製規範 PDF）。
4. **severity 是否補 Purple=Extreme**（NAPSG 7 級我們只用 3）。

> 本檔為「先記錄框架」；§3 表的 NAPSG icon 欄與 §6 決策待後續逐項定案後更新。
