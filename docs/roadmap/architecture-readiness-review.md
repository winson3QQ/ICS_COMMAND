# ICS_Command 架構與運維就緒度評論

> **這份是什麼**：以「民防工作者 + 軍勤支援」操作視角，對 ICS_Command 現況做的 code review **評論（critique）**，非設計 SoT、非工作清單。
> 兩大部分：**Part A 運維就緒度**（到了現場會不會死：斷網 / 下錯令 / 裝置被擄 / 陣地棄守 / 夜班單人）、**Part B marker/event 聚合架構**（資料模型脊椎的四個結構性偏差）。
> 設計 SoT 仍是 [`tak-integration-strategy.md`](tak-integration-strategy.md)、[`../design/cop-marker-event-decoupling.md`](../design/cop-marker-event-decoupling.md)、[`../compliance/threat_model.md`](../compliance/threat_model.md)；工作清單是 [`../ROADMAP.md`](../ROADMAP.md)。
> **立場**：作者觀點，非已拍板決策。code 發現分「已抽驗（事實）」與「待查（推論）」兩級標明，不混用。
> **建立**：2026-06-10（session `claude/new-session-cadiuy`，code review 後）。

---

## 0. 總評（一句）

**這套系統答對了民防最難的題：東西壞掉時還能不能用。** 三階降階（TAK 掛了退回原生 COP）、server 權威演習隔離、離線 PMTiles 底圖、audit hash chain——不是裝飾，是寫進 code 與 DoD 的紀律；threat model 會誠實寫下對自己不利的限制（這在投標文件罕見、在實戰系統救命）。後端工程品質足以信任。

**剩下的弱點不是「工程不會做」，而是兩個一致的偏差**：
- **（Part A）優先序偏功能軸**——民防值勤的風險集中在邊界情境（斷網過天 / 令下錯 / 裝置丟 / 陣地棄守 / 夜班單人），這五個目前都「文件知道、code 還沒接住」。
- **（Part B）架構把「能重組」做到位**——但「幫你看出該怎麼組 / 記住你何時看出來 / 用什麼語言描述」三件留白，而那才是緣起（協同攻擊打地鼠）真正需要的。

---
---

# Part A — 運維就緒度（會不會死在現場）

## A1. 站得住的設計（為何值得信任）

1. **「TAK 只是來源之一」紅線**（`tak-integration-strategy.md` §1）：TAK 降為 `cop_service` 正規化層其中一個 source，強制每 P2 item 宣告 fallback + 斷線負向測試。降階是設計、不是救火。
2. **演習 / 實戰 server 權威隔離**（threat_model TAK-C、`exercise_service.resolve_scope`）：不信 ATAK `opex` 自宣告，擋「演習注入污染實戰 COP」。紅隊建議讀 opex 被評估後**明確不採並記錄原因**。
3. **指揮責任鏈**：decisions 表 + audit hash chain（NIST AU-9(3)）+ 每筆寫入帶 account_id。事後究責與 AAR 有完整性**偵測**基礎。<br>⚠ 誠實界定（對齊 `threat_model.md` §157「偵測，非預防」，#372/#348-F3）：hash chain 是**竄改偵測**機制、非防竄改——目前純 SHA-256（**未 keyed**），擋意外損毀＋天真竄改（改列沒補鏈），但**擋不住有 DB 寫權者改列並重算下游 `hash_prev`**；需 keyed HMAC + key off-box 才抗此（延實機，#226/#372-B）。驗證器經 `GET /api/admin/audit-chain/verify`（sysadmin）＋開機 log 接上 runtime（#372 前：runtime 零 caller）。
4. **後端工程品質紮實**（抽驗一致）：全 parameterized query、exercise mutex 原子 UPDATE 防 TOCTOU、CoT 內容層白名單（座標 / callsign / type prefix）、TAK ingest 全域 token bucket、backup atomic write + SHA-256 + Fernet。

## A2. 到了現場會痛的地方（按情境）

### A. 斷網 48 小時——資料會靜默消失 ⚠️ 高
- **事實**：`server/sync.js:275` `DELETE FROM push_queue WHERE pushed_at < cutoff` **無 `sent=1` 條件**，超過 `MAX_QUEUE_AGE`（24h）的**未送出**佇列一律清掉，無 audit、無告警。
- **判斷**：24h 是「辦公室網路」假設，非「災區網路」假設（颱風 / 海纜 / 戰時斷網常超 24h）。Pi 撐過 48h 重連後，前 24h 回報已沒、且沒人會知道（silent loss）。
- **建議**：清除時記 audit + 計數告警；或斷線期間凍結老化計時。

