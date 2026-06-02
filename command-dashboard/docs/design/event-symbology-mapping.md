# 事件符號設計 — NAPSG 對齊 + TAK/CoT 預備（P1-10d 設計提案）

> 落定 2026-06-02。P1-10d（事件視覺）+ taxonomy 資料化地基 + 編輯器（[#66](https://github.com/winson3QQ/ICS_COMMAND/issues/66)）的共同設計依據。
> 為何不自創符號：業界已有標準，對齊可省 P2 重工。見〈參考來源〉。

## 決策（2026-06-02）

| # | 決策 | 選擇 |
|---|---|---|
| icon 風格 | ③ **混合**：◆ diamond + NAPSG 象形 icon（符號內），abbr 中文移到符號**外**（下方 label） | locked |
| 可編輯範圍 | **事件 + 群組全 CRUD**（admin）→ taxonomy 需資料化 | locked → #66 |
| severity 級別 | **固定 3 級**（critical/warning/info），對齊 NAPSG 色 + token；只可指派不可新增級別 | locked |
| 排程 | **地基（資料化）+ 視覺（P1-10d）先；編輯器另開 #66** | locked |

## NAPSG 框架（v4.0，已查證）

- **形狀 = 類別**：**◆ DIAMOND = emergency hazard**（任何可能造成傷害/社會擾動/阻礙進出的危害位置）= 我們的「事件」。**▲ TRIANGLE = public alert/warning**（公眾警報/行動呼籲，如撤離令）。
- **色彩 = severity（次要 modifier，非必要）**，NAPSG 標準 hex ramp：
  | 色 | hex | 意義 |
  |---|---|---|
  | Green | `#00AC3A` | 低 |
  | Blue | `#237ACF` | 中 |
  | Yellow | `#FFD718` | 中高（Watch） |
  | Orange | `#FF8918` | 高（Warning） |
  | Red | `#FF181E` | 極高 |
  | Purple | `#ED1AFC` | 超出紅 |
  慣例：Green=Good、Red/Purple=Bad、Yellow/Orange=warning。
- **符號內避免文字**（除非廣為接受的標準符號）→ 故 abbr 中文移到符號外（混合風格的依據）。

## severity token（採 NAPSG hex，加進 ds-tokens.css）

```
--severity-critical: #FF181E;  /* NAPSG Red    — 極高 */
--severity-warning:  #FF8918;  /* NAPSG Orange — 高/警告 */
--severity-info:     #237ACF;  /* NAPSG Blue   — 中/一般 */
/* 保留：#FFD718 未來 watch 中間級、#00AC3A 已解除/狀態良好 */
```
取代現況散落的 `--red`/`--yellow` alias 與 JS 寫死 hex（doctrine：saturated 色 = MIL affiliation token + severity token）。

## 混合 icon 風格規格

- 每事件 = **◆ diamond 外框**（NAPSG hazard）+ **NAPSG 象形 icon（灰階 SDF，icon-color = severity 色）** + **critical 事件 `@keyframes` halo pulse**（純 CSS，省 Pi CPU）。
- **abbr 中文**作為 label 顯示在符號**下方**（沿用既有 label marker 層），不放圈內。
- icon 來源：vendored **NAPSG icon 庫**（自訂型別從庫挑，不自畫；離線打包）。
- 沿用 P1-10b 已備的 SDF pipeline（`bakeSdfIcon` + `icon-color` data-driven）+ 4-layer state stack（base/glow/selected/halo）。

## 事件 ↔ NAPSG ↔ severity 色 ↔ CoT bucket

> CoT 欄為 **affiliation+dimension bucket（現可定）**；**精確 function suffix 對 TAK 官方 `cot-types.xml` 在 P2-04 定稿**（不杜撰未驗證碼）。severity 色 = 對應 `--severity-*` token。

| 群組 | key | label | severity | 形狀 | CoT bucket（P2-04 細化） |
|---|---|---|---|---|---|
| security | explosive | 疑似爆裂物 | critical | ◆ | `a-h-G`（→ 2525 EM Explosion） |
| security | drone | 無人機威脅 | critical | ◆ | `a-h-A`（hostile air/UAV） |
| security | violent | 暴力事件 | critical | ◆ | `a-h-G` |
| security | unknown_person | 不明人士 | warning | ◆ | `a-u-G` |
| security | perimeter | 管制區異常 | warning | ◆ | `b-a-g`（area/geofence alert） |
| security | crowd | 秩序問題 | warning | ◆ | `a-n-G`（civil disturbance） |
| rescue | rescue | 受困救援 | warning | ◆ | `a-f-G` / `t-`（SAR tasking） |
| rescue | qrf | QRF 出動 | warning | ◆ | `a-f-G`（friendly unit） |
| medical | mci | 大量傷亡 | critical | ◆ | `a-u-G-I`（EM mass-casualty） |
| medical | emergency | 緊急病症 | critical | ◆ | `a-f-G-U-U-M`（medical） |
| medical | infectious | 傳染疑慮 | warning | ◆ | `a-u-G-I`（CBRN-bio） |
| care | capacity | 量能超載 | warning | ◆ | `b-r`（area/status） |
| care | isolation | 隔離事件 | warning | ◆ | `a-u-G-I` |
| care | person_need | 人員狀況 | info | ◆ | `a-f-G` / `b-r` |
| infra | comm_fail | 通訊異常 | warning | ◆ | `b-r`（notice） |
| infra | facility | 設施異常 | info | ◆ | `a-u-G-I` |
| infra | equipment | 設備故障 | info | ◆ | `b-r` |
| ops | evacuation | 撤離 | warning | **▲ alert** | `b-a-*`（call-to-action） |
| ops | resource | 資源調度 | info | ◆ | `t-`（tasking） |
| ops | situation | 現場變化 | info | ◆ | `a-u-G-I` |
| ops | hazard | 危害回報 | info | ◆ | `a-u-G-I` |
| ops | other | 其他 | info | ◆ | `a-u-G` |

## 資料化地基（P1-10d 前置，#66 依賴）

沿用 P1-13 seed/runtime 模式：
- `static/event_taxonomy.seed.json`（tracked，factory default）
- `data/event_taxonomy.json`（gitignored，runtime）
- `GET/POST /api/event_taxonomy`（POST admin-only）+ startup ensure() 從 seed 兜底
- 每型別欄位：`{ key, label, group, icon, abbr, severity, defaultAssigned, cot_type }`；群組：`{ key, label, order }`
- **收斂重複**：`events.js NAPSG_EVENTS` 與 `map.js`（partial copy）併為讀 config 的單一 SoT。

### 雷（務必處理）
1. **重複定義先收斂**（events.js / map.js）→ 否則編一邊另一邊不動。
2. **參照完整性**：DB events 用 `event_type` key → 刪/改名走 **soft-delete + key 穩定**，不孤兒。
3. **CoT for 自訂型別**：新型別必填 CoT bucket（預設 `a-u-G`）→ 否則 P2 TAK 無法表示。
4. **severity 級別固定**：3 級對齊 token；只可指派。
5. **icon 庫**：自訂型別從 vendored NAPSG icon 庫挑。

## 決策的拆解

1. **地基**：taxonomy 資料化（seed/runtime + API + 收斂重複）
2. **視覺（P1-10d）**：讀 config 渲染 ◆ + NAPSG icon + severity 色 + critical pulse + severity token
3. **編輯器（[#66](https://github.com/winson3QQ/ICS_COMMAND/issues/66)）**：admin CRUD 事件/群組

## 事件資料模型：三軸正交 + ICS 組織 + TAK 對接（2026-06-02 落定）

> 動機：要跟既有系統（TAK）有共同語言，同時 ICS 內部有「事件類別 / 回報組 / 處理組」多個「組」概念被混在一起（連地圖符號、右側欄分類都受影響）。釐清軸線後整合方向才一致。

### 三條正交軸（不要混）

| 軸 | 是什麼 | 現有欄位 | 驅動 |
|---|---|---|---|
| **WHAT — 事件類型/類別** | 發生了什麼（爆裂物/醫療/火災）| `event_type` + `group`(類別) | **地圖符號** + **TAK 共同語言** |
| **誰回報** | 哪個 ICS 組通報 | `reported_by_unit` | 來源 provenance（次要）|
| **誰處理** | 指派哪個 ICS 組 | `assigned_unit`（taxonomy `defaultAssigned`）| 任務指派（次要）|

外加 **severity**（顏色 token）、**status**（open / in_progress / resolved 生命週期）。

### 整合原則（與 TAK 共同語言）

1. **地圖符號 = WHAT（事件類型）** → NAPSG glyph / `cot_type`。**這條才是對接外部系統的橋**：對方 ATAK 讀 CoT type 就懂「這是爆裂物」，不需要懂我們的「安全組」。
2. **ICS 組織（回報 / 處理）= metadata 屬性**，不是符號本體。在 2525/CoT 是 amplifier / `<detail>`（掛符號旁的文字/欄位），不進核心符號；地圖上當次要線索（label、右側欄 pivot、選配小角標）。
3. **`group`（類別）= 從 type 衍生的分組**，給篩選 / 右側欄用，不是獨立第三軸，也不當符號。
4. **severity → 顏色 token；status → CoT stale / 生命週期。**

一句話：**符號只講「是什麼事件」（對 TAK）；「誰報 / 誰處理 / 哪一組」是掛旁邊的屬性，給人看 + 右側欄分類用。**

### 右側事件欄：pivot 分組

三軸正交 → 右側欄應支援**切換分組維度**（by 類別 / by 處理組 / by status），而非寫死一種。同一批事件，指揮官要「看安全組要處理什麼」就 by 處理組；要「看有哪些爆裂類」就 by 類別。

### ⚠️ 命名解撞名（關鍵 cleanup）

事件**類別** `security` / `medical` ≠ ICS **組織單位** 安全組 / 醫療組（`node_type`）。兩者目前撞名、且地圖 marker 誤用 `node_type` 當類別 abbr。整合時必須**明確分開**：事件類別 key（NAPSG category）與 ICS 組織 unit key 各自一張表，**不再共用 `node_type`**。

### TAK 對接對照

| ICS 概念 | CoT/TAK 落點 |
|---|---|
| 事件類型 | **CoT `type`**（`cot_type`，已在 seed；P2-04 細化）← 共同語言 |
| severity | `--severity-*`（CoT 無直接對應，當 detail）|
| 回報組 / 處理組 | CoT `<detail>` 自訂欄位 / TAK 指派（P2-04）|
| status | CoT stale / 生命週期 |

### 衍生工作（ROADMAP 追蹤）

1. **符號改依 event type（非 group）** — P1-10d 收尾 / PR-2b-3（NAPSG 象形 glyph；中間步可先用事件 abbr）。
2. **#66 編輯器：事件類別表 / ICS 組織表分開**（解撞名）。
3. **右側欄 pivot 分組**（by 類別 / 處理組 / status）— 獨立小 item。
4. **report / handle → CoT `<detail>`** — P2-04 TAK。

## 參考來源
- [NAPSG Incident Symbology Framework & Guideline v4.0](https://www.napsgfoundation.org/wp-content/uploads/2020/03/NAPSG-Foundation-Incident-Symbol-Guideline_v4.0_03212020.pdf)（US DHS 背書；非中國）
- [About MIL-STD-2525 and CoT — FreeTAKServer Docs](https://freetakteam.github.io/FreeTAKServer-User-Docs/About/architecture/mil_std_2525/)（CoT type ↔ 2525 對映）
- MIL-STD-2525（US DoD）/ APP-6（NATO）；milsymbol（瑞典 MIT，P2-05 渲染）。全鏈非中國（CLAUDE.md）。
