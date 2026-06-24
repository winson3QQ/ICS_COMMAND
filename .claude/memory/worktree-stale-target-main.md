---
name: worktree-stale-target-main
description: 稽核/修補/review 前必確認 worktree 是否 stale；prod SoT = 主 repo Desktop\ICS_COMMAND 的 main，skills/agents 預設 cwd=worktree 會審錯 diff
metadata:
  node_type: memory
  type: feedback
  originSessionId: 256d8b90-93cb-49e3-b78d-e85a825bd46b
---

ICS session 常在 git worktree（`.claude/worktrees/<name>`）啟動，但**該 worktree 分支可能 stale**（落後 main 數十 commit）。

**事實（2026-06-20 踩到兩次）**：
1. 紅隊稽核若對 worktree（stale bfa823d）跑，會審到舊 code、漏掉 main 已上線的硬化 → 假結果。
2. `/security-review`、`/code-review` 等 skill 與 Agent 預設 **cwd = worktree** → 自動算的 `git diff` 是 worktree 的 stale diff，**不是你在主 repo 改的 fix 分支**。security-review 因此審了完全無關的舊變更。

**How to apply**：
- 動稽核/修補/review 前先 `git -C <主repo> rev-parse main` vs worktree HEAD，確認標的。
- **prod 真身 = 主 repo `C:/Users/yello/Desktop/ICS_COMMAND` 的 main**（見 [[deployment-topology-windows-docker]]）。實際修補、跑測試、git 操作都在主 repo 進行（用絕對路徑），不要在 stale worktree 改。
- 跑 skill/agent 做 diff review 時，**明確叫它對 `git -C <主repo> diff main`**，否則它審 worktree 的錯 diff。
- pytest 也要 `cd 主repo/command-dashboard` 才測到真 code。

**Why**：worktree 與主 repo 共用 object store 但各自 HEAD；工具預設只看自己 cwd 的分支。