### B. 下錯令收不回 ⚠️ 高
- **事實**：`tak_downlink.py:169-172` 註解自承（真機實證）CoT `t-x-d-d` 在 TAK 持久層無效、Marti REST 無單顆 CoT DELETE。可靠刪除要等 P2-14 Mission/DataSync。
- **判斷**：誤推一個 MEDEVAC 集結點，全網 ATAK 顯示它到 stale 過期。是架構限制，必須被操作程序吸收。
- **建議**：P2-14 落地前，下行（P2-13）UI 強制二次確認 + 短 stale 預設；SOP 明文「誤令更正 = 無線電口頭」。目前無此 SOP（compliance incident response 仍 0.1 草稿）。

### C. 裝置被擄（軍勤最現實威脅）⚠️ 高
- **事實**：threat_model §8.5 自診——現行 cert 由 `makeCert.sh` 離線簽發，**TAK GUI 撤銷不到**，無 CRL/OCSP、無「裝置遺失→撤銷」SOP。被擄 ATAK 在 cert 有效期內可注假敵我位置、讀真友軍位置（§8.3）。
- **判斷**：文件已自知，但 ROADMAP 無對應高優先 item（散在 P2-15 / step-ca 註記）。對軍勤場景，**這比多數功能 item 優先**——功能少一個是不便，撤不掉是反情報破口。
- **建議**：升格獨立 item——場端 cert 改走 TAK enrollment（:8446）+ 撤銷 SOP 演練。

### D. 指揮所主機被端 / 需棄守 ⚠️ 中高
- **事實**：P1-12（FIDO2 + SQLCipher + LUKS）pending；§8.4 承認 mTLS 私鑰、step-ca CA key 現在**明文落地**。撤離拔走的硬碟 = COP + 人員位置史 + 「冒充 ICS 對 TAK 注入」的鑰匙一起送人。
- **判斷**：threat_model 結論（LUKS 為主控、FIDO2 統一 unlock）方向正確，但「動工前先訂 at-rest 策略」已停在文件裡。
- **建議**：LUKS 部分（一行 cryptsetup）從 P1-12 拆出**先行**，不必等完整 FIDO2 金鑰階層。「硬碟不明文」不需要等完美架構。

### E. 夜班指揮所只有一個人盯螢幕 ⚠️ 中
- **事實**：`tak-integration-strategy.md` §4 自承主動告警**未建**，降階是「退回被動視覺靠人盯」。另：`IDLE_TIMEOUT=900s`（15min）；前端錯誤處理用 `alert()`、無斷線自動退避、樂觀更新失敗不回滾（`events.js:478-526`）。
- **判斷**：把「人盯」這個最不可靠元件留在關鍵路徑。15min idle 會逼出「共用帳號 + 貼牆 PIN」的真實繞過；壓力下操作員按掉 alert 就以為事件已建立 → AAR 上的「系統顯示已回報但指揮部沒收到」。
- **建議**：主動告警往前排（見 A4）；值勤模式差異化 timeout；前端補統一 API client（重試 / 退避 / 回滾）。

## A3. code 層發現（事實 vs 待查，分開）

### 已抽驗（事實）
| 發現 | 位置 | 嚴重度（校正後）|
|---|---|---|
| push_queue 無條件刪未送佇列 | `server/sync.js:275` | **高**（民防斷網常 >24h、silent）|
| CAS 重試耗盡丟較新事件 | `cop_service.py:435-464` | **中低**——code 自承、有 log（#108），前有單 uid 1s 節流 + 全域 60/s token bucket；探查回報「10Hz 丟 70%」為高估 |
| HMAC 不簽 `type` 欄 | `server/ws_signing.js:59` | **中低**——記錄過的設計（type whitelist 另一層）；利用需先破 WSS，但「簽章不覆蓋路由欄」原則不乾淨，下次動 ws_signing 順手收 |

### 待查（推論，未逐一驗證，不當結論用）
- CoT 無時區時間戳被當 UTC 的歧義（`tak_service.py:45-63`）
- nonce 檢查在 HMAC 之後的順序（`trusted_ingest.py`）→ replay DoS 放大面
- `tak_rest_client` session 生命週期 / pool 洩漏
- `_tak_status` 模組級 dict 無鎖並發

### 測試 / CI 缺口（事實）
- **前端 ~3,700 行 vitest 測試，CI 只跑 pytest，JS 測試完全沒進 CI** → 前端測試紀律是自願的。以「缺測不算完成」DoD，這是自家規則的執行漏洞、且最便宜可補。
- **「Pi 斷線→指揮部獨立運作→重連自動同步」無 integration test** → 降階是設計信仰，信仰要有負向測試背書（strategy §6 對 TAK item 已如此要求，同標準應回套 Pi 鏈路）。

