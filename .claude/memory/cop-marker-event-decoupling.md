---
name: cop-marker-event-decoupling
description: COP marker/event 解耦的 founding-why 與目標模型 — 演習第一波協同攻擊被當成 N 個孤立事件、指揮部窮於應付、只有 AAR 才見全貌 → 催生本系統；marker=原子 / event=工作流外殼 / junction=唯一關聯，N:1 即時聚合是脊椎非 polish
metadata:
  type: project
---

# COP marker/event 解耦：founding-why 與目標模型

**北極星（系統存在的理由）**：某次演習中，**第一波協同攻擊**打下來時，當初以為的「幾乎所有事件」（發現無人機、疑似爆裂物、接觸…）其實都是**同一隻手的各個切面**。指揮部**窮於應付一個個孤立事件**（打地鼠），**只有事後 AAR 復盤、時間軸攤開才窺見全貌**。這個「只有 AAR 才看得到的協同結構」就是本系統要**提前到交戰當下**呈現的東西。

**故 marker-first 解耦 + N:1 即時聚合 = 系統脊椎，不是 polish**：若每個感知都黏死成孤立「事件」→ 結構上無法重組、全貌永遠等 AAR；若每個感知都是 marker 原子、事件層在上面聚合 → 協同網能在當下被組起、被看見。

## 目標模型（2026-06-10 與使用者釐清）

- **marker = 原子**（感知層，可獨立存在/共享）：載**觀察類型**（drone / explosive，= 今天的 NAPSG event_type）。
- **event = 可選的工作流外殼**（事故層，留 ICS）：載**事件分類**（如「周界滲透」大事件，≠ 任一 marker 的 type）+ 嚴重度/狀態/指派/裁示，**reference ≥1 個 marker**。
- **junction `event_markers` = 唯一權威關聯**（role=primary/related，**可 re-parent**）。N:1（事件聚合多標記），N:M-ready。
- **兩 type 軸分離**：觀察類型 on marker / 事件分類 on event。標準 1:1 兩者重合，N:1 時 event 自成一格。

**「事件性」不寫進 marker**：同一顆「發現無人機」原子，是**自成獨立事件**還是**大事件的一塊**，由事件層決定且**事後可改**（triage 三態：升級為獨立事件 / 關聯進既有大事件 / 留裸標記 0 事件）。

**降級（decouple 的核心動作）**：今天兼任事件圖釘的 `cop_entity(kind='event')` **退回純標記身份**（kind 改 sighting/contact，停寫 `attributes.event_id` glue）；`events` row 改用 junction reference 它。事件本體（events 表那行）留著、不消失。降級後事件**在地圖上沒有自己的圖釘**——地圖看到的是 marker，事件是 marker 之上的聚合層。

**Why**：這是非顯而易見的系統原點，不在 code/git history 裡，但指導往後每個 P2-27/28/29/30/33 的取捨。

**How to apply**：
- 動 COP 事件/標記模型時，永遠回到「marker 原子 / event 外殼 / junction 唯一關聯」三分。
- **P2-28 入向升級不得「自動成孤立事件」**——report → marker → triage（且 triage 必含「關聯進既有大事件」），否則親手重造迷霧。
- 遷移安全：降級後現有事件**預設各自獨立 1:1 解耦對**（m021 junction 已 backfill），N:1 聚合是事後人工/輔助判級長出來。
- 降級 marker 的 kind 須與 [[p2-30-part3-handoff]] 的 sighting/contact 詞彙**對齊**（part-3 正在種第一批純標記，與降級標記同源收斂）。
- 終局交付物 = **即時長出來的事件全貌視圖 + 輔助聚合**（系統提示「這 N 顆時空相近、可能同一波」，人只需確認）——把 AAR 上帝視角前移到 live；AAR 回放地基（snapshot_repo + version_clock）已有，目標是前移。

長 form 設計 SoT（操作 / 呈現 / 緣起全文）= `docs/design/cop-marker-event-decoupling.md`；分層 / 資料模型 = `docs/design/cop-event-layering.md`。關聯：[[event-symbology-classification]]（NAPSG type-first，解耦後 = marker 觀察類型）、[[exercise-scope-server-authoritative]]、[[p2-30-part3-handoff]]。
