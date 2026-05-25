---
name: process-workflow-boris-route
description: ICS_Command 採 Claude Code 內建 skill + 兩個自寫 quality gate，不蓋 AGENTS.md 框架
metadata:
  type: project
---

ICS_Command 工作流走「Boris 路線」——用 Claude Code 內建工具，不蓋自己的 agent 框架。

**內建 skill 對應**：
- `/plan` = Arch brief（大重構必跑、小改可省）
- `/verify` = Human ② verify 自動化（跑 §8 script）
- `/code-review` = IA cross-check
- `/security-review` = Arch compliance review

**自寫補缺**：
- `scripts/doc_sync_check.py` — task 結尾必跑（PROCESS step 7.5 quality gate）
- `scripts/roadmap_issue_sync.py` — 週期性 / phase 收尾跑

**角色**：3 個 conceptual roles（Arch=Chat、IA+CA=Code）只在 [[docs/PROCESS.md]] 概念存在，**不形式化 handoff**。Session 數彈性：
- 典型 task：2 session（Chat + Code）
- 大重構（如 P1-10b MapLibre 全替換）：3 session（IA / CA 獨立 cross-check）
- 小修（doc / typo）：1 session

**為何不蓋 AGENTS.md**：ICS_DMAS 75 KB AGENTS.md 自承「重型治理引發越權」(REV6 line 16)，且 ICS_Command 單產品線 + 單人開發，3 角色 19 handoff 是 ceremony tax。WaveInk 0 handoff + 3 個 script 的反例證明 dogfood pattern 可行。Boris 路線 = 工具負責 enforcement，PROCESS.md 負責記載對應，不發明新 abstraction。

**Dogfood 證實**：P1-01 第一輪跑就抓到 `doc_sync_check.py` 自己的 PROCESS.md path drift（commit `3b7da18`）——quality gate 值得做。

參考：[[architecture_decisions]]、[[source-of-truth-remote]]
