# 第三方素材與授權標示（Third-Party Notices）

本專案使用下列第三方素材，於此標示來源與授權（供散布時可及，符合各授權之 attribution 要求）。

## NAPSG Incident Symbology（事件象形 glyph）

- **用途**：地圖事件 marker 的象形圖示（vendored 於 [`command-dashboard/static/js/map/napsg_glyphs.js`](command-dashboard/static/js/map/napsg_glyphs.js)）。
- **來源**：NAPSG Foundation — Incident Symbol Set / DHS-Symbol-Server
  <https://github.com/NAPSG/DHS-Symbol-Server>（US DHS / FEMA-aligned）。
- **授權**：Creative Commons Attribution 4.0 International（**CC BY 4.0**）
  <https://creativecommons.org/licenses/by/4.0/>。
- **已使用之符號**（NAPSG 代碼）：
  FAA（Explosion）、LEK（Transmission Tower）、MAAN（General Hazards）、
  GAAN（Evacuation Immediate, IPAWS）、AAB（Structure Damaged, USAR）、AAE（Victim Detected, USAR）。
- **修改聲明**（CC BY 要求標示是否修改）：
  - GAAN 移除原始紅三角警示底框、收緊 viewBox 放大跑人圖形；
  - AAB 移除原始黃色菱形底框，僅保留房屋圖形；
  - 全部改為單色 `fill="#fff"` 以白色渲染於 severity 色 ◆ 框內。
- 設計與對照詳見 [`docs/design/event-symbology-mapping.md`](command-dashboard/docs/design/event-symbology-mapping.md)。

## 執行期相依（npm / runtime）

下列以 npm 套件形式相依，各自授權隨 `node_modules` 內 LICENSE 檔提供：

- **MapLibre GL JS** — 地圖渲染（BSD-3-Clause）。
- **PMTiles / @protomaps/basemaps** — 向量底圖與樣式（見各套件 LICENSE）。

> 供應鏈紅線：本專案禁用任何與中國相關之軟體/函式庫/服務（見 `CLAUDE.md`）。
> 以上素材來源（US DHS/FEMA、MapLibre、Protomaps）均不在此列。
