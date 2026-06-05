# P1-10 地圖 UX baseline 細項 + P1-11 Dashboard PWA 清理

> 本文從 `docs/ROADMAP.md` 抽出，為獨立設計參考文件。狀態以 ROADMAP.md 為準。

---

## P1-10 細項展開（地圖 UX baseline）

**設計原則**：指揮中心的「專業感」90% 來自 chrome（sidebar / chip / legend / 字級），不是地圖本身——dark ops theme 是最大 CP 值的單一改動。地圖核心則一次到位（MapLibre + PMTiles），不走 Leaflet 折衷以免 P2 二次手術。

| 子項 | 內容 | 預估 |
|---|---|---|
| ✅ **P1-10a** | **採用 WaveInk Design System + MIL-STD-2525 token 體系 + 跨平台等寬字體**（**一次到位，不分階段**，TAK 整合就緒為優先設計約束）：<br>**(i) 基底**：vendor `colors_and_type.css` + `DESIGN.md` policy 自 WaveInk `docs/design/`（commit hash 釘版），落地為 `command-dashboard/static/css/ds-tokens.css` + `docs/design/POLICY.md`。直接繼承：GitHub Dark Dimmed 色階、`--space-1`~`--space-9`、`--radius` 三段、border-not-shadow 紀律、動畫節制（僅保留 keyframe，不引入 JS 動畫）、Unicode-as-iconography（禁 Material/Heroicons/Lucide/Phosphor/Font Awesome）、empty-state 文案語氣（terse / imperative / 無 marketing copy）。<br>**(ii) MIL-STD-2525 entity color token 作為一等公民**（**主要設計考量，非例外**）：新增 `--mil-friendly` / `--mil-hostile` / `--mil-neutral` / `--mil-unknown` token，標準色對齊 MIL-STD-2525C 附錄 A；frame 形狀（friendly 矩形 / hostile 菱形 / neutral 方形 / unknown 四葉草）由 P2-05 milsymbol 接管渲染，token 提供色彩 SoT。WaveInk policy 的「No new accents」原則於本 token group 例外開放，並反向標註：未來新增 entity affiliation 必須對齊 MIL-STD-2525，不得自創色。<br>**(iii) 字體 ops-grade upgrade**：**JetBrains Mono 離線打包進 `static/fonts/`** 取代 WaveInk 的 system mono 預設——指揮場景 callsign / 座標 / MGRS / 時間戳跨 Mac/Win/Linux 一致性是 ops 規範（system mono 在三平台分別是 Menlo / Consolas / DejaVu，視覺差異不可接受）；JetBrains Mono 對 0/O、1/l/I、5/S 有明確 disambiguation 設計。UI 文字維持 WaveInk system stack（Noto Sans TC fallback）。<br>**(iv) divergence 文件**：`docs/design/POLICY.md` 明文列出 ICS_Command 對 WaveInk DS 的兩處 fork 點（MIL-STD-2525 token、JetBrains Mono webfont）與 rationale，下次 WaveInk DS 升版時做為 conflict 解決依據 — 完成於 [#18](https://github.com/winson3QQ/ICS_COMMAND/pull/18) `1d46025`（2026-05-26）| 3-4 天 |
| P1-10a-2 | **DS token migration follow-up**（P1-10a 收尾遺留）：js/ 126 處硬寫 hex + commander_dashboard.html 68 處 inline `style=` + 其他 4 HTML 檔（scenario_designer / admin_backups / icon_preview / qr_scanner）token 對齊。**動工前 reality check 已做**（[#18 comment](https://github.com/winson3QQ/ICS_COMMAND/pull/18) + 2026-05-26 session）：51 hex 屬 in-DS 可對映、其中 41 在 JS object/canvas context 不能直 `var()` 替換（要 `getComputedStyle` helper）、102 hex 屬 out-of-DS semantic shades 需 design decision（加進 DS / 留 inline + 註解 / color-mix）。**故意延後到 P1-10b 之後**：map.js 49 hex（佔總量 39%）會在 P1-10b MapLibre 重寫時大部分消失，現在動 = 白工 53%。<br>**[2026-06-03 reality check #2 — scope 不減反增]**：P1-10b 拆了 Leaflet，但 **P1-10d/P1-16/P1-17 又加新硬寫 hex**。實測殘量：**JS hex ~199** + **HTML inline `style=` ~107**。大量在 MapLibre paint 表達式 / canvas SDF / SVG 裡**不能直接 `var()`**，加上 out-of-DS semantic 色需 design decision → **非機械 find-replace，需 `getComputedStyle` helper + 設計決策**。建議：等 DS 改版 / 加主題時再做，屆時連 helper 一起設計。 | 需 helper + 決策，獨立 scoped task |
| ✅ P1-10b | **MapLibre GL JS 全替換 + entity layer 精緻化基底**（**14/14 done，完成於 2026-06-01**）— [PR #22](https://github.com/winson3QQ/ICS_COMMAND/pull/22) `d3e9386`~`418a65a`、[#23](https://github.com/winson3QQ/ICS_COMMAND/pull/23) `1344913`、[#30](https://github.com/winson3QQ/ICS_COMMAND/pull/30) `a0d56c8`；step 14：**1000-entity FPS benchmark 60 FPS PASS**（avg 60.1 / median 59.9 / p95 59.9，遠超 DoD ≥30；2026-06-01 真實 Chrome 實測）。移除 Leaflet，EntityLayer 抽象（GeoJSON source + 批量替換）、4-layer state stack、SDF icon + `icon-color` data-driven（P2-05 鋪路）、`setFeatureState` hover/selected、`symbol-sort-key` priority、`text-halo` + JetBrains Mono、LOD。**達成 T2 基底** | 6-8 天 |
| ✅ **P1-10c** | **PMTiles 台灣底圖 + 戰術底圖 doctrine 落地** — 2 套 vector style（`dark` / `muted-day`）；底圖 doctrine 寫進 `docs/design/POLICY.md`；完全離線。✅ 完成（2026-06-01）：[#57](https://github.com/winson3QQ/ICS_COMMAND/pull/57) `9ccf190`、[#59](https://github.com/winson3QQ/ICS_COMMAND/pull/59) `632703d`、[#61](https://github.com/winson3QQ/ICS_COMMAND/pull/61) `ab2fbcd`、[#63](https://github.com/winson3QQ/ICS_COMMAND/pull/63) `fe84e6e`；白天 overlay 對比 follow-up [#95](https://github.com/winson3QQ/ICS_COMMAND/pull/95) `5653010`（2026-06-04）。**達成 T2 完成** | 1-2 天（重 build 則 3-5 天）|
| ✅ **P1-10d** | **事件符號系統（NAPSG 對齊）+ severity token + taxonomy 資料化** — ◆ diamond + NAPSG 象形 icon + abbr；severity NAPSG 標準 hex token（critical / warning / info）固定 3 級；taxonomy 資料化（`event_taxonomy.seed.json` + `/api/event_taxonomy`）。✅ 完成（2026-06-02）：taxonomy [#68](https://github.com/winson3QQ/ICS_COMMAND/pull/68) `8c3fb37`／前端+收斂 [#69](https://github.com/winson3QQ/ICS_COMMAND/pull/69)／視覺 [#70](https://github.com/winson3QQ/ICS_COMMAND/pull/70) `d8c13ab`；NAPSG glyph 第一批 [#75](https://github.com/winson3QQ/ICS_COMMAND/pull/75)；解撞名 [#77](https://github.com/winson3QQ/ICS_COMMAND/pull/77)；編輯器 [#76](https://github.com/winson3QQ/ICS_COMMAND/pull/76)/[#78](https://github.com/winson3QQ/ICS_COMMAND/pull/78)/[#79](https://github.com/winson3QQ/ICS_COMMAND/pull/79)。**剩 follow-up**：其餘 NAPSG 象形擴充 + icon picker → [#66](https://github.com/winson3QQ/ICS_COMMAND/issues/66) C2 | 3-4 天 |
| ✅ **P1-10e** | **Polygon / route hover + selected 狀態** — hover: outline 加粗 + fill opacity 微升；selected: 虛線外框 + opacity 呼吸。走 feature-state + paint case — 完成於 `79561a7`（2026-06-03）|
| ~~**P1-10f**~~ ❌ **descoped** | **Symbol-layer-based clustering** — 移除（2026-06-03）：COP 量級不大、clustering CP 值低，且與 critical 事件永遠可見衝突。決策見 `79561a7` commit |
| ✅ **P1-10g** | **圖層切換微動畫** — 200ms fade-in（EntityLayer.setVisible 可見性改變時 opacity 過渡）— 完成於 `79561a7`（2026-06-03）|
| ✅ **P1-10h** | **CSP enforce** — `CSP_MODE` 預設 report-only → enforce（`ENFORCE_PATHS`/commander 實際擋）；MapLibre worker + PMTiles range 已涵蓋；補 integration test 鎖定 directive — 完成於 `f1a3f3f`（2026-06-03）|

**總工時估**：13-18 天（單人）。

**視覺等級 ladder（2D only，無 3D，全程 MapLibre）**：

| 等級 | 對標 | 達成時點 |
|---|---|---|
| T0 | 現狀（Leaflet + grayscale + 預設 marker）| — |
| T1 | dashboard chrome only（Protomaps Dark 裸用）| P1-10a 完成 |
| **T2 基底** | 戰術 marker（4-layer state stack + SDF + hover state + LOD）| **P1-10b 完成** |
| T2 完成 | + dark/muted-day 底圖 doctrine 落地 | P1-10c 完成 |
| T2.5 | + critical halo pulse + severity icon 系統 | P1-10d 完成 |
| **T3**（ATAK Web / Felt 級）| + polygon/route hover-selected + 切換動畫 | ✅ P1-10e + 10g 完成（2026-06-03）|

**實作順序**（TAK 就緒度為優先排序原則）：
1. ✅ P1-10a — chrome T0→T1
2. ✅ **P1-10b** — 地圖 T0→T2 基底；**為 P2-04/05 鋪路**（SDF + EntityLayer + source×affiliation 雙維度）
3. ✅ P1-10c — 底圖 T2 完成
4. P1-10a-2 — DS migration follow-up（P1-10b 後重評殘餘）
5. ✅ P1-10d — T2→T2.5
6. ✅ P1-10e + P1-10g — T3（P1-10f clustering descoped）
7. ✅ P1-10h — T3 鎖緊（安全層）

**為 P2 鋪路的明確成果**：

| 鋪路項 | 落在 | P2 受惠 | 不做的後果 |
|---|---|---|---|
| SDF icon + `icon-color` pipeline | P1-10b | P2-05 milsymbol 直接套 | P2-05 多 2-3 天 |
| EntityLayer 抽象（多 source 合併 hook）| P1-10b | P2-04 TAK CoT push 直接套 | P2-04 多 2-3 天 |
| Source × affiliation 雙維度註解 | P1-10b | P2-04 schema 不誤用 | 撞牆才改 schema |
| 4-layer state stack | P1-10b | P2-04 affiliation × status 顯示 | P2 重寫 entity layer |
| 戰術底圖 doctrine in POLICY.md | P1-10c | 拒絕「加 sat 預設」反 doctrine 要求 | 沒 SoT 可引用 |
| CSP `wasm-unsafe-eval` enforce | P1-10h | 任何 wasm 工具（含 milsymbol wasm fallback）已備 | P2 補 CSP |

**P1-10 DoD**：
- [ ] Lighthouse Performance score ≥ 80（Pi 500 上）
- [ ] 1000 個 entity 同時渲染 FPS ≥ 30
- [ ] 主題切換無 FOUC（flash of unstyled content）
- [ ] CSP test green，無 `unsafe-inline` 例外
- [ ] PMTiles 離線（網路全斷）下地圖完整可用
- [ ] **戰術底圖 doctrine 寫入 `docs/design/POLICY.md`**：底圖必須 desaturated；唯一 saturated 色 = MIL-STD-2525 affiliation + severity token
- [ ] mini-taiwan 7 條反例 — 6 條避雷 checklist 全套用、2 條視覺反例（hover state、symbol-sort-key）已修正

**明確排除（移到 P2）**：
- **MGRS grid** — 軍規 grid 屬 TAK / MIL-STD-2525 同一生態，自然該與 CoT 符號渲染一批做
- **3D entity layer** — 等真實山地 / 空域需求出現再評估
- **Mapbox Standard 3D 建物 + lightPreset** — 需網路 + 商業 SDK，違反 P1-10 DoD「PMTiles 離線」紅線

---

## P1-11 範圍（Dashboard UI PWA 清理）

於 P1-01 dogfood 階段盤點出的 PWA-specific UI 元素，分四級處理：

**Tier 1 — 純 PWA，直接刪**

| 元素 | 位置 |
|---|---|
| `cd-shelter` / `cd-medical` 連線燈（頂部「收容 離線 / 醫療 未連線」）| `commander_dashboard.html:867-868` |
| 量能 chart legend「收容量能 / 醫療量能」 | `commander_dashboard.html:899-900` |
| 「傷患入站」chart 整段 | `commander_dashboard.html:908` 起 |
| `capacity` event defaultAssigned = `'shelter'` | `static/js/events.js:45` |

**Tier 2 — 硬編碼 shelter+medical 數學，需 refactor**

| 元素 | 位置 | 改法 |
|---|---|---|
| Zone A 量能狀態燈計算（`sp`/`mp` 二元） | `static/js/cop.js:331-358` | 改成依 `pi_nodes` 動態 unit 算（fed-ready）或暫 placeholder |
| Zone C「物資見底/容量飽和/人力超載」KPI 資料源 | `commander_dashboard.html:1080-1090` + `cop.js` renderZoneC | 同上模型，refactor |
| `cop.js:162` event labels 含 PWA 場景 | `static/js/cop.js:162` | 可清掉 `medication_mgmt` 等 |

**Tier 3 — 周邊工具保留**

- `static/icon_preview.html`（22 KB）— 設計資產 catalog，對未來 TAK / WaveInk icon set 有參考價值
- `static/scenario_designer.html`（52 KB）— TTX 場景設計工具，沒 PWA 不影響運作

**不在本 item scope（移其他 P1）**

- 「地圖載入中」卡住 → P1-10 map UX baseline
- 規格書同步移除 PWA 章節 → P1-07 規格書 v3.0

### P1-11 DoD

- [ ] Tier 1 四項移除，dashboard 載入無 console error
- [ ] Tier 2 三項重構為依 `pi_nodes` 動態渲染或顯式 placeholder（不再寫死 shelter+medical）
- [ ] §8 verification：用 admin 登入 commander_dashboard，頂部無「收容/醫療」連線燈、左側無「傷患入站」面板、無新增 broken UI
- [ ] PR description 附 before/after 截圖對照
- [ ] 對應測試：dashboard.html + js 模組 vitest / playwright 覆蓋（沿用既有 tests/js/）
