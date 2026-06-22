# 紅藍陣營隔離（faction isolation）：資料模型 + 強制點 + 歸屬設計

> **本文定位**：演習「紅藍軍視圖隔離」的**設計 SoT**（資料模型 / 可見性強制點 / producer 歸屬鏈 / 角色映射）。
> 建立於 2026-06-22（兩週後演習先決需求，與使用者釐清 + ATAK/iTAK 真機抓包驗證後長出）。
> 壓縮版北極星待落 memory。對應 tracking issue 見文末。

---

## 0. 緣起（founding-why）

兩週後演習：藍軍（指揮部 / 國軍 / RTF）+ 紅軍同場，平台公網直曝，要 live C2 + 事後 AAR。**紅藍必須隔離**：

- **指揮部前端（commander 及以下）只能看到藍軍動態**；
- **admin（白隊 / 導調）與 AAR 可全看**。

現況：exercise scope 為 server-authoritative（P1-14），但「**同場域內 affiliation 分流 + 角色感知過濾**」**無設計**。本文補這塊。

---

## 1. 核心概念：faction ≠ affiliation

> **faction（陣營）= 資料「歸誰所有 / 誰產生（producer）」，不是 CoT 符號畫成什麼。**

**鐵證**（2026-06-22 ATAK 真機）：同一台裝置丟出 4 個標記，符號各為 a-h（敵）/ a-u（不明）/ a-f（友）/ a-n（中立），但 `creator` 全指向同一台裝置。

→ 推論：**不能用 CoT `type` 的 affiliation 判紅藍**（藍軍標的「敵情接觸」符號是 a-h，但資料屬藍方、藍方該看、紅方不該看）。**faction 必須由 producer 決定，且由 admin server 端指派**（不信 client 自宣告的 `type`/`__group`）。對齊 [memory `exercise-scope-server-authoritative`]。

---

## 2. producer 歸屬鏈（真機實測坐實）

### 2.1 平台差異（真機抓包，2026-06-22）

| | self-SA 自身 | 丟的標記 | 繪圖 |
|---|---|---|---|
| **ATAK**（5.6.0.12 ATAK-CIV）| uid=裝置id、takv、__group | `creator.uid` ✅ **+** `link[p-p].uid` ✅ | `creator.uid` ✅ |
| **iTAK**（2.12.3）| uid=GUID、takv、uid.Droid | `creator` ✗、**`link[p-p].uid` ✅** | `creator` ✗、`link` ✗ → **無 uid 歸屬** |

### 2.2 歸屬鏈（**僅 by-uid，不靠猜**）

對每筆 tak 來源 entity，依序解 producer `client_key`（= 產生它的裝置 self-SA uid）：

1. **單位自身**：`entity.uid` 本身即裝置 self-SA → `client_key = uid`
2. **`attributes.creator.uid`**（ATAK 標記 + 繪圖）
3. **`attributes.link.uid`（`relation=p-p`）**（ATAK + iTAK 標記）
4. 以上皆無 → **不歸屬**（fail-closed，見 §4）

> **明確排除**：callsign 前綴匹配（iTAK 繪圖會有 `3QQ:iTAK.Circle.3` 這種內嵌裝置名）**不採用**（by-name 非 by-uid，信心不足、可被改名干擾）。決策：2026-06-22。代價：**iTAK 繪圖預設無 producer**，落 fail-closed，由 admin 手動點（§4）。

### 2.3 broadcast 前提（操作 SOP）

ATAK / iTAK 的標記/繪圖**預設不進 ICS，要「broadcast / 分享」才會串流到 server**。意涵：
- **SOP**：一線標敵情若沒 broadcast，指揮部 COP 看不到 → 寫進操作須知 / 訓練。
- **對隔離有利**：未 broadcast 的私有標記根本不到 ICS，隔離只需管已 broadcast 物件。

---

## 3. 資料模型

```sql
-- 新表：admin 對「連線 client（裝置）」的陣營分類（per-exercise，server-authoritative）
CREATE TABLE client_faction (
    exercise_id   INTEGER,            -- 綁場；NULL = 實戰池
    client_key    TEXT NOT NULL,      -- 裝置 self-SA uid（穩定識別）
    callsign      TEXT,               -- 顯示用（admin tab）
    faction       TEXT NOT NULL,      -- 'blue' | 'red' | 'neutral'
    classified_by TEXT,               -- 稽核：誰分的
    classified_at TEXT,
    PRIMARY KEY (exercise_id, client_key)
);

-- cop_entities 加兩欄（ALTER ADD，schema v1 內擴充，非 v2）
--   faction        解析結果；NULL = 未解析 / 未分類 → fail-closed
--   faction_source 'auto'（歸屬鏈解出）| 'manual'（admin 對單一 entity override）
ALTER TABLE cop_entities ADD COLUMN faction TEXT;
ALTER TABLE cop_entities ADD COLUMN faction_source TEXT;  -- auto | manual
```

