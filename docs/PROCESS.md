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
| 5. **Human verify** | `/verify`，並在 prompt 指明「跑 PR #N 描述裡的 §8 script」 | `/verify` skill 啟動 app + 跑 script + 截圖 |
| 6. **Code review** | `/code-review` | skill spawn subagent 看 diff 找正確性 bug |
| 7. **Security review** | `/security-review` | skill spawn subagent 看供應鏈 / auth / CSP |
| **7.5 Quality gate** | （Arch 指示 Code 跑）`python3 scripts/doc_sync_check.py` | exit 0 才可 merge；非 0 → 回 CA 修 doc-vs-code drift |
| 8. Merge + push main | （Code ask）「merge + push main ok?」→ 你回 `ok` | `gh pr merge --squash` + `git push origin main` |
| 9. Tag（若版號升） | （Code ask）「tag command-vX.Y.Z ok?」→ 你回 `ok` | `git tag` + `git push --tags` |
| 10. Memory（若有非顯而易見決策）| （Code ask）「memory 寫 X，ok?」→ 你回 `ok` | 寫 `.claude/memory/<slug>.md` + commit |

**Human 真正打的字**：6 個 `ok` + 3 個 `/skill`。其餘 Code 自為。`doc_sync_check` 由 Arch 在步驟 7.5 自動把關，**不需要你動手**。

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

## CLAUDE.md 的角色

CLAUDE.md 是 **policy SoT**（紅線、語言、git 規則、版號規則）。
本文件是 **process SoT**（工作流、command 怎麼下、subagent 怎麼用）。
[ROADMAP.md](ROADMAP.md) 是 **work SoT**（要做什麼）。

三者各司其職，不互相覆蓋。
