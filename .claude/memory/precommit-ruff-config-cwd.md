---
name: precommit-ruff-config-cwd
description: "pre-commit hook cd's into command-dashboard/ so ruff uses line-length=120; format root-level scripts with that config or the commit aborts"
metadata:
  node_type: memory
  type: project
  originSessionId: 597804a2-beeb-4f4c-8d8e-39c6b113a466
---

`.githooks/pre-commit`（`core.hooksPath=.githooks`）會 `cd command-dashboard` 再跑 `python3 -m pre_commit run`。因此 pre-commit 的 ruff / ruff-format 用的是 **`command-dashboard/pyproject.toml`**（`line-length = 120`、ruff rev **v0.11.7**），而非 repo 預設（88）。

**症狀**：改 repo root 層級的檔（如 `scripts/doc_sync_check.py`）後 commit，會無限迴圈「ruff-format Failed → files were modified → Stashed changes conflicted → Rolling back fixes」，commit 一直 abort。用 `python3 -m ruff format`（本機新版 0.15.11，預設 88）格式化反而與 pre-commit 不一致，越改越錯。

**How to apply**：格式化 root-level 檔時用 pre-commit 那把 ruff + 該 config：
```bash
RUFF=$(find ~/.cache/pre-commit -name ruff -type f | head -1)   # v0.11.7
$RUFF format --config command-dashboard/pyproject.toml <file>
$RUFF check --fix --config command-dashboard/pyproject.toml <file>
```
然後 `git add` 再 commit。`command-dashboard/` 內的檔不受影響（ruff 直接找到該 pyproject）。

**Why**：pre-commit 的 stash/restore dance 在「hook 改了檔 + 有 unstaged 殘留」時會整批 rollback，所以差一個空白都會卡死。相關 [[git-dual-remote-push-flow]]。
