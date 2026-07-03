# 演習/實戰＝記錄窗口 + faction 可見性模型（重設計）

> **本文定位**：把「演習/實戰生命週期」「可見性過濾」「AAR 記錄」三者的關係**重新定義**的設計 SoT。
> 建立於 2026-07-03（與使用者逐點釐清後長出）。**演進、部分取代** [`red-blue-faction-isolation.md`](red-blue-faction-isolation.md)
> 的「可見性以 exercise_id scope 為軸」部分——本文改為 **faction 為可見性軸、exercise_id 降為記錄歸屬鍵**。
> 動工前的 reality check 已做（見 §7）；tracking issue 見文末。

---

## 0. 緣起（founding-why）

現行 code 把兩件事綁在同一個 `exercise_id` scope 上：**(a) AAR 要記哪場的資料**、**(b) 誰看得到什麼**。這造成
[Problem]：演習啟動後，operator（非指揮層）只看得到「綁本場」的 entity，而演習前連進來的 TAK 單位 / 自建 route 是
NULL（常駐）→ **operator 進演習後整個 COP 空掉**（常駐疊看限 COMMAND）。使用者實測撞到（2026-07-03）。

**核心決策**：把兩件事**拆開**——
- **演習/實戰 ＝ 一段「記錄窗口」**（開始記錄→結束停止；供 AAR）。TTX 與實戰**機制完全相同、只是名詞不同**。
- **可見性改由 faction（紅/藍/中立）決定**，不再由 exercise_id scope 決定。`exercise_id` 只當**記錄歸屬鍵**。

---

## 1. 角色（承現行，不變）

| 角色 | 分組 | 定位 |
|---|---|---|
| 系統管理員 sysadmin | SYSADMIN_ONLY / 全見 | **白隊/導調（中立裁判）**——唯一能分類紅藍、看全 faction |
| 指揮官 commander | COMMAND_ROLES | **永遠藍隊玩家**（不經手分類，避免偷看紅隊名單） |
| 操作員 operator | WRITE_ROLES | 藍隊；日常畫圖/標記/廣播 |
| 觀察員 observer | READ_ROLES | 藍隊；唯讀 |

`visible_factions_for_session`：sysadmin→全見；其餘→{blue, neutral}。

---

## 2. faction 可見性規則（精確版——兩條並存）

> ⚠️ 不是「演習中才過濾」。是**兩條不同的規則疊加**：

| 裝置 faction | 平時（無 active 演習） | 演習/實戰進行中 |
|---|---|---|
| 已編 **藍/中立** | 可見 | 可見 |
| 已編 **紅** | **只 admin**（永遠藏，含平時） | 只 admin |
| **未分隊（unclassified）** | **可見** | **隱藏（fail-closed）** ← 「排除 NULL」的真義 |

- **「排除 NULL」＝排除未分隊的 TAK**，不是排除 `exercise_id=NULL`。演習中一個未分隊裝置＝身分不明＝**可能是紅隊**
  → fail-closed 先藏、等白隊裁定。平時無紅藍對抗，不明裝置無此顧慮 → 可見。
- **自建物件（source=manual/command）恆對藍可見**，不受 faction 過濾（承 #146 / 協作可編 doctrine）。
- **只 `source=tak` 受 faction 過濾**。

---

## 3. 生命週期

### 3.1 開場（僅 sysadmin/白隊）

演習/實戰**啟動精靈**（開場權收斂到 sysadmin，因為開場含分類、分類必須中立方做）：

1. **分隊**：把當下已連線 TAK client 分 **紅/藍/中立**（三選，非二選）。
2. **選擇性清圖**：選「這場帶哪些既有物件進來當初始條件」——**清上場殘留/stale，但保留永久設施等常駐底圖**。（非無腦全清）
3. **抓快照**：把當下地圖 entity 快照成**初始條件 baseline**（新增，現行 activate 不抓）。
4. **開始記錄**：開一筆 `exercise_active_intervals`（現有骨架）。

