# COP marker / event 解耦：操作與呈現設計

> **本文定位**：marker / event 解耦的**操作流 + 呈現設計 + 緣起（founding-why）** 的 SoT。
> 與 [`cop-event-layering.md`](cop-event-layering.md)（**分層 / 資料模型 / 全棧 SoT**）分工：那份講「有哪些層、資料怎麼流」；本份講「指揮官**怎麼操作**、畫面**怎麼呈現**、以及**為何**這是系統脊椎」。
> 壓縮版北極星見 [memory `cop-marker-event-decoupling`]。建立於 2026-06-10（與使用者釐清 P2-33 時長出）。

---

## 0. 緣起（founding-why / 北極星）

某次演習中，**第一波協同攻擊**打下來時，當初以為的「幾乎所有事件」（發現無人機、疑似爆裂物、接觸點…）其實都是**同一隻手的各個切面**。指揮部**窮於應付一個個孤立事件**（打地鼠），**只有事後 AAR 復盤、時間軸攤開，才窺見全貌**。

**這個「只有 AAR 才看得到的協同結構」，就是本系統要提前到交戰當下呈現的東西。**

推論（指導往後取捨）：
- 若每個感知都黏死成孤立「事件」→ 結構上**無法重組**，全貌永遠等 AAR。
- 若每個感知都是 **marker 原子**、事件層在上面聚合 → 協同網**能在當下被組起、被看見**。

**故 marker-first 解耦 + N:1 即時聚合 = 系統脊椎，不是 polish。**

---

## 1. 模型：三分

| 角色 | 是什麼 | 載什麼 | 數量關係 |
|---|---|---|---|
| **marker** | 感知層**原子**（可獨立存在 / 共享）| **觀察類型**（drone / explosive，= NAPSG type）、位置、時間、來源 | — |
| **event** | 事故層**可選工作流外殼**（留 ICS）| **事件分類**（如「周界滲透」）、嚴重度 / 狀態 / 指派 / 期限 / 裁示 | reference **≥1** marker |
| **`event_markers` junction** | **唯一權威關聯** | `role`（primary / related）、**可 re-parent** | N:1（N:M-ready）|

**核心原則：「事件性」不寫進 marker。** 同一顆 marker 是「自成事件」還是「大事件的一塊」，由**事件層**決定、且**事後可改**。

```
感知進來 ──► marker（原子，觀察類型）       ← 身份固定、不變
                  ▲
         event_markers junction（role, 可 re-parent）
                  ▼
              event（外殼，事件分類）         ← 聚合 ≥1 marker
```

---

## 2. 一個感知結果的命運（triage 三態 + 例）

**例**：演習中「發現無人機」「疑似爆裂物」進來。解耦後**一律先落成 marker**，身份固定；「是不是事件、屬不屬於大事件」由事件層決定：

```
「發現無人機」 → marker_D（觀察類型=drone）
「疑似爆裂物」 → marker_E（觀察類型=explosive）

三種命運（可互轉，marker 不重寫）：
A. 各自獨立： event_A ─primary→ marker_D ; event_B ─primary→ marker_E   （兩個 1:1 小事件）
B. 同屬大事件：event_X「周界滲透」 ├─related→ marker_D └─related→ marker_E （一個 N:1 大事件）
C. 先 A 後 B（re-parent）：本來各自獨立，後判定同一波 → 把 marker 重 link 到 event_X、
                            原 event_A/B merge 或 retire → 只動 junction、marker 不動
```

**triage 三態**（marker 進來後指揮官判級）：
1. **升級為獨立事件** → 建 event-row + junction(primary)（= case A）
2. **關聯進既有大事件** → junction(related)，不建新事件（= case B）
3. **留裸標記**（0 事件）→ 先記著，稍後再判（迷霧中先不立案）

→ 解耦的全部意義：**事件層說它自成事件就自成、說它是一塊就是一塊，且事後能改。**

---

## 3. 降級（decouple）：從今天的融合到三分

**今天（融合 1:1）**：建 event 時前端**同一動作做兩件事**——`POST /api/events`（events 表 row）+ `createEntity({attributes:{kind:'event', event_id}})`（cop_entity 圖釘），靠 `attributes.event_id` **JSON glue** 黏死、無 FK、僅 1:1。

**降級＝把兼任圖釘的 `cop_entity(kind='event')` 退回純標記身份**：
- kind `'event'` → `sighting` / `contact`（**對齊 P2-30 part-3 詞彙**）
- 停寫 `attributes.event_id` glue
- `events` row 改用 **junction** reference 其 primary 標記
- **降級後事件在地圖上沒有自己的圖釘**——地圖看到的是 marker，事件是 marker 之上的**聚合層**（這讓「事件層 / 感知層」分層視圖 P2-29 才分得乾淨）

**遷移安全**：`event_markers` 在 m021 建表時**已 backfill** 既有 `kind='event'` pin 的連結。降級後現有事件**預設各自獨立 1:1 解耦對**（= case A），**無資料遺失、行為等價於今天**；N:1 聚合是**事後**人工 / 輔助判級長出來，非遷移本身。

**降級硬約束**（reality check 2026-06-10）：junction 目前**被 glue 餵養**（`routers/cop.py` 讀 `attributes.event_id` 才 `link_marker`）、前端 `zone.event_id` **唯一來源 = glue**。故**不可 remove-first**：先加 first-class 連結（cop create 帶頂層 event_id + 序列化從 junction 解 event_id）→ 遷前端 → **才**刪 glue。

---

## 4. N:1 聚合：操作