### 供應鏈紅線執行落差（事實）
- CLAUDE.md 有「禁中國供應鏈」紅線，但 npm/pip 只版本 pin、無 lockfile hash 稽核 / 來源檢查；底圖只驗 SHA256 無簽章。政策有了、enforcement 未跟上。

## A4. 主動告警：依據什麼判斷（calc_engine 延伸，非另起爐灶）

> 源於「主動告警依據時間嗎」的釐清。**事實是門檻判斷已存在、只是在另一條路徑上。**

- **事實**：`calc_engine.py` 已是門檻引擎（規格 6.5），但吃 **pi-node 快照**（醫療/收容/前進/安全組節點狀態），不碰 `cop_entities`。判斷依據 `DEFAULT_THRESHOLDS`：**比例**（bed_usage 0.70/0.90、supply 0.30/0.10）+ **數量**（casualties 1/2、waiting 3/6）+ **時間**（freshness_warn 5min / crit 15min）。輸出 RAG 顏色點 `status-lamp`。
- **兩個結構限制**：① 只看節點快照、不看地圖空間（不碰 lat/lon/speed_mps/tracks）；② 輸出顏色、不主動往 `待裁示` 佇列塞卡。
- **「依據什麼判斷」分四類**（按系統手上有沒有資料）：

| 依據 | 系統有資料？ | 現狀 |
|---|---|---|
| 時間 / freshness（沉默多久＝失聯）| ✅ `stale` + calc_engine | 節點有、COP 沒接告警 |
| 數量 / 比例（傷亡 / 床位% / critical 數）| ✅ events.severity + 快照 | 節點有、跨事件聚合沒有 |
| 空間 / geofence（進出管制區 / 接近 / 衝向設施）| ✅ lat/lon + 已畫 polygon | **完全沒判斷邏輯** |
| 運動 / 異常（速度突變 / 位置跳變 / 朝威脅移動）| ✅ speed_mps/heading + tracks | 欄位有、從沒拿來判斷 |

- **判斷**：時間是必要但**最弱**那軸（只說「失聯」，說不出「危險」）。真正有決策價值的是空間 + 數量 + 運動**複合**，而那三類欄位都有資料、就缺規則引擎。**缺的不是一個告警功能，是「把 calc_engine 門檻模式從節點快照延伸到 COP 空間 / 運動軸」。**
- **沒做的真正阻力（推論）**：① **alert fatigue**——門檻訂錯整個功能變負資產；② **server 不臆測 doctrine**——與「系統判定這是不是事件」有張力。故最該先想清楚的不是 code，是**門檻訂多少、誰能調、響了塞不塞進待裁示佇列**。

---
---

# Part B — marker / event N:1 聚合架構：四個結構性偏差

> 設計 SoT：[`../design/cop-marker-event-decoupling.md`](../design/cop-marker-event-decoupling.md)。本部不複述設計，只提判斷與異議。

**骨架認可**：marker-first 原子化 + junction 可重組 + 觀察/事件兩軸概念，是有洞見的設計。下列四點按「會吵架的程度」排序。

## B1. ROADMAP 優先序排反：「輔助聚合」才是北極星，人工 triage 是退路
- **觀察**：緣起（decoupling §0）痛點是協同攻擊時指揮官「**窮於應付一個個孤立事件、打地鼠**」。但主機制（§2 triage 三態 / §4 多選聚合）要求**已過載的指揮官對每顆 marker 手動判級再連結**。
- **論據**：主流程全手動；真正兌現緣起的「系統提示時空相近、人確認」（§4 輔助 / §5 終局）標為**終局、未做**；P2-33c 把即時聚合列脊椎在做、輔助往後排。
- **判斷**：你最需要看見協同全貌的那一刻（第一波、過載），正是最沒腦力手動連線的一刻。手動 triage 解「AAR 能重組」、**解不了北極星「live 過載」**。**輔助聚合不是 polish，它就是這整個解耦的存在理由；手動 triage 才是 fallback。**
- **建議**：輔助聚合（候選提示 + 一鍵確認）從終局前移、與 P2-33c 綁同一里程碑。

## B2. 「時空相近」是對的 natural-hazard 訊號、錯的 adversary 訊號
- **觀察**：緣起講協同攻擊（會思考的對手），而輔助聚合依據是「時空相近」。
- **論據**：會思考的對手**刻意分散時空避免看起來協同**（欺敵基本動作）。proximity 為主訊號 →（a）漏掉刻意散開的真協同；（b）誤聚剛好湊一起的無關事件；（c）對手用佯動餵假「相近群」釣注意力。對照：野火飛火 / 地震連續倒塌**物理上本來群聚**，proximity 適用；但 security/軍事這條真正指示「同一隻手」的是**模式**（接近向量 / callsign 類 / TTP 簽章）非距離。
- **判斷**：架構用對「可重組」骨架，但**若輔助聚合核心是 proximity，會在最該發揮的 security 情境上最弱**。proximity 當候選排序可以、當協同偵測不行。
- **建議**：訊號**分情境**——natural hazard 用時空鄰近；security/軍事用關係/模式特徵，相近降為候選排序維度。與 A4「空間告警」是**同一個『依據什麼判斷』核心問題**。

