---
name: cop-collaborative-edit-shared-truth
description: COP doctrine（使用者 2026-06-14 拍板）—— 地圖資料以「當前真實」為準、共享協作維護；任何掌握可信變動者（原標圖者/隊友/指揮部）皆可編輯/移動/刪除，並即時 propagate，不因『誰建立』而 silo。對齊 TAK streaming last-write-wins，**撤掉 #146「實戰鎖死外部來源」防呆**；威脅不消失，改由 audit 問責 + P2-10 內容白名單 + _PUT_FORBIDDEN_FIELDS + RBAC 緩解。刪除仍受 TAK infra 限制（archived 不可靠 propagate，例外）。
metadata:
  type: project
---

# COP = 共享真實、協作可編輯（撤 #146 外部來源唯讀）

**使用者 2026-06-14 拍板**（ATAK 真機 e2e 衍生，sensor marker 雙向對稱討論）。

## Doctrine（一句）
**地圖資料以「當前真實」為準、由全體協作維護。任何掌握可信變動的人 —— 原標圖者、隊友幫忙、或指揮部收到可信回報 —— 都能編輯/移動/刪除任一 sensor 物件並即時 propagate，不因『誰建立』而 silo。** 對齊 TAK streaming 的 last-write-wins（任何 client 用同 uid 蓋掉/刪掉任一物件，group 內全開）。

## 為何（使用者理由）
現場環境動態變動：標圖的人可能無法即時回應、或隊友幫忙回應、或指揮部收到可信的變動。不論哪種狀況，**地圖上的資料要真實並共享** > 維護「建立者擁有權」。

## 這推翻了什麼
- **#146 / threat_model §8.2「TAK-B reverse」的「實戰模式鎖死外部來源」防呆**：原 `_require_editable_source`（`cop.py`）只准 `manual`/`command` 編輯、`tak`/pi-node/waveink 實戰鎖死。改為 **external 來源也可編輯/移動/刪除**（演習本就已開，現實戰也開）。
- 釐清的誤框架：「不改你不擁有的東西」**不是 TAK 邏輯**（TAK 無 streaming ownership）；#146 是 ICS 單方面加的防呆，且威脅在每個 TAK client 都存在 → 單方面鎖只擋誠實 HQ 誤觸，擋不了惡意者 = defense-in-depth 非真邊界。

## 撤防呆後保留的 guardrail（威脅沒消失，換問責機制）
- **audit-first 成為關鍵 backstop**：對 external 來源的編輯/移動/刪除**必須 audit**（誰/何時/改了什麼）= 新的問責主力（須確認 cop 操作 audit 涵蓋 external 來源編輯）。
- **P2-10 內容白名單**（座標越界拒、callsign 字元、type prefix）—— integrity/content 守門，留。
- **`_PUT_FORBIDDEN_FIELDS`**（uid/source/version_clock 不可竄）—— 防偽造來源/碰撞，留。
- **RBAC**（WRITE_ROLES；zone/infra 仍 COMMAND_ROLES）—— 留。
- **exercise-scope 仍 server-authoritative**（[[exercise-scope-server-authoritative]]，正交，不放）。

## 例外（使用者明確認的）
**刪除無法即時 propagate** —— external 物件多帶 `<archive/>`，TAK server 持久層不認 streaming t-x-d-d、reconnect 重播會 revive（[[tak-streaming-archive-stale-vs-mission]]）。故「刪除」實際是 best-effort（本地 soft-delete/hide + best-effort t-x-d-d），不保證現場端同步。view-level hide/declutter 是其可靠退路（#217）。

## 落地（拆 issue）
- **Issue α（自建物件編輯 UI，無 doctrine）**：ICS 對 source=manual/command 的 zone/route 補 move/reshape、marker 補 edit（後端早准，純前端 + 即時重推 CoT）。
- **Issue β（doctrine 改 + 安全）**：放開 `_require_editable_source` → external 可編輯/移動/刪除 + propagation + **threat_model §8.2 改寫**（TAK-B reverse「實戰鎖死」→「協作可編輯 + audit 問責」記殘餘風險）+ audit 強制 + 完整 `/security-review`。
- 刪除半併 β（best-effort），關聯 #217（view-hide 退路）。

承 [[tak-marti-authz-model]]（TAK 授權實情）、[[tak-streaming-archive-stale-vs-mission]]（刪除/archive 限制）、[[cop-marker-event-decoupling]]（sensor marker 為感知層原子）。是「TAK↔ICS 功能對等」原則（ROADMAP P2-35/#256）在「互動動作對稱」面的延伸。