**faction 取值語意**：
- `blue` → commander 可見
- `neutral` → commander 可見（共享 / 控制措施 / 民間角色）
- `red` → commander **不可見**
- `NULL`（未解析或 client 未分類）→ **fail-closed，commander 不可見**（視同 red 直到分類）

---

## 4. 解析時機 + fail-closed + 手動 override

- **ingest 時解析**（`cop_service.normalize_cot` / `ingest_cot_event`）：依 §2.2 算 `client_key` → 查 `client_faction(entity.exercise_id, client_key)` → 寫 `entity.faction`（`faction_source='auto'`）。解不到 producer 或 client 未分類 → `faction=NULL`。
- **admin (re)分類 client**：寫 `client_faction` → **重解析該 producer 名下所有 `faction_source='auto'` 的 entity**（不動 `manual`）→ **broadcast resync**（沿用既有 reset resync 管線，commander 視圖即時增減）。
- **admin 對單一 entity override**：iTAK 繪圖等無 producer 物件 → admin 在右 tab 直接點該物件陣營，寫 `faction` + `faction_source='manual'`（重解析不覆寫）。

---

## 5. 可見性強制點（三層，**全 server-side** — 前端過濾不算數）

> 紅軍資料**根本不送進 commander 瀏覽器**。只在前端隱藏 = XSS / F12 即破 = 演習作弊。

| 層 | 位置 | 做法 |
|---|---|---|
| REST 讀（entities）| `routers/cop.py list_entities` / `get_entity` | 依角色算 `visible_factions` → repo `_faction_clause` SQL 過濾；get 不可見→404 不洩漏存在性 |
| REST 讀（squads）| `routers/cop.py list_squads` → `aggregate_squads(visible_factions=)` | **聚合也過濾**——否則藍方經 centroid/兵力推得紅軍位置（code/security review 補；只 `source='tak'` 受限）|
| REST 讀（GeoChat）| `routers/chat.py /api/chat` → `build_chat_feed`/`list_chats(visible_factions=)` | 紅軍通聯不漏藍方；chats 全 tak → 規則化簡 `faction IN (...)`（NULL fail-closed）|
| **WS 即時推播** | `services/realtime_hub.py _Conn` | `visible_factions` 欄（handshake 依角色定，同 `include_standing` 模式）；`wants()` 加 faction gate；**所有 broadcast caller 帶 `source`+`faction`**（entity create/update/delete、exercise enroll、GeoChat）|
| AAR / timeline | `services/timeline_service.py`（tracks/markers/contacts/chats）+ `/exercises/{id}/aar`·`/tracks`·`/timeline`·`/kpis` | 走 §6 互斥閘（AAR 僅在無 active 演習時開放）——閘須蓋**全部**回放/PII 出口（PR-6）|

> **review 收尾（2026-06-22 code+security review）**：強制點的**讀取面比初版廣** —— 除 entities，squads / GeoChat / timeline 都讀 tak 資料。squads + GeoChat（live）已補 faction 過濾；timeline 家族（tracks/markers/contacts/chats）由 §6 AAR 互斥閘關閉 live 存取（PR-6）。共用 `_faction_clause` helper（空集 → `source != 'tak'`，避非法 `IN ()` 並 fail-closed）。

---

## 6. 角色 → 可見性映射（faction 是與 RBAC 正交的新軸）

| 角色 | 演習身分 | live 可見 faction |
|---|---|---|
| `sysadmin` | 白隊 / 導調 / 控台 | **全部**（blue + red + neutral + NULL） |
| `commander` | 藍軍指揮 | `{blue, neutral}` |
| `operator` | 藍軍一線 | `{blue, neutral}` |
| `observer` | 藍軍觀察 | `{blue, neutral}` |

> 運作假設：**白隊以 sysadmin 登入，藍軍幕僚以 commander/operator/observer 登入。紅軍只發 CoT、不登入 ICS**（2026-06-22 確認）→ 只需「藍看藍」單向隔離，無需紅方對稱視圖。

### AAR 規則（與演習互斥，對所有角色一致）

- **有任何 TTX / 實戰 active 時 → AAR 全關**（含 sysadmin，無 live 回放例外）。
- **無 active（場已歸檔）時 → AAR 開放、全見**。

理由：一刀斬斷「commander 用 AAR 偷看 live 紅軍」的後門，且不必為 AAR 另寫 per-role faction 過濾。白隊 live 上帝視角由其 COP 全見滿足（不需 live 時間軸回放）。決策：2026-06-22。

---

## 7. admin 右側 tab UI（需求 #1）

- 列出**本場觀測到的所有連線 client**（從 cop_entities 之 producer / 既有 `/api/cop/squads` 聚合）
- 每列：callsign / 最後位置時間 / 目前 faction；三態鈕 **🔵藍 / 🔴紅 / ⚪中立**
- 未分類 client 醒目標記（提醒 admin 歸類）
- iTAK 繪圖等無 producer 物件：另一區供 admin 對單一物件直接點陣營（§4 manual override）
- 任一分類動作 → 重解析 + resync 廣播

