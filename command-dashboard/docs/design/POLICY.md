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
