# ICS_Command 專案規則

由 [ICS_DMAS](https://github.com/winson3QQ/ICS_DMAS) 拆分而來的指揮部單體版本。原 PWA 組件（shelter / medical）不在本 repo 範圍內。

## 🚀 新 session START HERE

```bash
./status.sh                       # 1. 看當前 commit + ROADMAP 進度全景
```

> **換機器 / 新機器**：clone 後底圖與 DB 不隨 git（空白底圖 + 節點/格線不出現是正常）。
> 先照 [`README.md` §換機器 / 新接手者](README.md) 補 runtime：`./scripts/provision_basemap.sh` 取底圖 + 啟動後一次性 first-run。

然後依序看（自動載入的不再點名）：
1. **工作流**：[`docs/PROCESS.md`](docs/PROCESS.md) — 一個 task 怎麼走（10 步 lifecycle、要打哪個 slash command）
2. **路線圖**：[`docs/ROADMAP.md`](docs/ROADMAP.md) — 做什麼（38 items，✅/⏳/🚧 marker）
3. **架構決策**：[`.claude/memory/`](.claude/memory/)（已自動載入 index）— 為何這樣做

本檔（CLAUDE.md）是 **policy SoT**（紅線、語言、git 規則、版號規則）。`docs/PROCESS.md` 是 **process SoT**（怎麼做）。`docs/ROADMAP.md` 是 **work SoT**（做什麼）。三者各司其職。

## 安全與供應鏈規則

**禁止使用任何與中國相關的軟體、函式庫、或服務。**

包含但不限於：
- npm / PyPI 套件的維護者或主要貢獻者為中國實體
- 中國雲端服務（阿里雲、騰訊雲、百度雲等）
- 任何來源不明、無法驗證供應鏈的套件

遇到不確定的情況，**必須先詢問使用者確認**。

## Debug 規則

1. **平台與工具的設計規則優先**：開始前先確認平台慣例與工具設計，再判斷 log。
2. **一定有先例**：先找成功的 source code 或網路抓包，再 reinvent。
3. **基礎確認後才判斷 log**。

## 溝通與推論規則

1. **檢查假設**：使用者的假設若有問題必須明確指出。
2. **指出邏輯漏洞**：發現設計或推論中的漏洞主動說。
3. **區分事實、假設、推論、意見**。
4. **不確定就說「不確定」**，不得猜測填補。

## 語言規則

- 所有回覆與程式碼 comment 一律使用**繁體中文**
- 禁止混入日文或韓文
- 英文僅用於技術術語、變數名、API 名稱

## Git 操作規則

- **任何 git 操作（commit、push、tag、PR）一律等使用者明確指示才執行**
- 程式碼改完後停下來告知改了什麼，等使用者說 ok 再動 git
- 不得自行判斷「應該順便 commit / push」

### Remote 與 SoT 規則（C 方案，沿襲 ICS_DMAS）

- **Code SoT = Codeberg**（私有 repo）
- **Issue tracker SoT = GitHub**
- `origin` 配置雙 push URL，單一 `git push origin main` 同時推 GitHub + Codeberg
- 任一邊失敗 = 整個 push 失敗（feature，避免默默分歧）
- 同步檢查：`git fetch --all && git rev-parse origin/main codeberg/main main` 三 hash 應一致

> 本 repo 建立時（2026-05-25）remote 尚未設定，待使用者提供 URL 後再配置。

### Issue/PR Snapshot

- `.github/workflows/issue-snapshot.yml` + `scripts/snapshot_issues.sh` 沿襲自 ICS_DMAS
- 輸出 `docs/backups/{github-issues,github-prs}.json`
- 需要 GitHub secret `CODEBERG_TOKEN`（scope: `write:repository`）

### Branch 工作流

- Issue 對應分支：`feat/issue-NN-*` / `fix/issue-NN-*` / `docs/issue-NN-*`
- `main` 只接受 merge commit（feature 走 PR）
- hotfix / 純文件可直接在 `main` commit

## 版號規則

**兩軌版本，不可混用**（`/api/version` 同時回兩者）：

### 後端 SemVer — `command-vX.Y.Z`（`APP_VERSION`，code SoT = `core/config.py`）

- `command-vX.Y.Z` — 指揮部後端 + 儀表板（git tag）
- `server-vX.Y.Z` — Node.js relay（若獨立演進才打）

| 位號 | 觸發 |
|---|---|
| PATCH +1 | bug fix 完成，行為有改變 |
| MINOR +1 | 一個功能完整可用 |
| MAJOR +1 | 介面或資料格式破壞性變更（API、DB schema） |

### 前端 UI — `cmd-vX.Y.Z`（`CMD_VERSION`，SoT = `core/config.py`，dashboard chrome 顯示）

追蹤**前端使用者可感的 UI 演進**，與後端 SemVer 脫鉤（後端只動 API、前端零變動時不進前端版，反之亦然）。

| 位號 | 觸發 |
|---|---|
| PATCH +1 | 零星 UI 修補 / 視覺微調 |
| MINOR +1 | 一個 UI 功能組完整可用（如：演習面板、放置工具、稽核日誌 UX）|
| MAJOR +1 | 前端大紀元（如：地圖引擎全換、PWA→command-only 拆分、整體 redesign）|

> `v1.0.0` 起算點：拆分自 ICS_DMAS 後首個完整可用形態（MapLibre 全換 + PWA 移除 + 演習/放置/稽核 UI）。v0.x 為繼承自 DMAS 的 pre-1.0 開發線。

每次 commit 包含**任一軌**版號遞增**必須同時打對應 git tag**（`command-v*` 或 `cmd-v*`）。

## 功能完成定義（Definition of Done）

1. 程式碼 merged（main）
2. CI 測試補齊（unit / integration / security 對應）
3. 規格書同步（若有介面 / 資料格式 / 行為變更）
4. ROADMAP 對應 phase item 勾選 / 證據連結（PR# + commit hash 寫進 commit message）
5. Issue 在 GitHub 關閉

**缺測不算完成。** Compliance 對照已 inline 於 [`docs/ROADMAP.md`](docs/ROADMAP.md) 各 phase 的 *Compliance touchpoints* 區塊，不再維護獨立 matrix.md。

## Memory 同步（跨機器）

Memory 檔案存放在 repo 的 `.claude/memory/`（本 repo 初始時無 memory，由新對話累積）。`git pull` 透過 `.githooks/post-merge` 自動 sync 到 Claude Code 讀取位置：

```bash
# 每台新機器執行一次
git config core.hooksPath .githooks
```

`.githooks/post-merge` 同時包含 **Codeberg branch 同步刪除**：在 main 上 pull 後，自動清掉 codeberg 有但 origin 沒有的 `feat/*` branch（補 `gh pr merge --delete-branch` 只刪 GitHub 不刪 Codeberg 的破口）。其他 prefix 不動。

## 開發環境

- 主要開發機：Mac
- 部署目標：依交付場景而定（容器化 / Pi / 雲端）

## 專案結構

- `command-dashboard/` — FastAPI + SQLite 後端與儀表板
- `server/` — Node.js WebSocket relay（前端 / security 介接點）
- `docs/compliance/` — NIST / ASVS / ISO 對照、policies、threat model
- `systemd/` — Linux 服務檔（ics-command / ics-backup）

## User Data 邊界（P1-13 起確立）

| 位置 | 角色 | git tracked? | 操作邊界 |
|---|---|---|---|
| `command-dashboard/data/` | **runtime / user data**（演習場域資料、DB、map_config 等）| ❌ gitignored | P1-12b backup GUI 操作對象；P1-12c SQLCipher 加密邊界 |
| `command-dashboard/static/` | **factory default / 程式靜態資源**（seed、HTML、JS、CSS、icons）| ✅ tracked | 版本控管，release 隨 code 走 |
| `command-dashboard/src/` | code | ✅ tracked | — |

**紅線**：runtime 寫入物**不得**放在 `static/`（會造成 working tree dirty / 切 branch 洗資料 / snapshot script 把 user data commit）。P1-13 落地 `command-dashboard/static/map_config.seed.json`（factory default）+ `command-dashboard/data/map_config.json`（runtime）為這條規則的第一個 enforcement。後續新增的 runtime 檔（user uploads、cache、derived data）一律走 `data/`。

對應的 factory default 機制：`static/<name>.seed.<ext>` (tracked) + `data/<name>.<ext>` (gitignored) + startup ensure() 從 seed 兜底。

## 與 ICS_DMAS 的關係

本 repo 為 ICS_DMAS 在 2026-05-25 拆分出的指揮部單體版本。原 repo 仍保留三組件完整架構（shelter PWA / medical PWA / command）。當共用元件（server/、command-dashboard/）有改動時，需評估是否回饋上游 ICS_DMAS。

**注意**：本 repo 為清白 init（無 git 歷史繼承）。原始開發脈絡見 ICS_DMAS commit history。