---

## 8. 兩支隔離槓桿：ICS 視圖層 vs TAK 現場層

> **本設計（§1-7）只控「ICS 儀表板視圖」**——commander 開 ICS 看不到紅軍。但紅藍在**同一台 TAK server** 上，藍軍 ATAK broadcast / ICS 出向推回 TAK 的物件，**紅軍 ATAK 直接在 TAK 上收到，繞過 ICS**。故必須有**第二支槓桿（現場層）**。

### 8.1 三概念別混（TAK 經典坑）

| 概念 | 是什麼 | 是否隔離邊界 |
|---|---|---|
| **① CoT `<__group name="Cyan">`** | 使用者**自宣告隊伍顏色**，顯示/編組用；ICS 抽成 `team_color` + `/api/cop/squads` 聚合 = 本系統「隊伍」 | ❌ client 自填、可謊報 |
| **② TAK Server group / channel** | server 端**廣播 ACL**（決定誰收得到誰的 CoT），有方向性（送/收） | ✅ **現場層隔離的真正槓桿** |
| **③ ICS faction（本文 §3）** | admin 指派、server-authoritative 紅藍 | ICS 視圖層隔離 |

**隊伍 ≠ faction**：team_color（Cyan/Magenta…）是 **faction 內部的子編組**（顯示用，不動）；faction（blue/red）才是隔離軸。兩者粒度與用途不同，**不可綁在一起**。

### 8.2 目標架構：faction 作為 SoT，同時驅動兩層

```
admin 在 ICS 點一台 client = blue
        │
        ├─► ICS：client_faction（§3）→ commander 視圖隔離（§5）
        └─► TAK：把該 client 的 TAK 帳號加入「blue group」→ TAK server 不再把藍軍 CoT 廣播給紅軍
```
**一個分類動作、兩層同時隔離。** faction（§3）= 單一真相源。

**可行性**（2026-06-22 查 `takserver-5.7-openapispec.json`）：
- group 可程式化管理：`PUT /user-management/api/update-group-users`、`GET /Marti/api/groups/members`、`/users-in-group/{group}`、`/groups/{name}/{direction}`（方向性）。
- **gotcha 1**：group membership 綁 **TAK username/憑證**，非 CoT uid → 需「裝置 client_key ↔ TAK 帳號」對照表（與本文 §3 client_key 接起）。
- **gotcha 2**：group 變更對**已在串流的連線**是否即時生效、或須重連，**待本機 takserver 實測**（TAK 常見坑，docker 不 hot-reload）。

### 8.3 mission 是後話（更重的層）

Mission（spec 88 path）= data-sync 訂閱 feed + 自己的成員/權限/持久化。自然綁法 **ICS exercise ↔ TAK mission**（一場 = 一 feed），紅藍再用獨立 mission 切。但 mission 有一堆雷（[memory `tak-marti-authz-model`] defaultRole 授權、[memory `tak-streaming-archive-stale-vs-mission`] 刪除不傳 iTAK）→ **大工程、後續**。對「紅藍現場隔離」目標，**group 是更直接的解，mission 不擋這次演習**。

> **追蹤**：現場層（faction → TAK group）綁定 = 姊妹 issue #344；本文 §1-7 為 ICS 視圖層（issue #343）。兩支演習前都要做。

---

## 9. 其他已知邊界

1. **iTAK 繪圖無 producer**：落 fail-closed，commander 預設看不到，須 admin 手動放 → 演習中 admin 持續性工作負擔，列入導調 SOP。
2. **fail-closed 取捨**：未分類 client 一律 commander 不可見 → admin 須**演習前預先把藍軍 roster 分類完**（10–30 台可行），否則藍軍單位在分類前不顯。
3. **client_key 穩定性**：以裝置 self-SA uid 為鍵；裝置重裝 / 換 uid 需重新分類。

---

## 10. 落地分期（v1 = 演習可用）

- **v1（演習前必達 · ICS 視圖層 / 本 issue #343）**：client_faction 表 + entity.faction 欄 + ingest 解析（§2.2 chain 1-3）+ fail-closed + 三層強制點（§5）+ 角色映射（§6）+ admin 右 tab（§7）+ AAR 互斥閘。
- **v1（演習前必達 · TAK 現場層 / 姊妹 issue）**：faction → TAK group 綁定（§8.2）。
- **v1.1（時間允許）**：admin 單物件 override（§4 manual）UI 打磨。
- **後續**：mission ↔ exercise 綁定（§8.3）；neutral 細分；紅方對稱視圖（若紅軍改用 ICS）。

---

> 真機抓包證據：2026-06-22 對現役 prod TAK（ATAK-CIV 5.6.0.12 + iTAK 2.12.3）實測 `cop_entities.attributes` 之 `creator` / `link[p-p]` 欄位。
