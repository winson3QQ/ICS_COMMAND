# ICS_Command

**指揮部儀表板（Command Dashboard）** — 事件管理體系（ICS）通用作戰圖（COP）與決策支援系統。

從 [ICS_DMAS](https://github.com/winson3QQ/ICS_DMAS) 多組件平台拆分出來的單體交付版本，聚焦指揮部後端與儀表板。

## 🚀 Onboarding（新機器 / 新接手者）

```bash
git clone git@github.com:winson3QQ/ICS_COMMAND.git
cd ICS_COMMAND
git config core.hooksPath .githooks   # 啟用 memory sync hook（一次性）
./status.sh                            # 看當前 commit + ROADMAP 進度全景
```

### 換機器 / 新接手者：補 gitignored runtime（**必看**）

底圖、DB **不在 git 裡**（deploy artifact / per-machine 帳號）。只 clone 會看到「**空白底圖 + 節點/格線不出現**」——不是 bug，是少了兩樣：

```bash
# 1. 底圖（command-dashboard/static/tiles/taiwan.pmtiles，gitignored）
./scripts/provision_basemap.sh                          # 從雙 release 下載（需發佈者先跑過 publish_basemap）
# ./scripts/provision_basemap.sh --from /mnt/usb/taiwan-<ver>.pmtiles   # air-gap / 無 release 時

# 2. 啟動
./start_mac.sh   # macOS    /    ./start_pi.sh   # Linux / Pi

# 3. first-run（每台一次）：啟動時 console（或 ~/.ics/first_run_token）會印初始 PIN
#    → admin + 該 PIN 登入 → 改 PIN。完成後「節點/格線」會自動從 seed 出現。
```

> 🔁 **新 `git worktree` 自動補底圖**：`.githooks/post-checkout` 在新 worktree 缺底圖時自動跑 `scripts/ensure_basemap.sh`，**優先重用本機其他 worktree / 主 repo 的副本**（`provision --from`，免重下載 247MB）；本機完全無副本才需手動 `scripts/provision_basemap.sh`。需先 `git config core.hooksPath .githooks`（見〈Memory 同步〉）。
> ⚠️ 底圖 / DB 是 gitignored；**別把 live SQLite（`command-dashboard/data/ics.db`）丟 OneDrive/iCloud 同步**（WAL 會壞）。DB 路徑可用 `ICS_DB_PATH` env 固定在 repo 外（見 `.claude/launch.json`）。
> 底圖**發佈/整備/多機同步 SOP**（`publish_basemap` / `provision_basemap` / preflight）見 [`command-dashboard/docs/design/POLICY.md`](command-dashboard/docs/design/POLICY.md) §底圖資料來源與 build recipe。

生產環境用 `systemd/ics-command.service`（site config 經 `EnvironmentFile=/etc/ics/command.env`，見 `systemd/ics-command.service.d/README.md`）。

然後依序看：

1. [`CLAUDE.md`](CLAUDE.md) — 專案規則（紅線、語言、git、版號）
2. [`docs/PROCESS.md`](docs/PROCESS.md) — 工作流（task lifecycle + slash command 對應）
3. [`docs/ROADMAP.md`](docs/ROADMAP.md) — 路線圖（38 items，含 ✅/⏳/🚧 marker）

新 Claude session 開啟時，`CLAUDE.md` 與 `.claude/memory/` 會自動載入；只需要再跑 `./status.sh` 就能完整定位。

## 元件

| 路徑 | 說明 |
|---|---|
| `command-dashboard/` | FastAPI + SQLite 後端、指揮官 / 幕僚儀表板（HTML + JS） |
| `server/` | Node.js WebSocket relay（保留作前端 / security 整合介接點） |
| `docs/ROADMAP.md` | 三 Phase 路線圖（基底重構 → TAK → WaveInk），含 compliance inline 對照 |
| `docs/compliance/` | NIST SSDF / ASVS / ISO 25010 對照（policies + threat model） |
| `deploy/`（Phase 1 補） | nginx + step-ca 內網 PKI |

## 快速啟動（Mac）

```bash
chmod +x start_mac.sh
./start_mac.sh
```

啟動後開 <http://127.0.0.1:8000/static/commander_dashboard.html>。

## 測試

```bash
cd command-dashboard
.venv/bin/pytest
```

## 版號規則

- `command-vX.Y.Z` — 指揮部後端與儀表板（SemVer，git tag）
- `server-vX.Y.Z` — Node.js relay（若獨立演進才打）

## 安全聲明

見 [SECURITY.md](SECURITY.md)。Compliance 主張見 [docs/compliance/](docs/compliance/)。

## 授權

待定（拆分前 ICS_DMAS 為私有 repo；對外交付前需明定 license）。
