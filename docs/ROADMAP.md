# ICS_Command Roadmap

由 [ICS_DMAS](https://github.com/winson3QQ/ICS_DMAS) 拆分而來的指揮部單體版本。本 ROADMAP 為**單一 SoT**，不再維護獨立的 `matrix.md`；compliance 對照以 inline 註記方式融入各 phase 的 Definition of Done。

## 狀態 marker 約定

每個 item 完成時在 row 開頭加 ✅，並寫入 `(#PR, commit hash)` 供 audit。維護由 [`docs/PROCESS.md`](PROCESS.md) step 8 強制（**merge 同時必勾 ROADMAP**，不是事後想到才補）。

- ✅ = 完成 + merged
- ⏳ = 進行中（branch 已開）
- 🚧 = blocked（PR 留 `ESCALATE` 升級）
- 無 marker = pending

跨機器 / 跨 session 新接手者：跑 `python3 scripts/roadmap_issue_sync.py` 拿到「ROADMAP item ↔ GitHub issue」對照（Layer 3 status report 規劃中）。

---

## 願景

在指揮部儀表板上建構**共同作戰圖（COP, Common Operational Picture）**，作為事件管理體系（ICS）下的決策中樞。COP 透過兩個外部來源餵入：

1. **TAK Server**（官方 [TAK-Product-Center/Server](https://github.com/TAK-Product-Center/Server)，Apache 2.0）——標準化態勢圖、人員 / 載具位置、CoT 事件
2. **WaveInk**（[winson3QQ/WaveInk](https://codeberg.org/winson3QQ/WaveInk)）——SDR 多頻 PTT 錄音 + Breeze ASR 中文 STT，將無線電通聯轉成結構化事件

兩者皆透過 `services/cop_service.py` 單一正規化層落地，**不開新 table**——遵循 ICS_DMAS 既定架構決策（多來源 → 單一 COP 模型）。

---

## Phase 1 — Command Dashboard 基底重構（Re-construct）

**目標**：拆分後的 Command 單體能獨立運作、測試 green、文件對齊；改造原 PWA 殘留依賴為**通用上游節點介面**（保留未來 Medical / Shelter PWA 回流的可能性），並把地圖 UX 升級為 P2 TAK 整合的基底。

### Scope

| Item | 說明 |
|---|---|
| ✅ P1-01 | 盤點 18 routers / 18 repos / 7 services，移除真正的孤兒 import（**不刪除 federation infra**，見 P1-04） — 完成於 [#2](https://github.com/winson3QQ/ICS_COMMAND/pull/2) `05049e2`（2026-05-25）|
| P1-02 | `routers/pi_push.py` → **改名 `ingress.py`** 並重構為通用 ingress 介面（為 P2 TAK、P3 WaveInk 共用；既有 Pi push 路徑保留為 `/api/ingress/pi-node`，介面相容） |
| P1-03 | `services/cop_service.py` 正規化層 schema 凍結 v1（`source: enum[manual, pi-node, tak, waveink]` 必填欄位，預留 `pi-node` 不關門） |
| P1-04 | **保留並重新定位** `pi_batch_repo`、`pi_node_repo`、`sync_repo`（三 Pass 對齊）為「**上游節點 federation 介面**」——架構上已是通用設計（`sync_repo._unit_to_node()` 已參數化 shelter/medical/forward/security）。命名不改，文件補充說明：未來 Medical / Shelter PWA 重新對接、或 ICS_Command 變成多 Pi 站台中樞時，這層直接用。**僅清理**真正死掉的測試與 import |
| P1-05 | `routers/manual.py` 確認仍能手動建立 COP entity（最小可用 baseline） |
| P1-06 | `tests/` 全綠：unit / integration / security / api / js — 補充 ingress 重構與 federation 介面 contract test；保留 `pi_*_repo` 測試 |
| P1-07 | `docs/指揮部儀表板設計規格.md` 更新到 v3.0：(a) 移除 PWA-specific 描述但保留 federation 介面章節；(b) COP 章節對齊 Phase 1 凍結 schema |
| ✅ P1-08 | `start_mac.sh` 已純化（Stage 1 完成）；補 `start_pi.sh` 與 systemd unit drop-in。**順手修 venv shebang detection（P1-01 dogfood 發現）+ systemd EnvironmentFile pattern + ics-backup paths 同步** — 完成於 [#6](https://github.com/winson3QQ/ICS_COMMAND/pull/6) `918b675`（2026-05-26）|
| P1-09 | `deploy/`（Stage 1 未帶）補回必要部分：nginx reverse proxy + TLS（去掉 PWA server block，保留 ingress 通用路由） |
| P1-10 | **地圖 UX baseline 升級**：見下方〈P1-10 細項展開〉。改採 **MapLibre GL JS + PMTiles + dark ops theme + SVG marker + 等寬字體**，全替換 Leaflet（不走 `leaflet-maplibre-gl` 折衷路線，避免 P2 二次手術），預埋 P2 TAK + MIL-STD-2525 渲染接點 |
| ✅ P1-11 | **Commander Dashboard UI 移除 PWA-specific 元素**：見下方〈P1-11 範圍〉。配合 P1-04 backend federation infra 保留決策，UI 層拿掉「收容/醫療」固定二元呈現，**改為 Option B 整刪**（dogfood 中決議；P2 TAK / P3 WaveInk 接入再重蓋）。源於 P1-01 dogfood 跑 dashboard 時的截圖盤點 — 完成於 [#4](https://github.com/winson3QQ/ICS_COMMAND/pull/4) `23dee14`（2026-05-26）|

### Definition of Done

- [ ] `pytest` + `npm test` 全綠，coverage 不低於拆分前
- [ ] 無 import error / dead route（`uvicorn --reload` 啟動 clean，無 warning）
- [ ] `services/cop_service.py` 有 contract test 鎖定 source enum（含 `pi-node` 預留）
- [ ] `pi_*_repo` + `sync_repo` 通過「federation 介面相容性」測試（模擬非 PWA 上游節點推送）
- [ ] MapLibre GL JS 已上線、24/7 主題切換可運作、entity layer 抽象介面已定義
- [ ] 規格書 v3.0 merged
- [ ] **Tag**：`command-v1.0.0`

### Compliance touchpoints

- **ASVS V14 / NIST SSDF PW.7**：測試覆蓋（DoD #1）
- **ISO 25010 可靠性 / 可維護性**：federation 介面契約測試（DoD #4）
- **供應鏈** (CLAUDE.md)：
  - MapLibre GL JS — BSD-3，AWS/Meta/MapTiler/Felt 共同維護，非中國
  - PMTiles / Protomaps — BSD-3，maintainer Brandon Liu（US/台美籍），非中國
  - milsymbol — MIT，瑞典
  - JetBrains Mono / Noto Sans TC — 皆 OFL，離線打包
- **Design System provenance**：vendor 自 [WaveInk DESIGN.md + colors_and_type.css](https://codeberg.org/winson3QQ/WaveInk)，commit hash 釘版於 `docs/design/POLICY.md`；兩處 divergence（MIL-STD-2525 token、JetBrains Mono webfont）明文記錄
- 證據路徑：`command-dashboard/tests/reports/p1-baseline.html`、PR# + commit hash 寫進 commit message

### P1-10 細項展開（地圖 UX baseline）

**設計原則**：指揮中心的「專業感」90% 來自 chrome（sidebar / chip / legend / 字級），不是地圖本身——dark ops theme 是最大 CP 值的單一改動。地圖核心則一次到位（MapLibre + PMTiles），不走 Leaflet 折衷以免 P2 二次手術。

| 子項 | 內容 | 預估 |
|---|---|---|
| **P1-10a** | **採用 WaveInk Design System + MIL-STD-2525 token 體系 + 跨平台等寬字體**（**一次到位，不分階段**，TAK 整合就緒為優先設計約束）：<br>**(i) 基底**：vendor `colors_and_type.css` + `DESIGN.md` policy 自 WaveInk `docs/design/`（commit hash 釘版），落地為 `command-dashboard/static/css/ds-tokens.css` + `docs/design/POLICY.md`。直接繼承：GitHub Dark Dimmed 色階、`--space-1`~`--space-9`、`--radius` 三段、border-not-shadow 紀律、動畫節制（僅保留 keyframe，不引入 JS 動畫）、Unicode-as-iconography（禁 Material/Heroicons/Lucide/Phosphor/Font Awesome）、empty-state 文案語氣（terse / imperative / 無 marketing copy）。<br>**(ii) MIL-STD-2525 entity color token 作為一等公民**（**主要設計考量，非例外**）：新增 `--mil-friendly` / `--mil-hostile` / `--mil-neutral` / `--mil-unknown` token，標準色對齊 MIL-STD-2525C 附錄 A；frame 形狀（friendly 矩形 / hostile 菱形 / neutral 方形 / unknown 四葉草）由 P2-05 milsymbol 接管渲染，token 提供色彩 SoT。WaveInk policy 的「No new accents」原則於本 token group 例外開放，並反向標註：未來新增 entity affiliation 必須對齊 MIL-STD-2525，不得自創色。<br>**(iii) 字體 ops-grade upgrade**：**JetBrains Mono 離線打包進 `static/fonts/`** 取代 WaveInk 的 system mono 預設——指揮場景 callsign / 座標 / MGRS / 時間戳跨 Mac/Win/Linux 一致性是 ops 規範（system mono 在三平台分別是 Menlo / Consolas / DejaVu，視覺差異不可接受）；JetBrains Mono 對 0/O、1/l/I、5/S 有明確 disambiguation 設計。UI 文字維持 WaveInk system stack（Noto Sans TC fallback）。<br>**(iv) divergence 文件**：`docs/design/POLICY.md` 明文列出 ICS_Command 對 WaveInk DS 的兩處 fork 點（MIL-STD-2525 token、JetBrains Mono webfont）與 rationale，下次 WaveInk DS 升版時做為 conflict 解決依據 | 3-4 天 |
| **P1-10b** | **MapLibre GL JS 全替換** — 移除 Leaflet，map.js 核心重寫；marker / popup / polygon / layer 統一改 MapLibre Symbol Layer API。借鏡 mini-taiwan 的**架構**：entity layer 抽象、track 0–1 插值、collision detection 邏輯——**不借 Three.js 3D 實作**（COP 3D entity 價值低、Pi 500 GPU 會搶資源，3D 留作 P2 之後依需求評估） | 4-5 天 |
| **P1-10c** | **PMTiles 台灣底圖** — 用 Protomaps 工具產出台灣全圖 PMTiles 單檔（約 200-400 MB），Pi 直接 serve，完全離線。提供 day / dusk / night / sat 四種 vector style | 1-2 天 |
| **P1-10d** | **SVG marker 系統 + severity design token** — 每種 `node_type` × `event_severity` 一套 icon set；統一 stroke / corner radius / 陰影；severity 配色（critical / warning / info）以 CSS custom properties 集中管理，禁止散在 JS 寫死 hex；critical 事件用 `@keyframes` halo pulse（不用 JS 動畫，省 Pi CPU） | 2-3 天 |
| **P1-10e** | **Polygon / route hover + selected 狀態** — hover: outline 加粗 + fill opacity 微升；selected: dashed animated border（純 CSS） | 1-2 天 |
| **P1-10f** | **Symbol-layer-based clustering** — 用 MapLibre 內建 `cluster: true`（基於 supercluster），**不引入 leaflet.markercluster**；clustering 視覺與 design token 對齊 | 1 天 |
| **P1-10g** | **圖層切換微動畫** — 200ms fade-in / opacity 過渡，避免「啪」一下 | 0.5 天 |
| **P1-10h** | **CSP 對齊** — MapLibre + PMTiles 需要：`worker-src blob:` / `script-src 'wasm-unsafe-eval'` / `child-src blob:`；與 ICS_DMAS C1-B 階段 CSP 規劃對齊；補 CSP integration test | 1 天 |

**總工時估**：13-18 天（單人）；演練前**至少 3 週**開工，留 buffer。

**實作順序建議**（CP 值由高到低，**TAK 就緒度為優先排序原則**）：
1. P1-10a（WaveInk DS + MIL-STD-2525 token + JetBrains Mono）— 視覺感受立即 +50%，且 token 體系預埋 P2 直接吃
2. P1-10b + P1-10c（MapLibre + PMTiles）— 地圖質感 +40%，P2 TAK Symbol Layer 直接套
3. P1-10d（SVG marker + token）— 收斂業餘感；MIL-STD-2525 frame 等到 P2 milsymbol 接管
4. P1-10e + P1-10f + P1-10g（hover / clustering / 微動畫）— 精緻度收尾
5. P1-10h（CSP）— 與每步交叉驗證，最終整合測試

**P1-10 DoD（補充上層 DoD）**：
- [ ] Lighthouse Performance score ≥ 80（Pi 500 上）
- [ ] 1000 個 entity 同時渲染 FPS ≥ 30
- [ ] 主題切換無 FOUC（flash of unstyled content）
- [ ] CSP test green，無 `unsafe-inline` 例外
- [ ] PMTiles 離線（網路全斷）下地圖完整可用

**明確排除（移到 P2）**：
- **MGRS grid** — 軍規 grid 屬 TAK / MIL-STD-2525 同一生態，自然該與 CoT 符號渲染一批做。P1 只實作基本經緯度 grid。
- **3D entity layer** — 同上理由，等真實山地 / 空域需求出現再評估。

### P1-11 範圍（Dashboard UI PWA 清理）

於 P1-01 dogfood 階段（commander_dashboard 登入截圖）盤點出的 PWA-specific UI 元素，分四級處理：

**Tier 1 — 純 PWA，直接刪**

| 元素 | 位置 |
|---|---|
| `cd-shelter` / `cd-medical` 連線燈（頂部「收容 離線 / 醫療 未連線」）| `commander_dashboard.html:867-868` |
| 量能 chart legend「收容量能 / 醫療量能」 | `commander_dashboard.html:899-900` |
| 「傷患入站」chart 整段 | `commander_dashboard.html:908` 起 |
| `capacity` event defaultAssigned = `'shelter'` | `static/js/events.js:45` |

**Tier 2 — 硬編碼 shelter+medical 數學，需 refactor**

| 元素 | 位置 | 改法 |
|---|---|---|
| Zone A 量能狀態燈計算（`sp`/`mp` 二元） | `static/js/cop.js:331-358` | 改成依 `pi_nodes` 動態 unit 算（fed-ready）或暫 placeholder |
| Zone C「物資見底/容量飽和/人力超載」KPI 資料源 | `commander_dashboard.html:1080-1090` + `cop.js` renderZoneC | 同上模型，refactor |
| `cop.js:162` event labels 含 PWA 場景 | `static/js/cop.js:162` | 可清掉 `medication_mgmt` 等 |

**Tier 3 — 周邊工具保留**

- `static/icon_preview.html`（22 KB）— 設計資產 catalog，對未來 TAK / WaveInk icon set 有參考價值
- `static/scenario_designer.html`（52 KB）— TTX 場景設計工具，沒 PWA 不影響運作

**不在本 item scope（移其他 P1）**

- 「地圖載入中」卡住 → P1-10 map UX baseline
- 規格書同步移除 PWA 章節 → P1-07 規格書 v3.0

### P1-11 DoD

- [ ] Tier 1 四項移除，dashboard 載入無 console error
- [ ] Tier 2 三項重構為依 `pi_nodes` 動態渲染或顯式 placeholder（不再寫死 shelter+medical）
- [ ] §8 verification：用 admin 登入 commander_dashboard，頂部無「收容/醫療」連線燈、左側無「傷患入站」面板、無新增 broken UI
- [ ] PR description 附 before/after 截圖對照
- [ ] 對應測試：dashboard.html + js 模組 vitest / playwright 覆蓋（沿用既有 tests/js/）

---

## Phase 2 — TAK Server 整合（COP 第一個外部來源）

**目標**：部署官方 TAK Server，CoT 事件流入 COP，地圖渲染採 MIL-STD-2525 符號。

### Scope

| Item | 說明 |
|---|---|
| P2-01 | 部署官方 TAK Server（Docker compose，Java/Spring + Postgres）；產出 `deploy/tak-server/` 部署文件與 cert 設定 SOP |
| P2-02 | `services/tak_service.py`：CoT XML 解析（規格相容，禁止自創欄位）；TAK Server 推播訂閱（TCP/SSL 8089 或 federation port 9000） |
| P2-03 | `routers/tak.py`：從 stub 升級為真實 endpoint（接 TAK Server federation push + REST 查詢）；schema 已存在於 Stage 1 帶過來的 stub |
| P2-04 | `cop_service.normalize_cot(event)` — CoT → COP entity 映射（type / uid / time / stale / lat / lon → COP `entity` + `track`） |
| P2-05 | 前端 `static/js/map.js` 加 MIL-STD-2525 符號渲染（用 [milsymbol](https://github.com/spatialillusions/milsymbol) JS lib，MIT，非中國維護）。**MGRS grid 一併於本項實作**（zoom-adaptive 密度、淡灰底 + 強調 100km / 10km 分層、label 避讓）——P1-10 已預埋 MapLibre Symbol Layer 接點 |
| P2-06 | 時間軸支援：CoT `stale` 處理 + COP 快照寫入 `snapshot_repo`（Wave 6 時間軸回放預埋） |
| P2-07 | Federation 設定：與外部 TAK 節點交換 CoT（可選；先單機 PoC） |
| P2-08 | 測試：CoT parse unit + TAK Server ↔ command-dashboard integration（mock TAK 推播）+ security（CoT injection / XML XXE 防護） |
| P2-09 | 規格書補 TAK 整合章節 |

### Definition of Done

- [ ] ATAK / iTAK 客戶端可推 CoT 事件，5 秒內顯示在 commander_dashboard 地圖
- [ ] CoT `stale` 過期自動從 COP 移除
- [ ] XXE 防護測試通過（`defusedxml` 或等效）
- [ ] Federation 雙向流測試（兩台 TAK Server 互推）
- [ ] **Tag**：`command-v1.1.0`

### Compliance touchpoints

- **NIST SP 800-53 SC-8 / SC-13**：TAK Server TLS 8089 強制
- **ASVS V13 (API) + V5 (Validation)**：CoT XML 解析需 XXE 防護
- **MIL-STD-2525**：符號渲染對齊（外部標準）

### 風險與決策點

| 風險 | 緩解 |
|---|---|
| 官方 TAK Server Java 資源需求高（建議 4GB+ RAM），Pi 500 8GB 邊緣 | P2 部署目標暫定 x86 mini-PC（N100 class），Pi 500 留作 client / 備援 |
| Federation cert 與內網 step-ca 整合 | 沿用 ICS_DMAS C1-B 既有 step-ca 內網 PKI 架構 |
| License：TAK Server 雖 Apache 2.0，部分 plugin / DataSync 模組仍是 commercial | P2 只用 core CoT + federation，不依賴 commercial plugin |

---

## Phase 3 — WaveInk 整合（COP 第二個外部來源）

**目標**：WaveInk 的 STT 結果經 NLP parser 結構化後流入 COP；無線電通聯自動轉成事件 / ICS-214 條目。

### Scope

| Item | 說明 |
|---|---|
| **P3-00** | **前置阻塞**：WaveInk 端完成「資料平面分離」架構（見下方〈WaveInk 資料平面與邊界協定〉），且 WaveInk License 確定（建議 Apache 2.0，理由見《License 對齊》）。否則 P3 不啟動 |
| P3-01 | 與 WaveInk repo 議定 **API 合約 v1**（WS / REST、payload schema、auth）並 commit 至兩邊 `docs/api/waveink-contract-v1.md` |
| P3-02 | `routers/ingress.py`（P1 重構成果）增 `POST /api/ingress/waveink` 與 `WS /api/ingress/waveink/stream` 兩條路徑 |
| P3-03 | `services/waveink_service.py`：接 WaveInk push（`{transcript, timestamp, callsign?, channel, audio_ref, consent_id?}`）→ 寫入 audit + 觸發 NLP parser。`audio_ref` 視為 **opaque URI**，**不下載、不解析、不快取本體** |
| P3-04 | NLP parser：以規則 + LLM 雙軌（沿襲 ICS_DMAS AI roadmap）抽取 callsign / 座標 / SALUTE 元素 / MEDEVAC 9-line 欄位 |
| P3-05 | `cop_service.normalize_waveink(parsed)` — 結構化結果 → COP event（與 TAK CoT event 走**同一個 normalize 層**） |
| P3-06 | MEDEVAC 9-line：**ICS_Command 無 Medical PWA**，9-line 落地為 COP 上的 incident card + ICS-214 自動條目（取代 WaveInk README 中「→ Medical PWA」的目標） |
| P3-07 | 前端：commander_dashboard 加「無線電通聯時間軸」面板，可點擊回聽原始音檔。**回聽走瀏覽器直連 WaveInk** 的私有儲存（用 WaveInk 自己的 auth / signed URL），ICS_Command **不做 audio proxy、不暫存音檔**——保持「raw audio 永不跨界」邊界 |
| P3-08 | 測試：mock WaveInk push、NLP parser 結構化準確率回歸、prompt injection 防護（沿襲 ICS_DMAS C5-E）、**ingress 端拒收 raw audio payload** 的負向測試 |
| P3-09 | 規格書補 WaveInk 整合章節 + 修訂 ICS-214 自動填表流程 |

### Definition of Done

- [ ] WaveInk push → COP 顯示 < 3 秒（不含 STT 推論時間）
- [ ] NLP parser 結構化欄位準確率 > 85%（沿用 ICS_DMAS Phase 2 E2B baseline）
- [ ] Prompt injection 測試 green
- [ ] MEDEVAC 9-line demo：模擬語音 → 自動填好 incident card
- [ ] **負向測試**：ingress 收到含 base64 / binary audio 欄位的 payload 應 reject 並 audit
- [ ] **Tag**：`command-v1.2.0`

### Compliance touchpoints

- **OWASP LLM Top 10 LLM01 (Prompt Injection)**：必測
- **ASVS V8 (Data Protection)** + **台灣個資法 / 通保法**：原始音檔保管、同意書與 retention policy **完全在 WaveInk 端**；ICS_Command 因「邊界協定」自然不在個資範圍內
- **NIST AI RMF MEASURE 2.7**：輸出結構準確率追蹤
- **License 對齊**：見下方〈License 對齊〉

### WaveInk 資料平面與邊界協定（架構決策）

P3 整合的前提是 WaveInk 採以下五原則設計訓練 / 運行資料平面。原則歸 WaveInk repo 負責落實，ICS_Command 在介面層強制執行：

1. **資料平面隔離** — 程式碼可開源，**原始音檔走外部私有儲存**（自架 MinIO / Cloudflare R2 / Backblaze B2 / 私有 git repo + LFS），git 中只放指針
2. **同意書 registry** — 每段錄音對應一筆 metadata：`{recording_id, recorded_by, recorded_at, location, purpose, consent_scope, retention_until}`；無同意書不准進訓練集（自己本人錄音例外，但仍須登記 `recorded_by=self`）
3. **Pointer-only in git** — `pairs.jsonl` 結構為 `{audio_uri, sha256, duration_s, language, consent_id, transcript}`，音檔本體不入 git
4. **模型作為邊界** — ICS_Command 只消費**模型輸出（結構化文字事件）**，永不觸碰 raw audio；WaveInk push 到 ICS_Command 的 payload 內**不得**含 base64 編碼音檔（ingress 端會 reject）
5. **模型也是個資衍生品** — 若模型用真實錄音 fine-tune，模型權重納入個資政策；對外發佈前須確認訓練語料同意書涵蓋「模型發佈」用途

**ICS_Command 在這個架構中的責任邊界**：

| 範圍 | 屬於 ICS_Command | 屬於 WaveInk |
|---|---|---|
| 原始音檔儲存 | ❌ | ✅ |
| 同意書管理 | ❌ | ✅ |
| Retention policy | ❌（不存）| ✅ |
| 個資稽核 | ❌（介面不接觸）| ✅ |
| 結構化事件儲存與 audit | ✅ | ❌ |
| `audio_ref` URI 引用記錄 | ✅（僅 URI string）| ❌ |

### License 對齊

- **建議**：WaveInk 與 ICS_Command **皆採 Apache 2.0**。理由：
  1. **Patent grant**：SDR / 軍用周邊有專利地雷，MIT 沒保護
  2. **採購對標 TAK Server**：DoD 自己的 TAK Server 就是 Apache 2.0，整條 stack license 對齊（TAK Apache 2.0 + milsymbol MIT + MapLibre BSD-3 + Breeze ASR Apache 2.0 + WaveInk Apache 2.0 + ICS_Command Apache 2.0）
  3. **政府 / 民防採購友善**，無 AGPL 的法務障礙
  4. **商業模式不受傷**：consulting、hosted service、訓練資料 / 模型微調包不被 license 鎖
- **GPL 隔離**：WaveInk 內部 `rtl-sdr`（GPL-2.0）以 `subprocess` 呼叫即可避開 linking，repo 加 `NOTICE.md` 標明
- **替代方案**：若需保留商業控制，BSL 1.1 → N 年後轉 Apache 2.0（HashiCorp 模式）；但會增加採購談判摩擦

### 跨 repo 議題

- WaveInk Phase 進度與 ICS_Command P3 啟動時點需對齊（WaveInk 自述 P1/P2 仍在設計階段）
- API 合約 v1 (P3-01) 與資料平面架構 (P3-00) 是雙邊變更，需在兩 repo 同步 PR review

---

## 跨 Phase 持續事項

- **資安政策**：`docs/compliance/security_policies.md`（InfoSec / AC / AU / IR / CP / Privacy）每 phase 收尾 review
- **威脅模型**：`docs/compliance/threat_model.md` 每加一個外部資料源（P2 TAK / P3 WaveInk）就重跑 STRIDE
- **規格書**：每 phase 收尾發 vX.Y
- **Issue/PR snapshot**：`.github/workflows/issue-snapshot.yml` 已啟用，保護 GitHub 停權場景
- **DR drill**：每季演練 step-ca rotate + DB restore
- **版號**：`command-vX.Y.Z` SemVer，每 PATCH/MINOR/MAJOR 觸發按 CLAUDE.md 規則辦理

---

## Phase 之後（未規劃，意見區）

- Wave 6 時間軸回放 UI（COP 快照已在 P2 預埋）
- Wave 7+：**Medical / Shelter PWA 重新對接**——P1-04 保留的 `pi_*_repo` + `sync_repo` federation 介面可直接承接，無需架構翻修
- TAK Federation 大網部署（跨機關互通）
- 多上游節點中樞：ICS_Command 同時對接多個 Pi 站台 / 友軍 TAK Server / 多個 WaveInk 錄音站

---

## 與 ICS_DMAS 的關係

- 共用元件（`server/`、`command-dashboard/`）的修正若對 ICS_DMAS 也適用，應評估回饋上游 PR
- ICS_DMAS 仍維持三組件完整架構（shelter / medical / command），本 repo 為 Command 單體交付線

---

> **本文件為 Stage 2 起草版**，Phase 細項在實作開始前可能依據演練回饋與 WaveInk Phase 進度調整。每次調整以 PR 形式修訂並在 commit message 標示 `docs(roadmap):`。
