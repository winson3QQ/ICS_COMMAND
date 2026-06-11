# TAK 整合開發策略（主軸 + 降階）

> **這份是什麼**：**策略層**文件，不是工作清單（工作清單＝[`ROADMAP.md`](../ROADMAP.md) Phase 2）。
> 引進 TAK 後，開發沿 **TAK 主軸**走。每個能力回答三問（使用者 2026-06-09 拍板）：
> 1. **能用 TAK Server 哪些 API、怎麼設定**（① TAK 介面 + admin 設定）
> 2. **這些 API 對應到 ICS 的什麼功能**（② API → ICS，引用 P2 item 不重抄）
> 3. **TAK Server 掛掉時怎麼降階**（③ degraded mode fallback）
>
> 情境面的「為什麼要這功能」見 [`docs/reference/tak-use-cases-config.md`](../reference/tak-use-cases-config.md)（19 情境深挖）。本文是「**怎麼把它沿 TAK 軸做出來、且 TAK 掛了不死**」。

---

## 1. 不可動的前提（紅線 invariant）

**ICS_Command 不是 TAK client，它是比 TAK 更早存在的獨立指揮儀表板。**

- `cop_entities` 是 **COP 的單一 SoT**；**TAK 只是來源之一**（`source='tak'`），與 `manual` / `pi-node`（含回流 PWA 收容·醫療）/ `waveink` / `command` 並列，**全走 `cop_service` 同一個正規化層**（P1-03 凍結）。
- **紅線**：**任何 ICS 功能都不得硬依賴 TAK**。雛形已在 —— P2-03「subscribe 啟動失敗永不擋 app 開機」。
- 推論：「TAK 掛掉」**不是災難復原，是設計好的降階**——一個來源乾掉，COP 照樣從其他來源活著。**這條前提讓第 3 問（降階）從「救火」變「設計」。**

### 1.1 server-authoritative：不信任 client 宣告（doctrine，自 memory 收編）

> 與「不硬依賴 TAK」同一 invariant 家族——**信任邊界一律在 server**。

- **演習 / 實戰歸屬由指揮部 server 端決定**（`current_exercise_id()` = 指揮部 UI 當前 active exercise），**不信任 ATAK 裝置宣告**：有 active → 綁該場；無 → NULL 池（實戰/未分場）。連入的 ATAK 就歸屬指揮部 UI 開的那個模式。
- **TAK Server 與指揮部後端獨立啟停**：三種組合（只 TAK / 只指揮部 / 都開）**無一需要信 ATAK `opex`** → ATAK 端無從、也不該決定「演習 vs 實戰」（使用者 2026-06-07 確認）。
- **紅隊 TAK-C 評估後不採**（2026-06-07）：讓 `normalize_cot` 讀 ATAK `opex` 覆寫 server 模式 = 給每裝置「自宣告繞過演習場」開關，違反本 doctrine、反成完整性破口。真正解 = **演習/實戰部署隔離**（不同 instance），非信 client。`simulated` 只由 **P2-19 O/C server 端注入**設，TAK ingest 維持 default False。
- **適用**：任何「這筆屬哪場 / 是否演習」一律走 server（`resolve_scope` / `current_exercise_id`），不讀 client 的 `opex`/`source`/`exercise_id` 宣告。未來再提「讀 opex 分流」＝已評估不採。對接 ROADMAP **P2-19**。

---

## 2. 三階降階模型（第 3 問的骨架）

第 1/2 問大半已在 P2 ROADMAP（P2-02~25 就是 API→ICS）。**本文獨特貢獻＝① 降階模型 + ② 沿 TAK 軸的能力矩陣 + ③ 每能力宣告 fallback。**

| 階 | TAK 狀態 | COP 來源 | 連線彙總 |
|---|---|---|---|
| **Tier 0 全通** | :8089 串流 + :8443 Marti + :9000 federation 都通 | 最豐富（即時 track + presence + mission + 多機構）| 綠 |
| **Tier 1 降階** | 一條通道斷（如 :8089 通但 :8443 Marti 掛，或反之）| 部分（live track 有、presence/mission 無；或可查不可即時）| 黃 |
| **Tier 2 全掛** | TAK 全斷 | **退回原生 COP**：手動放置(P1-16) + Pi-node/PWA + (WaveInk) + **TAK 最後已知實體（凍結標 stale，不刪**）| 紅 |

> **設計原則**：每個能力都要**宣告「需要哪個 Tier + 掉到下一階的 fallback」**（見第 4 節矩陣 #3 欄）。降階是宣告出來的設計，不是壞掉後才發現。

---

## 3. 狀態表示：兩軸分離（連線 vs 信心）

> 使用者 2026-06-09 校正：**連線狀態 ≠ 資料信心**，不可折成一個。

