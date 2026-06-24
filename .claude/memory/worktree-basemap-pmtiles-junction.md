---
name: worktree-basemap-pmtiles-junction
description: "Fresh ICS worktrees have no basemap; junction static/tiles to main repo's pmtiles (not hardlink/symlink)"
metadata:
  node_type: memory
  type: project
  originSessionId: d79bcb7a-8115-4f66-80ce-9445f4a0613e
---

ICS dashboard 的向量底圖是 Protomaps pmtiles：`command-dashboard/static/tiles/taiwan.pmtiles`（~236MB，gitignored、要下載）。前端 style（`/static/styles/basemap-dark.json`）以 `pmtiles:///tiles/pmtiles/taiwan.pmtiles` 取，後端 `src/routers/map.py` 的 `serve_pmtiles` 從 `STATIC_DIR/tiles/` 服務、且有 `resolve()` + `is_relative_to(base)` 路徑穿越守門。

**新 worktree 沒這個檔 → 地圖整片空白**（map_config 的 `Satellite_map.png` 是另一回事、且該檔全機器都不存在，無關緊要）。

**修法：對 `static/tiles` 做目錄 junction 指向主 repo 的 tiles 目錄**，0 下載 0 複製：
`New-Item -ItemType Junction -Path <worktree>\command-dashboard\static\tiles -Target C:\Users\yello\Desktop\ICS_COMMAND\command-dashboard\static\tiles`

**為何不能 hardlink / symlink**：
- hardlink → 主 repo 檔在 OneDrive（Desktop 下）是雲端 placeholder，雲端過濾驅動拒絕 hardlink（"cloud operation cannot be performed on a file with incompatible hardlinks"）。
- symlink → `serve_pmtiles` 的 `resolve()` 會解到 base 目錄外 → `is_relative_to(base)` False → 404。
- junction → base（`MBTILES_DIR.resolve()`）與 path 都穿過 junction 解到同一真實目錄 → 通過守門。✓

驗證：`curl -H "Range: bytes=0-127" http://127.0.0.1:8000/tiles/pmtiles/taiwan.pmtiles` 應回 206。

相關：[[ics-worktree-server-setup]]