## B3. re-parent 會親手毀掉 AAR 最值錢的那塊證據
- **觀察**：緣起另一半是「只有事後 AAR 才窺見全貌」。AAR 最值錢一格 = **指揮官從『以為各自獨立』翻轉成『同一波』的那一刻**——那個 re-group 決定本身就是情報，是「我們多久才看穿協同」的唯一度量。
- **論據**：§2 case C 的 re-parent「只動 junction」；junction（`database.py:_m021_event_markers`）PK=`(event_id, cop_entity_uid)`，re-parent 實作 = **delete 舊 + insert 新**；表只有 `created_at`，**舊歸屬 / 翻轉時刻 / 翻轉的人沒了**。
- **判斷**：**讓 live 重組變可能的 re-parent，同時在抹掉 AAR 最該保留的認知時間軸。** 即時聚合與 AAR 全貌在此打架，schema 站在即時那邊、犧牲復盤。「我們花 12 分鐘才看穿協同」這句結論在現行 schema 下**永遠重建不出來**。
- **建議**：junction 不能只有 `created_at`、re-parent 不能 delete+insert。擇一：append-only 記 `unlinked_at`/`reparented_from`；或把歸屬變更打進 audit hash chain（`core/audit_chain.py` 已有基礎設施）。餵 P2-20 AAR 回放。

## B4. 兩軸分離是對的，但餵它的是同一桶詞彙，所以軸會在最關鍵時塌掉
- **觀察**：decoupling §6「事件分類自成一格、不繼承 marker 觀察類型」概念上非常對——這設計最聰明的一刀。
- **論據**（2026-06-10 code 查證）：marker 觀察類型 taxonomy `static/js/map.js` 共 22 種；event 事件分類 taxonomy 存在（NAPSG_EVENTS/GROUPS，P1-10d，`services/event_taxonomy_store.py`，可編輯）**但與 marker 同源 NAPSG 池**；搜不到「周界滲透」「協同攻擊」這類**大事件層級**分類詞。
- **判斷**：結構上兩軸、**詞彙上一軸**。指揮官給 N:1 大事件命名時，下拉只有 marker 等級類型，只能**拿其中一顆 marker 類型命名大事件**——正是 §6 明令避免的「event 繼承 marker type」。**軸在最需要它的時候（命名協同事件）塌回 1:1。**
- **建議**：兩軸分離需配一套**獨立的大事件 / 作戰樣式詞彙**（周界滲透 / 協同攻擊 / 佯動牽制…），否則只是 schema 兩欄、操作上一軸。這是把**指揮官戰術語言**編碼進系統，難度與價值遠高於 marker 22 種觀察類型，需獨立評估（doctrine 來源）。

---
---

## 優先序總表（若以值勤者立場排）

| # | 動向 | 出處 | 性質 |
|---|---|---|---|
| 1 | `sync.js:275` 加 `sent=1`/告警；CI 加 `npm test` | A2-A / A3 | 立刻、低成本 |
| 2 | 下行（P2-13）二次確認 + 誤令更正 SOP | A2-B | P2-13 落地前 |
| 3 | 憑證撤銷升獨立 item；LUKS 從 P1-12 拆出先行 | A2-C / A2-D | 升格 / 拆分 |
| 4 | 主動告警 / 決策觸發前移（calc_engine 延伸 COP 空間軸）| A2-E / A4 | 重排 |
| 5 | Pi 斷線重連端到端 integration test | A3 | 補測 |
| 6 | 輔助聚合前移、綁 P2-33c 同里程碑 | B1 | 重排 |
| 7 | 聚合訊號分情境（相近降為候選維度）| B2 | 設計修正 |
| 8 | junction append-only / 入 audit chain（保認知時間軸）| B3 | schema 修正 |
| 9 | 獨立大事件 / 作戰樣式詞彙 | B4 | 需拍板 |

> **兩條共同主軸**：
> - **Part A**：優先序偏功能軸，民防風險集中在邊界情境（斷網過天 / 令下錯 / 裝置丟 / 陣地棄守 / 夜班單人）。接住這五個 > 再推一格 TAK 能力矩陣。
> - **Part B**：架構把「能重組」做到位，把「幫你看出該怎麼組（B1,B2）／記住你何時看出來（B3）／用什麼語言描述（B4）」留白——而那才是緣起那個故事的解答。能重組是必要條件，不是解答。