| 軸 | 問的問題 | 層級 | 詞彙 |
|---|---|---|---|
| **A. 連線（transport）** | 這條管子通不通？ | 管路 | 4 態：未啟用(灰) / 斷線(紅) / 連線無流量(黃) / 連線正常(綠)（沿用 P2-23 `cd-tak`）|
| **B. 信心 DCI（content）** | 我該不該信地圖上的東西？ | 內容 | DCI 0-100 + freshness（即時/稍舊/過時）|

- **可以「連著但低信心」**（socket 通但久無資料）也可以**「剛斷但短期可信」** → 兩軸獨立。
- **關係＝因果**：連線斷（因）→ 資料變老 →（果）DCI 下降。同一份 **source registry 餵兩軸**；drill-down 可把同源「連線態 + freshness」並排，讓指揮看「TAK 斷 → 所以那區信心掉」。

### 連線軸的呈現＝**方案 B：來源 chips（逐源標籤）**

- chrome 放**一排來源 chips**：`TAK● Pi/PWA○ WaveInk○ …` 每顆＝一個來源、自帶標籤 + 4 態色。
- **加新來源＝多一片 chip（資料驅動），不是多一顆散落的燈。**
- **退役 P2-23 獨立 `cd-tak` 燈**，併入這排 chips（同一套 4 態詞彙）。
- **同時修既有破口**：目前 `cl-server` + `cd-tak` 兩顆 dot **沒有 legend、看不出對應誰**（使用者指出）→ chips 自帶標籤即解。
- `manual` 是 **Tier 2 地板**（本地、永遠在），不列入「連線」chips（沒有管子會斷）。

### 別跟這兩個既有指示混（三個不同問題，各一個指示）

| 指示 | 答什麼 | 動不動 |
|---|---|---|
| `#status-lamp`（正常/注意/警報）| **全局事件嚴重度**（IPI/critical）—**不是連線** | 留著，別動 |
| `cl-server` | **我 ↔ ICS 後端**（我自己斷了其它免談）| 留著，最優先（可併入 chips 列首或獨立）|
| **來源 chips（本節 B）** | **ICS ↔ 各 COP 來源** 連線 | 新統一元件 |
| **DCI** | **資料可信度** | 維持，連線是其輸入 |

> 四個 well-defined 指示 > N 顆散落的燈。

---

## 4. 能力 × TAK API × 降階 矩陣（主軸）

> 按**能力**組織（非逐情境）——~15 條能力即涵蓋全部 19 情境（情境＝能力的組合）。
> **#1 欄 Marti REST 路徑據官方 [`takserver-5.7-openapispec.json`](../reference/takserver-5.7-openapispec.json)**（5.7-RELEASE，301 paths，2026-06-09 收入）落實，base＝`/Marti/api`；**:8089 串流**（v0 明文 CoT XML）與 **:9000 federation transport** 不在此 REST spec，標 port。admin 選單對照 use-cases 文件 ③ 層（✅ 2026-06-08）。

