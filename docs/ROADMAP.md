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
| ✅ P1-10 | **地圖 UX baseline 升級**：見下方〈P1-10 細項展開〉。改採 **MapLibre GL JS + PMTiles + dark ops theme + SVG marker + 等寬字體**，全替換 Leaflet（不走 `leaflet-maplibre-gl` 折衷路線，避免 P2 二次手術），預埋 P2 TAK + MIL-STD-2525 渲染接點。<br>**[2026-06-06 reality check 補帳]**：子項 P1-10a/b/c/d/e/g/h 全 ✅（10f clustering descoped；10a-2 DS token migration 為**獨立延後 backlog**，非本項 blocker），對應 commit 全在歷史（`1d46025`/`d3e9386`~`418a65a`/`9ccf190`~`fe84e6e`/`d8c13ab`/`79561a7`/`f1a3f3f`）。**code 驗證**：Leaflet 已拆淨（`static/lib/` 無 leaflet.js、map.js 無 `L.*` 呼叫，殘留僅 `_leafletMap`/`refreshLeafletMarkers` 歷史變數名）、MapLibre+PMTiles 2 套 style（dark/muted-day）+ EntityLayer/SDF baking/setFeatureState/text-halo/symbol-sort-key/LOD 全落地、CSP enforce 預設開。**DoD 6/7 達成**（FOUC 由使用者親驗，見 p1-map-ux.md）；唯 **Lighthouse@Pi500 為 standing residual**（無 Pi 實機）。子項各走獨立 PR、未對單一 GitHub issue（故 status 腳本顯示 no-issue，非缺漏）。 |
| ✅ P1-11 | **Commander Dashboard UI 移除 PWA-specific 元素**：見下方〈P1-11 範圍〉。配合 P1-04 backend federation infra 保留決策，UI 層拿掉「收容/醫療」固定二元呈現，**改為 Option B 整刪**（dogfood 中決議；P2 TAK / P3 WaveInk 接入再重蓋）。源於 P1-01 dogfood 跑 dashboard 時的截圖盤點 — 完成於 [#4](https://github.com/winson3QQ/ICS_COMMAND/pull/4) `23dee14`（2026-05-26）|
| P1-12 | **統一 key management + At-rest 加密 + Backup GUI**：FIDO2-derived 主密鑰 + HKDF 衍生子鑰，統一 backup encryption + live DB at-rest encryption（SQLCipher）+ 未來簽章用途。詳見下方〈P1-12 範圍〉。<br>**[2026-06-08 同機部署修正 — threat_model.md §8.4]**：TAK Server 與 ICS 同機 → TAK PostgreSQL 同碟明文使「只 SQLCipher 加密 ICS DB」不一致 → **LUKS 整碟為主控必備、SQLCipher 退內層**；**P1-12a key 階層須加 `disk-v1` child = LUKS unlock key**（一次 FIDO2 unlock 開機碟+app+backup，勿碎裂託管）。動工前先訂部署 at-rest 策略。 |
| ✅ P1-13 | **`map_config.json` seed/runtime 分離（user-data boundary 建立，P1-12 prep）**：現況 `command-dashboard/static/map_config.json` 被 git tracked 又被 server runtime 寫入，造成 (a) 工作樹永遠 dirty、(b) 切 branch 洗掉 user zone/route、(c) snapshot script 把 user data 當 code commit、(d) 上線後場域 / 演習資料活在版控（違反 user data 邊界）。改為 `static/map_config.seed.json`（tracked，factory default）+ `data/map_config.json`（gitignored，runtime），讀寫對稱走 `GET/POST /api/map_config`，startup hook + migration script 兜底既有部署。CLAUDE.md 加「User Data 邊界」紅線段。順帶擴張 P1-12b scope（三層 backup 觸發 + Restore）並新增 P1-14（Exercise data scoping）+ P1-12b+14 bundling 決策。源於 issue #24 dogfood 副發現 — 完成於 [#28](https://github.com/winson3QQ/ICS_COMMAND/pull/28) `f4775d9`（2026-05-29，含 post-review code-review 2 HIGH + 1 MEDIUM + 2 LOW finding fixes）|
| ✅ P1-14 | **Exercise data scoping — events / map_config 加 exercise 綁定**（2026-05-28 dogfood 衍生）：當前 events / map_config / exercises 三者孤立沒外鍵關聯，導致「fresh deploy 後 map 復原成 seed 但 events 表還留上場演習的 8 筆」這種資訊孤兒狀態（dogfood 親見）。修法：(a) events 表加 `exercise_id` 外鍵 + `created_at` < exercise.started_at 的 backfill 策略；(b) map_config.json 的 `evt_*` zone 在 archive 時跟著 exercise 一起歸檔（搬到 backup tarball，不留在 live data/）；(c) GET endpoints 預設只回**當前 active exercise** 的 events / zones（admin override 可看歷史）；(d) UI 加「演習名稱」chip 在 header 強提示 user「現在在看哪場」。需先有 P1-12b 才能驗 reset 流程；建議 P1-12b 完成後接著做。**[2026-06-03 reality check — 上方 (a)-(d) 描述已過時，以本段為準]**：盤 code 後發現**資料模型 + repo + active 機制 + reset + AAR 報告幾乎全已建（ICS_DMAS C0 繼承）**，**不是「三者孤立沒 FK」**：① `exercises` 表有 `status`(setup/active/archived) + `mutex_locked`（單一 active）；② `events`/`cop_entities`(+索引)/audit/decisions… **皆已有 `exercise_id` FK**；③ `exercise_repo` 有 CRUD + `update_exercise_status`(mutex) + **`get_active_exercise()`**；④ `event_repo`/`cop_entity_repo`/`cop.py` create+list **已參數化 `exercise_id` 過濾**；⑤ admin **`/reset-exercise`** 已存在（清演習表 + `cop_entities WHERE exercise_id`）；⑥ **AAR 報告已存在**（`aar_entries` 表 + ai `/report/{exercise_id}` post-exercise report + ML export）。**真正缺口 = scoping「休眠」**：`get_active_exercise()` 幾乎沒被呼叫、**前端 dashboard 完全無演習 UI**（無 active 選擇器/header chip/`/api/exercise` 呼叫）→ 建立/查 events/cop 都不帶 exercise_id → **全部 `exercise_id=NULL` 未 scoped**（孤兒 bug 根因）。**故 P1-14 實為 wiring + 前端 UI**（非建模型）：(1) 前端演習管理 UI（建立/啟動/歸檔）+ active 選擇器 + header「演習名稱」chip；(2) create 預設 `exercise_id=get_active_exercise()`；(3) GET 預設過濾 active（admin override）；(4) backfill NULL。**無硬卡 P1-12**（reset endpoint 已在；只有 reset-to-factory *完整驗證* 鬆散想要 P1-12b backup）→ **現在就能做、且直接解鎖 P1-16 綁定**（cop create 帶 active id 即綁）。**AAR 修正**：報告級 AAR 已備；只剩**視覺逐格時間軸 scrub** = Wave 6（`snapshot_repo` 也已在）。**Out of scope 排除**：跨演習 cross-reference / 演習 fork / 多演習並行（Wave 6+）。<br>**[2026-06-03 v1 完成 — [#91](https://github.com/winson3QQ/ICS_COMMAND/pull/91) `34b61cd`（issue #89）]**：① 後端 `resolve_scope(session, requested)` 安全閘 + `NULL_SCOPE` 三態過濾（int 精確／NULL 實戰池／None 不過濾）套 6 repo（events/cop/decisions/manual/dashboard/audit）；**strict isolation**（無 active→實戰 NULL 池非「看全部」，歷史需 `COMMAND_ROLES` override）；create 由 server 蓋 active `exercise_id`（不信任 client），cop create 綁 active（= P1-16 接點）。② 演習硬刪（級聯白名單表·sysadmin-only·非 active·同連線 status 再確認防 TOCTOU）。③ **跨 session 即時**：`cop_hub.broadcast_all` 於 activate/archive/delete 廣播 `exercise_switched` 給**所有**連線（不過濾 scope）→ 各 client 重對帳 map/面板/chip（cop_stream → DOM `exercise:switched` → main.js refresh）。④ 前端演習管理面板 + active chip（文字「演習／」「實戰／」「無進行中場次」），退役 ttx-toggle/session_type。⑤ Review LOW 收尾：`GET /api/cop/entities/{uid}` by-uid scope gate（非指揮層跨場讀→404）、`reset-exercise` 補清 `exercise_kpis`。測試：test_exercise_scoping（scoping/strict/role-gate/delete/by-uid）+ test_cop_ws（operator 收廣播）+ test_realtime_hub + 前端 cop_stream/exercises 全綠（216 vitest + pytest）；`/code-review`＋`/security-review` 無 HIGH/MED 新破口。**解鎖 P1-16 綁定**。<br>**[follow-up [#93](https://github.com/winson3QQ/ICS_COMMAND/issues/93) 完成 — [#96](https://github.com/winson3QQ/ICS_COMMAND/pull/96) `156eb65`]**：稽核日誌綁 active session（**Model B**）—— `audit()` 未明傳 exercise_id → 自動戳當前 active session（演習/實戰），**全部 audit（含 decision/ttx/pi-sync/系統層）**綁該場，AAR 可查/回放完整時間軸；`exercise_*` lifecycle audit 例外保 NULL（cascade durability，review #93-1）。另補 **COP 操作 audit**（create/update/delete，原本完全沒記；update 全 kind 含 event → 捕捉 QRF/事件移動軌跡；best-effort 不擋即時同步）。顯示/TAK 不受影響（audit 正交）。2026-06-04 P1-16 follow-up dogfood 衍生。<br>**[follow-up #93 收尾完成 — [#97](https://github.com/winson3QQ/ICS_COMMAND/pull/97) `f9ef5af`]**：① 稽核日誌 UX 具體化（`cop_entity_*` 依 `detail.kind` 顯「新增/移動/刪除＋節點/路線/範圍/設施/圖釘」取代泛稱「標繪」、target 顯 callsign、`_escAudit` 跳脫、session 事件納入「帳號」篩選）；② 後端 abandoned session（切帳號/關頁→token 不再被用）逾時登出補 `SESSION_EXPIRED` audit、idle cutoff `14h→min(IDLE,SESSION)=15分`（與 `check_session` 同閾值）、`main.py` lifespan 加每 5 分週期 session 清理；③ 補 DoD 缺測 2（cleanup audit 留痕 + idle cutoff 回歸守門）；④ code-review/security-review 修正（shutdown `_cleanup_task` cancel 後 await 回收、`DELETE ... RETURNING` 去並發重複 audit、audit 失敗 `log.warning`、`_escAudit` 一致化 operator/fallback），**無 HIGH 以上破口**。pytest 607 passed/1 skipped。UI 最終驗（§8 Step 4）待人工瀏覽器驗（presentational）。 |
| ✅ **P1-15** | **多人即時共享 COP（[issue #29](https://github.com/winson3QQ/ICS_COMMAND/issues/29)）**：`cop_entities` 作為單一正規化 COP 儲存兼**同步邊界**——per-entity `version_clock` 樂觀鎖 + WebSocket 廣播 + 前端 per-uid LWW merge，解「畫了/釘了別人不 reload 即時看到、拖移/結案即時同步」痛點。**即時管線（done）**：PR-A 樂觀鎖 + soft-delete [#33](https://github.com/winson3QQ/ICS_COMMAND/pull/33) `e2a73c4`；PR-B `/api/cop/*` REST + If-Match [#35](https://github.com/winson3QQ/ICS_COMMAND/pull/35) `c2b4d3d`；PR-D WebSocket hub（`--workers 1` pin）[#36](https://github.com/winson3QQ/ICS_COMMAND/pull/36) `415b80c`；PR-E 前端 `cop_stream.js` [#37](https://github.com/winson3QQ/ICS_COMMAND/pull/37) `8ca2d34`。（PR-C normalize_manual 跳過，被 PR-B POST 涵蓋。）**cutover（map 物件從 map_config blob → cop_entities，done）**：PR-G1a route+polygon [#38](https://github.com/winson3QQ/ICS_COMMAND/pull/38) `022fb24`；PR-G1b event 位置圖釘 [#39](https://github.com/winson3QQ/ICS_COMMAND/pull/39) `86e389c`（事件記錄仍在 events 表、流程不變；reset 一併清 cop + 廣播 resync）。分類用 `attributes.kind`（route/polygon/event）+ CoT 相容 `type`（為 P2-04 TAK 預留）；不做 migration、從 0 開始；節點（icon='pin'）固定不 cutover。**收尾（done）**：**PR-H** [#41](https://github.com/winson3QQ/ICS_COMMAND/pull/41) —— 退役 PR-E ＋標記 MVP（cop_stream 改純資料層、移除自建 marker / ◉COP / ＋標記）+ **移除流向（flow）功能**（與 route 重疊、cutover 後連不到事件；route 才是有向移動路徑的正解）+ 規格書/ROADMAP 收尾 + close #29。**G2 descoped**：flow 移除（非 cutover）；infra 與節點真正缺的是「placement UI」（MapLibre 改寫時遺失的空 stub），與即時同步無關 → 另開 [issue #40](https://github.com/winson3QQ/ICS_COMMAND/issues/40)（補建立 UI 時順便做成 cop_entity + 綁 exercise）。**TAK 對齊**：cutover 時 type/source/origin_node_id 已對齊 CoT，雙向 TAK 實作留 P2-04 |
| ✅ P1-16 | **節點 / 設施 placement UI（[issue #40](https://github.com/winson3QQ/ICS_COMMAND/issues/40)）**：P1-10b（Leaflet→MapLibre 改寫）把節點/設施放置 UI 變成空 stub（`map.js` 的 `_openInfraForm` / `_startInfraPlace` / `_saveInfraPosition` / `_cancelNodePlace` 皆空）——**功能 regression**。每場演習地點不同，5 常駐編組（收容/醫療/指揮/前進/安全）+ 臨時設施（醫院/收容所/急救站/警局/消防）位置需逐場設定；現況節點只能 `map_config.seed.json` 寫死座標（換地點全錯）、設施完全無法新增。修法：(a) 工具列「放置節點 / 放置設施」→ 點地圖放置 → 拖移 / 刪除；(b) 落地為 **cop_entity**（`attributes.kind='zone'`（節點）/ `'infra'`（設施））→ 沿用 P1-15（#29）即時同步管線（cop_stream / EntityLayer / `copEntityTo*` adapter）+ 綁定當前 active exercise；(c) 設施類型沿用既有 `INFRA_TYPES`（醫院/收容所/警局/消防/公用 + 圖示/字母）。**依賴**：P1-15（#29 即時管線，done）；**對接**：P1-14（exercise data scoping — 放置物件綁 exercise）。源於 P1-15 PR-H G2 descope。<br>**[2026-06-03 reality check + A-Full 定案]**：空 stub regression 已確認（`_openInfraForm`/`_startInfraPlace`/`_saveInfraPosition`/`_cancelNodePlace` 皆 `{}`，刪除可用、放置 no-op；節點+設施現皆活在 map_config blob，非 cop_entity）。**決定走 A-Full（exercise 綁定 + 可回放做 AAR；不綁無法複盤）**。釐清出**三層資料模型**：① **永久設施**（真醫院/消防/警局/診所）= 公開資料底圖層、**不隨演習** → 另立 **P1-17**；② **5 常駐節點**（指揮/收容/醫療/前進/安全）**現為 `map_config.seed.json` 寫死座標（錯——每場地點不同）→ 需 cutover 成 exercise-scoped cop_entity**（**修正 P1-15「節點 icon='pin' 固定不 cutover」決定**）；③ **臨時節點/物件** = exercise-scoped cop_entity。②③ 落 `cop_entity + attributes.kind(zone/infra) + exercise_id`，沿用 P1-15 即時管線；因帶 `version_clock`（P1-15 已有）**為 Wave 6 AAR 逐格回放鋪資料**（snapshot_repo = P2-06 預埋、回放 UI = Wave 6）。**硬依賴：P1-14**（active-exercise 綁定機制）→ **P1-16 A-Full 不可早於 P1-14**。建議順序：P1-14（FK/scoping）→ P1-16 A-Full（P1-17 永久設施層可並行/後做）。<br>**[2026-06-03 v1 完成 — [#92](https://github.com/winson3QQ/ICS_COMMAND/pull/92) `59c7c3d`（issue #40）]**：① **節點(kind='zone')** on-demand：工具列選 5 編組 → 點地圖放 → cop_entity（P1-14 自動綁 active/實戰）+ 拖移 + 刪除；adapter `copEntityToZone` + `_allRenderedZones` cutover 改讀 cop entities；收容=避難所屋頂象形、醫療=白十字、其餘 abbr。② **設施(kind='infra')** on-demand：5 類（醫院/收容所/警局/消防/公用）放+刪（拖移 follow-up）；`copEntityToInfra` + `_renderInfra` cutover + `_scheduleCopRender` 即時重繪。③ **cutover**：移除 `map_config.seed.json` 寫死 5 zones（出廠開機無預設節點、全靠 on-demand；改正「每場地點不同」regression）。④ **RBAC**：前端 `canUseRealModeControls` gate UI + **後端** cop create/delete 對 kind∈{zone,infra} 加 `COMMAND_ROLES`（operator/observer 無法繞 API 建/刪；security review HIGH-1 落實）。⑤ 附帶：移除 vestigial「指揮部設定」+ 區段改名「站內地圖」、歸檔鈕只對 active、稽核 exercise_* 中文短標、放置 banner 重複 id。測試：copEntityToZone/Infra vitest + `TestNodeInfraRBAC`（後端 4 測），223 vitest + pytest 全綠；`/code-review`＋`/security-review` 通過（HIGH-1/MED-1/LOW 全修）。**follow-up（2026-06-03 記 → 2026-06-04 全收）**：✅ ① **設施拖移**（`_syncEventDragHandles` 納 infra + drag callback 按 kind 分支 click→`_onInfraClick`／per-frame→`_renderInfra`；dragend 泛型）；✅ ② **放置流程參數化**（`_nodePlaceState`+`_infraPlaceState` 合一 `_placeState`，共用 `_startPlace`/`_cancelPlace`/`_placeAt`，export 留 thin wrapper、main.js 不動；消 ~50 行 + 根除互斥隱患）—— ①② 完成於 [#94](https://github.com/winson3QQ/ICS_COMMAND/pull/94) `93d3d65`（in-browser 驗過）。✅ 死碼（`btn-node-place` toggle、map.js `showZoneDetail` 死刪除塊）已清 `7ed5e45`。**P1-16 follow-up 全清。**|
| ✅ P1-17 | **永久設施公開資料底圖層（[issue #88](https://github.com/winson3QQ/ICS_COMMAND/issues/88)，2026-06-03 reality check #1 衍生）**：真實醫院 / 消防 / 警局 / 診所等**永久公共設施**為**基準參考層、不隨演習走**——與 P1-16 的 exercise-scoped 節點/臨時物件分離（三層資料模型見 P1-16）。**動機**：這些是現實常駐基礎設施、跨演習不變；不該被 P1-16 的放置 UI 逐場手放、也不該被演習 reset 清掉。**資料來源**：台灣**政府開放資料**（醫療院所 / 消防分隊 / 警察機關位置等 dataset；NCDR/NFA 圖資生態）——**每個來源須逐一確認非中國供應鏈**（CLAUDE.md），離線打包匯入。**落地**：匯入為**唯讀基準圖層**（非 cop_entity、非 map_config user-data、不受 exercise scoping / reset 影響），工具列可切換顯示。**與 P1-16 分工**：P1-16 = 隨演習走的節點/臨時物件（放/搬/刪/綁 exercise）；P1-17 = 永久設施基準（匯入、唯讀、常駐）。**順序**：可獨立於 P1-16，建議 P1-16 後或並行；主要工作量在 open-data 匯入 pipeline + 供應鏈逐源確認。<br>**[2026-06-03 v1 完成 — [#90](https://github.com/winson3QQ/ICS_COMMAND/pull/90) `9614f7f`（醫療 → v1.1）]**：① **3 源上線**——避難收容處所(NCDR/內政部 #73242)/消防(消防署 #5969)/警察(警政署 #5958)，共 **8318 筆**；維護者腳本 `command-dashboard/scripts/import_facilities.py`（curl 下載→正規化→`static/facilities.seed.json`）。② **供應鏈全過**：來源皆台灣政府（非中國）、政府開放授權第1版（CC BY 4.0 相容）；`pyproj`（警政 TWD97 TM2→WGS84，MIT/NOAA/OSGeo 非中國）**僅維護者匯入用、不進 runtime requirements**。③ **PII 剝除**（管理人姓名/電話不入 seed）。④ **資料品質普查**：每筆「縣市↔座標」bbox 一致性檢查，抓掉 78 筆來源座標錯誤（海上點/placeholder/跨縣市），全 log 留痕。⑤ **前端**：自管 `facilities` clustered source（MapLibre `cluster:true`＝P1-10f 技術，**scope 限 facilities**）＋彩色圓＋110(警)/119(消)數字徽＋⌂避難所象形＋hover 名稱 tooltip；獨立層不碰 zones/infra/polygons/routes，不依賴 EntityLayer。⑥ **後端**：唯讀 `GET /api/facilities` + `facilities_store`（只讀 static seed）。⑦ **決策**：seed **tracked**（固定常駐、factory-default baseline，符 User Data 邊界；非 user-data）；普查用 **county-bbox**（零依賴、有 log，未升 point-in-polygon）。⑧ **命名**：程式名 `facilities`＝UI「**公共設施**」，**≠** 既有 `infra`（UI「設施」，P1-16 演習臨時物件）。⑨ **醫療（醫院＋診所）→ v1.1**：bulk 名單皆「有地址無座標」、需 TGOS geocode key，故延後。測試：Python 13 + JS（facilities 10）全綠；`/code-review`（7 修正）+ `/security-review`（0 finding）通過。<br>**[2026-06-03 close]**：v1 baseline（3 源 8318 筆）交付，本項標 ✅ 結案（[#88](https://github.com/winson3QQ/ICS_COMMAND/issues/88) 已關）。**唯一延後 = v1.1 醫療**（醫院+診所，卡 TGOS geocode key 外部依賴）；屬增強非阻塞，續做時另開 issue 追蹤 |

| ✅ P1-18 | **Dashboard 響應式 layout + 觸控支援（[issue #165](https://github.com/winson3QQ/ICS_COMMAND/issues/165)）**：主操作儀表板在 iPhone/iPad 無法正常使用——layout 固定不縮放、觸控目標過小、地圖長按無 touch 支援。**三層修正**：① **CSS 響應式**：`@media (pointer:coarse)` 觸控目標 ≥44px（`.map-btn`/`.map-tab`/`csel-btn`/MGRS）；`@media (max-width:900px)` Zone-A 壓縮、右側 panel 改 **overlay 模式**（`position:absolute + transform:translateX(100%)`，不壓縮地圖寬度）；`@media (max-width:600px)` 全寬。② **行動面板切換按鈕**：`#panel-toggle-btn`（44×44px 圓形，桌機 `display:none`，≤900px 才顯示；`data-action="toggleMobilePanel"` dispatch 切換 `.mobile-open`；點 panel 外自動收合，guard 用 `window.matchMedia` 非 `getComputedStyle`）。③ **MapLibre 觸控長按**：`touchstart/touchmove/touchend/touchcancel` + 10px slop + 650ms timer；`_touchActive` 旗標延遲 400ms 清除，攔截瀏覽器補發的 synthetic mousedown（防 lpTimer 雙路徑干擾）；全 `{ passive:true }` 不干擾 MapLibre pan/pinch。**⚠️ `settings-panel` hidden offset bug fix**：`min(-340px,-90vw)` → `max(-340px,-90vw)`（min() 回傳最負值，隱藏時偏移過遠）。**WCAG 2.1 AA SC 2.5.5 觸控目標 ≥44px**（P2-20 iPad AAR 頁面基礎）。`cmd-v1.1.0`。<br>**[2026-06-07 完成 — [#166](https://github.com/winson3QQ/ICS_COMMAND/pull/166) `c20da6e`+`7ce0420`+`17706c2`（issue #165）]**：feat + `fix(deps): aiohttp` + code-review 5 項修正；vitest 247 passed（8 pre-existing collection error 與本 PR 無關）。**Human verify 待做**：使用者需在實機 iPhone/iPad 以 Chrome DevTools 或直連驗觸控目標、overlay panel 開收、地圖長按事件（§8 驗收腳本見 PR #166 body）。 |

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

> **設計細節**：P1-10 子項（a-h）完成紀錄、視覺等級 ladder（T0→T3）、為 P2 鋪路成果、P1-11 Tier 1-3 分解 → [`docs/roadmap/p1-map-ux.md`](roadmap/p1-map-ux.md)
> **Key management 規格**：P1-12 FIDO2/HKDF 架構、三子項 12a/b/c、Bundling 決策 → [`docs/roadmap/p1-key-management.md`](roadmap/p1-key-management.md)


---

## Phase 2 — TAK Server 整合（COP 第一個外部來源）

**目標**：部署官方 TAK Server，完整接通 TAK 雙向介面——上行（CoT 位置/事件 + GeoChat + MEDEVAC + shape 幾何）流入 COP，下行（Mission API 指令下達）推送現場 ATAK；充分利用 Marti REST API（在線人員、Mission、DataSync、EXCHECK、影像串流）；地圖渲染採 MIL-STD-2525 符號（含 planned/actual 指令圖層）。

> **開發策略（主軸 + 降階）**：TAK 整合怎麼沿 TAK 軸開發、能力↔API↔ICS 對應矩陣、**TAK Server 掛掉時的三階降階模型**、連線/信心兩軸狀態表示（來源 chips）→ [`docs/roadmap/tak-integration-strategy.md`](roadmap/tak-integration-strategy.md)。情境面「為何要這功能」見 [`docs/reference/tak-use-cases-config.md`](reference/tak-use-cases-config.md)（19 情境深挖，按 OODA 循環組織）。

> **[2026-06-09 漸進重構]** 本 Phase 自此依上述兩份（19 情境 use-cases + TAK 整合策略）**漸進重構**——注入既有 ROADMAP 缺的維度，**不拆 P2-01~25 層結構（歷史留痕）**：① 操作面 grounding ＝上方連結；② 情境檢討衍生候選 → 見〈[情境檢討衍生候選（2026-06-09）](#情境檢討衍生候選2026-06-09)〉節；③ 降階紀律 → 入 Definition of Done；④ 活躍 code（#173/#176/#177）→ P2-14 / P2-26 連結。**檢討花的力氣一律留痕，不蒸發**（即使 later/不做也記）。

> **[Reality check 2026-06-04 — [#98](https://github.com/winson3QQ/ICS_COMMAND/issues/98)]**：盤 main `0db382f` 實際 code。**TAK 上行鏈路（P2-01 部署 / P2-02 `tak_service.py` CoT XML 解析 + 8089/9000 訂閱 / P2-08 XXE 測試）全 greenfield、零實作、XML lib 未選**；但 **P2-04 的下游落地層已被 P1 完整建好且刻意對齊 CoT**——`CoPEntity` v1 凍結（CoT 欄位對齊 + `source="tak"` 預留，P1-03/#15）、`cop_entity_repo`（version_clock CAS + stale 過濾 + TAK 風 soft-delete，P1-15/#29）；`routers/tak.py` schema + RBAC 已掛但 handler 是 stub、`cop_service.normalize_cot` 是 `NotImplementedError`。**關鍵路徑＝ P2-01+P2-02（重活、無 C0 繼承）→ P2-03 wire + P2-04 normalize（輕，下游全建）**。P2-05 可並行（設計 crosswalk §6/§8 已 LOCKED、管線預建；milsymbol 未 vendored / 2525 框零 / `POLY-ROUTE_TYPES` 仍寫死 hex；**疊加非全換**；MGRS #56 完全獨立可先做）。**動工前 3 個 drift（詳 #98）**：① **P2-06 指向錯層** — `snapshot_repo` 是 unit-KPI 聚合非 per-entity COP 時間軸，回放路徑（`cop_entity_tracks`／新表）須先重新確認；② **step-ca 憑證現 24h 且 federation 缺 peer cert** — 90 天 patch 與 client/peer cert profile + TAK Java keystore 信任 SOP 須提早於 C3-B；③ **CSP wasm** — milsymbol 若走 wasm fallback 需補 `wasm-unsafe-eval`（P1-10h 刻意未預放）。供應鏈現況乾淨；三相依 license 已圈定，lock 前逐一驗來源。

### Scope

| Item | 說明 |
|---|---|
| ✅ P2-01 | 部署官方 TAK Server（Docker compose，Java/Spring + Postgres）；產出 `deploy/tak-server/` 部署文件與 cert 設定 SOP。**[完成 — [#100](https://github.com/winson3QQ/ICS_COMMAND/pull/100) `35ed3d8`（issue #99）]**：`deploy/tak-server/`（compose + `.env.example` + `pki/issue-tak-certs.sh` step-ca→JKS + README）；TAK Server 5.7（官方 Docker 包不含 compose/.env，本目錄補）；M1 原生 arm64 build。**實機 boot dogfood（M1/16GB）**：DB tier 通（initdb+SchemaManager+cot/martiuser healthy）、Ignite 叢集 ACTIVE、**CoT streaming `:8089` 開**（P2-02 接點解鎖）；過程抓並修 3 整合 bug（volume chown / CoreConfig 密碼 / TAKIgniteConfig race）。web/API `:8443` 初次未起（api JVM 活但 Tomcat 不綁）→ **[#101](https://github.com/winson3QQ/ICS_COMMAND/issues/101) 已解（2026-06-05）**：兩根因皆憑證問題（非 Ignite 脆弱性）——① server 憑證須 RSA 非 EC（jwkSource bean 寫死 RSAPublicKey，`14b5373`）；② 缺 `fed-truststore.jks`（messaging 無條件部署 distributed-federation-manager Ignite service，缺檔→SSLContext 失敗→server node 掛→client 全斷線，PR #104 `fa38200`）。**實機 VERIFY-PASS**：messaging/api Started、:8443 綁定、mutual-TLS+admin RBAC 通、web GUI（Metrics Dashboard）可登入。`command-v2.2.1`。 |
| ✅ P2-02 | `services/tak_service.py`：CoT XML 解析（規格相容，禁止自創欄位）；TAK Server 推播訂閱（TCP/SSL 8089 或 federation port 9000）。**[Wave 0+1 — [#103](https://github.com/winson3QQ/ICS_COMMAND/pull/103) `d5deaad`（#102）]**：`parse_cot_xml()` XXE-safe（defusedxml forbid_dtd/entities/external）+ `CoTEventIn` 移 `schemas/tak.py` 補 CoT 必填 start/how/version（修 ↔CoPEntity contract gap）+ 時間戳 normalize 秒精度 Z（對齊 stale 字典序）+ detail 抽 callsign/remarks/結構化 dict。**[Wave 2 — [#109](https://github.com/winson3QQ/ICS_COMMAND/pull/109) `e7358fb`（#106）]**：`subscribe()` pytak[with-takproto] mTLS 連 :8089、`readuntil(</event>)` 分幀、濾 `t-x-takp-v` TakControl、斷線退避重連（只在收資料後重置）、只呼叫 `ingest_cot_event`（#105，不碰 cop_service.py）。reality-check 實測定案：8089 預設 **v0 明文 CoT XML**（v1 protobuf 選配）、強制 **fullchain client cert**（leaf+intermediate，非 user enrollment）。TLS **fail-closed**（無 cafile 須顯式 opt-in）。25 測試。/code-review（W1 2 + W2 2 修正）、/security-review（W1 0 + W2 1 TLS fail-closed）。**活 server 端到端閉環實測 PASS**：推 CoT → subscribe → 真 ingest 落 `cop_entities`。<br>**剩 follow-up（非 P2-02 scope）**：① **lifespan wiring** — 把 `subscribe()` 在 app 啟動跑成背景 task → **P2-03**（routers/tak.py wire + app 整合）；② client cert 正式簽發＝擴充 `issue-tak-certs.sh` 出 client fullchain（現只簽 server/fed-truststore）。 |
| ✅ P2-03 | `routers/tak.py`：從 stub 升級為真實 endpoint（接 TAK Server federation push + REST 查詢）；schema 已存在於 Stage 1 帶過來的 stub。**[完成 — [#112](https://github.com/winson3QQ/ICS_COMMAND/pull/112) `01ab9b6`（issue #107）]**：main.py lifespan 依 `TAK_ENABLED` 啟動 `subscribe()` 背景 task（啟動失敗永不擋 app 開機）+ `core/config.py` TAK_* env + `routers/tak.py` async ingest endpoint（RBAC/CAS/skip）+ `issue-tak-certs.sh` Section 5 離線簽 fullchain client cert。11 測試、/code-review 1 修正、/security-review 0 finding。**活 app 端到端 + 視覺驗收 PASS**：起 app(TAK_ENABLED) → 推 4 種 affiliation CoT 到新竹 → P2-05 milsymbol 渲染成 2525 符號（友軍藍矩形/敵軍紅菱/中立綠方/不明黃四葉）即時上圖。<br>**＋ P2-02 W2(#106) 移交的 production 整合 follow-up**（subscribe() 已實作+活 server 端到端實測過，但「在 app 真正跑起來」屬本項）：① **lifespan wiring** — main.py FastAPI lifespan 把 `tak_service.subscribe()` launch 成背景 task（startup）+ shutdown cancel；沒這步 CoT 不會在 production 流入。② **client cert 正式簽發** — 擴充 `deploy/tak-server/pki/issue-tak-certs.sh` 出 COP subscriber 的 **fullchain** client cert（leaf+intermediate；現只簽 server/truststore/fed-truststore）。③ **config plumbing** — `core/config.py` 加 env-driven `cot_url` + cert 路徑餵 `build_subscribe_config`。憑證區分見 memory `p2-tak-deploy-issue101`（:8089 streaming 只需 fullchain CA-trusted cert，**不需** UserManager enroll；enroll 是 :8443 web UI 才要）。 |
| ✅ P2-04 | `cop_service.normalize_cot(event)` — CoT → COP entity 映射（type / uid / time / stale / lat / lon → COP `entity` + `track`）。**[完成 — [#108](https://github.com/winson3QQ/ICS_COMMAND/pull/108) `28490cf`（issue #105）]**：`normalize_cot(CoTEventIn)->CoPEntity`（純函式，欄位直通 + source='tak' + detail→attributes + track 安全取值）+ **`ingest_cot_event` 共用接縫**（normalize→upsert version_clock CAS→cop_hub 廣播；out-of-order/重送守門；create 綁 active 場）由 P2-02 W2(#106)/P2-03(#107) 共同呼叫。13 新測試 + code/security review 過（0 HIGH）。track 逐格時間軸寫入留 P2-06。 |
| ✅ P2-05 | 前端 `static/js/map.js` 加 MIL-STD-2525 符號渲染（用 [milsymbol](https://github.com/spatialillusions/milsymbol) JS lib，MIT，非中國維護）。**MGRS grid 一併於本項實作**（zoom-adaptive 密度、淡灰底 + 強調 100km / 10km 分層、label 避讓）——P1-10 已預埋 MapLibre Symbol Layer 接點。**affiliation-aware 渲染模型**（敵我=2525 框 / 類型=NAPSG 象形 / severity=halo；情境表 A–E 由 cot_type 前綴分流；建立流程 type-first）依 [`classification-crosswalk.md`](../command-dashboard/docs/design/classification-crosswalk.md) §6。**＋ route/polygon（線/面）符號對齊 MIL-STD-2525 Tactical Graphics（control measures：route≈axis-of-advance、polygon≈area-control）+ 走 design token**——現況為 app 自訂寫死 hex（`POLY_TYPES`/`ROUTE_TYPES`），非標準、違 POLICY hex doctrine，正規化留本項，見 [`classification-crosswalk.md`](../command-dashboard/docs/design/classification-crosswalk.md) §8 <br>**[進度 2026-06-05]**：拆三子塊 — ✅ (b) hex→token 調色盤對齊（[#111](https://github.com/winson3QQ/ICS_COMMAND/pull/111) `16fb5b2`）、✅ (a) MGRS 多 zone+GZD+1-2-5 精度（[#113](https://github.com/winson3QQ/ICS_COMMAND/pull/113) `ad14bd3`，closes #56）；✅ (c) milsymbol 2525 affiliation 框（[#115](https://github.com/winson3QQ/ICS_COMMAND/pull/115) `07d59e7`，closes #110）。**P2-05 完成**；`cmd-v1.0.1`。框內 function 細分（兵種圖）/ TAK 單位全鏈視覺（配 P2-03）為後續。 |
| **——— 【地基層 Foundation】 純後端，自動化測試驗收，無 UI ———** | — |
| ✅ P2-06a | **CoT 軌跡寫入接線**（`insert_cop_track` 零 caller bug 修復）：`cop_entity_repo.insert_cop_track()` 定義存在但整個 codebase 零 caller → `cop_entity_tracks` 表一直是空的。修法：`ingest_cot_event` 每次 upsert 後同步寫一筆軌跡點。抽樣策略：per-entity 最短間隔 5s（ATAK 最短 2s push，不節流則爆量）；帶 active `exercise_id`（無演習 = NULL）。清理：exercise 刪除 cascade；archive 保留。**P2-20 AAR 回放的唯一資料基礎，不做則 AAR 無資料可播**。<br>**[完成 — [#121](https://github.com/winson3QQ/ICS_COMMAND/pull/121) `7dd5d7d`（issue #120）]**：`ingest_cot_event` create/update/reinsert 三點接 `_record_track` + per-uid 5s 抽樣（config `TRACK_MIN_INTERVAL_S` 可覆寫）。**設計 B（取代上文「帶 active exercise_id」）**：tracks **不存** exercise_id，靠 uid JOIN cop_entities 取得（SoT 單一不反正規化）；exercise 刪除靠既有 `uid ON DELETE CASCADE`。<br>**review follow-up（本收尾 PR）**：修 `_within_min_interval` aware−naive 相減 TypeError（REST push time 未正規化 → 軌跡靜默漏寫）、抽 `_helpers.iso_to_dt` 共用（消第 4 份 fromisoformat idiom）、抽樣間隔 configurable。`/code-review`（1 真 bug 已修）+ `/security-review`（0 finding）；測試 9 + ingest 回歸全綠。 |
| ✅ P2-06b | **軌跡查詢 API**：`GET /api/exercises/{id}/tracks?uid=&from=&to=` → 回傳該演習所有 entity 位置時間序列（`{uid, t, lat, lon, hae}`，按 t 升序，分頁）。**RBAC**：`COMMAND_ROLES`（軌跡含人員位置 PII，非 READ_ROLES 可見）。P2-20 回放前端的資料來源。<br>**[完成 — issue #123]**：repo `list_tracks_by_exercise`（設計 B `JOIN cop_entities`，不存 exercise_id）+ router `GET /api/exercises/{id}/tracks`（回**全欄位** `{uid,t,lat,lon,hae,heading_deg,speed_mps}`，t 升序，limit/offset 分頁，`from/to` 經 `iso_utc` 正規化）。**RBAC 零額外工**：自動繼承中央 gate（`role_enum.py:118` `/api/exercises/*` 非 DELETE → COMMAND_ROLES）。涵蓋演習(ttx)+實戰(real)；limit 服務端上限 5000（對齊 RT-L4）。**附 P2-06a NULL gating**：reality check 釐清 NULL=「非演習也非實戰」（非「實戰池」，實戰是 type='real' 有 id）→ `_record_track` 在 `exercise_id=NULL` 時不寫軌跡（entity 照常 upsert），並正名 code 註解。測試：repo 6 + router 6（含 operator/observer 403、404、ttx+real）+ NULL gating，全綠；`/code-review` 修 5 項（ORDER BY `t.id` tiebreak 防分頁漏/重、`iso_utc` date-only 補當天起訖、docstring filesort 正名、抽 `tests/api/conftest.py` 共用 role fixtures、clamp 註解去假對齊）+ 跨場軌跡 misattribution 開 [#125](https://github.com/winson3QQ/ICS_COMMAND/issues/125) follow-up；`/security-review` 0 finding |
| ✅ P2-06c | **CoT `<__group>` / `<status>` 小隊欄位提取**（前端小隊模組基礎）：資料**已進** `attributes`（`_extract_detail` 通用收 + `normalize_cot` 整包），本項為**提取成一等欄位** `team_color` / `role` / `battery`。<br>**[完成 — issue #126，設計 A]**：reality check 定案走 **CoPEntity 欄位 + DB column**（取代原 PR [#119](https://github.com/winson3QQ/ICS_COMMAND/pull/119) 述的 attributes 鍵 B）—— 對齊 `callsign`/`remarks` 提取先例，且 P2-06d `GROUP BY team_color` 走 column + `idx_cop_entities_team_color`（B 的 `json_extract` 無 index）。`_extract_squad` 從 `__group.name`→team_color（`title()` 標準化、**不強限 enum** 免丟 Orange/Teal 等 ATAK 真實色）/`__group.role`/`status.battery`（→int 0-100 防 garbage）；migration `_m015` 加 3 column + index；`_TAK_UPDATE_FIELDS` 納入（battery 隨 update 刷新）；原 `__group`/`status` 仍保留 attributes（CoT 忠實）。測試：normalize 提取/標準化/garbage/真 fixture + ingest update 刷新，回歸全綠。**解鎖 P2-06d** |
| ✅ P2-06d | **小隊聚合 API** `GET /api/cop/squads?exercise_id=`（[#119](https://github.com/winson3QQ/ICS_COMMAND/pull/119)）：按 `team_color` 分組聚合，回 `[{team_color, total, online, offline, avg_battery, centroid_lat, centroid_lon}]`。online/offline 以 `stale` 判定；centroid 後端算；同端點服務 live（active id）+ AAR（歷史 id）。**RBAC**：`READ_ROLES`（態勢摘要非 PII 軌跡）。**依賴**：P2-06c。<br>**[reality check]** 後端聚合 API + `dashboard_service.build_dashboard()` 整合 `tak_squads`（修補 dashboard 對 TAK 盲視 —— 屬實：`build_dashboard` 現無任何 cop_entities/TAK 引用）為本項交付；但原 PR 列的消費方 **UI（右側欄 TAK Presence 彙總列、zone-c 摘要）與 P2-12「UI 面板統一落地」重疊** → UI 落點併入 P2-12 設計，本項只交付後端。<br>**[完成 — [#130](https://github.com/winson3QQ/ICS_COMMAND/pull/130)（issue #128）]**：repo `aggregate_squads`（單句 SQL GROUP BY team_color → total/online/offline/avg_battery/centroid，**未分隊 NULL 組保留**）+ router `GET /api/cop/squads`（resolve_scope 三態安全閘，RBAC 自動 READ_ROLES）+ `build_dashboard` 加 `tak_squads`。total 含 stale → offline 反映「該隊有幾顆但失聯」。測試 16；`/code-review`+`/security-review` 乾淨（SQL 參數化 / IDOR resolve_scope 擋 / RBAC 正確）。背景 agent 併行實作 |
| ✅ P2-07 | **GeoChat 誤路由修正—後端**（reality check 2026-06-05 bug）：CoT type `b-t-f` 存進 `cop_entities`，行為錯誤。修法（純後端）：(1) `tak_service._consume_cot` type prefix 路由（`b-t-f` 不進 `ingest_cot_event`）；(2) 新 `services/chat_service.py` + `chats` 表（`sender_uid` / `callsign` / `message` / `group` / `lat` / `lon` / `time` / `exercise_id`，對齊 ICS-214 Unit Log）；(3) 後端 `html.escape()` 處理 `message`（XSS 防線在後端）。測試：`b-t-f` 不得進 `cop_entities`（負向）；XSS 負向（`<script>` 不得原樣輸出）；exercise scoping 對齊。**通聯記錄面板 UI → P2-12 統一落地**。<br>**[完成 — [#131](https://github.com/winson3QQ/ICS_COMMAND/pull/131)（issue #129）]**：**分流點實放 `ingest_cot_event` 共用接縫**（取代上文「`_consume_cot`」—— reality check 發現 REST push 路徑也漏，共用接縫一處擋 :8089 串流 + REST push 兩條）：`type.startswith("b-t-f")` → `chat_service.ingest_chat` + return。新 `chat_service`/`chat_repo`/`schemas/chat` + `chats` 表（`_m016`）；message 存前 `html.escape`（XSS 後端防線）；exercise_id 綁 active 場；只交付後端。測試 7（不進 cop_entities / 進 chats / escaped / scoping / 真 fixture / 非 b-t-f 不誤擋 / group 不取 groupOwner）；`/code-review`（修 group 誤取 groupOwner 布林旗標）+ `/security-review`（0）。並行於 P2-06d |
| ✅ P2-08 | **CoT `<shape>` 幾何萃取 + `services/geometry_service.py`**（純後端）：ATAK `<shape>` 幾何埋在 `attributes` 被忽略，地圖只渲染 `<point>` 單點。修法：(1) 抽 `services/geometry_service.py`（CoT `<shape>` + DataSync GeoJSON 統一解析入口，P2-14 共用，避免重工）；(2) `CoTEventIn` 加選填 `geometry` 欄位；(3) `normalize_cot` 依幾何類型映射到 `kind='route'`（polyline）/ `kind='polygon'`（polygon），沿用 P1-15 管線。<br>**[完成 — [#133](https://github.com/winson3QQ/ICS_COMMAND/pull/133)（issue #132）]**：新 `geometry_service`（re-parse CoT `<shape>`/`<link>` → GeoJSON，**不污染 `_extract_detail`** 既有一層提取；`geojson_to_vertices` 轉前端格式；P2-14 DataSync 共用入口）+ `CoTEventIn.geometry` + `normalize_cot` shape 分支（kind=route/polygon + vertices，**沿用 P1-16 格式零前端改動**；lat/lon **沿用 CoT `<point>`** 忠實對位）。**circle/ellipse 先不支援** → follow-up [#134](https://github.com/winson3QQ/ICS_COMMAND/issues/134)。`/code-review`+`/security-review`：修 garbage geometry→REST 5xx（`geojson_to_vertices` 防禦不可信座標）+ 移除 centroid 覆寫（對位失準）+ Polygon ≥3 頂點；XXE 已防、無新注入面。測試 17 全綠 |
| ✅ P2-09 | **MEDEVAC 9-line 正規化—後端**（純後端）：CoT MEDEVAC 9-line 欄位（pickup / frequency / casualties…）埋在 `attributes` blob。修法：`normalize_cot` 偵測 → 萃取 9-line 進 `attributes` 結構化子物件；`severity` 設 critical。<br>**[完成 — [#137](https://github.com/winson3QQ/ICS_COMMAND/pull/137)（issue #135），設計 B]**：reality check 校正——9-line 其實**已在 attributes**（`_extract_detail` 通用收 `<_medevac_>` 屬性，同 `__group` 模式，非規格所述「埋在 blob 待解析」），本項實為**提取結構化摘要 + 設 severity**，對齊 P2-06c/P2-08 先例。**設計 B（進 attributes，不開新表）覆蓋 ROADMAP 內 TAK-F 紅隊 `medical_records` 分流建議**：9-line 是聚合後送態勢（傷亡數 by precedence / pickup 位置 / 通訊），**非個別病患 PII**（無姓名病史）→ 進 attributes 隨 COP 廣播、指揮部透明可見，符合單一 COP 不開新表紅線。`_extract_medevac`（precedence→int、casevac→bool、case-insensitive，原始 `_medevac_` 完整保留）+ `<_medevac_>` 偵測設 critical + **severity 單調升級**（ingest update 只升不降）。測試 normalize 7 + ingest 2；`/code-review`（cleanup + #135-3 severity 升級缺口修）+ `/security-review`（0 finding；pre-existing TAK ingest XSS gap → #136）。<br>**待驗證**：真機 ATAK CASEVAC type 字串 + `<_medevac_>` 屬性語意（precedence 數量 vs 布林）→ fixture 依 FreeTAKServer CoT domain 文件建構，待 P2-12 human verify 終驗（#135）。**MEDEVAC incident card UI → P2-12 統一落地** |
| ✅ P2-11 | **TAK Marti REST 抽象層 `services/tak_rest_client.py`**（純後端，**P2-12~P2-19 共用前置**）：指揮部「主動查」TAK Server :8443 Marti REST（vs :8089 被動收串流）。<br>**[完成 — [#139](https://github.com/winson3QQ/ICS_COMMAND/pull/139)（issue #138），⚠️ 規格更正：cert 非 OAuth2]**：原規格述「OAuth2 client credentials（JWT Bearer），非 enrolled cert」**前提錯誤** —— reality check 證實部署是官方 TAK Server 5.7-RELEASE-43（tak.gov），Marti :8443 走 **client cert（mTLS）**；OAuth2 是 OpenTAKServer（第三方）才有（SoT：`deploy/tak-server/README.md` + `issue-tak-certs.sh` 全 step-ca cert；見 memory `tak-server-marti-cert-not-oauth`）。改正：**複用 P2-03 既有 `TAK_CLIENT_CERT/KEY/CAFILE`**，砍掉 JWT 取得/refresh + OAuth2 SOP。交付 `build_marti_ssl_context`（fail-closed，無 CA 不裸奔）+ `TakRestClient`（`get_json`：rate-limit + 指數退避（cap 30s）+ 4xx 不重試 + 204 空 body；`poll` scheduler stop_event 軟停 + best-effort；`asyncio.Lock` 並發保護）+ `build_tak_rest_client` factory；config `TAK_MARTI_URL`；HTTP=aiohttp（pytak 已帶，非中國）。測試 15；`/code-review`（204/並發鎖/退避 cap/isawaitable）+ `/security-review`（0 finding，+ `_join_url` 杜絕 SSRF host override）。<br>**待真機（P2-10/P2-12）**：Marti `clientEndPoints` 端點格式 + :8443 cert 是否需綁 TAK user/role |
| ✅ P2-11b | **CoPEntity schema 擴充**：(1) `source` enum 加 `"command"`（P1-03 解凍）；(2) `planned: bool`（空心/實心 2525 框，P2-13）；(3) `simulated: bool`（合成注入標記，`how="h-g-i-g-o"`，P2-19）。**分階段 B 完成**。<br>**[planned/simulated — [#143](https://github.com/winson3QQ/ICS_COMMAND/pull/143)（issue #140）]**：零風險 `ADD COLUMN`（`_m017` INTEGER 0/1 + repo int↔bool、schema bool default False）。<br>**[source `'command'` — [#144](https://github.com/winson3QQ/ICS_COMMAND/pull/144)（issue #141）]**：SQLite **CHECK 不可 ALTER** → 整表 rebuild。`legacy_alter_table` 實測無效（3.50 仍改寫 child FK）→ 改 **`foreign_keys=OFF` + DROP old + rename new**（避 child FK cascade 刪光 tracks/links），自含 commit/autocommit/BEGIN-COMMIT dance 繞 transaction 內 PRAGMA 無效，**動態重建所有原 index**（讀 sqlite_master，含 m015 team_color）。`_m018` up/down 共用 `_rebuild`、idempotent。`/code-review` 抓真 bug（#1 team_color index 漏建 → 動態重建；#4 ROLLBACK 遮蔽 → began 旗標）+ `/security-review` 0 finding。rebuild 安全網測試（資料完整 + cascade 運作）+ 常數==Literal 防漂移。<br>**[security wiring 守則]** P2-13/P2-19 wiring 時 `planned`/`simulated` 不可被 untrusted CoT 偽造（`_TAK_UPDATE_FIELDS` 已排除、normalize_cot 不設）。**解鎖 P2-13（planned）/ P2-19（simulated）** |
| **——— 【核心雙向層 Core TAK Bidirectional】 有 UI，需 Human Verification ———** | — |
| ✅ P2-10 | **地基層驗收 + E2E 上行測試**：地基層（P2-06a～P2-11b）完成後的整合驗收里程碑。涵蓋：(1) 真實 ATAK/iTAK 推 CoT → 地圖 < 5s 更新（**human verify**）；(2) shape 幾何渲染（route/polygon **human verify**）；(3) mock TAK integration tests；(4) CoT 內容層驗證（座標越界拒絕、callsign 字元白名單、type prefix 白名單——任何 ATAK 裝置可推 CoT，**此為最後一道防線**）；(5) XXE 防護驗測（`defusedxml`）；(6) GeoChat 路由負向（不進 `cop_entities`）。**全通後才進 P2-12/P2-13**。<br>**[進度 2026-06-07]** 純後端項先行：✅ (4) 內容層 type/callsign 白名單（[#157](https://github.com/winson3QQ/ICS_COMMAND/issues/157)，`CoTEventIn` field_validator，:8089+REST 兩路徑；座標越界 P2-04 已擋）；(5) XXE 已於 P2-02 `defusedxml`、(6) GeoChat 負向已於 P2-07 落地。<br>**[2026-06-08 驗收完成 — 真機 iTAK(iOS) dogfood，[PR #167](https://github.com/winson3QQ/ICS_COMMAND/pull/167)]**：TAK Server 5.7（Windows Docker, mTLS）上線，iTAK `曙豐-3QQ-CH1` → :8089 → ICS subscribe → `cop_entities` → 地圖。✅ (1) 真機 CoT < 5s 上圖（位置/2525 marker、`version_clock` live 遞增、真 GPS lat/lon）；✅ (2) shape 幾何 human-verify（矩形 `u-d-r`/多邊形 `u-d-f`/圓 `u-d-c-c`→48-gon、顏色 ARGB、solid/dashed/dotted 三筆觸）；✅ (3) mock 整合測試齊（`test_tak_subscribe`/`test_tak_events`/`test_cop_router`/`test_cop_ws`）。dogfood 連帶修：#159/#4 矩形封閉、#5 顏色、#134 圓、#160/#161 軟 stale、how-based 生命週期、t-x-d-d 刪除、TAK 圖形唯讀、iPad 觸控/版面（PR #167 三 commit；`command-v2.4.0`/`cmd-v1.2.1`）。**上行 E2E 全通 → 解鎖 P2-12/P2-13**。**殘留（不擋上行驗收）**：iTAK 對 dropped marker/drawn shape 刪除不送串流信號 → **可靠刪除生命週期 deferred P2-14**（已留痕）；持久化資安 MEDIUM 同掛 P2-14。<br>**[2026-06-08 #161 後續更正]**：上文「#160/#161 軟 stale、how-based 生命週期」描述的是 WIP（`0ba8fde`）的 **last-heard / how-based 舊解，經 reality check（真機 iTAK + 活 server wire/Marti 雙證）推翻**——改對齊 **TAK 原生 honor `stale` + honor `<archive/>`**（archived 持久豁免 stale、非 archived 過 stale 移除）：[#174](https://github.com/winson3QQ/ICS_COMMAND/pull/174) `f3db03e`，`command-v2.4.1`/`cmd-v1.2.2`，#161 closed。設計動機（CoT 為何如此）見 `docs/reference/` MITRE CoT Developer Guide + memory `tak-streaming-archive-stale-vs-mission`。衍生 [#173](https://github.com/winson3QQ/ICS_COMMAND/issues/173)（ICS reconnect 漏靜態標記，streaming 不重播 → 需 Marti 權威 resync → P2-14）。 |
| P2-12 | **新 UI 面板統一落地（Human Verification 里程碑）**：P2-07/09 後端已備，本項統一設計所有新 panel——**一次 layout 設計決策（整合缺口 #5）** 涵蓋以下三面板，統一 human verify：(1) **TAK 在線人員面板**（`tak_rest_client` poll clientEndPoints/contacts，30s）；(2) **通聯記錄面板**（`chats` 表，前端 `textContent` 禁 `innerHTML`，可點位置跳地圖）；(3) **MEDEVAC incident card**（P2-09 後端，9-line 格式化，severity=critical pulse）。**RBAC**：以上三面板均 `READ_ROLES`。**cop_hub None 廣播不洩漏業務 COP 欄位**（presence 廣播只帶 callsign/UID/online-status）。整合缺口 #4/6 確認（resolve_scope bypass + cop_stream.js null 廣播守門）。<br>**[P2-09 連動依賴]**：(a) MEDEVAC fixture 真機 ATAK CASEVAC 抓包驗證 type 字串 + 屬性語意（[#135](https://github.com/winson3QQ/ICS_COMMAND/issues/135)）；(b) **TAK ingest path server-side XSS escape**（[#136](https://github.com/winson3QQ/ICS_COMMAND/issues/136)）—— MEDEVAC incident card / 通聯面板渲染 `medevac.*`/chat 前須先落地後端 escape（pre-existing gap，P2-09 `/security-review` 發現）|
| P2-13 | **Mission API 下行指令 + COP 指令圖層（Human Verification 里程碑）**：(1) `tak_rest_client.py` Mission CRUD（依賴 P2-11）；(2) 前端「下達指令」UI（選 entity → 指定 group → 推 Mission；**COMMAND_ROLES 限定按鈕**）；(3) MIL-STD-2525 空心框（`planned=True`）vs 實心框（`planned=False`）地圖視覺（P2-11b schema 基礎）。**RBAC**：COMMAND_ROLES 嚴格限（403 負向測試必通）。**Audit**：每次 push 強制寫 `MISSION_PUSH_TAK operator={uid} group={name} cop_entity_id={id} mission_id={id}`（指揮行為，**不得 best-effort**）。**E2E bidirectional 里程碑**：P2-10 + P2-13 → 真實 ATAK ↔ ICS Dashboard 雙向驗證 |
| ✅ P2-23 | **ICS↔TAK 連線狀態顯示（[issue #163](https://github.com/winson3QQ/ICS_COMMAND/issues/163)，2026-06-07 P2-10 dogfood 衍生）**：TAK subscribe(:8089) 在 lifespan 背景 task 跑，但**前端完全看不出連上沒/斷了沒**（dogfood 時要看 server log 才知道）。落地：(1) 後端暴露 TAK 連線健康——subscribe socket 狀態 + **last CoT timestamp** + Marti(:8443) 可達性 →（`/api/status` 擴充或新 `/api/tak/health`）；(2) 前端 header **TAK 連線指示燈**（對齊既有 `status-lamp` 機制：綠＝連線收串流／黃＝連上但無流量／紅＝斷線／灰＝未啟用 `TAK_ENABLED=false`）。**RBAC**：狀態唯讀 `READ_ROLES`，不洩漏業務 COP 欄位（只報連線 meta）。依賴 P2-03（subscribe 已落地）。**[完成 — [#169](https://github.com/winson3QQ/ICS_COMMAND/pull/169) `879f4ee`]**：狀態燈四態（灰/紅/黃/綠）+ `GET /api/tak/status`（last_cot_age server 算、不信 client 時鐘）；cop.js 每 5s 輪詢。**真 iTAK 端到端驗收 PASS**（燈紅→綠、iTAK 單位 + CoT 幾何上圖）。**[衍生 deploy 修補 — [#170](https://github.com/winson3QQ/ICS_COMMAND/issues/170)/[#171](https://github.com/winson3QQ/ICS_COMMAND/pull/171) `75819fb`]**：dogfood 真機 onboarding 撞兩關（truststore 缺 intermediate / server SAN 缺裝置 IP），把手動憑證修補固化進 `issue-tak-certs.sh`（雙層 CA truststore + `TAK_EXTRA_SANS` 參數化，prod=FQDN doctrine）。 |
| P2-24 | **TAK 連結 runtime 開關（[issue #164](https://github.com/winson3QQ/ICS_COMMAND/issues/164)，2026-06-07 P2-10 dogfood 衍生）**：現況 TAK 連線由啟動時 `TAK_ENABLED` env 決定、改需**重啟 process**；指揮部應能 **runtime 啟用/停用** subscribe，不重啟。落地：(1) `SYSADMIN_ONLY` 限定開關 `POST /api/tak/connection {enabled}`；(2) 後端能 start/stop subscribe 背景 task（不重啟 process）；(3) 狀態持久化（重啟後維持選擇）。**RBAC 決策（2026-06-08）= `SYSADMIN_ONLY`**：關掉 TAK＝整 COP 態勢全斷、blast radius 最大 → 比照演習刪除等「破壞性最高」動作鎖 admin；**commander 不可**（避免指揮官誤觸把全 COP 弄瞎；斷線屬基礎設施控制，非 per-incident 指揮決策）。**安全**：每次開/關**強制 audit**（`TAK_CONNECTION_TOGGLE operator={uid} enabled={bool}`，**不得 best-effort**——記不下不放過）；403 負向（commander/operator/observer）必通。依賴 P2-23（狀態顯示）+ P2-03。 |
| ✅ P2-25 | **地圖 TAK 篩選器（map-side TAK filter，2026-06-08 P2-10 dogfood 衍生）**：真機可見 COP 易塞——多 affiliation marker + 矩形/多邊形/圓繪圖疊一起（dogfood 截圖驗證），操作員需在地圖上篩選顯示哪些 TAK 內容。**純前端 view 篩選（不刪資料、不改 server、不送 TAK）**——各操作員篩自己的地圖視圖。**v1 篩選維度**：(1) **圖層開關**：TAK 單位（2525 markers）/ TAK 繪圖（polygon/route/circle）各自顯示/隱藏（延伸既有 `_layerVis` + EntityLayer `setVisible`）；(2) **敵我 affiliation**：友/敵/中立/不明勾選（對映 2525 色，前端依 `type` 推 affiliation；MapLibre filter expression）；(3) **stale 顯示**：顯示/隱藏已過 stale 的活追蹤。**enhancement**：team_color 分組、callsign 文字搜尋、how（活追蹤 vs 人工標註）分流。**RBAC**：`READ_ROLES`（純視圖篩選、無 server 變更、**無 audit**——不是改資料只是改自己看到什麼）。落地於既有地圖工具列圖層控制。**依賴** P2-05（2525 渲染、affiliation 推導已備）；可與 P2-23 並行。[issue #168](https://github.com/winson3QQ/ICS_COMMAND/issues/168)。**[完成 — #169 `879f4ee`]**：「TAK 單位」圖層開關 + affiliation（友/敵/中/不明）/ stale 子篩選（MapLibre `setFilter` 純視圖、不動資料）。真機驗證篩選即時生效。 |
| **——— 【TTX 演習驗證層 Exercise Validation】 先於實戰 ———** | — |
| P2-19 | **情境注入 + O/C 控制台**（新 `services/scenario_service.py`）：讓 O/C（Observer/Controller）向學員 ATAK 注入合成情境 + 向 Dashboard 注入事件/任務。三組件：(A) **ATAK 側注入**：`tak_rest_client.py` 推合成 CoT（`how="h-g-i-g-o"` → `simulated=True`，P2-11b）到 TAK Server，學員 ATAK 地圖出現合成位置/威脅；(B) **腳本執行器**：JSON 腳本（`[{t_offset, action, params}]`）→ background task 按時觸發；`action` 嚴格白名單（`inject_cot` / `inject_event` / `inject_mission`），Pydantic strict 驗證，**禁任何動態執行路徑**；(C) **O/C 控制頁** `/admin/exercise-control`（獨立頁面，與學員 dashboard 分離，sysadmin role-gate）+ `scenario_designer.html` → API 接線（export JSON → `POST /api/exercises/{id}/scenario/upload`）。**安全**：注入 API sysadmin-only（commander/operator 403）；per-exercise mutex（`scenario_running` 欄位，並發第二個 run → 409）；合成實體地圖顯示虛線框 + `[SIM]` callsign 前綴（學員看得出是情境道具）；archive 時 `simulated=True` 實體整批清除（防污染下一場或實戰）|
| P2-20 | **AAR 回放（Wave 6 前移）**：(A) **後端統一時間軸 API**：`GET /api/exercises/{id}/timeline` → 按 t 排序的合併陣列（`{type, t, actor, payload}` from cop_entity_tracks + events + chats + missions audit）；(B) **前端 AAR 頁面** `/aar/{exercise_id}`（獨立頁面，非 ops dashboard toggle；layout：地圖 60% + 通聯/事件面板 40% + 底部時間軸 slider）；**Step mode（優先）**：逐事件跳進（桌面推演首選）；**Play mode（後）**：1x/2x/4x 連續播；地圖顯示各 entity 選定時間點位置 + **軌跡尾跡**（過去 N 分鐘路徑線）；**Touch-friendly**（iPad 觸控，點擊目標 ≥ 44px，時間軸可 swipe）。**安全**：`COMMAND_ROLES` + `resolve_scope()` 跨演習存取守門；軌跡 PII retention policy（90 天 TTL 或 exercise 刪除 cascade，`docs/compliance/threat_model.md` 文件化，P2-17）；AAR 匯出（若做）= COMMAND_ROLES + `AAR_EXPORT` audit log |
| P2-21 | **演習指標 + 課程標記**：(1) 回放時可 bookmark → 帶時間戳的 AAR entry（`created_at=T+N`，連結回放時間點）；(2) 演習統計面板（**COMMAND_ROLES 限定，不暴露為 public API**）：MEDEVAC 請求 → 確認反應時間、Mission 下達 → EXCHECK 完成率、GeoChat 通聯量 by 組；(3) 演習報告（COMMAND_ROLES + `AAR_EXPORT` audit log）|
| P2-22 | **TTX Gateway（實戰延伸層解鎖條件）**：**P2-15 ~ P2-18 被此 gate 保護**，TTX 通過後才解鎖（**P2-14 已於 2026-06-08 提前解鎖**——與 #161/#173 同屬 TAK 同步 feature、是當前 dogfood 需求，不受此 gate 限制，見 P2-14 行說明）。完成標準：≥ 2 次完整演習流程（建立 → 腳本注入 → 學員應變 → 指揮回應 → 歸檔 → AAR）；每次 AAR ≥ 5 條課程標記；`cop_entity_tracks` 完整無缺失；`simulated` 實體無污染到下一場（驗清除機制）；無系統崩潰。**ZELLO**：語音通聯暫走 ZELLO（不整合），頻道分配記錄在 exercise SOP 文件；P3 WaveInk 上線後取代 |
| **——— 【實戰延伸層 Real Ops Extensions】 TTX Gateway(P2-22) 完成後解鎖（P2-14 例外：已提前解鎖，屬當前 TAK 同步 feature；本層實際受 gate 者為 P2-15~18）———** | — |
| P2-14 | **DataSync client**（ATAK Mission / Route / 照片，Marti REST `/missions/` + `/sync/`）：<br>**[2026-06-08 gate 解鎖]** 本項（尤其新增的 **(C) 權威 resync** 與 **(A) 可靠刪除**）與 **#161 / #173 為同一個 TAK 同步 feature**——reconnect 漏靜態標記、streaming 無可靠刪除是**當前 dogfood 就會遇到的痛**（非「實戰才需」）→ 自實戰延伸層**提前解鎖**，**不受 TTX Gateway(P2-22) 限制，可即刻動工**。建議承接 #161 之後做；完整 DataSync（照片/route 等）可分階段，先落 (C) resync + (A) 可靠刪除 + 安全網。工作：(1) `tak_rest_client.py` poll（依賴 P2-11）；(2) geometry 透過 `services/geometry_service.py`（P2-08 共用）轉 cop_entity；(3) 照片：reference-only URI（**永不 follow**——防 SSRF，OWASP A10；`datasync_service` 不得有任何 `requests.get(uri)` 呼叫）；(4) 新 `services/datasync_service.py`。**動工前 DataSync API reality check**<br>**[P2-10 dogfood 帶出的兩個需求（[PR #167](https://github.com/winson3QQ/ICS_COMMAND/pull/167)）]**：(A) **可靠刪除**——iTAK 對 dropped marker/drawn shape 刪除**不送任何串流信號**（無 `t-x-d-d`、無 past-stale 重送，實證），串流模型偵測不到 → 標繪/標記的刪除生命週期須改走此 server 權威通道（明確 create/update/delete 事件）。`t-x-d-d` 串流處理已落地（self-marker 或 Mission 來源刪除即時生效），但不涵蓋 ephemeral 標繪。(B) **🔒 資安（/security-review 補做，MEDIUM）**：`how=h-*`（人工標記/繪圖）改持久化後**豁免 stale → 無自動清理、無 entity 總量上限**；被盜用的 mTLS 場端憑證可 fire-and-forget 灌爆 COP/DB（限速 60/s 但時間拉長無界）。本 server 權威生命週期為正解；過渡期若需先擋，加「`how=h-*` 絕對最大壽命安全網」（`stale` 超過可調 `PERSIST_MAX_AGE`≈7d 即移除）或每來源 entity 上限 + 操作員「移出 COP」。<br>**[威脅模型補註]** `t-x-d-d` 刪除命令在 TAK 信任邊界內（持證 client）可刪任何 `source='tak'` entity（manual/command 受所有權守門保護）；屬 TAK 本質行為，補入 `docs/compliance/threat_model.md`（隨 P2-17）。<br>**[2026-06-08 #161 更正 + #173 補入]**：(A)(B) 的持久化訊號**不是 `how=h-*`，是 CoT `<archive/>`**（#161 reality check，[#174](https://github.com/winson3QQ/ICS_COMMAND/pull/174)）→ 安全網（B 的 `PERSIST_MAX_AGE`/總量上限/操作員「移出 COP」）改以 `archived` 為界。**新增 (C) 權威 resync**：實測此 server streaming **不對重連者重播靜態標記**（latestSA 只推 PLI）→ ICS 重啟/斷線會漏既有靜態 marker、與 server 分歧 → 須 Marti 主動拉權威全集對帳（同 poll 管線）→ [#173](https://github.com/winson3QQ/ICS_COMMAND/issues/173)。t-x-d-d 實測：清 live 畫面（ICS+client drop）但**不清 server archive**（需 server 端/mission 才徹底 purge）。 |
| P2-15 | **Federation 設定**：server-to-server 交換 CoT（`:9000`/`:8444`）。含 `fed-truststore.jks` + step-ca peer cert profile。**Security**：peer cert 加入須 sysadmin 審批 + audit log；`fed-truststore.jks` 變更納入 change management。先單機 PoC → 跨機關（Wave 7+）<br>**[2026-06-08 admin UI 實證]**：TAK Server 有 federation 管理 GUI（`Configuration → Federation` + `Federate Certificate Authorities`）。現況 **federation server DISABLED**；scaffold 齊（V1 `:9000` TLS1.2/1.3、V2 `:9001`、`Allow Mission/DataFeed Federation: true`、**`Allow Federated Delete: false`**、Mission Disruption Tolerance 12h、`fed-truststore.jks` 路徑已在）、**無 peer**。→ **GUI 開關 trivial，真正瓶頸＝第二台 TAK server + peer cert 治理（缺口 #8）**；對齊「gated（P2-22）+ 需第二台 + 低即時價值」評估，現行只先備 PKI 地基（軸2）。`Allow Federated Delete: false` 亦印證跨域刪除預設關（呼應 #161/P2-14 可靠刪除主線）。 |
| P2-16 | **影像串流整合**（Marti REST `/video/`）：URI reference only，**ICS Command 不 proxy 串流**。可選 / 低優先 |
| P2-17 | **規格書補 TAK 整合章節**（P2-07 ~ P2-21 完成後補）：含 Marti REST cert 認證 SOP（非 OAuth2，#138 更正）、Mission downlink 流程、planned/actual 視覺、TTX 運作 SOP（ZELLO 頻道分配、ATAK 學員設定）、track PII retention policy、TAK 信任邊界（補入 `docs/compliance/threat_model.md`）|
| P2-18 | **EXCHECK 任務查核整合**（Marti REST `/excheck/`）：ICS-204 任務指派追蹤。依賴 P2-11；視 P2-13 downlink 完成度決定優先順序 |

### TAK 介面整合缺口與架構決策（2026-06-05）

以下缺口在對應項目動工前需設計決策。

| # | 缺口 | 現況 | 影響 | 決策方向 |
|---|---|---|---|---|
| ✅ 1 | **CoPEntity `source` enum 凍結** | `schemas/cop.py:21` 加 `"command"`（P1-03 解凍） | **[#144](https://github.com/winson3QQ/ICS_COMMAND/pull/144)** | 完成：table rebuild（`foreign_keys=OFF`，CHECK 不可 ALTER；`legacy_alter_table` 實測無效已棄用）。`CoPSource` 含 command |
| 2 | **CoPEntity 缺 `planned` / `simulated` 欄位** | 無 planned/actual 區分；無合成標記欄位 | P2-11b | P2-11b 統一加（`planned: bool = False` + `simulated: bool = False`）|
| 3 | **GeoChat taxonomy 衝突** | `events` 表 FK → event_types（NAPSG）；GeoChat 通聯不符 NAPSG | P2-07 | **決策：獨立 `chats` 表**（ICS-214 通聯語意不同 NAPSG 事件）|
| 4 | **TAK 在線狀態須 bypass exercise scoping** | `resolve_scope()` 套 cop_entities；presence 是基礎設施狀態 | P2-12 | `cop_hub.broadcast(exercise_id=None)`；前端不套 exercise filter |
| 5 | **前端 UI 落點無既成 panel** | P1-11 已移除 left sidebar；無現成 panel 位置 | P2-12 | **P2-12 統一一次 layout 設計決策**，三面板同批落地 |
| 6 | **cop_hub None 廣播前端守門** | `cop_stream.js` 是否誤丟 `exercise_id=null` 廣播未確認 | P2-12 | 確認 `cop_stream.js` filter 邏輯 |
| 7 | **CoT 裝置准入隱性信任假設** | ICS 信任 TAK Server 所有 CoT；裝置准入是 TAK 管理員責任 | 全部 CoT 流入 | `docs/compliance/threat_model.md` 文件化（P2-17）；P2-10 內容層驗證作最後一道防線 |
| 8 | **Federation peer authorization governance** | `fed-truststore.jks` 無正式審批流程 | P2-15 | sysadmin 審批 + audit log；納入 change management |
| 9 | **CoPEntity 缺 `simulated` 欄位** | 合成注入實體（`how="h-g-i-g-o"`）無標記，archive 後無法辨識清除 | P2-19 | **P2-11b 統一補**（與 `planned` 同批）|
| 10 | **O/C 控制頁落點** | 無 sysadmin 專屬頁面；放主 dashboard 學員可能看到 | P2-19 | 獨立頁面 `/admin/exercise-control`（sysadmin role-gate）|
| 11 | **scenario_designer.html 脫離 API** | 52KB 靜態工具，無任何 API 連接 | P2-19 | 補 export → JSON → `POST /api/exercises/{id}/scenario/upload` 接線 |
| 12 | **AAR 頁面 layout 未定** | 無現成頁面；嵌主 dashboard 與即時 ops 模式衝突 | P2-20 | 獨立頁面 `/aar/{exercise_id}` |
| 13 | **PII retention policy 未定義（兩個保留域，目前都無界）** | `cop_entity_tracks` 累積 = 人員移動時間序列（高度敏感）；無清除 SOP。**[2026-06-08 admin UI 實證]** 還有**第二個域**：TAK Server PG。TAK 有保留 GUI（`Administrative → Data Retention`，per-type TTL：Cot/GeoChat/**Mission(含 tracks/files)**/Files + 排程器），**但現況全 TTL 空白、排程 `Never`** → TAK PG 也無界（呼應 #161 server 留全部）| P2-06a/P2-20 | **兩域分治**：① TAK 側 = 部署時開 Data Retention GUI（設 TTL + 排程）② ICS 側 = `cop_entity_tracks` 自管 TTL（TAK 保留管不到 ICS SQLite）。皆 90 天 TTL 或 exercise 刪除 cascade；文件化 threat_model.md（P2-17）|
| 14 | **Scenario runner 並發控制** | 無 per-exercise mutex；兩腳本同時跑 → 學員地圖混亂 | P2-19 | `exercises.scenario_running` 欄位；第二個 run 請求 409 |
| 15 | **REST ingest 端點缺機器間認證**（→ TAK-A）| `POST /api/tak/events` 要求 WRITE_ROLES session token；外部 TAK server / federation 無 session 取得機制（無 API key / HMAC inbound / service account 路徑）→ 端點對「REST federation push」用途事實上不可呼叫 | P2-03 後 / 任何需要 REST federation push 的場景前 | **三選一**：(A) HMAC inbound（對齊 Pi-node `verify_hmac` 模式）；(B) mTLS inbound client cert；(C) 只保留 :8089 pull、標此端點 internal-only。動工前先確認 TAK federation 架構需求（影響 P2-11 M2M auth 設計） |

#### 確認架構決策（後續 PR 的 SoT）

1. **認證**：Marti REST API（`:8443`）走 **client cert（mTLS）**，複用 :8089 同一套 step-ca cert（`TAK_CLIENT_CERT/KEY/CAFILE`）。**[P2-11 #138 規格更正]** 原述「OAuth2 client credentials」為錯誤前提 —— 官方 TAK Server 5.7 Marti 走 cert，OAuth2 是 OpenTAKServer 才有（見 memory `tak-server-marti-cert-not-oauth`）。
2. **下行指令**：走 **Mission API**（group-scoped + 持久），**不走** pytak TXWorker（無 group scope）。
3. **GeoChat 落地**：進獨立 `chats` 表（ICS-214 通聯語意），**不進** `cop_entities`，**不進** NAPSG `events` 表。
4. **幾何解析共用**：`services/geometry_service.py` 統一解析 CoT `<shape>` 與 DataSync GeoJSON，P2-08 建、P2-14 共用。
5. **多媒體原體邊界**：照片/串流只保留 URI reference，ICS Command **不 proxy 原體**。
6. **DataSync URI 不 follow**：`datasync_service` URI 為純 string，**永不 HTTP fetch**（防 SSRF，OWASP A10）。
7. **合成實體清除**：演習 archive 時所有 `simulated=True` 的 cop_entities 整批清除，**不污染下一場或實戰模式**。

### 情境檢討衍生候選（2026-06-09）

> 來源：19 情境深挖（[`tak-use-cases-config.md`](reference/tak-use-cases-config.md)）+ TAK 整合策略（[`tak-integration-strategy.md`](roadmap/tak-integration-strategy.md) §6）累積出的跨情境候選。**此為留痕 backlog**——花大力氣檢討的東西，即使最終 later/不做也記在此，不蒸發。**狀態待使用者圈定**（進＝升 numbered item / later / 不做）；圈定後本表標結果 + 連結對應 item。狀態 marker 沿用本文件全域約定（無 marker = 待圈定）。

| 代號 | 候選 | 服務哪些情境 | 與既有 ROADMAP 關係 | 依賴 | 狀態 |
|---|---|---|---|---|---|
| **SC1** | **天氣 / 環境 feed**（雨量 / 風 / 風向 / 河川水位）| 颱風水災 · 野火（60% 受困死亡集中 3% 火災天氣日）· HazMat（plume 完全靠風）· 航空 | **全新**——非 TAK，是 COP 第 N 個外部來源，走 `cop_service` normalize（同 TAK/WaveInk 多源架構）| 不依賴 TAK（**降階獨立**）| 待圈定（檢討頭號候選）|
| **SC2** | **sensor source 模式**（CBRN / SDR / UGS / CCTV → COP）| HazMat（CBRN 偵測器）· 設施巡邏（CCTV/UGS/雷達）· SDR/RF | **半新**——泛化 P3 WaveInk 的 sensor ingest；對應 TAK `/Marti/api/datafeeds` + `/inputs` | `cop_service` normalize（與 WaveInk 共用接縫）| 待圈定 |
| **SC3** | **主動告警 / 決策觸發**（geofence 後端 + 門檻告警）| 設施巡邏 · 指揮決策（OODA Decide「何時」）· HazMat 門檻 | **全新後端**——ICS 自建（非 TAK）；現多被動視覺（severity pulse / stale 灰）| SC2（感測器進來門檻才有料）| 待圈定（排 SC2 後）|
| **SC4** | **連線 / 降階可視化收斂**（來源 chips、退役 P2-23 獨立 `cd-tak` 燈、連線 vs 信心兩軸分離）| 全情境（降階）| **改**——併 P2-12 面板 + 重構 P2-23；修「`cl-server`+`cd-tak` 兩 dot 無 legend」破口 | P2-12 | 待圈定（低風險、修既有破口）|
| **SC5** | **威脅圖層**（`threat_state` 動態區 / 武裝掩護已清走廊 / 敵我 + 老化威脅情報）| RTF · 多機構 | **新**——擴 P1-16 zone（加 threat_state 屬性）+ P2-08 | P1-16 / P2-08 | 待圈定（單一高威脅情境、價值較窄 → 傾向 later）|
| **SC6** | **降階 DoD 紀律**（每 TAK item 宣告 Tier + fallback + `TAK_ENABLED=false`/斷線負向測試）| 全 TAK 能力 | **改**——加進 Phase 2 DoD（本批 S3 已落地，見下方 DoD「降階」段）| — | ✅ 本批入 DoD |
| **SC7** | **安全 backlog 重排優先級**（RTF/多機構給「人命級」論據：§8.3 COP poisoning / 可靠刪除 #161 / 權威 resync #173·P2-14 / federation 治理 #8 / 跨機構分級 TAK-D）| RTF · 多機構 · 共享 COP | **改**——既有 RT-*/TAK-* 重新排序，非新項 | 既有 backlog | 待檢視（#161/P2-14 已因 dogfood 提前解鎖） |

> **原則**（使用者拍板）：**統一檢視非全做**——多數候選不一定要做（計劃群誠實結論同調：多數計劃分析本不在 COP 工具範疇）。圈定時逐一評估「服務情境廣度 × 是否撞既有缺口 × 降階獨立性」。

### Definition of Done

**地基層**
- [ ] `cop_entity_tracks` 每次 CoT upsert 後有寫入（含 exercise_id），5s min-interval 抽樣（P2-06a）
- [ ] `GET /api/exercises/{id}/tracks` 回傳完整時間序列，COMMAND_ROLES 限定（P2-06b）
- [x] `cop_entities` 加 `team_color`/`role`/`battery` 一等 column + team_color index（源自 CoT `__group`/`status`，設計 A）（P2-06c）
- [x] `GET /api/cop/squads` 回 per team_color 聚合（total/online/avg_battery/centroid），READ_ROLES（P2-06d）
- [x] `dashboard_service.build_dashboard()` 含 `tak_squads`（dashboard 對 TAK 不盲視）（P2-06d）
- [x] GeoChat（`b-t-f`）**不進** `cop_entities`（負向），進 `chats` 表；XSS 負向（P2-07）
- [x] CoT `<shape>` 幾何萃取正確，`geometry_service.py` 單元測試通過（P2-08）
- [x] MEDEVAC 9-line 欄位完整萃取（→ `attributes["medevac"]` 摘要 + severity critical，設計 B 進 attributes 不開新表），schema 對齊 P3-06（P2-09）
- [x] Marti REST `:8443` 走 **client cert（mTLS）** 複用 :8089 cert（**規格更正：非 OAuth2**，#138）；`build_marti_ssl_context` fail-closed 無 CA 不裸奔（P2-11）
- [x] `TakRestClient`：rate-limit + 指數退避重試（cap）+ poll scheduler + 並發鎖；`_join_url` 杜絕 SSRF host override（P2-11）
- [x] CoPEntity 加 `planned` / `simulated`（`_m017` ADD COLUMN，bool↔INTEGER），現有資料不破壞（P2-11b）
- [x] CoPEntity `source` enum 加 `"command"`（`_m018` table rebuild `foreign_keys=OFF`，動態重建 index）（[#144](https://github.com/winson3QQ/ICS_COMMAND/pull/144)，issue #141）

**核心雙向層（Human Verification 必通）**
- [ ] 真實 ATAK 推 CoT → 地圖 < 5s 更新（**human verify**）（P2-10）
- [ ] CoT 內容層驗證：座標越界拒絕、callsign 白名單、type prefix 白名單（P2-10）
- [ ] XXE 防護（`defusedxml` forbid_dtd/entities/external）（P2-10）
- [ ] 三個 UI 面板 human verify：在線人員 + 通聯記錄（`textContent`）+ MEDEVAC card（P2-12）
- [ ] Mission downlink：ATAK 收到指令，`planned=True` 空心框顯示（**human verify**）（P2-13）
- [ ] Mission push RBAC：COMMAND_ROLES 以外 403 負向；audit log `MISSION_PUSH_TAK` 存在（P2-13）
- [ ] **E2E bidirectional**：真實 ATAK app ↔ ICS Dashboard 雙向全鏈驗證（P2-10 + P2-13）

**TTX 演習驗證層**
- [ ] 情境腳本執行：上傳 → background 按 t_offset 觸發；action 白名單拒絕非法 action（P2-19）
- [ ] 注入 API sysadmin-only，403 負向；runner 並發 409（P2-19）
- [ ] 合成實體：`simulated=True`，虛線框 + `[SIM]` prefix，archive 清除（**human verify**）（P2-19）
- [ ] AAR timeline API 回傳 tracks + events + chats + missions 合併時間軸（P2-20）
- [ ] AAR Step mode：逐事件跳進，地圖顯示對應位置 + 軌跡尾跡（**human verify**）（P2-20）
- [ ] AAR Touch-friendly：iPad 可操作時間軸（P2-20）
- [ ] TTX Gateway：≥ 2 次完整演習，simulated 無污染，track 無缺失（P2-22）

**實戰延伸層**
- [ ] DataSync URI follow 負向：`datasync_service` 不發任何 HTTP request to DataSync URI（P2-14）
- [ ] DataSync Mission geometry 進 cop_entities route/polygon（P2-14）
- [ ] Federation 雙向流測試（P2-15）
- [ ] EXCHECK 任務狀態顯示於 dashboard（P2-18）
- [ ] **Tag**：`command-v2.3.0`（TAK 整合 MINOR；rebase 至 2.x）

### Compliance touchpoints

- **NIST SP 800-53 SC-8 / SC-13**：TAK Server TLS 8089/8443 強制
- **NIST SP 800-53 IA-9**（服務識別與認證）：Marti REST M2M 走 **client cert（mTLS）**（P2-11；規格更正非 OAuth2）
- **NIST AC-3**（存取控制）：TAK presence bypass exercise scope（P2-12）；軌跡 COMMAND_ROLES 限定（P2-06b/P2-20）
- **NIST AU-2 / AU-12**（Audit Events / Audit Record Generation）：Mission push audit log（P2-13）；AAR export audit log（P2-21）；scenario inject audit（P2-19）
- **ASVS V13 (API) + V5 (Validation)**：CoT XML XXE 防護；Marti REST mTLS cert 認證；CoT 內容層白名單（P2-10）
- **OWASP API2（Broken Authentication）**：Marti REST 走 client cert（mTLS）；無 CA 不裸奔 fail-closed（P2-11；規格更正非 OAuth2）
- **OWASP A03（Injection / XSS）**：GeoChat `html.escape()` + `textContent`（P2-07）；CoT callsign / type 白名單（P2-10）；scenario 腳本 action 白名單 + Pydantic strict（P2-19）
- **OWASP A04（Insecure Design）**：O/C 注入 API sysadmin-only gate（P2-19）；scenario runner mutex（P2-19）
- **OWASP A10（SSRF）**：DataSync URI 永不 follow（P2-14）；影像串流 URI reference only（P2-16）
- **ASVS V7（Error Handling / Logging）**：Mission push 必須留痕（P2-13）；AAR export audit（P2-21）
- **MIL-STD-2525C**：planned（空心）/ actual（實心）/ simulated（虛線框）符號視覺區分（P2-11b/P2-13/P2-19）
- **ICS-214 Unit Log**：GeoChat → `chats` 表；MEDEVAC 9-line incident card（P2-07/09）
- **ICS-204 Task Assignment**：EXCHECK 任務查核（P2-18）
- **PII / 台灣個資法**：`cop_entity_tracks` 人員移動軌跡資料 retention policy（90 天 TTL）；`docs/compliance/threat_model.md` 文件化（P2-17）
- **TAK 信任邊界假設**：裝置准入責任在 TAK 管理員；threat_model.md 文件化（P2-17）

### 風險與決策點

| 風險 | 緩解 |
|---|---|
| 官方 TAK Server Java 資源需求高（RAM ≥ 8GB），Pi 500 邊緣 | P2 部署目標暫定 x86 mini-PC（N100 class），Pi 500 留作 client / 備援 |
| Federation cert 與內網 step-ca 整合 | 沿用 ICS_DMAS C1-B 既有 step-ca 內網 PKI；fed-truststore.jks 管理 SOP 已在 P2-01 #101 |
| License：TAK Server 部分 plugin / DataSync 模組是 commercial | P2 只用 core CoT + Marti REST（Apache 2.0 core）+ federation，不依賴 commercial plugin |
| ~~OAuth2 client credentials 在 TAK Server 5.7 設定文件稀少~~ **[P2-11 #138 已解]** | reality check 證實官方 TAK Server 5.7 Marti 走 **client cert（mTLS）非 OAuth2**（OAuth2 是 OpenTAKServer）→ 已改用 cert（複用 P2-03 step-ca），risk 消解 |
| CoPEntity schema v2 migration 可能破壞 P1-03 凍結契約 | P2-11b 統一一次完成（不分批）；P1-03 contract tests 作為回歸守門 |
| 前端 UI 落點無既成 panel（P1-11 已拆 left sidebar）| **P2-12 統一一次 layout 設計決策**，三面板同批落地，避免各項各自貼 UI |
| ~~JWT refresh token 若裸存明文，成為新 secret 洩漏點~~ **[P2-11 #138 不適用]** | P2-11 改走 client cert（mTLS）非 OAuth2 → 無 JWT / refresh token，此 secret 洩漏點不存在 |
| CoT 資料完整性：任何 ATAK 裝置可推入 COP | 內容層三道驗證（座標 / callsign / type 白名單，P2-10）；裝置准入靠 TAK cert enrollment |
| Federation governance：加 peer cert 無正式審批流程 | sysadmin 審批 + audit log；fed-truststore.jks 變更納入 change management（P2-15）|
| 情境腳本 server-side 執行：惡意 payload 若沒擋住 | action 白名單 + Pydantic strict；**禁任何動態執行路徑**（P2-19）|
| 合成實體污染實戰模式：archive 後 simulated 實體殘留 | `simulated=True` flag + archive 整批清除 + TTX Gateway 驗證（P2-19/P2-22）|
| 軌跡 PII：cop_entity_tracks 累積長期人員位置 | 90 天 TTL + COMMAND_ROLES 限存取 + retention policy 文件化（P2-06a/P2-20）|
| AAR 暴露歷史指揮決策：可能成為 OPSEC 洩漏點 | COMMAND_ROLES 限定 + resolve_scope 跨演習守門 + 匯出 audit log（P2-20）|

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

## 紅隊審查發現待辦（2026-06-05）

> 以紅軍視角審視全系統後的發現，**僅記錄事實 + 歸屬，未動程式碼**（待排程）。
> 分級已**按部署範圍校正**——先前疑似 CRITICAL 的項目經查落在「未部署的休眠 Node 層」，在 ICS_COMMAND 非現役攻擊面。每項標明 事實 / 推論，便於後續 session 直接接手不必重查。
>
> **代號與狀態約定（別的 session START HERE）**：
> - **`RT-x#` 代號**：`RT` = Red Team（2026-06-05 全系統紅隊審查）；字母 `H`/`M`/`L` = 該次審查原始嚴重度（High/Medium/Low）；數字 = 流水號。代號是**穩定 anchor**，本節與 commit / issue / branch 名互引時用它（例：`fix/issue-NN-rt-h1-snapshots-auth`）。
> - **這些是「發現 backlog」，尚未升為 GitHub issue**（無 issue 號 = 還沒排程）。狀態 marker 沿用本文件全域約定（**無 marker = pending**／⏳ = branch 已開／✅ = merged）；本節各項目前皆 pending。
> - **怎麼動工（升 issue → 收尾）**：排到某項時 →（1）開 GitHub issue，標題帶 RT 代號 + 一句事實；（2）開 branch `fix/issue-NN-rt-x#-*`；（3）走 [`docs/PROCESS.md`](PROCESS.md) 10 步 lifecycle（含 `/security-review`）；（4）merged 後**回到本節該列標 ✅ + `(#PR, hash)`**，與其他 ROADMAP item 同規格。**動工前先讀同列「動工時機」與〈三陷阱〉**。
> - **什麼時候動工**：見各表「動工時機」欄（已寫成具體觸發條件，非空泛 phase 名）。RT-H1/L4 無依賴、隨時可做；其餘綁特定 item 或 deploy 里程碑。
>
> **動工前的範圍鐵則（決定一切分級）**：`server/` Node.js relay **在 ICS_COMMAND 未被部署**——
> 事實證據：① `systemd/` 只有 `ics-command.service`（FastAPI uvicorn）+ `ics-backup`，無任何 unit 跑 node；
> ② `server/package.json` 不存在；③ `start_*.sh` / CI / `deploy/` 皆未引用 `server/index.js`；
> ④ `server/config.js` 只認 `--unit shelter|medical`（PWA 單元，CLAUDE.md 明文不在本 repo 範圍）。
> 故 `server/` = P1-04 保留的 federation infra（休眠），其內漏洞在 ICS_DMAS（PWA+Pi 真跑）才現役。

### 現役（command-dashboard，已部署）— 需排程修補

| 編號 | 事實 | 性質 / 推論 | 動工時機（觸發）+ 怎麼動 |
|---|---|---|---|
| **RT-H1** ✅ **已修（[#153](https://github.com/winson3QQ/ICS_COMMAND/issues/153)，2026-06-07）** | `auth/middleware.py:46` 豁免 `GET /api/snapshots/{node_type}`，handler（`routers/snapshots.py:30`）無任何 auth；全 repo grep **查無前端/Pi 呼叫者**（dashboard 走 service 層 `get_snapshots`，非此路由） | 推論：孤兒豁免 → 匿名可讀資源快照（床位/傷亡聚合）。屬 P1-14 exercise scoping 的漏網（6 repo 套了 `resolve_scope`，此路由整條繞過 session） | **已修**：middleware 移除該 GET 豁免行 → 走預設 `READ_ROLES`；保住 `POST /api/snapshots` 的 HMAC 豁免（Pi push 命脈）；`test_snapshot_get_is_open_by_design` → `test_snapshot_get_requires_auth`（401）。與 RT-L4 同一獨立 hardening PR |
| **RT-L4** ✅ **已修（[#153](https://github.com/winson3QQ/ICS_COMMAND/issues/153)，2026-06-07）** | `routers/admin.py:282` `audit_log(limit:int=100)` 無服務端上限，`?limit=` 由呼叫端全控 | 推論：`?limit=999999` → 記憶體/慢查詢壓力（DoS 弱面，需 sysadmin session，影響有限） | **已修**：`limit = max(0, min(limit, 1000))` clamp（**下限 0 並修原規劃漏洞 —— SQLite `LIMIT -1` = 無上限，負值會反成「全撈」破口**）+ 負向測試（超大 clamp、負值回空）|
| **RT-M1** | `auth/rate_limit.py:29` + `auth/service.py:45` 皆優先信 `X-Forwarded-For`。nginx 已覆寫 XFF（P1-09 `command.conf:62`），但 `ics-command.service:18` 綁 `--host 0.0.0.0:8000` | 事實：走 nginx(443) 安全；推論：**直連 :8000** 可偽造 XFF 繞 10 次/分 login 限速 + IP binding | **觸發：正式上線前的部署 hardening checklist（非 app code）**。**怎麼動**：把 `ics-command.service` 的 `--host` 改 `127.0.0.1`（只讓 nginx 對外），或 OS firewall 擋外部 :8000；同步記 `threat_model.md §3.5 DoS`。⚠️不要改 app 內 XFF 解析邏輯（會壞掉 nginx 後的合法取值） |
| **RT-M3** | `core/input_safety.py:23` `_UNSAFE_CHAR_RE` 擋 `<>` `{}` 反引號 `&#` `&entity;` `javascript:`/`data:`/`vbscript:`，**未擋** `"` `'` | 推論：`<>` 已擋 → 開新 tag 受阻；殘餘=attribute 跳脫（`" onmouseover=`）需既有 innerHTML sink 未跳脫引號才成立。屬縱深不足非破口 | **觸發：P2-07 或 P2-12 動工時**（那兩項 DoD 已要求前端 `textContent` 禁 `innerHTML`，同批做）。**怎麼動**：改 sink 端輸出編碼（innerHTML→textContent），不是改本 validator。⚠️**禁擴張此 blocklist**（見下方陷阱 1） |

### 休眠 / 非 bug — 不在 ICS_COMMAND 動

| 編號 | 事實 | 為何不動 | 動工時機（觸發）+ 去向 |
|---|---|---|---|
| **RT-C1** | `server/ws_handler.js:220` WS `auth_result` 把 `HMAC_SECRET`（= Pi→Command ingress 的 `trusted_keys` secret，見 `config.js:95` 註）明文下發給每個已認證 WS client | 事實：此檔在 ICS_COMMAND 未部署（見上方鐵則）。在 ICS_DMAS 是現役且嚴重（低權帳號可拿 ingress 偽造金鑰） | **觸發：Wave 7+ PWA 回流 kickoff，且列為其 DoD 前置**（啟用 `server/` 前必先解此 secret 共用設計）。**去向**：另開上游 ICS_DMAS issue（該 repo 才現役）+ 本 repo Wave 7+ 規劃引用本代號 |
| **RT-H2** | `middleware.py:44` 放行 `POST /api/sync/push`（無 session） | 事實：仍受 `verify_hmac` Depends 把關，**非無認證**；只有 RT-C1 洩 secret 才可偽造。RT-C1 休眠 → 此項在 ICS_COMMAND 不可獨立利用 | 隨 RT-C1（Wave 7+）一併評估 |
| **RT-L3** | `server/config.js:64` 無憑證時退回明文 WS（`STRICT_TLS` 預設 false） | 同 RT-C1：休眠 Node 層；且 `STRICT_TLS=true` 已可 fail-fast | Wave 7+ |
| **RT-M2** | `routers/admin.py:61` `_check_admin_pin` 實際只驗 sysadmin role，無第二因素 | 推論：非 bug——「admin PIN 第二因素」是 P1-12 key-mgmt 概念，現況 backup 路由 sysadmin-role gate 是當前設計 | **P1-12**（要做第二因素才談） |
| **RT-M4** | `auth/service.py:54` session IP binding 只比 /24 前綴 | 事實：刻意取捨，容忍現場 iPad 同網段漫遊。收緊 /32 會誤踢登出（見陷阱 2） | 不動，記 residual risk |
| **RT-L1/L2** | HMAC skew 預設 5 分（`config.py:97`）；TAK insecure TLS flag（`config.py:109`） | nonce 已蓋重放；TAK TLS 已 fail-closed（P2-02 security review 落實） | 不動 / 已緩解 |

### TAK 介面安全（TAK_ENABLED=false 時休眠，啟用前必查）

> 以下六項在 `TAK_ENABLED=false`（現役預設）時不可觸達，**不影響當前系統**。TAK-A 屬架構決策（啟用前必解）；其餘依對應 P2 子項動工時機處理。代號 `TAK-x` 為本次審查 TAK 介面專項，與 RT-* 並列追蹤。

| 代號 | 事實 | 性質 / 推論 | 動工時機（觸發）+ 怎麼動 |
|---|---|---|---|
| **TAK-A** 🔵 **決策已定（2026-06-07）** | `POST /api/tak/events` 走 WRITE_ROLES session token；外部 TAK server / federation 無機器間 session 取得機制（無 API key / HMAC inbound / service account 路徑），端點對「REST federation push」用途不可呼叫 | 設計間隙（非 bug）；見缺口 #15 | **決策：選 (A) HMAC inbound，延後實作**。端點現況 session-gated（WRITE_ROLES）本就擋外部機器、僅測試/internal 可呼叫，**無現役破口**；production 上行走 :8089 pull、federation server↔server 落 :8089，皆不經此端點。真有「外部系統 REST 推 CoT 進 Command」需求時才接 HMAC inbound（對齊 Pi-node `verify_hmac`）。詳 [threat_model §8.2](compliance/threat_model.md) |
| **TAK-B** ✅ **已修（[#147](https://github.com/winson3QQ/ICS_COMMAND/pull/147) `c5088df`，issue #145，2026-06-07）** | `cop_entities.uid` 無命名空間隔離；TAK CoT uid 與 ICS 本地 entity uid 相同 + 時間戳更新 → `ingest_cot_event` CAS update 覆蓋本地 entity 的 type/lat/lon/callsign/remarks（`_TAK_UPDATE_FIELDS`） | 完整性風險（Tampering）；意外碰撞或惡意偽造 uid 均可觸發 | **[2026-06-07 改採來源所有權守門，取代原「tak: 前綴」構想]** 業界/CoT 標準查證（[The Developer's Guide to CoT](https://tutorials.techrad.co.za/wp-content/uploads/2021/06/The-Developers-Guide-to-Cursor-on-Target-1.pdf)）：uid 由來源系統命名、應已全域唯一，且轉傳須**保留 uid 不改寫**（供 P2-13 下行對 ATAK 下令對位）→ **不前綴改名**。改在 `ingest_cot_event` 加守門：TAK 事件只准動 `source='tak'` 的 entity，撞本地他源（`manual:*`/pi-node/waveink）一律拒絕覆寫、回 None 記 log。`cop_service.py` + 負向測試 `test_tak_event_does_not_overwrite_non_tak_entity`。詳 [threat_model §8.2](compliance/threat_model.md)。**✅ 反向缺口亦已修**（operator PUT/DELETE 覆寫 tak entity → 來源所有權+情境守門；並順手收緊 POST /api/tak/* 至 COMMAND_ROLES 關注入後門）→ [issue #146](https://github.com/winson3QQ/ICS_COMMAND/issues/146) |
| **TAK-C** ❌ **評估後不採（2026-06-07）** | `CoTEventIn.opex` 被收但 `normalize_cot()` 不讀；`exercise_id` 由 `current_exercise_id()` 決定 | 紅隊原述：演習中 TAK 推 `opex="o"`（實戰）→ 綁 exercise_id → reset 隨場清（資料完整性） | **不採：違反 server-authoritative doctrine**。模式（演習/實戰）由**指揮部 UI 的 active exercise 決定（server 權威）**——對齊 [resolve_scope](command-dashboard/src/services/exercise_service.py) + cop create 強制 `source='manual'` 的「**不信任 client 宣告**」doctrine。讓 ATAK `opex`（client 宣告）覆寫 server 模式 = 給每裝置「自宣告繞過演習場」開關，反成新完整性破口。`simulated` 同理應由 **P2-19 O/C 注入（server 端）** 設，非信 `opex`。紅隊原始擔憂（reset 清實戰）的真正解 = **演習/實戰部署隔離**（指揮部不同時跑兩模式 / 獨立 instance），非信 client opex。**使用者 2026-06-07 確認無「同 instance 混演習+實戰」需求** → 維持 `current_exercise_id()` 現狀，不動 code |
| **TAK-D** 🔶 **重歸屬 P2-12（2026-06-07）** | `CoPEntity.visible_to` schema 預設 `["all"]`（`schemas/cop.py:85`）；`normalize_cot()` 不覆寫此欄位 → 所有 TAK entity 對全部 COP 訂閱者可見（含 observer）；`cop_hub` broadcast 亦無分級過濾 | 未來多分類環境的資訊洩漏（Information Disclosure）；現況 observer = read-only 非敏感部署下無害 | **歸屬 P2-12**（UI 面板分層顯示時評估）。**怎麼動**：建立 CoT `access` 欄位 → `visible_to` 映射規則（如 `access="FOUO"` → `["commander","sysadmin"]`）；無多分類需求可延後。詳 [threat_model §8.2](compliance/threat_model.md) |
| **TAK-E** ✅ **已修（[#151](https://github.com/winson3QQ/ICS_COMMAND/issues/151)，2026-06-07）** | `:8089` 串流訂閱（`subscribe()`）無流量管制；每 uid 有 1s 精度保護（`_normalize_iso8601` 秒 floor + `_is_newer` 比較），但 uid 數量無上限；50 ATAK @ 1Hz = 50 DB write + 50 WS broadcast/s 持續壓 | DoS 弱面（多 uid 高頻 burst）；單 uid 已有 1s 保護，uid 爆量未擋 | **獨立 hardening PR 完成**：`_TokenBucket`（全域 events/s 速率）套 `_consume_cot`（parse + 濾 TakControl 後、ingest 前），超量丟棄該筆 + 節流 `log.warning`、不中斷串流；config `TAK_INGEST_MAX_EVENTS_PER_SEC`（預設 60，`<=0` 關閉）；limiter 跨重連共用同桶（subscribe 建一次）。**per-exercise uid cap descope**（受信任 mTLS 源價值低、每筆 create 多一 COUNT query；速率桶已涵蓋持續高率 DoS，未來 entity 爆量再開 follow-up）。`/code-review` 通過（token bucket 數學無誤、向後相容 limiter=None） |
| **TAK-F** 🔶 **部分處理（2026-06-07 校正）** | ATAK 醫療擴充（CasEvac / 9-line）在 `<detail>` 放資料；`_extract_detail()` 全部進 `attributes` → 明文 JSON 存 `cop_entities`；`cop_hub` broadcast 傳給所有訂閱者 | PII 洩漏（Information Disclosure） | **原建議「9-line 分流 `medical_records`」評估後不採** — P2-09（[#137](https://github.com/winson3QQ/ICS_COMMAND/pull/137)，設計 B）釐清 **9-line 是聚合後送態勢（傷亡數 by precedence / 位置 / 通訊），非個別病患 PII（無姓名病史）** → 進 `attributes` 隨 COP 廣播、指揮部透明可見，符合單一 COP 不開新表紅線。<br>**殘餘風險（仍待）**：(1) 其他 TAK `attributes` 的明文邊界 → **P1-12c SQLCipher**（at-rest）；(2) TAK ingest path 無 server-side XSS escape → **[#136](https://github.com/winson3QQ/ICS_COMMAND/issues/136)**（P2-12 panel 連動）。9-line 本身的個資疑慮已由「態勢非個資」釐清消解 |

### 使用者操作安全風險（合法操作者誤操作）

> 以下五項不需攻擊者，普通 sysadmin 在壓力下誤操作即可觸發。分級以「系統損害」為主軸。代號 `OP-x` 為本次審查操作安全專項。

| 代號 | 事實 | 性質 / 推論 | 動工時機（觸發）+ 怎麼動 |
|---|---|---|---|
| **OP-1** ✅ **已修（[#153](https://github.com/winson3QQ/ICS_COMMAND/issues/153)，2026-06-07）** | `account_repo.suspend_all_accounts()` SQL：`WHERE status='active'` 無排除發起者本人；執行後零 active 帳號，系統進入「需主機 shell 直操 DB 才能解救」狀態 | 自鎖風險（Denial of Access，操作失誤）；`POST /api/admin/accounts/suspend-all` 一次觸發不可逆 | **已修**：SQL 加 `AND username != ?`（排除發起者）；端點強制 body `confirm:"SUSPEND_ALL"`（422）；測試「sysadmin 不被自鎖 + 確認字串強制」。與 RT-H1/L4 同一 hardening PR |
| **OP-2** | `POST /api/admin/reset-db` / `reset-exercise` 後端接受空 body，無確認欄位；前端若只靠 JS `confirm()` dialog，一次誤點觸發不可逆清除；`reset-db` 無 L3 防呆 backup（P1-12b 尚未完成） | 不可逆破壞（演習中觸發 = 全毀）；需要 sysadmin session，但誤操作門檻低 | **P1-12b（L3 防呆 hook）同批**。**怎麼動**：後端強制 body 帶 `confirm: "RESET"` 欄位（422 強制，不依賴前端 dialog）；P1-12b L3 hook 完成後自動備份再清 |
| **OP-3** | `IDLE_TIMEOUT=900s`（15 分）；`WARNING_THRESHOLD_SECONDS=120` 已存於 `core/config.py:42` 但前端尚未實作倒數 banner；session 到期後若前端靜默回 401 → 操作員誤以為資料已送出 | UX 可靠性（資料遺失風險）；壓力演習中影響最大 | P1-14 follow-up。**怎麼動**：前端於 `WARNING_THRESHOLD_SECONDS=120` 時彈 session 警示 banner（config 已備，補 UI 即可）；全域 fetch error handler 攔 401 明確提示「已登出，資料**未**送出」 |
| **OP-4** | `GET /api/admin/backups/{name}/restore-cmd` 回傳完整 CLI 還原指令（含路徑），為「系統關機後離線執行」設計；系統**運行中**執行 `cp` 覆蓋熱 DB → SQLite WAL 不一致 / 損壞；endpoint 無任何「需先停服務」警示 | 資料損壞風險（操作情境誤解）；P1-12b restore GUI 完成後此端點應廢棄 | **P1-12b（restore GUI）同批**。**怎麼動**：response 加 `warning: "stop service before running this command"` 欄位；P1-12b 完成後廢棄此端點（GUI restore 取代）|
| **OP-5** ✅ **已緩解（reality check [#153](https://github.com/winson3QQ/ICS_COMMAND/issues/153)，2026-06-07，不動 code）** | `ensure_default_admin(default_pin="1234")` 舊函式仍在 `account_repo.py:226`；原述「無強制換 PIN 流程」 | 預設弱憑證留存（Spoofing） | **reality check：風險已被既有機制消解，不需再動。** ① production 開機走 `ensure_initial_admin_token`（**隨機 PIN**，非 1234，`main.py:113`）；② `first_run_gate_middleware`（`main.py:159`）對 `is_default_pin=1` 帳號**擋全部 `/api/*`→423**，僅放行登入/改PIN/health → **強制改 PIN、不得繞過 API**（即原「怎麼動」要求，**已實作**）。③ `ensure_default_admin` 為**測試 fixture 命脈**（conftest 建 `admin/1234` 供全套測試登入），**production 零 caller** → 刪除會打掉全套測試、零 production 效益 → **保留**。原始擔憂（預設弱憑證留存）不成立 |

### ⚠️「補完又產生別的」三陷阱（修補前必讀）

1. **RT-M3 別擴張 regex**：把 `"` `'` 加進 blocklist 會**誤殺合法輸入**（人名 `O'Brien`、座標/label 含引號 → 全 422）。XSS 正解 = sink 改 `textContent`，不是擴張輸入過濾。
2. **RT-M4 別收緊成 /32**：現場 iPad 同網段換 IP 會被**誤踢登出**。設計取捨要保留。
3. **RT-H1/L4 別夾帶進 feature PR**：這兩個與任何 feature item 無關，硬塞進 P2 PR 違反 PROCESS.md 一 task 一 PR 紀律（這本身就是管理面的「補一個生一個」）→ 開**獨立 hardening issue/PR**。

---

## 安全執行計劃（2026-06-08，dogfood 安全策略對話衍生）

> 待辦 security work 的**排程 / 並行計劃**，與〈紅隊審查發現待辦〉（點狀 findings）、`threat_model.md`（分析）互補。RT-* 多已修（#153）或刻意取捨 / 休眠 → 不在此（見該節）。

### 待辦項（已排除「接受的殘餘」）
| 項目 | 類型 | 撞 TAK 熱區？ | 依賴 / 對接 |
|---|---|---|---|
| **P1-12** key 管理 + LUKS/SQLCipher + backup（+ §8.4 `disk-v1`）| 後端 + ops | ❌ 零撞 | FIDO2 token（真驗收）；部署 at-rest 策略 |
| **#136** TAK ingest XSS | code | render 在新 panel | **併 P2-12**（panel `textContent` = 主防線；勿 escape-at-ingest，見 [#136 comment](https://github.com/winson3QQ/ICS_COMMAND/issues/136)）|
| **§8.5 cert 撤銷 + TAK 硬化** | ops / SOP | ❌ 不碰 code | step-ca、P2-15 |
| **PII retention**（track 90 天 TTL，缺口 #13）| 後端 | 🟡 碰 track 寫入 | 可併 P2-20 |
| **TAK-D** `visible_to`/`access` 映射 | code | — | 併 P2-12 |
| **Audit 簽章 key**（HKDF `child[2]` 預留）| 後端 | ❌ | P1-12 之後 |

### 並行軸（依能否與 active TAK sessions（P2-13/14/#161）並行排）
- **🟢 軸 1 — P1-12（現可開，零熱區撞，丟專屬後端 session）**：`12a`（key 基建 + `disk-v1` child，mock/env 全綠）→ `12b`（backup 加密 + GUI，掛既有演習面板）→ `12c`（SQLCipher）。前置：① 訂部署 at-rest 策略（threat_model §8.4）② FIDO2 token 採購（真驗收用，mock 先行）。
- **🟢 軸 2 — 部署硬化（現可開，純 ops/config，不碰 app code）**：TAK PG 強密碼（汰 `takdevpass123`）+ 只綁 localhost；§8.5 step-ca CRL/OCSP + 被擄裝置撤銷 SOP；部署目標機 LUKS 整碟。可與任何 code session 並行。
- **🟡 序列 — #136**：併 P2-12（panel `textContent`）。修完連帶解 **TAK-D**（`visible_to`）+ COP poison 縱深（來源標註 / 異常偵測，→ P2-12/P2-19）。
- **⚪ 後段（依賴前項）**：PII TTL（併 P2-20）→ Audit 簽章（P1-12 後，用 key 階層 `child[2]`）。

### 建議起手
**現開軸 1（P1-12）+ 軸 2（部署硬化）並行**——皆在 TAK 熱區外、與 P2-13/14/#161 零撞。**#136 / TAK-D / poison 縱深隨 P2-12**（#136 框架已釘，不會修錯層）。

---

## Phase 之後（未規劃，意見區）

- Wave 6 時間軸回放 UI（COP 快照已在 P2 預埋）
- Wave 7+：**Medical / Shelter PWA 重新對接**——P1-04 保留的 `pi_*_repo` + `sync_repo` federation 介面可直接承接，無需架構翻修。⚠️ **啟用 `server/` Node relay 前必先解 RT-C1/H2/L3**（見〈紅隊審查發現待辦〉）：現休眠所以無害，一通電 `HMAC_SECRET` WS 明文下發即成現役破口
- TAK Federation 大網部署（跨機關互通）
- 多上游節點中樞：ICS_Command 同時對接多個 Pi 站台 / 友軍 TAK Server / 多個 WaveInk 錄音站

---

## 與 ICS_DMAS 的關係

- 共用元件（`server/`、`command-dashboard/`）的修正若對 ICS_DMAS 也適用，應評估回饋上游 PR
- ICS_DMAS 仍維持三組件完整架構（shelter / medical / command），本 repo 為 Command 單體交付線

---

> **本文件為 Stage 2 起草版**，Phase 細項在實作開始前可能依據演練回饋與 WaveInk Phase 進度調整。每次調整以 PR 形式修訂並在 commit message 標示 `docs(roadmap):`。
