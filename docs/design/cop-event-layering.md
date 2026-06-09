# COP 分層模型：感知標記 / 事件 / 決策 / 下行

> **狀態**：設計 doctrine（2026-06-09 確立，#176 / P2-13 設計對話衍生）。
> 本文是 **ICS COP 資料分層的 SoT**。對應 ROADMAP 缺口項 **P2-27 ~ P2-30**（Phase 2，TTX 前）。
> 相關：[[tak-marti-authz-model]]（下行機制）、[strategy §1.1](../roadmap/tak-integration-strategy.md)（server-authoritative）。

## 一句話

ICS 是**多源 COP 的匯流 + 指揮中樞**：各路「感知標記」匯成一張共享態勢圖（可**雙向**流向 TAK client）；
指揮部把多起感知**收斂成「事件」**進入「追蹤 → 決策 → 行動 → 結案」流程——**事件留指揮部，不外流**。

## 全景（canonical，使用者 2026-06-09 定）

1. **感知標記（sensing markers）**：TAK client 的 marker、**無線電回報的手動 marker**、（未來）**WaveInk RF 足跡**——這些感知標記都顯示在 ICS map。
2. **標記可與 TAK client 共享**（雙向 COP）。
3. **多起感知標記收斂成一個需被追蹤的事件（event）**。**事件留在 ICS 指揮部，不分享出去**。
4. 事件追蹤**包含指揮官的決策（decision）**。
5. 事件**產生後續行動**（下行指令 / 派任）。
6. 指揮部**持續追蹤至結案（resolved）**。

## 兩層 + 「來源無感」原則

| | **感知層（COP 標記）** | **事故層（事件 / 決策 / 行動）** |
|---|---|---|
| 本質 | 位置性、即時態勢快照 | 流程性、有生命週期 + 問責 |
| 成員 | marker / 線 / 區 / 單位 | event / decision / tasking |
| `source` | tak · manual（無線電上車口）· waveink · command · pi-node | —（ICS 內部） |
| 對 TAK | **可共享（#2，雙向）** | **不外流（#3）** |

**來源無感（關鍵）**：感知層**對來源無感** —— 不論 `tak`（裝置送）、`manual`（幕僚聽無線電鍵入）、`waveink`（RF）、`command`（指揮部下達），**只要是感知標記就屬同一張共享圖、都可流向 TAK client**。「共 COP」不是某個功能，是**感知層本身的雙向性**：進得來（P2-04 已做）就該出得去（P2-13 + 幾何序列化）。
> ICS 因此是「**只有無線電的單位**」進入 TAK 共享圖的**手動上車口**：非 TAK 報 → 幕僚鍵入 → 全部 ATAK 看得到。

### COP 感知層的兩態：觀察 vs 指令（`planned` 區分）

感知層**同時承載兩種語意的圖元**，由 `planned` 旗標區分——這是「指令（directive）」在分層中的定位（它**既非純觀察、亦非事件**，是 COP 層的第三態）：

| 態 | `planned` | 語意 | 典型 source |
|---|---|---|---|
| **觀察（observed）** | `false` | 「**已經在那**」——敵蹤、我方位置、感測足跡 | tak / manual / waveink |
| **指令（directive）** | `true` | 「**要去那 / 要發生**」——計畫指令、派任、行動點 | command |

兩態都是 COP 圖元、都走同一份下行基建外推；視覺上 `planned=true` = 2525 空心框（計畫）、`planned=false` = 實心框（實際）。P2-13(A) 下行已正確產出 `source=command, planned=true`。

## 報 → 事 → 決 → 行 → 結案 鏈

```
chats(報/通聯)  →  events(事)  →  decisions(決)  →  下行 tasking(行)  →  追蹤至 resolved(結案)
```

四者目前**散在四張表、彼此無導航**（缺口 P2-27）。模型要求可從任一節點導航全鏈（看一樁事的「報→事→決→行」完整脈絡）。

## 事件 vs 標記的梳理（核心，使用者 2026-06-09 提）

- **現狀**：建 event 時前端配一個 `cop_entity`（`attributes.kind='event'` + `event_id`），**JSON 名義綁定、無 FK、只能 1:1** —— 把「感知標記」與「工作流事件」黏在一起。
- **模型**：
  - **感知標記 = 一等公民**（感知層，可共享 #2）。
  - **事件 = 工作流物件**（事故層，留 ICS #3），**N:1 聚合**一或多個感知標記。
- **梳理方向**：把標記**分出來**當 sensing entity（可共享）；事件 **reference**（聚合）一或多個標記，不自帶 JSON-glued pin。分出來的標記即屬 #2 可共享集合。

## 下行共用基建（P2-13 / P2-30 邊界釐清）

「推 CoT 出去」有**兩種意圖**，但**共用一份基建**，不可各做一套：

```
            ┌─ P2-13：發「指令」（directive，planned=true）──「要發生的」
共用下行基建 ─┤
（send_cot + 幾何序列化 + audit + 共享閘）
            └─ P2-30：分享「既有感知標記」（observed）──「已觀察的」
```

- **共用基建**（一次建好）：`tak_downlink.send_cot`（A 已做，點）+ **`geometry_service.geometry_to_cot` / `build_geometry_cot`**（線/區，P2-30）+ audit + **per-entity 共享閘**（P2-30）。
- **P2-13** = 此基建的消費者之一（發指令）；**P2-30** = 另一消費者（分享觀察標記）+ 提供線/區序列化與閘。
- **依賴**：P2-13 要下「線/區指令」須等 P2-30 的幾何序列化器 → **故先做 P2-30 共用基建，再回頭補 P2-13 B/C**（2026-06-09 定路線 2）。
- **指令 ↔ 事件**：自事件衍生的派任（#5）其指令應可連結 originating event（依賴 P2-27 event↔marker 關聯）；P2-13 B/C 設計不得堵死此路。

## 不外流邊界（安全 doctrine）

**事件本體**（`status` / `decision` / `assigned_unit` / 問責鏈）**不推 TAK**。出向只有兩條：
1. **感知標記**（#2，含手動/衍生標記）——經 per-entity 共享閘（P2-30）。
2. **自事件衍生的派任指令**（#5 → P2-13 下行）。

## 缺口（→ ROADMAP Phase 2，TTX 前）

| 項 | 缺口 | 現狀（實證） |
|---|---|---|
| **P2-27** | 感知標記 / 事件 / 決策 **分層 + 導航鏈 + 事件梳理（標記分出）** | event↔cop 靠 attributes JSON（無 FK、會孤立、僅 1:1）；decisions 又獨立；報→事→決→行 四表割裂 |
| **P2-28** | **入向升級**：現場回報自動成 tracked event | MEDEVAC/GeoChat → COP/chats，**不進 events**；重大回報只在地圖閃 icon，無流程追蹤 |
| **P2-29** | **角色 / 分層感知地圖視圖** | map filter 僅 3 維（來源-kind / TAK affiliation / stale）；`cop_entities.visible_to` 預留未用；指揮官與幕僚看同一張圖 |
| **P2-30** | **感知標記共享閘 + 手動感知標記（無線電上車口）+ 線/區出向幾何序列化** | 無 per-entity「推 TAK」flag；手動感知標記非命名能力；`geometry_service` 只有入向 `extract_geometry`，無出向 `geometry_to_cot` |

> 位置 SoT 三份（events.lat/lon 死 + cop_entity.lat/lon 活 + location_zone_id）併入 P2-27 一起清。