| 能力 | #1 TAK API + 設定（Marti base `/Marti/api`）| #2 ICS 功能（P2 item）| #3 降階 fallback（Tier）|
|---|---|---|---|
| **友軍/單位定位 (BFT)** | **:8089** CoT 串流；admin **Groups**（`GET /groups`, `/groups/all`, `/groups/members`）分流 | P2-02 subscribe → P2-04 normalize → P2-05 2525 渲染 | **T2**：TAK track 凍結標 stale；改靠 Pi-node/PWA/手動標記；COP 不中斷 |
| **共享標繪（marker/shape）** | **:8089** `u-d-*`；持久層 **Mission**：`GET/DELETE /missions`、`/missions/guid/{guid}`、`.../contents` | P2-08 幾何萃取 → P1-15/P1-16 cop_entity 即時管線 | **T2**：手動放置(P1-16) 全可用；TAK 標繪凍結（含可靠刪除 #161/P2-14）|
| **在線人員 (presence)** | **`GET /clientEndPoints`**、**`GET /contacts/all`**(`/full`,`/lite`) poll | P2-12 TAK 在線人員面板（P2-11 client）| **T1**：Marti 掛 → 面板顯「TAK 離線」、不報假在線；不影響地圖 track |
| **通聯 (GeoChat)** | **:8089** `b-t-f` | P2-07 `chats` 表（分流不進 cop_entities）| **T2**：通聯面板停更；改本地 ICS 事件/無線電 |
| **MEDEVAC 9-line** | **:8089** `<_medevac_>`；(查最新態勢 `GET /cot/sa`) | P2-09 9-line 萃取 + severity → P2-12 incident card | **T1**：Marti 掛仍收（走串流）；**T2**：改**手動建 MEDEVAC 事件**（manual source）|
| **影像 (IMINT/UAV/CCTV)** | **`GET/POST /video`**、**`/video/{uid}`**；Enterprise Sync `GET /sync/search` | P2-16 URI reference-only（**ICS 不 proxy**，防 SSRF）| **T2**：feed URI 失效顯「來源不可達」；ICS 本就不存本體，無資料殘留問題 |
| **感測器 (CBRN/SDR/UGS)** | **`GET/POST /datafeeds`**、**`GET/POST /inputs`**（＝admin「Inputs and Data Feeds」）| **sensor source 模式（未建，❓ P3）**：→ cop_service normalize（同 WaveInk）| **T2**：感測器斷 → COP 少該層；其餘來源不受影響 |
| **任務下達 (Mission downlink)** | **`POST /missions`** + **`POST /missions/{name}/subscription`**（group-scoped 持久）| P2-13 下達指令 + `planned` 空心框（COMMAND_ROLES + audit）| **T2**：**不能推 ATAK** → 降回**口頭/無線電下令**；ICS 內部 `planned` 標記仍可畫 |
| **DataSync（mission/照片/resync）** | **`/missions/guid/{guid}/contents`** + **`GET /sync/search`**、`/sync/metadata/{hash}`；**Federated Delete 預設 false** | P2-14 datasync_service（URI 永不 follow）+ **權威 resync**（#173）| **T2**：無權威 resync → 重連後**標記可能漏**（已知風險），靠本地 cop_entities 撐 |
| **多機構 (federation)** | **:9000/:8444** transport；admin：**`GET/PUT /federatedetails`**、**`POST /federategroups`**、**peer cert `GET/POST/DELETE /federatecertificates`**（治理 #8）、`/federate-outbound-groups-hop-limit` | 多機構基礎（P2-15）；ICS 為 federation 一節點 | **T2**：federation 斷 → 只剩本地單位 COP；各機構各自降階 |
| **情境注入 (TTX)** | **`POST /injectors/cot/uid`**（server 端注入 CoT；`GET`/`DELETE` 同）| P2-19 scenario_service（`simulated=True`，sysadmin-only）| **T2**：注入停 → 演習中止/改人工注入；實戰不受影響（注入本就演習用）|
| **任務查核 (ExCheck/ICS-204)** | **`/excheck/checklist`**、**`/excheck/template`**、**`POST /excheck/{templateUid}/start`**、`.../stop` | P2-18 EXCHECK → ICS-204 任務追蹤 | **T2**：查核停更；改本地任務面板/紙本 |
| **軌跡 → AAR** | （TAK 在動作中產生的 track 被記錄；歷史可 `GET /cot/search/date`）| P2-06a 軌跡寫入 → P2-20 回放 + P2-21 指標 | **T2**：TAK 軌跡斷點 → 回放有洞（資料完整性=AAR 品質）；其餘來源軌跡照記 |
| **決策觸發 / 告警** | geofence/threshold（前端/後端規則，**ICS 自建**）；sensor 走 `/datafeeds` | **主動告警後端（未建，缺口）**：geofence/門檻 → 提示「何時決策」 | **T2**：主動告警停 → 退回**被動視覺**（severity pulse/stale 灰）靠人盯 |
| **連線狀態 / 降階** | ICS `GET /api/tak/status`（已有，內部探 :8089/:8443）| P2-23 → **本文第 3 節：併入來源 chips**；DCI 信心軸 | （這條**就是**降階本身的可視化）|
| **資料保留 (PII)** | admin **Data Retention**（per-type TTL，現全空/Never）| ICS 側 `cop_entity_tracks` 自管 TTL（缺口 #13）| 兩域分治：TAK PG 自管、ICS SQLite 自管，互不依賴 |

> **注**：以上路徑為官方 5.7 spec 的 `paths` 鍵；**精確 query 參數 / request body schema 動工時再對 spec `components` 與 P2-11 `tak_rest_client` 實作**。本表確立「哪個能力打哪條」，非完整 API 契約。

---

## 4b. CoT 生命週期 / 刪除語意（archive / stale / Mission，自 memory 收編）

> 真機 iTAK + 活 server dogfood 實證（2026-06-08，#161）。**直接決定 P2-14 可靠刪除 / 權威 resync 的設計**，故收進策略。

