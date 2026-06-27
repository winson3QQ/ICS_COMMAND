# ICS_Command 工作流程

**核心原則**：用 Claude Code 內建 skill / subagent / hook，**不蓋 workflow 框架**。本文件取代原計畫中的 AGENTS.md。

---

## 一個 ROADMAP item 的生命週期（典型）

| 步驟 | 你打的字 | 誰做什麼 |
|---|---|---|
| 1. 起手 | 「做 P1-10a」 | Code session 讀 ROADMAP，必要時 `/plan` |
| 2. 開 issue + branch | （Code 自己 ask）「開 issue + branch ok?」→ 你回 `ok` | `gh issue create` + `git checkout -b feat/issue-NN-xxx` |
| 3. 實作 | （Code 自己做）| 寫 code + 補測 + 跑 `pytest` |
| 4. Push branch + 開 PR | （Code ask）「push branch + 開 PR ok?」→ 你回 `ok` | `git push -u origin <branch>` + `gh pr create`（PR 套用 [PR template](../.github/PULL_REQUEST_TEMPLATE.md)，§8 verification script 必填）|
| **4.5 部署到驗證環境（dogfood）** | （Code ask）「build + 上線 ics-command ok?」→ 你回 `ok` | 把 branch 改動 build 成 image 推上 prod 容器供真機/公網驗（見下方〈部署〉）。**改動若無法在 unit/preview 驗（真機 mTLS、iOS、跨裝置、公網行為）才需要**；純邏輯/測試覆蓋得到的可略。 |
| 5. **Human verify** | `/verify`，或直接從**已部署的真機/公網**驗 | `/verify` skill 啟動 app + 跑 script + 截圖；或使用者實機驗（如手機公網登入、憑證安裝），結果 `VERIFY-PASS`/`FAIL` 留痕 PR comment |
| 6. **Code review** | `/code-review` | skill spawn subagent 看 diff 找正確性 bug |
| 7. **Security review** | `/security-review` | skill spawn subagent 看供應鏈 / auth / CSP |
| **7.5 Quality gate** | （Arch 指示 Code 跑）`python3 scripts/doc_sync_check.py` | exit 0 才可 merge；非 0 → 回 CA 修 doc-vs-code drift |
| 8. Merge + push main | （Code ask）「merge + push main ok?」→ 你回 `ok` | `gh pr merge --squash` 即可；Codeberg mirror 由 `.github/workflows/mirror-to-codeberg.yml` 自動補（需先設 `CODEBERG_TOKEN` secret） |
| **8.5 ROADMAP tick** | （Code 自動）merge 完同一輪內動作 | `docs/ROADMAP.md` 該 item row 開頭加 ✅ + 寫入 `(#PR, commit hash)`；**不是事後想到才補** — 漏勾就違反本步驟。狀態 marker 約定見 [ROADMAP 開頭](ROADMAP.md#狀態-marker-約定) |
| 9. Tag（若版號升） | （Code ask）「tag command-vX.Y.Z ok?」→ 你回 `ok` | `git tag` + `git push --tags` |
| **9.5 Release 重部署** | （Code ask）「乾淨 build 上線 ok?」→ 你回 `ok` | merge 後用**乾淨 merge sha** 重 build（`ICS_BUILD_ID=release-<sha>-<time>`，去掉 dirty 標記）+ force-recreate，讓 prod 跑 main 的正式 build（見〈部署〉）。**後端版升者順帶產 release SBOM**（見〈部署〉規矩）。 |
| 10. Memory（若有非顯而易見決策）| （Code ask）「memory 寫 X，ok?」→ 你回 `ok` | 寫 `.claude/memory/<slug>.md` + commit |

**Human 真正打的字**：6 個核心 `ok` + 3 個 `/skill` + 部署 `ok`（4.5 dogfood / 9.5 release，視改動需不需要）。其餘 Code 自為。`doc_sync_check` 由 Arch 在步驟 7.5 自動把關，**不需要你動手**。

---

## 部署（步驟 4.5 dogfood / 9.5 release）

> 對應 [`deploy/prod/`](../deploy/prod/) 單機棧。**git 操作規則同 CLAUDE.md：部署是 outward-facing 動作，等 Human `ok` 才上線。**

**機制**（兩段都一樣，差別只在 `ICS_BUILD_ID` 標記）：
```bash
cd deploy/prod
# 1. build（#300 OneDrive reparse 殘渣已解；僅當 repo 再被 OneDrive 同步污染才會失敗，見 deploy/build-env.md）
#    BUILD_ID 注入登入頁 → 公網/真機可辨識「實際跑哪個 build」（版號常數分不出每次 rebuild）
docker build --build-arg ICS_BUILD_ID="<marker>" -t ics-command:dev ../../command-dashboard
#    dogfood（4.5）：marker = fix<NN>-<sha>-dirty-<MMDD.HHMM>（未 merge、working tree dirty）
#    release（9.5）：marker = release-<merge-sha>-<MMDD.HHMM>（已 merge、乾淨）
# 2. 上線（force-recreate 僅 ics-command；nginx/step-ca/TAK 不動）
docker compose --env-file .env up -d --force-recreate ics-command
```

**規矩**：
- **保留 `ics-data` / `ca-data` 兩卷** → 帳號 / 憑證綁定 / CA 全不動；force-recreate 僅數秒中斷（nginx 期間短暫 502）。
- 上線後**驗證 live build**：`docker exec … /api/version` 的 `build` 欄＝你剛注入的 marker；`State.Health.Status=healthy`。
- **動 auth / RBAC / 憑證的改動，部署前先查 prod 狀態**（例：#306 bootstrap 放行 → 先確認 prod `accounts>1` 或 `account_certs` 非空，確保不會在 live prod 誤開 bootstrap 窗口）。
- **無法在現役 prod 驗的改動**（如 fresh-deploy bootstrap）→ 用**隔離測試棧**（`docker compose -p <name>` + 獨立卷/port），驗完 `down -v`，不碰 live。
- **（僅 release 9.5，後端版升才做）產 SBOM**：build 完跑 `./scripts/gen_release_sbom.sh ics-command:dev` → `sbom/releases/backend-v<APP_VERSION>.cdx.json`（對映像產，含 transitive + 實際版本 + OS 套件）。**隨 release commit 進 main，由步驟 9 的 `backend-v*` tag 釘版**（SBOM 綁後端軌；純前端版升不產）。供投標 / 資安盡職調查 / 漏洞·授權偵測（#351）。需先裝 syft 或 trivy（見 [`deploy/build-env.md`](../deploy/build-env.md)）。

---

## Post Findings & Results — 強制紀律

**任何 task 過程中產生的 finding、verification 結果、dogfood 副發現，必須留痕在 Issue 或 PR**，不能只在對話裡講過就算。理由：(a) 沒留痕 = 沒發生（review / audit 時消失）、(b) 跨 session 接手者看不到、(c) Codeberg / GitHub 哪邊壞了還能從另一邊還原。

| 內容類型 | 留痕位置 | 時機 |
|---|---|---|
| Inventory / 盤點結果 | Issue comment（task 對應 issue）| 進實作前 |
| Test result（pytest / pyflakes / app boot）| PR comment（步驟 4 開 PR 後）| Push branch 完成、開 PR 後 |
| Verification 結果 | PR comment `VERIFY-PASS` / `VERIFY-FAIL: <step> <現象>` | Human ② verify 結束時 |
| Dogfood 副發現（不屬本 task scope）| 寫進**新 Issue** 並在當前 PR description 連回 | 一發現就開，不留到事後 |
| Plan subagent 輸出 | Issue comment 或 PR description 引用 | Plan 跑完當下 |
| 升級給 Human 的 exception | PR comment 留 `ESCALATE: <一行原因>`，停手等回覆 | 任何時候 |

**默契**：Code session 自動執行的指令輸出（如 pyflakes 0 lines、pytest 全綠）若要當作 verification 證據，必須**完整 paste 到 PR comment**（含指令 + 輸出），不只是 chat 裡說「跑了 pass」。

---

## Skill 速查（Claude Code 內建）

| 打這個 | 做什麼 | 何時用 |
|---|---|---|
| `/plan` | spawn Plan subagent，產生實作計畫 | 動工前確認方向（大重構必跑、小改可省）|
| `/verify` | 啟動 app、跑 verification 流程、截圖 | PR ready 後 Human 驗收（**步驟 5**）|
| `/code-review` | spawn subagent 審 diff 找 bug | PR ready 後（**步驟 6**）|
| `/security-review` | spawn subagent 審供應鏈 / auth / 配置 | PR ready 後（**步驟 7**），或新加外部依賴時 |
| `/review` | review 既有 PR | code review 別人 PR 時用（單人開發少用）|
| `/run` | 啟動本專案 app | 想手動戳 app 時 |
| `/loop <interval> /<cmd>` | 重複跑 | 例：`/loop 5m /verify` 持續 verify |
| `/schedule` | cron 排程 | issue snapshot 已用 GitHub Actions，本地排程少用 |
| `/consolidate-memory` | 整理 memory 檔 | 每月一次整理重複 / 過時記憶 |
| `/init` | 初始化 CLAUDE.md | 已建立，不會再用 |
| `/fewer-permission-prompts` | 加 allowlist 減少權限詢問 | 設定階段一次性跑 |

---

## Subagent（不打字直接動用的）

Claude Code 在合適時機自動 spawn 這些（你也可以指名要求）：

| Subagent | 用途 |
|---|---|
| `Plan` | 架構設計（也由 `/plan` 觸發）|
| `Explore` | 唯讀廣度搜尋（找 reference / convention）|
| `general-purpose` | 多步驟研究 / 不知用哪個時的 fallback |
| `claude-code-guide` | 問「Claude Code 怎麼做 X」 |

---

## 缺工具補了什麼

| 檔 | 用途 |
|---|---|
| [`.github/PULL_REQUEST_TEMPLATE.md`](../.github/PULL_REQUEST_TEMPLATE.md) | PR 開啟自動套 §8 verification script slot |
| [`scripts/doc_sync_check.py`](../scripts/doc_sync_check.py) | CLAUDE.md / ROADMAP.md 引用的路徑真實存在；偵測 doc-code drift |
| [`scripts/roadmap_issue_sync.py`](../scripts/roadmap_issue_sync.py) | ROADMAP item ↔ GitHub issue 雙向 sync 報告 |

### 跑法

| Script | 觸發時機 | 誰跑 | 通過條件 |
|---|---|---|---|
| `scripts/doc_sync_check.py` | **每個 task 結尾**（步驟 7.5，merge 前）| Arch 指示 Code 跑 | exit 0 |
| `scripts/roadmap_issue_sync.py` | **週期性**（建議週四 / phase 收尾 / 你想看時）| Arch 主動 or Human 觸發 | 落差數量 vs 上週對比 |

```bash
python3 scripts/doc_sync_check.py        # task 結尾必跑
python3 scripts/roadmap_issue_sync.py    # 週期 / phase 結尾跑
```

**Why not pre-commit hook**：
- doc_sync 對應 PR 層而非 commit 層（CA 寫到一半 doc 暫時不一致是正常）
- 每次 commit 跑會干擾開發節奏
- 嵌進 step 7.5 由 Arch 把關更乾淨

若你硬要 commit-level 強制，編輯 [`command-dashboard/.pre-commit-config.yaml`](../command-dashboard/.pre-commit-config.yaml) 加 local hook：

```yaml
- repo: local
  hooks:
    - id: doc-sync-check
      name: doc sync check
      entry: python3 scripts/doc_sync_check.py
      language: system
      pass_filenames: false
      stages: [pre-push]   # 推薦 pre-push 而非 pre-commit
```

---

## Worktree 衛生（每個 session 照辦）

> 背景：Code session 常在 `git worktree`（`.claude/worktrees/<name>/`）裡開工。每個 worktree 共享 `.git`，但 **working tree 與 `command-dashboard/data/` 各自獨立**。`data/` 是 gitignored runtime 資料，**不隨 git 走、不繼承主 repo**。

### 隔離事實（先理解再操作）

| 項目 | 主 repo | 各 worktree |
|---|---|---|
| `command-dashboard/data/ics.db`（帳號 / first-run 狀態）| 你的正式資料 | **各自一份**，多半是空/半空 DB |
| `command-dashboard/data/map_config.json` | 正式 | 各自一份 |
| code（tracked 檔）| — | 共享 `.git`，branch 隔離 |

`DATA_DIR` 由程式碼檔案位置（`__file__`）推導，所以「從哪個 worktree 起 server，就讀寫那個 worktree 的 `data/`」。

### 常見陷阱：worktree 起的 server，地圖「只剩底圖能動」

**症狀**：登入後地圖底圖可縮放/平移，但畫 zone / route / 拖事件 / 任何寫入都失效。
**根因**：worktree 的空 DB **first-run 首次設定沒完成** → `first_run_gate` 對所有 `/api/*` 回 **423 Locked**；底圖走 `/static/`（白名單）所以照動，其餘全擋。
**判定**：`curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/api/map_config` → 423 即中獎。
**解法**（擇一）：
1. 從**主 repo** 起 server（`cd <主 repo> && ./start_mac.sh`），用已完成 first-run 的正式 DB。
2. 在該 worktree DB 完成 first-run：用 `~/.ics/first_run_token` 的初始 PIN 登入 admin → 改 PIN（`POST /api/auth/change-initial-pin`）→ gate 解除。

### Merge 後清理 worktree

merge 進 main 後，worktree 已無獨有的 tracked 內容（獨有的只剩可丟棄的 `data/`），**可安全移除**。規則：

1. **用 `git worktree remove <path>`，不要 `rm -rf`** —— `rm` 會留下 `.git/worktrees/` metadata 殘骸，變成孤兒登記。
2. **砍前確認**：該 worktree branch 已 merge 進 main（`git rev-list --count main..<branch>` = 0）且 `git status --porcelain` 乾淨。**有未提交/未 merge 的工作不准砍**。
3. **不能砍自己所在的 worktree** —— 先 `cd` 回主 repo 再 remove。
4. **清孤兒登記**：`git worktree prune`（掃掉已被 `rm` 掉但登記還在的）。

```bash
# 標準清理流程(在主 repo 跑)
cd <主 repo>
git worktree list                                   # 先看現況
git -C <wt> status --porcelain                       # 確認乾淨
git rev-list --count main..<branch>                  # 確認 = 0(已 merge)
git worktree remove .claude/worktrees/<name>         # 安全砍
git worktree prune                                   # 清孤兒
```

> git 操作仍遵守 CLAUDE.md：**等 Human 明確指示才動**。本節是「怎麼判斷可砍 + 怎麼砍對」，不是授權自動砍。

---

## CLAUDE.md 的角色

CLAUDE.md 是 **policy SoT**（紅線、語言、git 規則、版號規則）。
本文件是 **process SoT**（工作流、command 怎麼下、subagent 怎麼用）。
[ROADMAP.md](ROADMAP.md) 是 **work SoT**（要做什麼）。

三者各司其職，不互相覆蓋。
