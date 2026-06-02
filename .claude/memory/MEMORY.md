# ICS_Command 專案記憶索引

由 [ICS_DMAS](https://github.com/winson3QQ/ICS_DMAS) 於 2026-05-25 拆分而來的指揮部單體版本。
ICS_DMAS 的 memory（行為規則、架構決策、HTTPS 決策、Remote SoT、演練排程等）仍適用於本專案，請參照原 repo `.claude/memory/`。

## 本 repo 特有

- [Boris 路線工作流](process-workflow-boris-route.md) — Claude Code 內建 skill + 兩個自寫 quality gate；3 角色不形式化 handoff
- [事件符號 / 分類體系決策](event-symbology-classification.md) — NAPSG/CoT/台灣 四軸對照、視覺 affiliation-aware（敵我=2525框/類型=NAPSG象形/severity=halo）、type-first 建立流程、字典擴充模型；#66 編輯器(剩 C2) / #64 硬化(剩 #3→P1-10h) 狀態 + 環境 quirk