### 3.2 進行中

- **可見性**：command 以下只見 藍+中立 TAK + 自建物件；未分隊/紅 藏（§2）。
- **編輯**：外部 TAK 幾何**可編**（TTX=實戰，拆除 TTX-only 閘），走 audit 問責。
- **中途新連線**：未分隊＝藏 → **系統提示白隊分類**（接現有背景 poller）。
- **即時重分隊**：白隊改分類 → **即時傳播三軸**（地圖 SA marker / 通聯 GeoChat / 聚合視圖），沿用 #343 resync。
  優先「未分隊→藍」的加法；「藍→紅」修正會有已洩漏窗（人為分類錯誤，fail-closed 預設已壓到最小）。**重分隊本身也是一筆記錄。**
- **AAR**：`require_no_active_exercise` → 全員（含白隊）409 灰掉。

### 3.3 收場

- **停記錄**：關 `exercise_active_intervals` 該區間。
- **NULL 復見**：平時規則生效（未分隊可見、紅仍藏）。
- **AAR 開放**：對 **command+**（現況已是）開全貌——**繞過 faction 過濾**（唯一一處紅方對藍指揮曝光、且是事後）。
- **不清圖**：地圖 entity 保留（平時延續 + 下場開場快照的來源）。**stale 須自動灰化/過期**，免平時地圖被上場殘渣塞滿、免污染下場 baseline。

---

## 4. 公平性 invariants（缺一即破功）

1. **分類者恆為白隊（SYSADMIN_ONLY）**：commander 永遠藍隊，不得經手分類（否則偷到紅隊裝置名單）。
2. **開場權收斂 sysadmin**：因開場含分類。→ 每一場（含實戰）須有 sysadmin 在。
3. **fail-closed 未分隊**：不明裝置預設藏，不預設可見。
4. **#344 TAK group 隔離（另一半，硬前提）**：本設計只管「藍隊 ICS 看不到紅隊」；「**紅隊 ATAK 看不到藍隊 SA**」在 TAK 層（cert→group，#344）。**少了它整個公平性破功**，正式演習前須同場驗證。

---

## 5. 不偏離 AAR 初衷（faction ≠ affiliation）

- **faction＝哪一隊操作那台裝置**（cert 身分）；**affiliation＝標記代表什麼**（友/敵/中立/不明）。二軸正交。
- 「藏紅隊」**≠「看不到敵人」**：藍隊 operator 自己放的**敵軍標記**（source=manual、affiliation=敵）是自建物件、恆可見。
- 系統催生初衷（[[cop-marker-event-decoupling]]）＝把 AAR 的**聚合上帝視角前移到 live**——那個聚合是聚**藍方自己的偵測**
  （N marker→1 event），不需紅隊真值。**故本模型不背離**：live 聚合藍方偵測 ✓、AAR 事後給指揮官全貌 ✓。
- **實戰真相**：敵人不在你的 TAK server 上 → 實戰「紅 faction」幾乎空 → faction 過濾在實戰無害空轉，真正生效的只有「記錄 + 藏未分隊」。故「TTX=實戰」站得住。

---

## 6. 完整規則表（三情境 × 角色）

| 規則 | 平時 | 演習/實戰進行中 | 收場後 |
|---|---|---|---|
| TAK 可見（command 以下） | 藍+中立+未分隊 | 藍+中立 | 藍+中立+未分隊 |
| TAK 可見（sysadmin） | 全 | 全 | 全 |
| 自建物件 | 恆對藍可見 | 恆對藍可見 | 恆對藍可見 |
| 編輯外部 TAK | 可編（audit） | 可編（audit） | 可編（audit） |
| 建節點/設施 | COMMAND | COMMAND | COMMAND |
| AAR/timeline/回放 | 開放（command+） | **全員 409** | 開放（command+，全貌繞 faction） |
| 記錄動作 | 否 | **是**（record 窗口內） | 停 |