**主流程（事件視角・多選聚合）** — 重用 P1-16 放置模式 idiom（banner + 點地圖 + 確認 / 取消）：
```
1. 地圖上已有 n 個感知標記（各自獨立 cop_entity）
2. 開事件 modal → 「關聯標記」區 → 「＋關聯標記」
3. 進「多選模式」：頂部 banner「點選要關聯的標記，完成按確認 ✓ (已選 0)」
4. 逐一點標記 → 套「選取環 + 序號」、計數 +1（再點取消）
5. 「✓ 確認關聯」→ 一次寫 n 筆 junction（link_marker，首顆 role=主、其餘 related）
6. 「關聯標記 (n)」列出全部；banner 收掉
```

**輔助流程**（補入口）：
- **標記視角**：點感知標記 → 「歸入事件 →」選既有事件 /「升級為新事件」。適合**逐筆增補**。
- **自動 / 輔助**：從標記「升級為事件」自動 link 原標記為主；或系統**提示**「時空相近 N 顆可能同一波」，人**確認**即聚合（過載下不需手動 link 每顆 — 見 §5 終局）。

---

## 5. N:1 聚合：呈現（「不目不暇給」的鐵則）

**三條鐵則**：
1. **關聯是資料、預設隱形** —— 沒在看任何事件時，地圖**零連線零閃**。
2. **視覺一律 on-demand + 限 active 單一事件 + 一次一顆 / 可關閉** —— 你同時只在看一個事件，不可能「全部 event-marker 對一起閃」。
3. **清單優先、地圖效果次要** —— 文字清單(n) 是主力（零閃）；flyTo / 連線是輔助、要主動觸發。

**四個呈現態**：

| 態 | 地圖呈現 | 生命週期 |
|---|---|---|
| **靜止** | 完全隱形（只照常顯示 marker / event）| — |
| **選取中**（§4 step 4）| 選中標記套**穩定選取環 + 序號**，其餘**不暗化** | 進多選模式 → 確認 / 取消 |
| **看某事件**（modal）| 「關聯標記 (n)」**文字清單**；點一顆 → flyTo **那一顆** | modal 開著 |
| **長按事件「窺視關聯網」** | 查 junction → 亮「**事件 + n 標記**」、其餘**灰**、畫**細線**；放開**復原** | **press / release 瞬時、不全域** |

**兩種呈現互補**（不是二選一）：

| | 長按「窺視關聯網」 | modal 清單「逐標記導航」 |
|---|---|---|
| 性質 | **瞬時 peek**（按住看全貌、放掉走人）| **持續 navigate**（開著、可點某顆精準飛過去）|
| 適合 | 「這事件牽連哪些、散在哪」一眼 | 「帶我去處理第 3 顆」 |
| 實作 | 擴 `_highlightEvent` 的 press/release（已有半套：右側卡長按 + dim others + flyTo），加「放過 n 個關聯標記 + 細線」 | events.js modal 內清單 + 單顆 flyTo |

> ⚠️ **避免地圖 pin 手勢過擠**：事件圖釘已背 點擊(modal) + 拖移(move)，再塞「按住不動→web」是第三種，易誤觸 → 長按 web 以**右側事件卡**為主入口；地圖側若做，門檻要切（移動=拖、定住=web、快放=modal）。

**終局交付物（founding-why 的兌現）= 即時長出來的事件全貌視圖 + 輔助聚合**：marker 一個個進來時，指揮官能把它們併進**正在成形的大事件**、一眼看見協同網——把 AAR 上帝視角搬到 live。過載下靠**輔助聚合**（系統提示時空相近群、人確認）降低手動成本。AAR 回放地基（`snapshot_repo` + `version_clock`）已有，目標是**前移到即時**。

---

## 6. 兩 type 軸分離

大事件逼出的決定：**事件要有自己的分類，不繼承單一 marker 的觀察類型**。

| 軸 | 載於 | 例 |
|---|---|---|
| **觀察類型**（看到什麼）| **marker** | drone / explosive（= 今天的 NAPSG event_type）|
| **事件分類**（這是什麼事故）| **event** | 「周界滲透」大事件（≠ 任一 marker 的 type）|

標準 1:1 兩者重合；N:1 時 **event 自成一格**。接 [`event-symbology-classification`](../../command-dashboard/docs/design/classification-crosswalk.md) 的 NAPSG type-first 流程（解耦後 type-first = marker 觀察類型）。

---

## 7. 與其他文件 / ROADMAP item 的關係

- **資料 / 分層 SoT** → [`cop-event-layering.md`](cop-event-layering.md)（§「事件 vs 標記的梳理」指向本文要操作 / 呈現細節）
- **壓縮北極星（AI recall）** → memory `cop-marker-event-decoupling`
- **符號 / 分類** → `command-dashboard/docs/design/classification-crosswalk.md`（§6 LOCKED）

**ROADMAP 落地**（[`../ROADMAP.md`](../ROADMAP.md)）：
- **P2-27**（parent / 設計錨）：分層 + 梳理 + 導航鏈；ownership 已下放 P2-31/32/33。
- **P2-28**（入向升級）：報 → **marker → triage**（含關聯既有大事件，**非自動生孤立事件**）。
- **P2-29**（角色 / 分層視圖）：事件層（解耦後無自身圖釘）/ 感知層 toggle；即時全貌視圖同源。
- **P2-30**（手動感知標記）：part-3 sighting/contact = 第一批純標記，與降級同套 kind 詞彙、可被 N:1 聚合。
- **P2-33a/b/c**：a=唯讀導航鏈清單 / b=**降級**（§3）/ c=**N:1 即時聚合（脊椎，§4-5）**。
