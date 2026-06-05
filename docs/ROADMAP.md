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
| ✅ P1-02 | `routers/ingress.py`（自 `pi_push.py` 改名）為通用 ingress 路由集合；目前承載 pi-node（P3 將加 waveink，TAK 留在 `routers/tak.py`）。新主路徑 `/api/ingress/pi-node/{unit_id}`；舊 `/api/pi-push/{unit_id}` 以同 handler 雙裝飾器保留為別名（ICS_DMAS Pi client 向後相容）— 完成於 [#12](https://github.com/winson3QQ/ICS_COMMAND/pull/12) `9253c88`（2026-05-26）|
| ✅ P1-02b | **`server/sync.js` 遷移到新 ingress 主路徑** `/api/ingress/pi-node/{unit_id}`：Wave 4 piPush 三處（heartbeat / 正常 push / replay）統一改用新路徑；抽 const 集中化。別名 `/api/pi-push/` 在 ingress.py 雙裝飾器仍保留為長期 fallback — 完成於 [#14](https://github.com/winson3QQ/ICS_COMMAND/pull/14) `c3c2034`（2026-05-26）|
| ✅ P1-03 | `services/cop_service.py` 正規化層 schema 凍結 v1（`source: enum[manual, pi-node, tak, waveink]` 必填欄位，預留 `pi-node` 不關門）。3 新表 (cop_entities/tracks/links) + events ALTER lat/lon；Pydantic 模型 + 4 normalize_* stub + repo CRUD + 35 contract tests；11 個 end-state scenario 壓測（含 TTX）— 完成於 [#16](https://github.com/winson3QQ/ICS_COMMAND/pull/16) `ba5f942`（2026-05-26）|
| ✅ P1-04 | **保留並重新定位** `pi_batch_repo`、`pi_node_repo`、`sync_repo`（三 Pass 對齊）為「**上游節點 federation 介面**」——架構上已是通用設計（`sync_repo._unit_to_node()` 已參數化 shelter/medical/forward/security）。命名不改，文件補充說明：未來 Medical / Shelter PWA 重新對接、或 ICS_Command 變成多 Pi 站台中樞時，這層直接用。**僅清理**真正死掉的測試與 import — 在 [P1-01 #2](https://github.com/winson3QQ/ICS_COMMAND/pull/2) `05049e2` 盤點時順手完成（3 檔 docstring 已含 P1-04 + Wave 7+ 錨點；pyflakes clean；每個 export 都有 ≥1 caller；無孤兒 test）— retroactively 標記於 2026-05-26 |
| ✅ P1-05 | `routers/manual.py` 確認仍能手動建立 COP entity（最小可用 baseline）— direct repo call 驗證 `create_manual_record` + `get_manual_records` 整鏈通 — 完成於 [#10](https://github.com/winson3QQ/ICS_COMMAND/pull/10) `f0555cf`（2026-05-26）|
| ✅ P1-06 | `tests/` 全綠 + **CI workflow 落地**（`.github/workflows/test.yml` + `.forgejo/workflows/test.yml`，跑 pyflakes + pytest + doc_sync_check）— 完成於 [#10](https://github.com/winson3QQ/ICS_COMMAND/pull/10) `f0555cf`（2026-05-26）|
| ✅ P1-07 | `docs/指揮部儀表板設計規格.md` v3.0 **framework header**（拆分 delta + incremental rewrite policy；v2.2 既有內容保留，後續隨 P1-03/P1-10/P2/P3 PR 配對段落重寫）— 完成於 [#10](https://github.com/winson3QQ/ICS_COMMAND/pull/10) `f0555cf`（2026-05-26）|
| ✅ P1-08 | `start_mac.sh` 已純化（Stage 1 完成）；補 `start_pi.sh` 與 systemd unit drop-in。**順手修 venv shebang detection（P1-01 dogfood 發現）+ systemd EnvironmentFile pattern + ics-backup paths 同步** — 完成於 [#6](https://github.com/winson3QQ/ICS_COMMAND/pull/6) `918b675`（2026-05-26）|
| ✅ P1-09 | `deploy/`（Stage 1 未帶）補回必要部分：nginx reverse proxy + TLS（去掉 PWA server block，保留 ingress 通用路由）。**順手修 7 個 code-review + 2 個 security-review findings**（DB path / ProtectHome / nginx install / Host injection / XFF spoofing / PII scrub gap / map_config world-writable…）；**ROADMAP P1-12 新增**（dogfood 衍生：統一 key management + at-rest 加密 + backup GUI）— 完成於 [#8](https://github.com/winson3QQ/ICS_COMMAND/pull/8) `a6ea334`（2026-05-26）|
| P1-10 | **地圖 UX baseline 升級**：見下方〈P1-10 細項展開〉。改採 **MapLibre GL JS + PMTiles + dark ops theme + SVG marker + 等寬字體**，全替換 Leaflet（不走 `leaflet-maplibre-gl` 折衷路線，避免 P2 二次手術），預埋 P2 TAK + MIL-STD-2525 渲染接點 |
| ✅ P1-11 | **Commander Dashboard UI 移除 PWA-specific 元素**：見下方〈P1-11 範圍〉。配合 P1-04 backend federation infra 保留決策，UI 層拿掉「收容/醫療」固定二元呈現，**改為 Option B 整刪**（dogfood 中決議；P2 TAK / P3 WaveInk 接入再重蓋）。源於 P1-01 dogfood 跑 dashboard 時的截圖盤點 — 完成於 [#4](https://github.com/winson3QQ/ICS_COMMAND/pull/4) `23dee14`（2026-05-26）|
| P1-12 | **統一 key management + At-rest 加密 + Backup GUI**：FIDO2-derived 主密鑰 + HKDF 衍生子鑰，統一 backup encryption + live DB at-rest encryption（SQLCipher）+ 未來簽章用途。詳見下方〈P1-12 範圍〉 |
| ✅ P1-13 | **`map_config.json` seed/runtime 分離（user-data boundary 建立，P1-12 prep）**：現況 `command-dashboard/static/map_config.json` 被 git tracked 又被 server runtime 寫入，造成 (a) 工作樹永遠 dirty、(b) 切 branch 洗掉 user zone/route、(c) snapshot script 把 user data 當 code commit、(d) 上線後場域 / 演習資料活在版控（違反 user data 邊界）。改為 `static/map_config.seed.json`（tracked，factory default）+ `data/map_config.json`（gitignored，runtime），讀寫對稱走 `GET/POST /api/map_config`，startup hook + migration script 兜底既有部署。CLAUDE.md 加「User Data 邊界」紅線段。順帶擴張 P1-12b scope（三層 backup 觸發 + Restore）並新增 P1-14（Exercise data scoping）+ P1-12b+14 bundling 決策。源於 issue #24 dogfood 副發現 — 完成於 [#28](https://github.com/winson3QQ/ICS_COMMAND/pull/28) `f4775d9`（2026-05-29，含 post-review code-review 2 HIGH + 1 MEDIUM + 2 LOW finding fixes）|
| ✅ P1-14 | **Exercise data scoping — events / map_config 加 exercise 綁定**（2026-05-28 dogfood 衍生）：當前 events / map_config / exercises 三者孤立沒外鍵關聯，導致「fresh deploy 後 map 復原成 seed 但 events 表還留上場演習的 8 筆」這種資訊孤兒狀態（dogfood 親見）。修法：(a) events 表加 `exercise_id` 外鍵 + `created_at` < exercise.started_at 的 backfill 策略；(b) map_config.json 的 `evt_*` zone 在 archive 時跟著 exercise 一起歸檔（搬到 backup tarball，不留在 live data/）；(c) GET endpoints 預設只回**當前 active exercise** 的 events / zones（admin override 可看歷史）；(d) UI 加「演習名稱」chip 在 header 強提示 user「現在在看哪場」。需先有 P1-12b 才能驗 reset 流程；建議 P1-12b 完成後接著做。**[2026-06-03 reality check — 上方 (a)-(d) 描述已過時，以本段為準]**：盤 code 後發現**資料模型 + repo + active 機制 + reset + AAR 報告幾乎全已建（ICS_DMAS C0 繼承）**，**不是「三者孤立沒 FK」**：① `exercises` 表有 `status`(setup/active/archived) + `mutex_locked`（單一 active）；② `events`/`cop_entities`(+索引)/audit/decisions… **皆已有 `exercise_id` FK**；③ `exercise_repo` 有 CRUD + `update_exercise_status`(mutex) + **`get_active_exercise()`**；④ `event_repo`/`cop_entity_repo`/`cop.py` create+list **已參數化 `exercise_id` 過濾**；⑤ admin **`/reset-exercise`** 已存在（清演習表 + `cop_entities WHERE exercise_id`）；⑥ **AAR 報告已存在**（`aar_entries` 表 + ai `/report/{exercise_id}` post-exercise report + ML export）。**真正缺口 = scoping「休眠」**：`get_active_exercise()` 幾乎沒被呼叫、**前端 dashboard 完全無演習 UI**（無 active 選擇器/header chip/`/api/exercise` 呼叫）→ 建立/查 events/cop 都不帶 exercise_id → **全部 `exercise_id=NULL` 未 scoped**（孤兒 bug 根因）。**故 P1-14 實為 wiring + 前端 UI**（非建模型）：(1) 前端演習管理 UI（建立/啟動/歸檔）+ active 選擇器 + header「演習名稱」chip；(2) create 預設 `exercise_id=get_active_exercise()`；(3) GET 預設過濾 active（admin override）；(4) backfill NULL。**無硬卡 P1-12**（reset endpoint 已在；只有 reset-to-factory *完整驗證* 鬆散想要 P1-12b backup）→ **現在就能做、且直接解鎖 P1-16 綁定**（cop create 帶 active id 即綁）。**AAR 修正**：報告級 AAR 已備；只剩**視覺逐格時間軸 scrub** = Wave 6（`snapshot_repo` 也已在）。**Out of scope 排除**：跨演習 cross-reference / 演習 fork / 多演習並行（Wave 6+）。<br>**[2026-06-03 v1 完成 — [#91](https://github.com/winson3QQ/ICS_COMMAND/pull/91) `34b61cd`（issue #89）]**：① 後端 `resolve_scope(session, requested)` 安全閘 + `NULL_SCOPE` 三態過濾（int 精確／NULL 實戰池／None 不過濾）套 6 repo（events/cop/decisions/manual/dashboard/audit）；**strict isolation**（無 active→實戰 NULL 池非「看全部」，歷史需 `COMMAND_ROLES` override）；create 由 server 蓋 active `exercise_id`（不信任 client），cop create 綁 active（= P1-16 接點）。② 演習硬刪（級聯白名單表·sysadmin-only·非 active·同連線 status 再確認防 TOCTOU）。③ **跨 session 即時**：`cop_hub.broadcast_all` 於 activate/archive/delete 廣播 `exercise_switched` 給**所有**連線（不過濾 scope）→ 各 client 重對帳 map/面板/chip（cop_stream → DOM `exercise:switched` → main.js refresh）。④ 前端演習管理面板 + active chip（文字「演習／」「實戰／」「無進行中場次」），退役 ttx-toggle/session_type。⑤ Review LOW 收尾：`GET /api/cop/entities/{uid}` by-uid scope gate（非指揮層跨場讀→404）、`reset-exercise` 補清 `exercise_kpis`。測試：test_exercise_scoping（scoping/strict/role-gate/delete/by-uid）+ test_cop_ws（operator 收廣播）+ test_realtime_hub + 前端 cop_stream/exercises 全綠（216 vitest + pytest）；`/code-review`＋`/security-review` 無 HIGH/MED 新破口。**解鎖 P1-16 綁定**。<br>**[follow-up [#93](https://github.com/winson3QQ/ICS_COMMAND/issues/93) 完成 — [#96](https://github.com/winson3QQ/ICS_COMMAND/pull/96) `156eb65`]**：稽核日誌綁 active session（**Model B**）—— `audit()` 未明傳 exercise_id → 自動戳當前 active session（演習/實戰），**全部 audit（含 decision/ttx/pi-sync/系統層）**綁該場，AAR 可查/回放完整時間軸；`exercise_*` lifecycle audit 例外保 NULL（cascade durability，review #93-1）。另補 **COP 操作 audit**（create/update/delete，原本完全沒記；update 全 kind 含 event → 捕捉 QRF/事件移動軌跡；best-effort 不擋即時同步）。顯示/TAK 不受影響（audit 正交）。2026-06-04 P1-16 follow-up dogfood 衍生。<br>**[follow-up #93 收尾完成 — [#97](https://github.com/winson3QQ/ICS_COMMAND/pull/97) `f9ef5af`]**：① 稽核日誌 UX 具體化（`cop_entity_*` 依 `detail.kind` 顯「新增/移動/刪除＋節點/路線/範圍/設施/圖釘」取代泛稱「標繪」、target 顯 callsign、`_escAudit` 跳脫、session 事件納入「帳號」篩選）；② 後端 abandoned session（切帳號/關頁→token 不再被用）逾時登出補 `SESSION_EXPIRED` audit、idle cutoff `14h→min(IDLE,SESSION)=15分`（與 `check_session` 同閾值）、`main.py` lifespan 加每 5 分週期 session 清理；③ 補 DoD 缺測 2（cleanup audit 留痕 + idle cutoff 回歸守門）；④ code-review/security-review 修正（shutdown `_cleanup_task` cancel 後 await 回收、`DELETE ... RETURNING` 去並發重複 audit、audit 失敗 `log.warning`、`_escAudit` 一致化 operator/fallback），**無 HIGH 以上破口**。pytest 607 passed/1 skipped。UI 最終驗（§8 Step 4）待人工瀏覽器驗（presentational）。 |
| ✅ **P1-15** | **多人即時共享 COP（[issue #29](https://github.com/winson3QQ/ICS_COMMAND/issues/29)）**：`cop_entities` 作為單一正規化 COP 儲存兼**同步邊界**——per-entity `version_clock` 樂觀鎖 + WebSocket 廣播 + 前端 per-uid LWW merge，解「畫了/釘了別人不 reload 即時看到、拖移/結案即時同步」痛點。**即時管線（done）**：PR-A 樂觀鎖 + soft-delete [#33](https://github.com/winson3QQ/ICS_COMMAND/pull/33) `e2a73c4`；PR-B `/api/cop/*` REST + If-Match [#35](https://github.com/winson3QQ/ICS_COMMAND/pull/35) `c2b4d3d`；PR-D WebSocket hub（`--workers 1` pin）[#36](https://github.com/winson3QQ/ICS_COMMAND/pull/36) `415b80c`；PR-E 前端 `cop_stream.js` [#37](https://github.com/winson3QQ/ICS_COMMAND/pull/37) `8ca2d34`。（PR-C normalize_manual 跳過，被 PR-B POST 涵蓋。）**cutover（map 物件從 map_config blob → cop_entities，done）**：PR-G1a route+polygon [#38](https://github.com/winson3QQ/ICS_COMMAND/pull/38) `022fb24`；PR-G1b event 位置圖釘 [#39](https://github.com/winson3QQ/ICS_COMMAND/pull/39) `86e389c`（事件記錄仍在 events 表、流程不變；reset 一併清 cop + 廣播 resync）。分類用 `attributes.kind`（route/polygon/event）+ CoT 相容 `type`（為 P2-04 TAK 預留）；不做 migration、從 0 開始；節點（icon='pin'）固定不 cutover。**收尾（done）**：**PR-H** [#41](https://github.com/winson3QQ/ICS_COMMAND/pull/41) —— 退役 PR-E ＋標記 MVP（cop_stream 改純資料層、移除自建 marker / ◉COP / ＋標記）+ **移除流向（flow）功能**（與 route 重疊、cutover 後連不到事件；route 才是有向移動路徑的正解）+ 規格書/ROADMAP 收尾 + close #29。**G2 descoped**：flow 移除（非 cutover）；infra 與節點真正缺的是「placement UI」（MapLibre 改寫時遺失的空 stub），與即時同步無關 → 另開 [issue #40](https://github.com/winson3QQ/ICS_COMMAND/issues/40)（補建立 UI 時順便做成 cop_entity + 綁 exercise）。**TAK 對齊**：cutover 時 type/source/origin_node_id 已對齊 CoT，雙向 TAK 實作留 P2-04 |
| ✅ P1-16 | **節點 / 設施 placement UI（[issue #40](https://github.com/winson3QQ/ICS_COMMAND/issues/40)）**：P1-10b（Leaflet→MapLibre 改寫）把節點/設施放置 UI 變成空 stub（`map.js` 的 `_openInfraForm` / `_startInfraPlace` / `_saveInfraPosition` / `_cancelNodePlace` 皆空）——**功能 regression**。每場演習地點不同，5 常駐編組（收容/醫療/指揮/前進/安全）+ 臨時設施（醫院/收容所/急救站/警局/消防）位置需逐場設定；現況節點只能 `map_config.seed.json` 寫死座標（換地點全錯）、設施完全無法新增。修法：(a) 工具列「放置節點 / 放置設施」→ 點地圖放置 → 拖移 / 刪除；(b) 落地為 **cop_entity**（`attributes.kind='zone'`（節點）/ `'infra'`（設施））→ 沿用 P1-15（#29）即時同步管線（cop_stream / EntityLayer / `copEntityTo*` adapter）+ 綁定當前 active exercise；(c) 設施類型沿用既有 `INFRA_TYPES`（醫院/收容所/警局/消防/公用 + 圖示/字母）。**依賴**：P1-15（#29 即時管線，done）；**對接**：P1-14（exercise data scoping — 放置物件綁 exercise）。源於 P1-15 PR-H G2 descope。<br>**[2026-06-03 reality check + A-Full 定案]**：空 stub regression 已確認（`_openInfraForm`/`_startInfraPlace`/`_saveInfraPosition`/`_cancelNodePlace` 皆 `{}`，刪除可用、放置 no-op；節點+設施現皆活在 map_config blob，非 cop_entity）。**決定走 A-Full（exercise 綁定 + 可回放做 AAR；不綁無法複盤）**。釐清出**三層資料模型**：① **永久設施**（真醫院/消防/警局/診所）= 公開資料底圖層、**不隨演習** → 另立 **P1-17**；② **5 常駐節點**（指揮/收容/醫療/前進/安全）**現為 `map_config.seed.json` 寫死座標（錯——每場地點不同）→ 需 cutover 成 exercise-scoped cop_entity**（**修正 P1-15「節點 icon='pin' 固定不 cutover」決定**）；③ **臨時節點/物件** = exercise-scoped cop_entity。②③ 落 `cop_entity + attributes.kind(zone/infra) + exercise_id`，沿用 P1-15 即時管線；因帶 `version_clock`（P1-15 已有）**為 Wave 6 AAR 逐格回放鋪資料**（snapshot_repo = P2-06 預埋、回放 UI = Wave 6）。**硬依賴：P1-14**（active-exercise 綁定機制）→ **P1-16 A-Full 不可早於 P1-14**。建議順序：P1-14（FK/scoping）→ P1-16 A-Full（P1-17 永久設施層可並行/後做）。<br>**[2026-06-03 v1 完成 — [#92](https://github.com/winson3QQ/ICS_COMMAND/pull/92) `59c7c3d`（issue #40）]**：① **節點(kind='zone')** on-demand：工具列選 5 編組 → 點地圖放 → cop_entity（P1-14 自動綁 active/實戰）+ 拖移 + 刪除；adapter `copEntityToZone` + `_allRenderedZones` cutover 改讀 cop entities；收容=避難所屋頂象形、醫療=白十字、其餘 abbr。② **設施(kind='infra')** on-demand：5 類（醫院/收容所/警局/消防/公用）放+刪（拖移 follow-up）；`copEntityToInfra` + `_renderInfra` cutover + `_scheduleCopRender` 即時重繪。③ **cutover**：移除 `map_config.seed.json` 寫死 5 zones（出廠開機無預設節點、全靠 on-demand；改正「每場地點不同」regression）。④ **RBAC**：前端 `canUseRealModeControls` gate UI + **後端** cop create/delete 對 kind∈{zone,infra} 加 `COMMAND_ROLES`（operator/observer 無法繞 API 建/刪；security review HIGH-1 落實）。⑤ 附帶：移除 vestigial「指揮部設定」+ 區段改名「站內地圖」、歸檔鈕只對 active、稽核 exercise_* 中文短標、放置 banner 重複 id。測試：copEntityToZone/Infra vitest + `TestNodeInfraRBAC`（後端 4 測），223 vitest + pytest 全綠；`/code-review`＋`/security-review` 通過（HIGH-1/MED-1/LOW 全修）。**follow-up（2026-06-03 記 → 2026-06-04 全收）**：✅ ① **設施拖移**（`_syncEventDragHandles` 納 infra + drag callback 按 kind 分支 click→`_onInfraClick`／per-frame→`_renderInfra`；dragend 泛型）；✅ ② **放置流程參數化**（`_nodePlaceState`+`_infraPlaceState` 合一 `_placeState`，共用 `_startPlace`/`_cancelPlace`/`_placeAt`，export 留 thin wrapper、main.js 不動；消 ~50 行 + 根除互斥隱患）—— ①② 完成於 [#94](https://github.com/winson3QQ/ICS_COMMAND/pull/94) `93d3d65`（in-browser 驗過）。✅ 死碼（`btn-node-place` toggle、map.js `showZoneDetail` 死刪除塊）已清 `7ed5e45`。**P1-16 follow-up 全清。**|
| ✅ P1-17 | **永久設施公開資料底圖層（[issue #88](https://github.com/winson3QQ/ICS_COMMAND/issues/88)，2026-06-03 reality check #1 衍生）**：真實醫院 / 消防 / 警局 / 診所等**永久公共設施**為**基準參考層、不隨演習走**——與 P1-16 的 exercise-scoped 節點/臨時物件分離（三層資料模型見 P1-16）。**動機**：這些是現實常駐基礎設施、跨演習不變；不該被 P1-16 的放置 UI 逐場手放、也不該被演習 reset 清掉。**資料來源**：台灣**政府開放資料**（醫療院所 / 消防分隊 / 警察機關位置等 dataset；NCDR/NFA 圖資生態）——**每個來源須逐一確認非中國供應鏈**（CLAUDE.md），離線打包匯入。**落地**：匯入為**唯讀基準圖層**（非 cop_entity、非 map_config user-data、不受 exercise scoping / reset 影響），工具列可切換顯示。**與 P1-16 分工**：P1-16 = 隨演習走的節點/臨時物件（放/搬/刪/綁 exercise）；P1-17 = 永久設施基準（匯入、唯讀、常駐）。**順序**：可獨立於 P1-16，建議 P1-16 後或並行；主要工作量在 open-data 匯入 pipeline + 供應鏈逐源確認。<br>**[2026-06-03 v1 完成 — [#90](https://github.com/winson3QQ/ICS_COMMAND/pull/90) `9614f7f`（醫療 → v1.1）]**：① **3 源上線**——避難收容處所(NCDR/內政部 #73242)/消防(消防署 #5969)/警察(警政署 #5958)，共 **8318 筆**；維護者腳本 `command-dashboard/scripts/import_facilities.py`（curl 下載→正規化→`static/facilities.seed.json`）。② **供應鏈全過**：來源皆台灣政府（非中國）、政府開放授權第1版（CC BY 4.0 相容）；`pyproj`（警政 TWD97 TM2→WGS84，MIT/NOAA/OSGeo 非中國）**僅維護者匯入用、不進 runtime requirements**。③ **PII 剝除**（管理人姓名/電話不入 seed）。④ **資料品質普查**：每筆「縣市↔座標」bbox 一致性檢查，抓掉 78 筆來源座標錯誤（海上點/placeholder/跨縣市），全 log 留痕。⑤ **前端**：自管 `facilities` clustered source（MapLibre `cluster:true`＝P1-10f 技術，**scope 限 facilities**）＋彩色圓＋110(警)/119(消)數字徽＋⌂避難所象形＋hover 名稱 tooltip；獨立層不碰 zones/infra/polygons/routes，不依賴 EntityLayer。⑥ **後端**：唯讀 `GET /api/facilities` + `facilities_store`（只讀 static seed）。⑦ **決策**：seed **tracked**（固定常駐、factory-default baseline，符 User Data 邊界；非 user-data）；普查用 **county-bbox**（零依賴、有 log，未升 point-in-polygon）。⑧ **命名**：程式名 `facilities`＝UI「**公共設施**」，**≠** 既有 `infra`（UI「設施」，P1-16 演習臨時物件）。⑨ **醫療（醫院＋診所）→ v1.1**：bulk 名單皆「有地址無座標」、需 TGOS geocode key，故延後。測試：Python 13 + JS（facilities 10）全綠；`/code-review`（7 修正）+ `/security-review`（0 finding）通過。<br>**[2026-06-03 close]**：v1 baseline（3 源 8318 筆）交付，本項標 ✅ 結案（[#88](https://github.com/winson3QQ/ICS_COMMAND/issues/88) 已關）。**唯一延後 = v1.1 醫療**（醫院+診所，卡 TGOS geocode key 外部依賴）；屬增強非阻塞，續做時另開 issue 追蹤 |

### Definition of Done

- [ ] `pytest` + `npm test` 全綠，coverage 不低於拆分前
- [ ] 無 import error / dead route（`uvicorn --reload` 啟動 clean，無 warning）
- [ ] `services/cop_service.py` 有 contract test 鎖定 source enum（含 `pi-node` 預留）
- [ ] `pi_*_repo` + `sync_repo` 通過「federation 介面相容性」測試（模擬非 PWA 上游節點推送）
- [ ] MapLibre GL JS 已上線、24/7 主題切換可運作、entity layer 抽象介面已定義
- [ ] 規格書 v3.0 merged
- [ ] **Tag**：`command-v2.2.0`（原規劃 1.0.0；後端因 #24/#26 RBAC/deploy 早期已躍 2.x，故版號階梯 rebase 到 2.x 線）

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
| ✅ **P1-10a** | **採用 WaveInk Design System + MIL-STD-2525 token 體系 + 跨平台等寬字體**（**一次到位，不分階段**，TAK 整合就緒為優先設計約束）：<br>**(i) 基底**：vendor `colors_and_type.css` + `DESIGN.md` policy 自 WaveInk `docs/design/`（commit hash 釘版），落地為 `command-dashboard/static/css/ds-tokens.css` + `docs/design/POLICY.md`。直接繼承：GitHub Dark Dimmed 色階、`--space-1`~`--space-9`、`--radius` 三段、border-not-shadow 紀律、動畫節制（僅保留 keyframe，不引入 JS 動畫）、Unicode-as-iconography（禁 Material/Heroicons/Lucide/Phosphor/Font Awesome）、empty-state 文案語氣（terse / imperative / 無 marketing copy）。<br>**(ii) MIL-STD-2525 entity color token 作為一等公民**（**主要設計考量，非例外**）：新增 `--mil-friendly` / `--mil-hostile` / `--mil-neutral` / `--mil-unknown` token，標準色對齊 MIL-STD-2525C 附錄 A；frame 形狀（friendly 矩形 / hostile 菱形 / neutral 方形 / unknown 四葉草）由 P2-05 milsymbol 接管渲染，token 提供色彩 SoT。WaveInk policy 的「No new accents」原則於本 token group 例外開放，並反向標註：未來新增 entity affiliation 必須對齊 MIL-STD-2525，不得自創色。<br>**(iii) 字體 ops-grade upgrade**：**JetBrains Mono 離線打包進 `static/fonts/`** 取代 WaveInk 的 system mono 預設——指揮場景 callsign / 座標 / MGRS / 時間戳跨 Mac/Win/Linux 一致性是 ops 規範（system mono 在三平台分別是 Menlo / Consolas / DejaVu，視覺差異不可接受）；JetBrains Mono 對 0/O、1/l/I、5/S 有明確 disambiguation 設計。UI 文字維持 WaveInk system stack（Noto Sans TC fallback）。<br>**(iv) divergence 文件**：`docs/design/POLICY.md` 明文列出 ICS_Command 對 WaveInk DS 的兩處 fork 點（MIL-STD-2525 token、JetBrains Mono webfont）與 rationale，下次 WaveInk DS 升版時做為 conflict 解決依據 — 完成於 [#18](https://github.com/winson3QQ/ICS_COMMAND/pull/18) `1d46025`（2026-05-26；含 8 個 /code-review finding fixes + CI snapshot script 順手修；/security-review 0 vuln；後續 P1-10a-2 follow-up 處理 js/inline style hex 遷移 + 其他 4 HTML 檔 SoT 對齊）| 3-4 天 |
| P1-10a-2 | **DS token migration follow-up**（P1-10a 收尾遺留）：js/ 126 處硬寫 hex + commander_dashboard.html 68 處 inline `style=` + 其他 4 HTML 檔（scenario_designer / admin_backups / icon_preview / qr_scanner）token 對齊。**動工前 reality check 已做**（[#18 comment](https://github.com/winson3QQ/ICS_COMMAND/pull/18) + 2026-05-26 session）：51 hex 屬 in-DS 可對映、其中 41 在 JS object/canvas context 不能直 `var()` 替換（要 `getComputedStyle` helper）、102 hex 屬 out-of-DS semantic shades 需 design decision（加進 DS / 留 inline + 註解 / color-mix）。**故意延後到 P1-10b 之後**：map.js 49 hex（佔總量 39%）會在 P1-10b MapLibre 重寫時大部分消失，現在動 = 白工 53%。P1-10b done 後重評殘餘 scope。<br>**[2026-06-03 reality check #2 — scope 不減反增]**：P1-10b 拆了 Leaflet,但 **P1-10d/P1-16/P1-17 又加新硬寫 hex**（`_NODE_COLORS`/`_SEV_COLORS`/`INFRA_TYPES`/`ZONE_ICON_SVG`/facilities 色）。實測殘量:**JS hex ~199**（map.js 57、auth 36、charts 30、events 24、facilities 17、entity_layer 11…）+ **HTML inline `style=` ~107**（commander 70、qr 14、scenario/admin_backups 各 10…）。**性質確認**:大量在 MapLibre paint 表達式 / canvas SDF bake / SVG 字串裡,**不能直接 `var()`**（`_SEV_COLORS` 自註「JS canvas 需字面值、與 ds-tokens 同步維護」），加上 out-of-DS semantic 色需 decision → **非機械 find-replace,需 `getComputedStyle` helper（架構）+ 設計決策**。**不修的影響 = 維護性 debt**（改 DS token / 加新主題時硬寫值不跟著變，須手動獵殺易漏），無 user 可見破壞。建議:等 DS 改版 / 加主題時再做,屆時連 helper 一起設計。 | 需 helper + 決策，獨立 scoped task |
| ✅ P1-10b | **MapLibre GL JS 全替換 + entity layer 精緻化基底**（**14/14 done，完成於 2026-06-01**：steps 1-9 [PR #22](https://github.com/winson3QQ/ICS_COMMAND/pull/22) `d3e9386`~`418a65a`；step 10 [PR #23](https://github.com/winson3QQ/ICS_COMMAND/pull/23) `1344913` `ee1a843`；steps 11-13（清死碼 + Leaflet API sweep / unit tests / CSP smoke）[PR #30](https://github.com/winson3QQ/ICS_COMMAND/pull/30) `a0d56c8`；**step 14：1000-entity FPS benchmark 60 FPS PASS**（avg 60.1 / median 59.9 / p95 59.9，遠超 DoD ≥30；2026-06-01 真實 Chrome 實測；benchmark 為一次性 dev 工具，未進 prod））— scope 已擴張，2026-05-26 /plan + mini-taiwan deep-dive 後修訂 — 移除 Leaflet，map.js 1972 行 → **實際 2436 行** / 126 Leaflet refs → **實際 69 refs（大多 alias/legacy fallback/註釋）**核心重寫，內部拆 5 子檔（maplibre_core / entity_layer / coord_tools / draw_tools / event_popup）→ **實際成 7 子檔**（多 event_drag + label_markers），public API 門面保留；HTML `#leaflet-map` 與 CSS `.leaflet-*` 保留 alias。**借鏡 mini-taiwan 7 條**（[deep-dive 結論](https://github.com/winson3QQ/ICS_COMMAND/issues/19)）：(i) EntityLayer 抽象（1 GeoJSON source per entity-class + `update(featureCollection)` 批量替換，取代 marker 物件陣列）— 1000 entity FPS≥30 唯一可行路徑；(ii) 4-layer state stack（base / glow / selected / collision-or-halo）— T1→T2 視覺關鍵；(iii) **SDF icon + `icon-color` data-driven** — 單一灰階 PNG runtime 著色，**為 P2-05 MIL-STD-2525 4 affiliation × N symbol 鋪路**，不做 = P2-05 重工；(iv) `setFeatureState` hover/selected + paint case expression（**修正 mini-taiwan 未做之反例**）；(v) `symbol-sort-key` priority placement（修正反例 — 戰術場景 critical 不被擋）；(vi) `text-halo` + JetBrains Mono overlay 文字；(vii) LOD（minzoom/maxzoom 切換 entity 密度）。**Source × affiliation 雙維度註解寫進 `cop_entity_repo.py`** 修正 mini-taiwan 單維度反例 — **為 P2-04 鋪路**，不做 = schema 誤用。**不借**：track 0–1 插值（COP entity 靜態座標，移動 entity 是 P2 TAK 範圍）、collision detection（指揮場景量級不對，clustering 由 P1-10f 處理）、Three.js 3D（ROADMAP 排除）。應用 mini-taiwan `PERFORMANCE_OPTIMIZATION_PLAN.md` 6 條避雷 checklist。CSP report-only 不爆，enforce 留 P1-10h。**達成 T2 基底** | 6-8 天 |
| ✅ **P1-10c** | **PMTiles 台灣底圖 + 戰術底圖 doctrine 落地**（scope 已縮減，2026-05-26 修訂）— 用 Protomaps 工具產出台灣全圖 PMTiles 單檔（約 200-400 MB），Pi 直接 serve，完全離線。**只做 2 套 vector style**：`dark`（夜間 ops / 室內預設）+ `muted-day`（白天演練，低飽和度）；**砍 dusk + sat**（dusk 花俏無 ops 價值；sat 違反戰術底圖 doctrine 不得作為 default）。**戰術底圖 doctrine 寫進 `docs/design/POLICY.md`**：底圖必須 desaturated；唯一 saturated 色 = MIL-STD-2525 affiliation + severity token；禁彩色底圖預設、禁 satellite default（特定情報需求才開）。接上 P1-10b 已建好的 MapLibre 殼，只是 style.json 換來源。**達成 T2 完成**。<br>**底圖來源 / build recipe 已落定（2026-06-01）**：採 Protomaps 體系 `pmtiles extract` 切台灣（路徑 A）、planetiler+Geofabrik 為 fallback（路徑 B），schema 對齊 `@protomaps/basemaps`，全鏈非中國 + ODbL attribution，詳見 [`command-dashboard/docs/design/POLICY.md` §底圖資料來源與 build recipe](../command-dashboard/docs/design/POLICY.md)。**reality check 更正**：本項另含「dark/muted-day 主題切換 UI」+「`setStyle` 後 EntityLayer/MGRS 重建」隱性 scope；`taiwan.pmtiles` 為 gitignored deploy artifact、fresh clone 無此檔，**若需重 build 實估 3-5 天**（檔已在則接近原估）。<br>**釐清**：mini-taiwan 是 P1-10b 渲染架構借鏡（#19），**非**底圖來源（其底圖為 Mapbox 線上，已拒）。<br>**✅ 完成（2026-06-01）**：① 步驟 1-3 底圖點亮（pmtiles protocol + dark/muted-day style + ODbL attribution）[#57](https://github.com/winson3QQ/ICS_COMMAND/pull/57) `9ccf190`；② 整備/側載基建（manifest + provision/publish/preflight + 雙 release + start preflight）[#59](https://github.com/winson3QQ/ICS_COMMAND/pull/59) `632703d`；③ 步驟 4 dark↔muted-day segmented 主題切換（setStyle-free 抽換底圖層、overlay 不掉）+ MGRS grid 隨主題換色 [#61](https://github.com/winson3QQ/ICS_COMMAND/pull/61) `ab2fbcd`；④ post-merge code/security review 修正（切換 race + token argv）[#63](https://github.com/winson3QQ/ICS_COMMAND/pull/63) `fe84e6e`；doctrine 早於 P1-10b stub 於 POLICY.md。**衍生**：低 zoom MGRS gap → [#56](https://github.com/winson3QQ/ICS_COMMAND/issues/56)（P2-05）；硬化 LOW → [#64](https://github.com/winson3QQ/ICS_COMMAND/issues/64)（**1+2+4 完成** [#80](https://github.com/winson3QQ/ICS_COMMAND/pull/80)：dead exempt 移除 / pmtiles 穿越縱深 / lib SHA 驗；**#3 CSP 留 P1-10h**）。muted-day 視覺微調 / 前端「未整備」橫幅為非阻塞 follow-up。<br>**[2026-06-04 follow-up：白天 overlay 對比 — [#95](https://github.com/winson3QQ/ICS_COMMAND/pull/95) `5653010`]**：dogfood 發現 muted-day 淺底圖上 marker overlay 對比不足（白外框消失、整體糊）。查業界（Carto Positron land `#fafaf8` / Esri Light Gray Canvas）確認**正解＝淺去飽和底圖當 ground + overlay 自拉對比當 figure（figure-ground），非調暗底圖**（且對齊 TAK/2525 顯示哲學）。`_applyOverlayThemeContrast(theme)`：marker 外框隨主題翻（夜間白 / 白天深 `#0d1117`，接 setBasemapTheme + init），**只調外框對比、不改 severity/affiliation 戰術語意色**（兩主題恆定 → 2525 日夜一致、不違 doctrine）；三類 marker 外框統一（節點/設施圓 circle-stroke 1.5；事件 ◆ 因 SDF 實心 alpha halo 無法加寬，改「下層墊大菱形」當 theme-aware 外框）。為 P2-05 (2525) 鋪 overlay 對比基建。| 1-2 天（重 build 則 3-5 天）|
| ✅ **P1-10d** | **事件符號系統（NAPSG 對齊）+ severity token + taxonomy 資料化** — 每種事件型別一套 icon；統一 stroke / corner radius / 陰影；severity 配色集中為 CSS token，禁散在 JS 寫死 hex；critical 事件用 `@keyframes` halo pulse（純 CSS，省 Pi CPU）。<br>**設計落定（2026-06-02，[`docs/design/event-symbology-mapping.md`](../command-dashboard/docs/design/event-symbology-mapping.md)）**：① icon 風格 = **③ 混合**（◆ diamond + NAPSG 象形 icon + abbr 移符號外）；② severity 採 **NAPSG 標準 hex** token（critical `#FF181E` / warning `#FF8918` / info `#237ACF`），**固定 3 級**；③ **taxonomy 資料化**為前置地基（`event_taxonomy.seed.json` + `data/` runtime + `/api/event_taxonomy` + **收斂 events.js/map.js 重複定義**），且每事件型別帶 **CoT bucket**（TAK 互通，精確 suffix 留 P2-04）。**拆解**：地基 + 視覺（本項）先；**admin CRUD 編輯器另開 [#66](https://github.com/winson3QQ/ICS_COMMAND/issues/66)**（事件+群組全 CRUD、severity 級別固定）。<br>**事件資料模型（三軸：類型 / 回報組 / 處理組）+ 命名解撞名 + TAK 對照**（2026-06-02）見 design 檔 §事件資料模型。**衍生工作**：(a) 符號改依 **event type 非 group**（PR-2b-3 NAPSG glyph；中間步先用事件 abbr）、(b) #66 把「事件類別表 / ICS 組織表」分開（解撞名，不再共用 node_type）、(c) 右側欄 **pivot 分組**（by 類別 / 處理組 / status）、(d) report/handle → CoT `<detail>`（P2-04）。<br>**✅ 完成（2026-06-02）**：taxonomy 資料化 [#68](https://github.com/winson3QQ/ICS_COMMAND/pull/68) `8c3fb37`／前端讀取+收斂 [#69](https://github.com/winson3QQ/ICS_COMMAND/pull/69)／**視覺**（◆ diamond + NAPSG severity token + critical pulse + 事件型別 abbr + 尺寸/顏色全站同步）[#70](https://github.com/winson3QQ/ICS_COMMAND/pull/70) `d8c13ab`。設計 [#67](https://github.com/winson3QQ/ICS_COMMAND/pull/67)/[#73](https://github.com/winson3QQ/ICS_COMMAND/pull/73)。<br>**後續完成（2026-06-02 續）**：(a) **NAPSG 象形 glyph 第一批 6 強配**（explosive/comm_fail/hazard/evacuation/facility/rescue；其餘維持 abbr，無乾淨外部標準對應）[#75](https://github.com/winson3QQ/ICS_COMMAND/pull/75)；(b) **解撞名**（event 改 `event_group` 不借 node_type）[#77](https://github.com/winson3QQ/ICS_COMMAND/pull/77)；**#66 編輯器**：後端守門 [#76](https://github.com/winson3QQ/ICS_COMMAND/pull/76)／編輯+soft-delete [#78](https://github.com/winson3QQ/ICS_COMMAND/pull/78)／來源標註+`source` 定義欄（NAPSG/ICS，唯讀+回填）[#79](https://github.com/winson3QQ/ICS_COMMAND/pull/79)（**剩 C2 新增+icon picker**，見 [#66](https://github.com/winson3QQ/ICS_COMMAND/issues/66)）。**剩 follow-up**：(a) **其餘事件 NAPSG 象形字典擴充 + icon picker → 歸 [#66](https://github.com/winson3QQ/ICS_COMMAND/issues/66) C2**（民事 D/E 用 NAPSG 象形；軍事 A/B/C 內部符號走 2525/milsymbol P2-05；見 [`classification-crosswalk.md`](../command-dashboard/docs/design/classification-crosswalk.md) §6）、(c) 右側欄 pivot（不趕）、(d) report/handle → CoT `<detail>`（P2-04）。| 3-4 天 |
| ✅ **P1-10e** | **Polygon / route hover + selected 狀態** — hover: outline 加粗 + fill opacity 微升；selected: 虛線外框 + **opacity 呼吸**（修正 spec「dashed animated」：MapLibre 無 `line-dasharray` data-driven / 無 `line-dash-offset` → 改虛線 + RAF opacity 呼吸，便宜穩定）；點空白取消、重繪後重套。走 feature-state + paint case，不碰顏色語意 — 完成於 `79561a7`（2026-06-03，hover human-verify pass）|
| ~~**P1-10f**~~ ❌ **descoped** | **Symbol-layer-based clustering** — **移除（2026-06-03）**：COP 量級不大、clustering CP 值低，且與「critical 事件永遠可見/脈動」衝突（cluster 會吞掉 critical）。決策見 `79561a7` commit。未來真有「點太多」痛點再評估 |
| ✅ **P1-10g** | **圖層切換微動畫** — 200ms fade-in（EntityLayer.setVisible 改可見性「真的改變」時 opacity 過渡；首次瞬時避免載入閃、同值 no-op、hide-timer 防競態）。spec 即「fade-in」，隱藏即時（資料 clear）為預期 — 完成於 `79561a7`（2026-06-03）|
| ✅ **P1-10h** | **CSP enforce** — `CSP_MODE` 預設 report-only → **enforce**（僅 `ENFORCE_PATHS`/commander 實際擋，其餘 report-only fallback）。**#64-3 確認**：enforce CSP 已涵蓋 MapLibre worker（`worker-src 'self' blob:`）+ PMTiles range（`connect-src 'self'`）；**不需** `wasm-unsafe-eval`/`child-src`（原列為 P2-05 milsymbol wasm 前瞻，現加=無謂放寬）。補 integration test 鎖定 directive — 完成於 `f1a3f3f`（2026-06-03，human-verify 無 CSP violation）|

**總工時估**：13-18 天（單人）；演練前**至少 3 週**開工，留 buffer。P1-10b 擴張 +2-3 天的成本被 P1-10c 縮 scope + P1-10d 因 SDF infra 已備而提速抵銷 + P2-04/05 各省 2-3 天，**淨持平或更省**。

**視覺等級 ladder（2D only，無 3D，全程 MapLibre）**：

| 等級 | 對標 | 達成時點 |
|---|---|---|
| T0 | 現狀（Leaflet + grayscale + 預設 marker）| — |
| T1 | dashboard chrome only（[Protomaps Dark](https://maps.protomaps.com/?theme=black) 裸用）| P1-10a 完成（地圖本身未動）|
| **T2 基底** | 戰術 marker（4-layer state stack + SDF + hover state + LOD）| **P1-10b 完成** |
| T2 完成 | + dark/muted-day 底圖 doctrine 落地 | P1-10c 完成 |
| T2.5 | + critical halo pulse + severity icon 系統 | P1-10d 完成 |
| **T3**（[ATAK Web](https://wiki.tak.gov/) / [Felt](https://felt.com) 級）| + polygon/route hover-selected + 切換動畫（clustering descoped）| ✅ P1-10e + 10g 完成（2026-06-03）|

**實作順序**（**TAK 就緒度為優先排序原則**）：
1. ✅ P1-10a（WaveInk DS + MIL-STD-2525 token + JetBrains Mono）— chrome T0→T1
2. ✅ **P1-10b**（擴張版：MapLibre + entity layer 精緻化基底）— 地圖 T0→T2 基底；**為 P2-04/05 鋪路**（SDF + EntityLayer + source×affiliation 雙維度）— 完成 2026-06-01
3. ✅ P1-10c（縮 scope：2 套 style + doctrine 文件）— 底圖 T2 完成
4. P1-10a-2（DS migration follow-up）— map.js 49 hex 占 P1-10a-2 39%，P1-10b 後重評殘餘
5. ✅ P1-10d（SDF icon set + severity halo pulse）— T2→T2.5；SDF infra 已備
6. ✅ P1-10e + P1-10g（hover-selected polish / 微動畫）— T3（**P1-10f clustering descoped**）— 完成 2026-06-03
7. ✅ P1-10h（CSP enforce + integration test）— T3 鎖緊（安全層）— 完成 2026-06-03

**為 P2 鋪路的明確成果**（P1-10b 不做這條 = P2 重工）：

| 鋪路項 | 落在 | P2 受惠 | 不做的後果 |
|---|---|---|---|
| SDF icon + `icon-color` pipeline | P1-10b | P2-05 milsymbol 直接套 | P2-05 多 2-3 天 |
| EntityLayer 抽象（多 source 合併 hook）| P1-10b | P2-04 TAK CoT push 直接套 | P2-04 多 2-3 天 |
| Source × affiliation 雙維度註解 | P1-10b | P2-04 schema 不誤用 | 撞牆才改 schema |
| 4-layer state stack | P1-10b | P2-04 affiliation × status 顯示 | P2 重寫 entity layer |
| 戰術底圖 doctrine in POLICY.md | P1-10c | 拒絕「加 sat 預設」反 doctrine 要求 | 沒 SoT 可引用 |
| CSP `wasm-unsafe-eval` enforce | P1-10h | 任何 wasm 工具（含 milsymbol wasm fallback）已備 | P2 補 CSP |

**P1-10 DoD（補充上層 DoD）**：
- [ ] Lighthouse Performance score ≥ 80（Pi 500 上）
- [ ] 1000 個 entity 同時渲染 FPS ≥ 30
- [ ] 主題切換無 FOUC（flash of unstyled content）
- [ ] CSP test green，無 `unsafe-inline` 例外
- [ ] PMTiles 離線（網路全斷）下地圖完整可用
- [ ] **戰術底圖 doctrine 寫入 `docs/design/POLICY.md`**：底圖必須 desaturated；唯一 saturated 色 = MIL-STD-2525 affiliation + severity token
- [ ] mini-taiwan 7 條反例 — 6 條避雷 checklist 全套用、2 條視覺反例（hover state、symbol-sort-key）已修正

**明確排除（移到 P2）**：
- **MGRS grid** — 軍規 grid 屬 TAK / MIL-STD-2525 同一生態，自然該與 CoT 符號渲染一批做。P1 只實作基本經緯度 grid。
- **3D entity layer** — 同上理由，等真實山地 / 空域需求出現再評估。
- **Mapbox Standard 3D 建物 + lightPreset** — 需網路 + 商業 SDK，違反 P1-10 DoD「PMTiles 離線」紅線；2D MapLibre 在 desaturated 底圖 + entity layer 精緻化下可達 T3，視覺天花板對戰術場景夠用。

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

### P1-12 範圍（統一 Key Management + At-rest 加密 + Backup GUI）

源於 P1-09 dogfood：當前 backup 機制（cron-style + env file 存 raw key）有 gap；加上 live DB at-rest 完全沒加密。**單獨修任一塊都會引入兩套 key management，所以統一設計**。

#### 架構：分層 key derivation

```
FIDO2 token (CTAP2 hmac-secret extension)
     │  unlock at service start：PIN + touch
     ▼
master key (32 bytes，僅 process memory)
     │  HKDF-SHA256，label-based derive
     ▼
     ├── child[0] = "backup-v1"   → Fernet key（backup_db.py 用）
     ├── child[1] = "db-v1"       → SQLCipher key（live DB 加密）
     └── child[2..] = 未來用（audit log signing、session token 簽章）
```

- **單一 unlock 流程**：服務啟動時 prompt FIDO2 一次，所有 key 推導出來
- **單一 enroll 流程**：operator 一次註冊 2+ token，所有用途共享
- **單一復原 SOP**：rescue master key 紙本印出，所有層次都能還原
- **版本化 label**（`backup-v1` / `db-v1`）→ 未來 rotate 時新 label 對齊 migration window

#### 三個 sub-item

**P1-12a：Key management 基礎建設**（4-6 天）

- `scripts/keymgmt/enroll_fido2.py`：operator 插 token + PIN + touch，產 `/etc/ics/master-key.enc`（hmac-secret-wrapped）
- `scripts/keymgmt/unlock_key.py`：systemd ExecStartPre 呼叫，prompt FIDO2，解出 master，HKDF 推 child keys，export 環境變數給 service
- `scripts/keymgmt/derive_child.py` lib：標準 HKDF helper
- 多 token 冗餘：enroll N 把（主 / 備援 / 災後）任一把能 unlock
- Rescue：master key BIP-39-style 助記詞紙本輸出（可手動 reenroll）
- Fallback opt-in：env file mode 保留作 dev / no-FIDO2 場景，警示明顯
- 依賴：`python-fido2` (BSD, Yubico)、`libfido2` (BSD-2)、`cryptography` (Apache 2.0)。**全非中國**

**P1-12b：Backup / Restore 加密 + GUI**（2-3 天，**scope 從原本「backup DB」擴張到「整個 `data/` user-data 邊界」+ 三層觸發 + restore**，2026-05-28 修訂）

#### Scope 擴張 rationale

P1-13 確立 `data/` = user-data 邊界後（CLAUDE.md 紅線），backup 應該涵蓋整個 `data/`（含 `ics.db` + `map_config.json` + 未來 user uploads / derived data），而不是 DB-only。原本 1-2 天估時擴成 2-3 天。

#### 三層 backup 觸發模型

| 層 | 觸發 | 目的 | 性質 |
|---|---|---|---|
| **L1 手動** | Admin 按「立即備份」按鈕 | 隨時 checkpoint（午休 / 換班 / 不安心 / debug 前） | user-driven，無語意 |
| **L2 演習結束** | `POST /api/exercises/{id}/archive` 自動觸發 | 「這個 backup = 演習 X 收尾完整狀態」 | 系統 ceremony，**有語意 + metadata** |
| **L3 防呆** | server shutdown lifespan / `POST /api/admin/reset-db` 之前自動觸發 | 不可逆操作前的最後一道保險 | 系統 enforce，**user 看不到但救得回來** |

#### 細項

- `backup_service.py` 擴張為 `user_data_backup_service.py`（或保留名稱、擴大職責）：tar `data/` 整包（含 ics.db / map_config.json / 未來檔），排除 `data/backups/` 自己防遞迴
- `backup_db.py` CLI 保留作相容入口（內部呼叫新 service）；新增 `backup_user_data.py` 主入口
- `BACKUP_KEY` env var（由 P1-12a unlock script 提供，HKDF child[0]）；移除舊 `BACKUP_ENCRYPTION_KEY` 路徑（migration script 把舊 backup 用舊 key 解再用新 key 重加密）
- `POST /api/admin/backup`（admin role-only）：回 `{backup_file, sha256, size_mb, manifest}`，manifest 列出 backup 含哪些檔讓 restore 可選擇性恢復；附 signed download URL（短 TTL）
- L2 整合：`/api/exercises/{id}/archive` 處理流：archive 前先呼叫 backup → backup filename 含 exercise_id + name + ended_at（例 `backup-exercise-3-2026-05-28-1830-收容醫療演練.tar.gz.enc`）→ manifest 含 exercise metadata（從 exercises 表抓）→ 寫 audit log `EXERCISE_ENDED_BACKUP exercise_id=3 backup_sha256=...` → UI 顯示「Backup 完成」+ 下載按鈕 + 可選「重置回出廠（下場演習）」
- **「重置回出廠（下場演習）」明確定義**（2026-05-28 dogfood 觀察）：必須**同時**清 events + map_config + archive 當前演習，三件**不可分**。理由：當前架構下 events / map_config / exercises 三者沒外鍵關聯（見 P1-14），只清 map_config 不清 events 會造成「右側欄留上場 events、地圖看不到對應 marker」的資訊孤兒；user 認知污染、ops 沒意義。具體執行：(1) 觸發 backup（L2 路徑）→ (2) `UPDATE exercises SET status='archived', ended_at=now() WHERE status='active'` → (3) **`DELETE FROM events`**（或加 `archived_at` 欄 soft-delete，看 P1-14 決定）→ (4) `rm data/map_config.json` → (5) 下次 `_loadMapConfig` 觸發 startup ensure() 從 seed 復原。Audit log `EXERCISE_RESET_TO_FACTORY exercise_id=3 events_cleared=42 map_config_reset=true`
- L3 hook：
  - lifespan shutdown 攔 SIGTERM 跑 backup（best-effort，超時 30 s 放行）
  - `/api/admin/reset-db` 強制先 backup（`force=true` 才能跳過，audit 記 `FORCE_RESET_NO_BACKUP`），失敗則 409
- Admin panel：「立即備份」按鈕 + 進度動畫 + 下載；上下方列出歷史 backup（含 manifest 預覽）

#### Restore（新加）

```
[admin panel] → [備份管理]
  📤 還原備份
  [選擇檔案] backup-exercise-3-2026-05-15.tar.gz.enc
  ⚠ 將覆蓋當前資料。系統會先自動備份當前狀態為
     'pre-restore-{timestamp}.tar.gz.enc'
  [Manifest 預覽：exercise_id=3 / ended_at=2026-05-15 18:30 / 含 ics.db (3 MB) + map_config.json (8 KB)]
  [☐ 我了解，繼續還原]   [取消]
```

- `POST /api/admin/restore`（admin role-only）：上傳 tar.gz.enc + key unlock
- **自動 backup current 為 `pre-restore-{ts}`**（pre-flight 防呆，相當於 L3 第三條 hook）
- 解密 + 驗 manifest → 顯示給 user 確認 → 替換 `data/` + restart
- audit log `DATA_RESTORED from={file} pre_restore_backup={pre_ts}`
- 限制：當前 `status='active'` 的演習不允許 restore（避免覆蓋進行中場次，需先 archive 或強制終止）
- 失敗情境：解密失敗 / manifest schema 不符 / disk 空間不足 → 不動 current data + 清 tmp + 回 errored response

#### 測試

- admin role 200 / operator role 403（backup + restore 雙路）
- backup 內容可解 + manifest 對得上
- migration（舊 backup 升 key 後仍能還原）
- L2：archive 觸發 backup 寫到指定路徑 + filename 帶 exercise metadata
- L3：reset-db 沒帶 force 必先 backup；SIGTERM 觸發 shutdown backup
- restore：pre-restore-{ts} 自動產生；active exercise 拒 restore（409）；解密失敗不動 current

#### 與未來 Wave 6 時間軸 replay 的分工

P1-12b 解的是「**結束時的完整快照**」+ 「**全或無 restore**」場景。Wave 6 才解「**演習中任一時間點**」+ 「**雙視窗對比**」場景（snapshot_repo 累積 COP entity state，UI 時間軸 scrub）。兩者不衝突；P1-12b 是 ops 紀律基本盤，Wave 6 是複盤金本位。

**P1-12c：Live DB at-rest 加密（SQLCipher）**（4-7 天）

- 替換 `sqlite3` driver 為 `pysqlcipher3`（BSD-style，Zetetic 維護，**非中國**）
- 連線時 `PRAGMA key = "$DB_KEY";`（HKDF child[1]，從 P1-12a unlock 提供）
- Schema migration：既有明文 DB → 加密 DB 一次性轉換
- 風險評估：
  - SQLCipher 加密 overhead ~5-15%（讀寫），可接受
  - Pi 500 CPU 是否吃得消（CRYPTO benchmark needed）
  - 整 backup 鏈：明文 SQLite → SQLCipher 後 backup_db.py 需要拿 DB key 才能讀
- **不取代 LUKS 整碟加密**：SQLCipher 只保護 DB 檔；其他檔案（log / config / static）仍裸。整碟 LUKS 是獨立 P1-NN（沿用 ICS_DMAS `initramfs/fido2-luks-hook` 設計脈絡）

#### 依賴

- `python-fido2` (BSD, Yubico)、`libfido2` (BSD-2)
- `pysqlcipher3` (BSD-style, Zetetic)
- `cryptography` (Apache 2.0)
- Operator 採購 2+ FIDO2 token（YubiKey 5 / SoloKey / 任何 CTAP2 + hmac-secret）

**所有依賴非中國維護**（per CLAUDE.md 供應鏈規則）

#### P1-12 整體 DoD

- [ ] **P1-12a**: FIDO2 enroll + unlock + HKDF 三 script 上線；多 token 冗餘可用；rescue 紙本 SOP 完成
- [ ] **P1-12b**: Backup 改用 derived key + 涵蓋整個 `data/`；三層觸發（L1 手動 / L2 演習結束 / L3 防呆）；Restore + 自動 pre-restore-{ts} 防呆；admin role-only GUI；migration script 過既有 backup
- [ ] **P1-12c**: SQLCipher live DB 加密；既有明文 DB migrate 完成；pytest 全綠（含 DB key inject）；Pi 500 效能 benchmark 不超 +20% latency
- [ ] **Unified key management 文件**：`docs/security/key-management.md` 含架構圖 + enroll SOP + rescue SOP + rotate SOP
- [ ] **Threat model 更新**：`docs/compliance/threat_model.md` 加 at-rest encryption 章節
- [ ] **失去所有 token 演練**：rescue 紙本可成功 reenroll 並解出歷史 backup + DB
- [ ] 測試覆蓋：mock FIDO2 device unit test + 真 token integration（CI 跑 mock，本機驗收真 token）

#### Out of scope（明確不在 P1-12）

- **整碟 LUKS 加密**（Pi boot-time）→ 獨立 P1-NN 或 P2 hardening item，沿用 ICS_DMAS `initramfs/fido2-luks-hook` 移植但需 Pi 硬體實機驗證
- **Audit log signing key** / **Session token signing key**：架構已預埋（HKDF child[2..]），但實際接線交給之後 audit log / session 改造 item

#### 工時總計

- P1-12a：4-6 天
- P1-12b：2-3 天（2026-05-28 修訂：scope 擴張到整個 `data/` + 三層觸發 + restore）
- P1-12c：4-7 天
- 文件 + threat model + 演練：1-2 天
- **總計**：10-17 天（不可並行做，依序）

#### Bundling 決策（2026-05-28，dogfood 衍生）

P1-12b 與 P1-14 因架構重疊度高（重置 SOP / backup manifest / archive 行為 / UI 面板），建議**合併為單一 sub-phase「P1-12b+14：Exercise lifecycle data management」**（4-5 天，省 2 天 vs 序列做）。

| 重疊維度 | 影響 |
|---|---|
| 重置 workflow | P1-12b reset SOP 必須跟 P1-14 events scoping 同時設計，否則先做 P1-12b 用 `DELETE FROM events` 粗暴清，P1-14 來補時要 refactor |
| Backup manifest schema | P1-14 events 跟 exercise 綁定後 manifest 才能寫「這 backup 屬於 exercise X」；分兩次做 schema 改兩次 |
| Archive 行為 | P1-12b L2 archive backup 涵蓋 events 歸檔；P1-14 events FK 讓歸檔 query 乾淨；兩者天生綁定 |
| Admin GUI | 「演習管理」面板自然容納 backup 按鈕 + restore + 演習名稱 chip + 結束按鈕 |

排程：**P1-12a 之後 / P1-12c 之前**。P1-12a 仍是前置（backup 加密 key 來自 HKDF child[0]）。

Phase 1 內部建議順序：P1-10 全部完成 → P1-12a → **P1-12b+14 合併** → P1-12c。

---

## Phase 2 — TAK Server 整合（COP 第一個外部來源）

**目標**：部署官方 TAK Server，CoT 事件流入 COP，地圖渲染採 MIL-STD-2525 符號。

> **[Reality check 2026-06-04 — [#98](https://github.com/winson3QQ/ICS_COMMAND/issues/98)]**：盤 main `0db382f` 實際 code。**TAK 上行鏈路（P2-01 部署 / P2-02 `tak_service.py` CoT XML 解析 + 8089/9000 訂閱 / P2-08 XXE 測試）全 greenfield、零實作、XML lib 未選**；但 **P2-04 的下游落地層已被 P1 完整建好且刻意對齊 CoT**——`CoPEntity` v1 凍結（CoT 欄位對齊 + `source="tak"` 預留，P1-03/#15）、`cop_entity_repo`（version_clock CAS + stale 過濾 + TAK 風 soft-delete，P1-15/#29）；`routers/tak.py` schema + RBAC 已掛但 handler 是 stub、`cop_service.normalize_cot` 是 `NotImplementedError`。**關鍵路徑＝ P2-01+P2-02（重活、無 C0 繼承）→ P2-03 wire + P2-04 normalize（輕，下游全建）**。P2-05 可並行（設計 crosswalk §6/§8 已 LOCKED、管線預建；milsymbol 未 vendored / 2525 框零 / `POLY-ROUTE_TYPES` 仍寫死 hex；**疊加非全換**；MGRS #56 完全獨立可先做）。**動工前 3 個 drift（詳 #98）**：① **P2-06 指向錯層** — `snapshot_repo` 是 unit-KPI 聚合非 per-entity COP 時間軸，回放路徑（`cop_entity_tracks`／新表）須先重新確認；② **step-ca 憑證現 24h 且 federation 缺 peer cert** — 90 天 patch 與 client/peer cert profile + TAK Java keystore 信任 SOP 須提早於 C3-B；③ **CSP wasm** — milsymbol 若走 wasm fallback 需補 `wasm-unsafe-eval`（P1-10h 刻意未預放）。供應鏈現況乾淨；三相依 license 已圈定，lock 前逐一驗來源。

### Scope

| Item | 說明 |
|---|---|
| ✅ P2-01 | 部署官方 TAK Server（Docker compose，Java/Spring + Postgres）；產出 `deploy/tak-server/` 部署文件與 cert 設定 SOP。**[完成 — [#100](https://github.com/winson3QQ/ICS_COMMAND/pull/100) `35ed3d8`（issue #99）]**：`deploy/tak-server/`（compose + `.env.example` + `pki/issue-tak-certs.sh` step-ca→JKS + README）；TAK Server 5.7（官方 Docker 包不含 compose/.env，本目錄補）；M1 原生 arm64 build。**實機 boot dogfood（M1/16GB）**：DB tier 通（initdb+SchemaManager+cot/martiuser healthy）、Ignite 叢集 ACTIVE、**CoT streaming `:8089` 開**（P2-02 接點解鎖）；過程抓並修 3 整合 bug（volume chown / CoreConfig 密碼 / TAKIgniteConfig race）。web/API `:8443` 初次未起（api JVM 活但 Tomcat 不綁）→ **[#101](https://github.com/winson3QQ/ICS_COMMAND/issues/101) 已解（2026-06-05）**：兩根因皆憑證問題（非 Ignite 脆弱性）——① server 憑證須 RSA 非 EC（jwkSource bean 寫死 RSAPublicKey，`14b5373`）；② 缺 `fed-truststore.jks`（messaging 無條件部署 distributed-federation-manager Ignite service，缺檔→SSLContext 失敗→server node 掛→client 全斷線，PR #104 `fa38200`）。**實機 VERIFY-PASS**：messaging/api Started、:8443 綁定、mutual-TLS+admin RBAC 通、web GUI（Metrics Dashboard）可登入。`command-v2.2.1`。 |
| P2-02 | `services/tak_service.py`：CoT XML 解析（規格相容，禁止自創欄位）；TAK Server 推播訂閱（TCP/SSL 8089 或 federation port 9000） |
| P2-03 | `routers/tak.py`：從 stub 升級為真實 endpoint（接 TAK Server federation push + REST 查詢）；schema 已存在於 Stage 1 帶過來的 stub |
| P2-04 | `cop_service.normalize_cot(event)` — CoT → COP entity 映射（type / uid / time / stale / lat / lon → COP `entity` + `track`） |
| P2-05 | 前端 `static/js/map.js` 加 MIL-STD-2525 符號渲染（用 [milsymbol](https://github.com/spatialillusions/milsymbol) JS lib，MIT，非中國維護）。**MGRS grid 一併於本項實作**（zoom-adaptive 密度、淡灰底 + 強調 100km / 10km 分層、label 避讓）——P1-10 已預埋 MapLibre Symbol Layer 接點。**affiliation-aware 渲染模型**（敵我=2525 框 / 類型=NAPSG 象形 / severity=halo；情境表 A–E 由 cot_type 前綴分流；建立流程 type-first）依 [`classification-crosswalk.md`](../command-dashboard/docs/design/classification-crosswalk.md) §6。**＋ route/polygon（線/面）符號對齊 MIL-STD-2525 Tactical Graphics（control measures：route≈axis-of-advance、polygon≈area-control）+ 走 design token**——現況為 app 自訂寫死 hex（`POLY_TYPES`/`ROUTE_TYPES`），非標準、違 POLICY hex doctrine，正規化留本項，見 [`classification-crosswalk.md`](../command-dashboard/docs/design/classification-crosswalk.md) §8 |
| P2-06 | 時間軸支援：CoT `stale` 處理 + COP 快照寫入 `snapshot_repo`（Wave 6 時間軸回放預埋） |
| P2-07 | Federation 設定：與外部 TAK 節點交換 CoT（可選；先單機 PoC） |
| P2-08 | 測試：CoT parse unit + TAK Server ↔ command-dashboard integration（mock TAK 推播）+ security（CoT injection / XML XXE 防護） |
| P2-09 | 規格書補 TAK 整合章節 |

### Definition of Done

- [ ] ATAK / iTAK 客戶端可推 CoT 事件，5 秒內顯示在 commander_dashboard 地圖
- [ ] CoT `stale` 過期自動從 COP 移除
- [ ] XXE 防護測試通過（`defusedxml` 或等效）
- [ ] Federation 雙向流測試（兩台 TAK Server 互推）
- [ ] **Tag**：`command-v2.3.0`（TAK 整合 MINOR；原規劃 1.1.0，rebase 至 2.x）

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
- [ ] **Tag**：`command-v2.4.0`（WaveInk 整合 MINOR；原規劃 1.2.0，rebase 至 2.x）

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