- **iTAK「從地圖刪除」是純本機 declutter**：刪 marker/繪圖 → :8089 wire **零 `t-x-d-d`**、TAK server repository 原封不動。**新增/編輯會上 wire，只有刪除不傳播**。
- **TAK server 持久化一切**：CoreConfig `<repository>`（PostgreSQL）存每 uid 最新 CoT，**不靠 stale 移除**；`<latestSA>` 對新連線補發、`<repeater>` 僅 4 種 emergency 重播（一般 marker 不重播）→ **ICS 重連會漏既有靜態標記**（= #173，須 Marti 權威 resync）。
- **持久訊號 = CoT `<archive/>`**（不是 `how`）：帶 `<archive/>`（如 `a-u-G` 放置標記）→ 過 stale 仍保留；無 archive（如繪圖 `u-d-r`）→ 過 stale 即移除（server repository 仍留）。**`stale` = client 顯示提示，非 server 刪除條件**。
- **決策（#161）**：ICS = 一般 streaming subscriber，**對齊原生 = honor `stale` + honor `<archive/>`**（archived 豁免 stale、non-archived 過 stale 移除）；退掉 last-heard 時間窗。
- **~~可靠刪除 / 權威 resync 只在 Mission/DataSync 層~~ → [2026-06-11 dogfood 推翻可靠刪除半]**：原假設「mission 內刪除廣播訂閱者 → 可靠刪除」**經真機 iTAK iOS dogfood 推翻**（[#194](https://github.com/winson3QQ/ICS_COMMAND/issues/194#issuecomment-4677048174)）：mission `REMOVE_CONTENT`（iTAK 收到 REMOVE 訊息但地圖 marker 不消失）、t-x-d-d、**DELETE 整個 mission（任務包沒了 marker 還在）** —— **iTAK(iOS) 對 server 任何刪除信號都不移除地圖 marker，只能裝置本機刪 = client 硬限制，Mission 也救不了**。→ **(A) 可靠刪除：不可解（client 擋死），不建 Mission delete 子系統**；過渡期＝操作員「移出 COP」（ICS 端）+ 無界成長安全網。**(C) 權威 resync（讀方向）不受影響、仍可行**（`/cot/sa` 已敲定，#194）→ **P2-14 收斂以 resync 為主**。
- **威脅模型角度**：「streaming 層刪除不同步」為 **COP 完整性結構性限制** → 文件化 `docs/compliance/threat_model.md` §8（隨 P2-17）。
- **降階關聯**：archived 標記是 Tier 2「凍結 TAK 最後實體」可保留的依據（archived 不隨 stale 消失）。

## 5. 沿 TAK 主軸的開發順序（引用 ROADMAP，不重排）

- **已完成（上行 + 地基）**：P2-01~11b（部署/CoT 解析/normalize/2525/Marti client）、P2-23（連線燈）、P2-25（地圖篩選）。上行 E2E 真機驗收過（P2-10）。
- **關鍵路徑（核心雙向）**：P2-12（UI 面板統一，**含本文第 3 節來源 chips 收斂**）→ P2-13（下行）。
- **TTX 驗證層**：P2-19~22（gate 實戰延伸層）。
- **降階補強（本文新增、ROADMAP 未系統化）**：
  - 來源 chips 統一元件（退役 cd-tak 獨立燈、修兩燈無 legend）→ 併 P2-12。
  - 每能力 **fallback 宣告** 納入各 P2 item 的 DoD。
  - sensor source 模式（感測器→COP）+ 主動告警後端 = 浮出的新候選（見第 6 節）。

---

## 6. 降階是「設計」不是「壞掉」：DoD 增訂

> 把降階釘進「完成定義」，否則永遠是事後才發現 TAK 掛了整個瞎。

每個碰 TAK 的 P2 item，**DoD 增一條**：
- [ ] **宣告所需 Tier + 降階 fallback**（對照第 4 節矩陣 #3 欄），且**有負向測試**：`TAK_ENABLED=false` 或模擬 :8089/:8443 斷線時，該功能**降階而非報錯/白屏**。

---

## 附：本策略浮出的 ROADMAP 候選（與 use-cases 深挖累積一致）

1. **🔑 天氣/環境 feed**（颱風水災/野火/HazMat 三情境卡 + 計劃群點名）—— 頭號。
2. **sensor source 模式**（SDR/CBRN/CCTV/UGS 同架構：外部感測器 → cop_service normalize → COP）。
3. **主動告警 / 決策觸發**（geofence 後端 + 感測器門檻；現多被動視覺）—— 與 2 互補（感測器進來門檻告警才有料）。
4. **連線/降階可視化收斂**（來源 chips 統一、兩軸分離）—— 本文第 3 節，併 P2-12。
5. **威脅圖層**（RTF：threat_state 動態區 / 已清走廊 / 敵我老化情報）。
6. **安全 backlog 升級論據**（§8.3 COP poisoning / 可靠刪除 #161 / 權威 resync P2-14 / federation 治理 #8 / 跨機構分級 TAK-D）。

> 使用者拍板：**統一檢視**非「全做」——多數缺口不一定要做。下次切入時逐一評估。
