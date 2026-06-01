# ICS_Command Design System — Policy

> 來源：[ROADMAP P1-10a](../../../docs/ROADMAP.md) · [issue #17](https://github.com/winson3QQ/ICS_COMMAND/issues/17) · vendor: [WaveInk DS](https://codeberg.org/winson3QQ/WaveInk)

---

## SoT

| Layer | File | 角色 |
|---|---|---|
| Tokens | [`command-dashboard/static/css/ds-tokens.css`](../../static/css/ds-tokens.css) | 唯一 SoT |
| 字體 | `command-dashboard/static/fonts/JetBrainsMono-*.woff2` | 4 weights，離線打包 |
| 本檔 | `docs/design/POLICY.md` | divergence 紀錄 + 維護規則 |

### v1 涵蓋範圍（明示）

**已套用 ds-tokens.css**（本 PR scope）：
- `static/commander_dashboard.html`

**未套用 ds-tokens.css**（P1-10a-2 follow-up 處理；目前仍各自 inline `:root` 或無 tokens）：
- `static/scenario_designer.html`（含 own `:root` 用舊 palette + IBM Plex Mono — bypass SoT，遷移前不算 P1-10a 達標）
- `static/admin_backups.html`
- `static/icon_preview.html`
- `static/qr_scanner.html`

→ 本檔「ds-tokens.css 為唯一 SoT」**目前僅指 commander_dashboard.html scope**。其他 HTML 檔遷完才算全 repo SoT。

---

## Vendor pinning

### WaveInk DS (color + type + spacing + radius + shadow + .ds-* classes)

| 項目 | 值 |
|---|---|
| Repo | `ssh://git@codeberg.org/winson3QQ/WaveInk.git` |
| Commit | `58226e4b5c75ac65ee383d09cfabdc0844e295e6` |
| Source file | `docs/design/colors_and_type.css` |
| Vendored as | `static/css/ds-tokens.css`（含 ICS_Command divergence section）|
| Policy ref | WaveInk `docs/DESIGN.md` 同 commit |

WaveInk DS 升版時，**先比對本檔 § Divergence，再合併**。divergence section 不可被 vendor 覆蓋。

### JetBrains Mono v2.304 (webfont)

| 項目 | 值 |
|---|---|
| Release | https://github.com/JetBrains/JetBrainsMono/releases/tag/v2.304 |
| License | Apache 2.0 (`static/fonts/LICENSE.txt`) |
| Bundled weights | 400 / 500 / 600 / 700（italic 不打包，指揮場景無使用情境）|
| ZIP SHA256 | `6f6376c6ed2960ea8a963cd7387ec9d76e3f629125bc33d1fdcd7eb7012f7bbf` |

### 4 個 woff2 個別 SHA256

```
c503cc5ec5f8b2c7666b7ecda1adf44bd45f2e6579b2eba0fc292150416588a2  JetBrainsMono-Bold.woff2
086c48dfbea9ddaff1320f7e09399b8e2924e88ce67453721255db3bdbb5a353  JetBrainsMono-Medium.woff2
a9cb1cd82332b23a47e3a1239d25d13c86d16c4220695e34b243effa999f45f2  JetBrainsMono-Regular.woff2
918edad542a1da608fd2ba8daebaff9ac802309103fe760eed465b8b4e47faf1  JetBrainsMono-SemiBold.woff2
```

升版時驗 SHA256 確認沒被替換成 backdoor 版本。

### MapLibre GL JS v4.7.1（地圖引擎，P1-10b 起）

| 項目 | 值 |
|---|---|
| Release | https://github.com/maplibre/maplibre-gl-js/releases/tag/v4.7.1 |
| License | BSD-3-Clause |
| Source | https://unpkg.com/maplibre-gl@4.7.1/dist/ |
| Vendored as | `static/lib/maplibre-gl.js` + `static/lib/maplibre-gl.css`（離線打包，不走 CDN）|
| 版本選擇 rationale | v4.x 最後一個 release；v5 已出但 v4 路徑更穩、Pi 500 WebGL2 兼容驗證較多。升 v5 視 P2 需求評估 |

**SHA256**：
```
be9633c4d870e26fb37f1cfe5c5a77181667114003ea16207ac7850d8da8add1  maplibre-gl.js
576b085fdd9487a65a19215328c1e086c07ce5bf6da09b666b3806d3d008dae9  maplibre-gl.css
ee5fc05a0677eaf69601d2c7db0d9ecd6cc27c3abc1d0733bc9ed34707cf8ef2  LICENSE-maplibre.txt
```

**LICENSE 文字**：vendored at `static/lib/LICENSE-maplibre.txt`（BSD-3-Clause 散布義務 #1：散布時保留 copyright + license 全文）。

**供應鏈紅線確認**（CLAUDE.md）：MapLibre 為 Mapbox GL JS v1 OSS fork，社群維護（主要貢獻者：Stadia Maps、MapTiler、Microsoft、各 OSS contributor），non-Chinese 實體。

### pmtiles v4.4.1（PMTiles MapLibre protocol，P1-10b 起）

| 項目 | 值 |
|---|---|
| Release | https://github.com/protomaps/PMTiles/releases/tag/js-v4.4.1 |
| License | BSD-3-Clause |
| Source | https://unpkg.com/pmtiles@4.4.1/dist/pmtiles.js |
| Vendored as | `static/lib/pmtiles.js` |
| 用途 | 為 MapLibre 註冊 `pmtiles://` protocol，使 P1-10c 台灣 PMTiles 單檔可直接 serve |

**SHA256**：
```
36bcbe1ba97cc07b3fc90cee9cba11729b04e25ec8790cf65a0787d5b38e091b  pmtiles.js
0371c38f338835f7fc13ed71176f3d92144e22c8b736a31cced57adbbeb647b3  LICENSE-pmtiles.txt
```

**LICENSE 文字**：vendored at `static/lib/LICENSE-pmtiles.txt`。JS 實作為 BSD-3-Clause；PMTiles spec 本身為 public domain / CC0。

**供應鏈紅線確認**：Protomaps 由 Brandon Liu（US 籍）維護，non-Chinese 實體。

### Noto Sans Regular pbf glyphs（MapLibre text-field 渲染，P1-10b 步驟 7 階段 3b 起）

| 項目 | 值 |
|---|---|
| 來源 repo | https://github.com/protomaps/basemaps-assets |
| Vendored from | tag main (zip download) — basemaps-assets-main/fonts/Noto Sans Regular/ |
| License | **SIL Open Font License 1.1**（OFL）|
| Copyright | 2022 The Noto Project Authors（Google / Adobe collaboration via Noto project）|
| Vendored as | `command-dashboard/static/fonts/glyphs/Noto Sans Regular/<range>.pbf`（256 files）+ `command-dashboard/static/fonts/glyphs/OFL.txt` |
| 總大小 | ~6.8 MB |
| 涵蓋範圍 | Basic Multilingual Plane 全範圍（U+0000–U+FFFF）含 ASCII + 拉丁 + 繁體中文 CJK Unified Ideographs |
| 用途 | MapLibre `text-field` glyphs source — polygon/route/flow/infra/zone label 渲染中文（user-typed 內容） |
| Style URL | `/static/fonts/glyphs/{fontstack}/{range}.pbf`（FastAPI static serve）|

**OFL 散布義務**：保留 copyright 通知 + 不能單獨販賣字體 + 衍生作品需用相同 license。
LICENSE 文字 vendored at `static/fonts/glyphs/OFL.txt`。

**供應鏈紅線確認**：Noto 字體 = Google + Adobe 合作（non-Chinese 實體 + open standard）；
Protomaps 是 Brandon Liu (US) 自製的 build pipeline 產生 pbf。Pipeline 工具
`maplibre/font-maker` 為 MapLibre 社群維護（non-Chinese）。

**選擇 rationale**（B1 全 CJK）：
- 戰術系統 user-typed label 涵蓋人名/地名/部隊代號等不可預期字
- 與 TAK / 政府指揮系統對標：完整 CJK 是標準
- subset 解法（B2）會在邊緣 case 破圖且 subset 選擇本身是技術債
- repo 增量 6.8MB vs P1-10c PMTiles 200-400MB 微不足道 (~2-3%)

**未來如要加 Bold / Italic weight**：同 repo 已有 `Noto Sans Medium` / `Noto Sans Italic`，
依需求再 vendor，預期再 +6-7MB。當前 Regular + `text-halo-width` 已足夠 readable。

### 將被移除（P1-10b 步驟 11）

完成 MapLibre 替換後 `static/lib/` 內以下檔案刪除：
- `leaflet.min.js`、`leaflet.min.css`
- `protomaps-leaflet.js`
- `marker-icon.png`、`marker-icon-2x.png`、`marker-shadow.png`

---

## Divergence — ICS_Command 對 WaveInk DS 兩處例外

### Divergence 1：新增 MIL-STD-2525 4 個 affiliation token

**WaveInk policy** (DESIGN.md § 1)：
> One accent: `#58a6ff`. No new blues, teals, purples, or "brand" colours.

**ICS_Command 例外**：在 `:root` 加 4 個 token group：
```css
--mil-friendly: #00FFFF;   /* Cyan       — friend / assumed-friend */
--mil-hostile:  #FF0000;   /* Red        — hostile / suspect */
--mil-neutral:  #00FF00;   /* Lime       — neutral */
--mil-unknown:  #FFFF00;   /* Yellow     — unknown / pending */
```

**理由**：
- MIL-STD-2525C 附錄 A 標準 affiliation color 是戰術圖示 SoT
- ATAK / WinTAK / TAK Server 全生態以這 4 色為基線；不對齊 = EOC 互通失敗
- 屬於 `entity` 概念層需求，WaveInk（無線電通聯系統）沒這個需求

**反向約束**（ICS_Command 內部）：未來新增 entity affiliation **必須**對齊 MIL-STD-2525C，**不得自創色**。新加 `--mil-*` token 需引用 2525C 章節並 update 本檔。

---

### Divergence 2：JetBrains Mono 離線打包 webfont

**WaveInk policy** (DESIGN.md § 1 Typography)：
> Production ships **no webfonts**. Use `var(--font-system)`, not `var(--font-body)`, when matching live UI.

**ICS_Command 例外**：bundle JetBrains Mono v2.304 (4 weights) 進 `static/fonts/`：
```css
--font-code: 'JetBrains Mono', var(--font-mono-system);
```

**理由**：
- **指揮場景跨平台一致性是 ops 規範**：callsign / 座標 / MGRS / timestamp 在 Mac / Win / Linux 上若用 system mono（分別是 Menlo / Consolas / DejaVu），視覺差異不可接受
- JetBrains Mono 對 `0/O` / `1/l/I` / `5/S` 有明確 disambiguation 設計（誤讀座標 = 戰場失誤）
- 「離線部署」是本 repo 核心約束（CLAUDE.md），webfont **必須**入 repo，不能 Google Fonts CDN
- 不採 IBM Plex Mono：disambiguation 沒 JetBrains Mono 明顯

**反向約束**：UI 文字（非 mono）維持 WaveInk system stack（`--font-body: var(--font-system)`），不打包 Inter。

---

## 戰術底圖 doctrine（P1-10c 起 SoT，於 P1-10b vendor 時 stub）

戰術 / 指揮 / COP 地圖底圖必須遵守：

### Rule 1：底圖必須 desaturated

**允許**：grayscale（黑白）、muted earth-tone（低飽和度，beige/khaki/dark green）。

**禁止**：任何 vivid 飽和色作為 default basemap（含 Google Maps / OpenStreetMap 預設 style / Mapbox Streets / 任何 colorful raster tile）。

### Rule 2：唯一 saturated 色 = MIL-STD-2525 affiliation token + severity token

整個畫面 saturated 色只能來自：
- `--mil-friendly` / `--mil-hostile` / `--mil-neutral` / `--mil-unknown`（entity affiliation）
- `--severity-critical` / `--severity-warning` / `--severity-info`（event severity）
- selection / hover 強調色（DS 內 `--accent-*`）

理由：戰術地圖**符號是焦點，底圖是背景**。底圖若與 affiliation 搶飽和度，MIL-STD-2525 顏色就跳不出來，戰術判讀崩。

### Rule 3：Satellite imagery 不得作為 default

特定情報 / 地形評估需求可開 satellite layer 作為 **opt-in toggle**，但**預設關閉**。

### Rule 4：底圖切換只走「光線/時段」軸，不走「主題/風格」軸

P1-10c 提供 2 套：
- `dark`：夜間 ops / 室內預設（depth 高、對比強的灰階）
- `muted-day`：白天演練（低飽和度、提高 ambient 亮度）

**禁止** dusk / sunset / vintage / hand-drawn 等任何「風格化」style 作為 ops 預設。

### Rule 5：底圖 style.json 改動需走 PR + 視覺 regression 截圖

底圖直接影響戰術判讀，style 改動視同 entity token 改動，需 review 截圖確認 affiliation 仍跳出來。

---

## 底圖資料來源與 build recipe（P1-10c，2026-06-01 落定）

> 背景：`taiwan.pmtiles` 在舊 Leaflet 部署即存在（被 `protomaps-leaflet` 以 `flavor: 'grayscale'` 引用），
> 但**從未進版控、也沒留 build recipe**，fresh clone 一律缺檔、無法 audit 也無法重建。本節補上這塊缺口。

### 採用體系（事實）

| 項目 | 值 |
|---|---|
| 引擎 | MapLibre GL JS v4.7.1 + `pmtiles.js` v4.4.1（見上方 Vendor pinning）|
| 底圖檔 | `command-dashboard/static/tiles/taiwan.pmtiles` |
| 存放政策 | **gitignored deploy artifact**（`.gitignore` 已排除 `command-dashboard/static/tiles/`）；**不進 git**，隨部署帶 |
| serving | 同源 `GET /tiles/pmtiles/{filename}`（含 HTTP Range），見 `src/routers/map.py` |
| schema | **Protomaps basemaps schema**（OSM + Natural Earth 衍生）|
| style | `@protomaps/basemaps` npm `namedFlavor()` 產 MapLibre style JSON |
| flavor 對應 | `dark` → 夜間 ops 預設；`muted-day` → 用 `grayscale`（舊版沿用）或 `light`，擇 desaturated 較佳者 |

**相容性紅線（必記）**：PMTiles 的 schema 版本必須與 `@protomaps/basemaps` style 版本**對齊**——
兩者釘同一個 `@protomaps/basemaps` 版本，否則 source-layer 名對不上 → 底圖空白。

### Build recipe — 路徑 A（採用，pmtiles extract 切片）

單一 binary、HTTP Range 只抓台灣那塊（不下整顆 ~120GB planet）、原生 Windows 可跑、~15-30 分鐘出檔：

```bash
# 工具：go-pmtiles（BSD + ODbL，Protomaps / Brandon Liu, US；Win/Mac/Linux 單一 binary）
pmtiles extract https://<protomaps-planet-build-URL@釘日期>.pmtiles taiwan.pmtiles \
  --bbox=119.3,21.7,122.2,25.4 \
  --maxzoom=15
# 來源：Protomaps daily planet build（maps.protomaps.com/builds，OSM/ODbL）
# 輸出：約 200-400MB（z0-15）；--maxzoom=14 約砍半
```

**bbox 涵蓋台灣本島 + 澎湖。⚠ 金門（~118.2-118.5°E）/ 馬祖（~26.2°N）未含**——
若演習場域含金馬，改 `--bbox=118.1,21.7,122.3,26.4`。

### Build recipe — 路徑 B（fallback，planetiler 自 build）

要「完全可重現、不依賴 Protomaps 線上主機、精準裁到台灣」時改走這條（代價：需 JDK 21 + Maven）：

```bash
# 來源：Geofabrik taiwan-latest.osm.pbf（~309MB，每日，ODbL，Geofabrik GmbH 德國）
#   https://download.geofabrik.de/asia/taiwan-latest.osm.pbf
# 工具：planetiler（Apache 2.0，OnTheGoMap / Michael Barry, US）+ protomaps/basemaps profile（BSD-3）
java -jar protomaps-basemaps-with-deps.jar --osm-path=taiwan-latest.osm.pbf
# 輸出 planet.pmtiles → rename taiwan.pmtiles
```

### 供應鏈紅線確認（CLAUDE.md，全部非中國 ✓）

| 元件 | 維護者 / 國 | License |
|---|---|---|
| go-pmtiles / basemaps / `@protomaps/basemaps` | Protomaps LLC，Brandon Liu（US）| BSD-3（code）/ ODbL（資料）|
| planetiler | OnTheGoMap / Michael Barry（US）| Apache 2.0 |
| Geofabrik 台灣 extract | Geofabrik GmbH（德國 Karlsruhe）| ODbL 1.0 |
| OSM 原始資料 | OpenStreetMap Contributors（全球）| ODbL 1.0 |
| Natural Earth（低 zoom）| NACIS（US）| Public Domain |

**Attribution 義務（ODbL，必做）**：地圖需可見標註 **© OpenStreetMap contributors**
（對應 `static/js/map/maplibre_core.js` 目前 `attributionControl:false` 的 TODO）。

### Provenance 留痕（每次 build 後補）

每次重 build 必須在本節釘：planet build 日期 **或** Geofabrik pbf 日期、`--bbox`、`--maxzoom`、
`@protomaps/basemaps` 版本、輸出檔 BLAKE3 hash。否則此檔再次失去可重現性。

```
build 日期：<填>   bbox：<填>   maxzoom：<填>   basemaps 版本：<填>
taiwan.pmtiles BLAKE3：<填>
```

### 釐清：mini-taiwan 不是底圖來源（避免再次誤認）

[mini-taiwan-learning-project](https://github.com/ianlkl11234s/mini-taiwan-learning-project) 是 **P1-10b 渲染架構**的
借鏡來源（見 [issue #19](https://github.com/winson3QQ/ICS_COMMAND/issues/19) 的 7 條：EntityLayer / SDF / 4-layer
state stack / text-halo / symbol-sort-key / LOD / 效能 checklist），**不是底圖資料來源**——
該專案底圖用 Mapbox 線上圖磚 + token，ICS 已明確拒絕（違反「PMTiles 離線」紅線）。

### 新舊差異（為何換引擎，非底圖好看）

底圖資料 / schema 新舊**幾乎相同**（同 `taiwan.pmtiles`、同 Protomaps schema）；質變在**引擎**：
Leaflet/Canvas（CPU）→ MapLibre/WebGL（GPU）。換引擎是為了解鎖 SDF icon + 資料驅動上色 +
4-layer state stack——P2 TAK + MIL-STD-2525 符號渲染的必要條件（Leaflet 做不到，不換 = P2 重工）。
**推論（高信心）**：舊 `taiwan.pmtiles` 可直接給 MapLibre 用，但若 schema 版本與新 style 對不上則需重 build。

---

## 維護規則

### 改 WaveInk 對應 token
1. 上游 sync：拉 WaveInk repo 最新 commit
2. 比對 `colors_and_type.css` diff，更新 `ds-tokens.css` 對應段（**保留 divergence section 不動**）
3. 本檔 § Vendor pinning 更新 commit hash
4. 視覺 regression test：截圖比對 dashboard before/after

### 改 ICS_Command 自有 token（`--mil-*` / `--font-*` / aliases）
1. 直接 PR，不必動 WaveInk
2. 改 `--mil-*` 必須引用 2525C 章節
3. legacy aliases (`--bg`, `--mono` etc) 之後 P1-10a-2 follow-up 全 grep-replace 完才能 deprecate

### 新增 token
- 必須符合「現有不夠用」原則
- 必須在本檔 § Divergence 加說明（如果是 ICS_Command 自有）
- 字體新增需提 PR + license check（CLAUDE.md 供應鏈規則）

---

## js/ + inline style 遷移計畫（P1-10a-2）

本 PR 不動：
- `static/js/*.js` 中 126 處硬寫 hex color（charts.js 一堆）
- `static/commander_dashboard.html` 中 68 處 inline `style=`
- 其他 4 個 HTML 檔（admin_backups / icon_preview / qr_scanner / scenario_designer）

統一在 **P1-10a-2 follow-up** 處理：
- js/ hex → `getComputedStyle(document.documentElement).getPropertyValue('--xxx')`
- inline style → 提取為 .ds-* class 或 utility class
- 完成後 deprecate `--bg`/`--surface`/... legacy aliases