---

## 7. Reality check：現況 vs 新增/改（2026-07-03 讀 code）

**已有骨架（大半在）：**
- **開關**：`activate`/`archive` + `exercise_active_intervals`（#267，多區間活躍時段 log ＝記錄窗口）+ `ts_in_active_window`。
- **清**：`reset-db`（全清）/ `reset-exercise`（演習範圍清、保 NULL 池）。
- **AAR**：`timeline_service`（衍生 from tracks/events/decisions/chat）+ `aar_entries`（手記）；讀取**已是 COMMAND_ROLES**；`require_no_active_exercise` 進行中全員 409。
- **faction 三層強制**：cop.py（REST/WS）、chat.py、tak.py + `visible_factions_for_session` + #343 resync 即時重分隊。
- **exercise_id 綁定**：ingest `current_exercise_id()`，#267 起可 restamp。

**要新增/改：**
| 項目 | 動作 |
|---|---|
| 可見性軸 | `resolve_scope`（exercise_id）**改 faction 為軸**；exercise_id 降記錄歸屬鍵（接 active_intervals 時間窗）；移除常駐疊看 COMMAND-only |
| faction 開關 | `FACTION_ISOLATION_ENABLED` 靜態 env → **狀態驅動**（紅永遠藏、未分隊只演習中藏、平時放行未分隊） |
| 開場快照 | activate **新增** entity baseline 快照 |
| 開場精靈 | 串「分隊→選擇性清圖→快照→開始記錄」；分類/reset 機制已在，需串流程 + 選擇性 |
| 編輯閘 | `_isReadonlySource`/`_require_editable_source`（TTX-only）**拆除** |
| 中途新連線提示 | **新增** UX（接背景 poller） |
| stale 政策 | **新增**平時 stale 灰化/過期 + 不進下場 baseline |
| #344 | 外部硬前提，**同場驗證** |

---

## 8. 待拍板 / 開放問題

1. **記錄歸屬**：時間窗（`ts_in_active_window`，已有）vs 顯式 record-id。建議沿用時間窗（一次一場，#11 使其可行）。
2. **快照儲存**：`resource_snapshots` 表可否複用當 baseline，或新表。
3. **「每場重來」重置語意**：清「裝置→紅藍」對照表 + 舊 entity faction 標記失效（牽 uid/CN 穩定性 [[tak-faction-group-identifier]]）。
4. **開場精靈的「選擇性清圖」粒度**：哪些算「永久底圖」不清、哪些算「上場殘留」該清。

---

## 9. Tracking

- **Umbrella**：[#471](https://github.com/winson3QQ/ICS_COMMAND/issues/471)。
- **分片**：
  - [#472](https://github.com/winson3QQ/ICS_COMMAND/issues/472) 可見性軸轉 faction + faction 開關狀態驅動（§2 / §7）
  - [#473](https://github.com/winson3QQ/ICS_COMMAND/issues/473) 開場精靈：分隊→選擇性清圖→開場快照→開始記錄（§3.1）
  - [#474](https://github.com/winson3QQ/ICS_COMMAND/issues/474) 拆編輯閘 TTX-only → 都可編+audit（β-2，§3.2）
  - [#475](https://github.com/winson3QQ/ICS_COMMAND/issues/475) 中途新連線提示 + 即時重分隊三軸（§3.2）
  - [#476](https://github.com/winson3QQ/ICS_COMMAND/issues/476) stale 政策：平時灰化 + 不污染 baseline（§3.3）
  - [#477](https://github.com/winson3QQ/ICS_COMMAND/issues/477) #344 TAK group 隔離同場驗證（§4 硬前提）
- 演進自：[`red-blue-faction-isolation.md`](red-blue-faction-isolation.md)（#343/#344）、#267（active_intervals / 常駐疊看）、#260（route 編輯 dogfood 撞出 Problem）。
