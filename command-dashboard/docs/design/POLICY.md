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
```

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
```

**供應鏈紅線確認**：Protomaps 由 Brandon Liu（US 籍）維護，non-Chinese 實體。

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
