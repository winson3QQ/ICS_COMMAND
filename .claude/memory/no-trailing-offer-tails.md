---
name: no-trailing-offer-tails
description: 回覆結尾不要掛「要不要我…/還是…？」的可選小尾巴；決定後直接執行
metadata:
  node_type: memory
  type: feedback
  originSessionId: 0ce2986b-a122-4dc9-a757-37f02a55d890
---

使用者 2026-06-24 明確指示：「不要每次都留個小尾巴」。指做完一段工作後，結尾又附一個「要不要我做 X，還是 Y？」的可選提問。

**Why:** 使用者已建立信任、要的是我**決定方向並直接執行**，不是每步都回去要授權。反覆的小尾巴拖慢節奏、把該我承擔的判斷推回給他。

**How to apply:** 做完 reality check / 一段工作後，依分析**自己選定下一步直接做下去**，不在結尾掛可選提問。只有在真正是使用者才能決定、且我無法從脈絡/預設合理推斷的岔路，才用 AskUserQuestion 正式問——而非隨手附一句。git 操作（commit/push/tag/PR）仍照 [[CLAUDE.md 紅線]] 等明確指示，但那是「執行前確認」不是「結尾兜售」。
