# TAK / COP 使用情境深挖 ↔ 四層配置

> 每個情境深挖：**作業案例 → 痛點/需求邏輯 → TAK/ICS 怎麼用（四層+為什麼）→ 效率提升 → 注意/失效模式**。
> 目的：把「為什麼這情境要用這功能」的**作業邏輯**講清楚，讓個人/團隊在該情境下更有效率；並讓 ROADMAP item 回溯「為哪個情境而做」。
> 衍生自 2026-06 真機 dogfood + admin GUI 實證 + CONOPS 對話。**基礎 → 進階**排序，逐批深挖（一次 2 個）。

## ⚠️ 可信度標註（不亂掰）
- **作業案例**：ICS / C2 / 應變的**既有實務**（領域知識，非發明）。
- **TAK/ICS 怎麼用**：多為**從功能用途推導的邏輯**（非官方 CONOPS——那種權威「情境→配置食譜」基本不存在）；**屬合理推論，動工前仍應對 `TAK_Server_Configuration_Guide.pdf` / 實機驗證**。
- **✅ 實證** = dogfood 真機 / admin GUI 截圖 / ICS code；**❓ 未查證** = 需對 ATAK/iTAK 文件或實機確認，本文不編造（尤其 **iTAK 精確按鈕路徑大多 ❓**）。

## 四層定義
| 層 | 是什麼 | 配置面 |
|---|---|---|
| **① Client (ATAK/iTAK)** | 操作員在 app 的動作；server 直通轉發 | 發包(group/cert) + 操作 |
| **② 協定/資料** | CoT type / MIL-STD-2525 / 9-line | 格式，不用配 |
| **③ TAK Server** | admin GUI / CoreConfig.xml | **真正的 server 設定** |
| **④ ICS Dashboard** | ICS normalize/渲染/COP（我們的 code） | ROADMAP P1/P2 |

## ③ TAK Server admin GUI 菜單（✅ 2026-06-08 實證，引用基準）
進入 `https://<host>:8443/` → 接受 Distribution Statement → 需 **admin client cert**。
- **Data**：Cot Query｜File Manager｜Send Mission Package｜Video Feed Manager｜**Mission (COP) Manager**｜ExCheck
- **Situation Awareness**：Export Mission｜KML SA Feed｜WebTAK
- **Configuration**：**Inputs and Data Feeds**｜**Federation**（現 DISABLED）｜Federate Certificate Authorities｜**Injectors**｜Security and Authentication
- **Administrative**：Database｜**Data Retention**（TTL 全空/排程 Never）｜Manage Users｜**Client Certificates**（Revoke 只管 enrollment cert）｜Tokens｜Device Logs｜**Device Profiles**｜File Config｜VBM Configuration
- **Monitoring**：Alarms｜Metrics Dashboard｜Client Dashboard

## 通則
- **動作型情境**：③ TAK Server 多為**零/極少設定**（:8089 通 + cert/group 對即直通）；重心在 ① 發包 + ④ ICS。
- **③ 設定吃重**集中在：Federation / Mission(DataSync) / Inputs and Data Feeds / Video / ExCheck / Data Retention / Groups —— 對到 ROADMAP 未做 P2 item。

---

# 深挖順序（**功能群**，群內基礎 → 進階）

> 按**功能**分群（感知=怎麼收集態勢、動作=怎麼應變、指揮=怎麼協同），非按應變層級。群內再基礎→進階。

| 功能群 | 情境 | 狀態 |
|---|---|---|
| **基礎** | 友軍即時定位 (BFT) | ✅ |
| **基礎** | 共同作戰圖 (Shared COP) | ✅ |
| **感知/偵查（收集態勢）** | 人員目標偵查與回報 (Recon) | ✅ 本批 |
| **感知/偵查** | 無人機 / 空中感知 (UAV ISR) | ✅ 本批 |
| **感知/偵查** | 多影像情資 (IMINT) | ✅ 本批 |
| **感知/偵查** | SDR/RF 感測 (→ WaveInk/P3) | ✅ 本批 |
| **計劃/決策** | 軍用計劃流程（MDMP/METT-TC，含 COA 標繪/Tasking/下達 + **誠實缺口**：天氣/地形分析/兵推/OPORD/同步矩陣）| ✅ 本批 |
| **計劃驗證** | 桌上演習 (TTX) + AAR 回放（計劃→TTX→行動；ICS 強項 P2-19~22）| ✅ 本批 |
| **動作/應變** | 都市地震搜救 (SAR / USAR) | ✅ 本批 |
| **動作/應變** | 颱風/水災疏散收容 | 待挖 |
| **動作/應變** | 野火延燒應變 | 待挖 |
| **動作/應變** | 危險物質 (HazMat) 洩漏 | 待挖 |
| **動作/應變** | 重大傷亡後送 (MCI/MEDEVAC) | 待挖 |
| **動作/應變** | 關鍵設施巡邏監控 | 待挖 |
| **高威脅/多組織/訓練** | RTF 武裝掩護搜救 | 待挖（有對話內容） |
| **高威脅/多組織/訓練** | 多機構聯合災害指揮 | 待挖 |

> 註：**無人機/空中感知**為本輪新增的獨立感知情境（原先誤併入野火/巡邏）。

---

# 階 0：基礎

## 友軍 / 人員即時定位（Blue Force Tracking, BFT）

### 作業案例（實況）
一支隊伍散在區域裡（人員、車輛）。沒有 BFT 時，指揮要知道誰在哪只能靠**無線電點名**（「A 組你位置？」）。問題鏈：點名**佔線**（災害/戰術現場無線電是瓶頸）；口述座標**易錯、一問完就過時**（人在動）；指揮**無即時空間圖** → 新任務不知「誰最近」、重複派遣、把人派進危險、**友軍誤擊**（RTF/戰時尤甚）。

### 痛點 → 需求邏輯
指揮需要：**連續、自動、準確的全員位置，不必開口問**。效率關鍵 = **消除「你在哪」的無線電流量** + **即時就近調度**。

### TAK/ICS 怎麼用（四層 + 為什麼）
- **① Client**：iTAK 連上即**自動廣播自身 GPS（PLI）**（✅ dogfood 見 `曙豐-3QQ` 每分鐘）。*為什麼有效*：自動 = 零操作員負擔、零無線電佔線，直解「點名佔線」根痛。
- **② 協定**：`a-f-*` + `<track>`，2525 **友軍藍框**。*為什麼*：敵我框讓指揮**一眼分出自己人**（RTF 是生死）。
- **③ TAK Server**：幾乎零設定；多隊用 **Groups（Manage Users）** 分流。*為什麼*（推論）：大行動不該讓每人圖上塞滿所有單位 → 按角色 scope，看到的才相關。
- **④ ICS**：dashboard COP 渲染（✅ P2-02~05）；**敵我/隊伍篩選器（P2-25）**讓指揮只看自己隊；**活追蹤過 stale 變灰 = 失聯警示**。

### 效率提升
- 砍掉「點名/回報位置」無線電 → **頻道留給真正指令**（最大效率點）。
- **就近派遣**：事件跳出 → 看圖派最近的人，不用問。
- **失聯偵測**：變灰 = 這隊掉了 → 主動關注（安全）。
- **降友軍誤擊**：看得到自己人位置。

### 注意 / 失效模式
依賴 GPS + 通聯；變灰 = 不確定（非確定離線）；**OPSEC**——位置會被截收（戰時/RTF）→ 連回 mTLS 加密 + COP poisoning 顧慮（假位置→誤判）。

## 共同作戰圖（Shared COP）

### 作業案例（實況）
事件事實一直變：危險區、封路、避難所、集結點、目標。沒有共享圖時，每人/每隊各有腦中/紙本地圖 → **發散**。問題鏈：甲標了危險點，**乙被口頭告知前不知道**→走進去；指揮與現場**看的不是同一張圖**→決策衝突、反覆 re-brief；換班/增援**沒當前圖**→重新口述交接，慢且漏。

### 痛點 → 需求邏輯
需求：**所有人（現場+指揮）看同一張當前圖，更新自動傳所有人，免反覆口頭同步**。效率關鍵 = **共享態勢免重複口述**。

### TAK/ICS 怎麼用（四層 + 為什麼）
- **① Client**：operator 在 iTAK 就地畫區/放標記（✅ dogfood 送 `u-d-f`/`u-d-r`/`u-d-c-c`）。*為什麼*：現場就地標，比回指揮所畫快、貼合實況。
- **② 協定**：`u-d-*` + 形狀 + **顏色/符號語意**（紅=危險）。*為什麼*：符號讓人一眼讀懂，免文字解釋。
- **③ TAK Server**：**串流 = 即時但短暫共享**（快，但刪除/resync 不可靠）；**Mission/DataSync = 持久權威 COP**（耐久共享疊層的正解）。*邏輯*：即時 SA 用串流、耐久共享用 mission。
- **④ ICS**：dashboard 為指揮端權威視圖；`cop_entities` + WS 廣播即時同步所有觀看者；**生命週期（archive/stale/可靠刪除）直接決定 COP 可不可信**。

### 效率提升
- **標一次→全員看到**：免重複 brief（核心效率點）。
- 指揮+現場**同圖** → 決策對齊不衝突。
- 增援/換班**秒接當前圖** → 交接效率。

### 注意 / 失效模式（**本情境暴露核心 backlog**）
- **COP 完整性放大**：一個錯標**誤導所有人**（blast radius 比個人圖大）。
- **可靠刪除/resync 缺口（#161/#173）直接侵蝕本情境效率**：刪不乾淨、重連漏靜態標記 → 共享的是錯/舊圖，**比沒有更糟** → **這就是 P2-14 對本情境為何是必需，非 nice-to-have**。

---

# 群：感知 / 偵查（收集態勢）

## 人員目標偵查與回報（Recon & Report）

### 作業案例（實況）
偵查員定位觀察一個目標/區域（建物、路口、疑似敵陣地、進不去的災點）：盯著看、回報所見——敵情活動、數量、狀態變化、地形、危險。沒有好工具時，**口述回報**（「北側入口附近 3 人」）→ 指揮要**腦中放到地圖上**、轉錄、且**講完即逝**；偵查員自身位置/視線方向不明；回報**不會累積成圖**。

### 痛點 → 需求邏輯
指揮需要觀察被**精確放上地圖**（在哪）、**有時戳**、**有署名**（誰看的）、**會累積**（隨時間疊成圖，不是講完就沒）。效率關鍵 = **精確 geo-located 觀察，免口述轉錄、自動疊成情報圖**。

### TAK/ICS 怎麼用（四層 + 為什麼）
- **① Client**：偵查員在**觀察到的位置**（非自身）放標記 + 敵我屬性（敵/不明）+ 註記；GeoChat 補敘述；自身 BFT 顯示「從哪觀察」。（iTAK 放標記 ✅；精確按鈕 ❓）
- **② 協定**：`a-h-*`/`a-u-*` 觀察標記、`b-t-f` GeoChat、可把 SALUTE（員額/活動/位置/單位/時間/裝備）塞進 `<detail>` 成結構化 spot report。*為什麼*：2525 敵我色讓威脅**一眼可讀**。
- **③ TAK Server**：零設定（串流直通）；偵查回報若需限閱用 Groups。
- **④ ICS**：觀察標記落 COP 成敵/不明 entity，指揮看到 geo-located + 時戳 + 署名（source/callsign）；**敵我篩選器**可「只看敵/不明」聚焦威脅圖；**stale**：敵標是**會老化的情報**（敵可能移動）→ 過 stale 變灰 = 「最後出現於此、可能已移動」。

### 效率提升
- 口述「北側 3 人」→ 精確 geo 標記 → 指揮**零腦補**、看得精確。
- 觀察**累積成威脅圖**（vs 口述即逝）。
- 署名+時戳 → 可評估**可信度與時效**。
- 多偵查員回報**自動融合**到同一張圖。

### 注意 / 失效模式
- 觀察標是偵查員的**判斷**（可能誤判/誤識）→ COP 完整性不只防惡意，也防**誠實錯誤** → 指揮要權衡來源。
- 敵標**會老化**（freshness）——把舊標當現況 = 危險（RTF 尤甚）；stale 變灰是提示。
- OPSEC：偵查員 BFT 暴露 OP 位置（若被截收）。

## 無人機 / 空中感知（UAV / Aerial ISR）

### 作業案例（實況）
無人機/航空器提供**空中俯視**——火勢蔓延、人群流動、地形、淹水範圍、敵方移動、搜索區的即時頂視。沒整合時，無人機操作員**只在自己遙控螢幕單獨看**；指揮看不到影像、無法跟地面圖對位；無人機的覆蓋/位置不在 COP 上。**空中視角孤島化、留在操作員手上**。

### 痛點 → 需求邏輯
指揮需要**空中影像 + 無人機位置/感測覆蓋**融進共享 COP——才能把「無人機看到的」與「地面單位在哪」對位、同時指揮兩者。效率關鍵 = **空中俯視融入共同圖，不孤島**。

### TAK/ICS 怎麼用（四層 + 為什麼）
- **① Client/平台**：無人機發布自身位置（CoT）+ 影像串流；地面 client 可拉該影像。（無人機→TAK 影像管線屬平台/編碼相關 ❓）
- **② 協定**：無人機位置 = sensor/track CoT；影像 = **串流 URI**（非把原始幀塞 CoT）；感測覆蓋可畫多邊形。
- **③ TAK Server**：**`Data → Video Feed Manager`**——註冊/管理影像 feed（✅ 實證見過此選單）；feed 以 URI 引用。
- **④ ICS**：依架構決策——**影像 = reference-only URI、ICS 不 proxy 串流**（P2-16，防 SSRF）。ICS 在 COP 顯無人機位置 + feed 連結/嵌入（reference）；無人機位置為**活追蹤**（會動，stale 治理）。

### 效率提升
- 空中視角與地面 COP 融合 → 指揮用俯視**指揮地面單位**（「火越過你東邊那條路了」）。
- 多方**看同一 feed**（vs 孤島在操作員）。
- 無人機**覆蓋足跡上圖** → 指揮知道哪裡被看、哪裡盲區。

### 注意 / 失效模式
- 頻寬：影像很重 → ICS **不 proxy（URI-only）**避免成瓶頸/SSRF（= P2-16 決策）；feed 可達性看網路。
- 無人機→TAK 影像管線**平台/編碼相關**（❓ 確切設定）。
- 活追蹤 stale：遙測中斷則位置老化。

## 多影像情資（IMINT — 靜態影像產品）

### 作業案例（實況）
現場人員拍**靜態照片**——災損（倒塌、路況）、敵陣地、跡證、前後對比、傷患記錄、地圖看不出的地形細節。沒整合時，照片**留在手機**、靠口述（「橋斷了」）或另走通訊軟體分享 → **沒 geo-located 到 COP、沒綁事件圖、事後難找、無紀錄鏈**。指揮**看不到實況**，只聽得到。

### 痛點 → 需求邏輯
指揮/分析需要影像**綁地點（geo-tag）+ 掛在 COP 標記上 + 相關人取得 + 留存**（供評估/AAR/跡證）。效率關鍵 = **視覺實況綁在圖上，不孤島在裝置裡**——看到實際倒塌 vs 聽「很慘」。

### TAK/ICS 怎麼用（四層 + 為什麼）
- **① Client**：operator 在 iTAK 把照片**附到標記/mission**（geo-tag）。*為什麼*：附在地點 = 影像被放進空間，不漂浮。（iTAK 附件按鈕 ❓）
- **② 協定**：照片走 **Enterprise Sync / DataSync 檔案附件**（以 hash/URI 引用），連到 CoT 標記/mission；**非把原始 bytes 塞 CoT**（太重）。
- **③ TAK Server**：**`Data → File Manager`**（Enterprise Sync 檔案庫）+ Mission 附件。*為什麼*：server 存檔、client 按 reference 拉。（✅ 實證見過 File Manager）
- **④ ICS**：依架構——**照片 = reference-only URI、ICS 永不 follow/fetch（防 SSRF，P2-14）**。ICS 顯標記 + 影像 reference（URI），操作員從來源（TAK server）開，**ICS 不 proxy/下載**。*為什麼*：C2 dashboard 不該變影像代理（頻寬 + SSRF 風險）。

### 效率提升
- **精確地點的視覺實況**（vs 「那邊很慘」）。
- 前後對比、災損評估、跡證——geo-tag + 留存 → 餵評估/AAR。
- 多張現場照**按地點自動組織**在圖上。
- 與無人機區別：**IMINT = 靜態產品供紀錄/分析；UAV = 即時俯視 overwatch**。

### 注意 / 失效模式
- **SSRF（P2-14 明訂守則）**：ICS **絕不可 fetch 照片 URI**（惡意 URI→SSRF）→ reference-only。**這是本情境的核心安全約束**。
- 頻寬：影像大、現場上行可能慢 → 照片會延遲。
- 留存/PII：影像可能含 PII（人臉/傷患）→ TTL（缺口 #13）+ TAK Data Retention 的 `Files` policy。

## SDR/RF 感測（電磁感知 → WaveInk/P3）

### 作業案例（實況）
SDR（軟體定義無線電）感測器——或 **WaveInk**（你的 SDR 多頻 PTT + Breeze ASR 專案）——偵測 RF 發射：敵方電台（方位/強度）、干擾、異常 emitter；WaveInk 則把監聽的無線電網**轉錄成事件**。沒整合時，RF 圖**留在獨立 SDR 工具/操作員**手上；指揮看不到「emitter 在哪」；無線電截收**孤立地聽/轉錄**。**電磁態勢孤島化**。

### 痛點 → 需求邏輯
指揮需要 RF/電磁圖**融進 COP**——emitter 位置/方位上圖、無線電活動與地面/威脅圖**對位**。WaveInk 則：無線電通聯 → COP 上的結構化事件（誰在何處發什麼）。效率關鍵 = **電磁 SA 與實體 SA 融合**：靠發射偵測/定位威脅、把無線電話務與移動對位。

### TAK/ICS 怎麼用（四層 + 為什麼）
- **① Client/感測源**：SDR 裝置或 WaveInk 產生 RF 資料（emitter 位置/方位，或轉錄的無線電事件）。
- **② 協定**：RF emitter = sensor-type CoT（位置 + 方位線），或 WaveInk 自有格式 → normalize。（確切 sensor CoT type ❓）
- **③ TAK Server**：若走 TAK → **`Configuration → Inputs and Data Feeds`**（註冊 SDR/sensor feed）。*為什麼*：外部感測器 = 一個 data feed input。（✅ 實證見過此選單）
- **④ ICS**：**這就是 WaveInk = COP 第二外部來源（Phase 3）**。WaveInk → `cop_service` normalize（與 TAK **同一個 normalize 層**）→ COP；RF emitter = COP entity（sensor kind）。**目前 P3 全未做**。

### 效率提升
- 電磁 SA 與實體圖**同一張** → 靠發射**定位隱藏威脅**（有 emitter = 那裡有人）。
- WaveInk：無線電話務**自動轉錄成結構化事件**上 COP（vs 人工監聽+手記）→ 解放監聽員、全捕捉、geo-tag。
- 多 SDR 測向 → **三角定位** emitter。
- 對位：「這裡訊號爆量 + 那裡有移動」→ 融合情報。

### 注意 / 失效模式
- RF 資料**噪雜/不確定**（方位≠精確位置、誤報）→ COP 應標**低信心情報**（同偵查標記的可信度問題）。
- WaveInk 是**獨立專案**（Codeberg）→ 整合屬 P3、自有 normalize/邊界（架構：WaveInk → ingress → normalize → COP，同 TAK）。
- **中國供應鏈紅線**：SDR 硬體/函式庫須驗**非中國**（CLAUDE.md）——SDR 生態多中國廠商，特別注意。
- untrusted 輸入（同 TAK 的 ingest 驗證紀律）。

---

> **✅ 感知群完整**：四種收集模式皆深挖——**人眼（Recon）/ 空中（UAV）/ 影像（IMINT）/ 電磁（SDR/RF）**。共通模式 = 「感測/觀察 → geo-located 進 COP → 疊成情報圖」，且多為 **untrusted 外部輸入**（→ ingest 驗證 + COP 完整性 + 可信度標註）。

---

# 群：計劃 / 決策（軍用計劃流程）

## 軍用計劃流程 ↔ TAK/ICS 支援與缺口

### 作業案例（軍用計劃流程，實況）
依 **MDMP（軍事決策程序）** + **METT-TC** 因素 + **IPB（戰場情報預備）**：
收到任務 → **任務分析**（盤點 M任務 / E敵情 / **T 地形與天氣** / T 兵力 / T 時間 / C 民事）→ 發展**行動方案(COA)** → **兵棋推演**(action-reaction-counteraction) → 比較選案 → 指揮官**決心** → 產製**命令(OPORD，5 段式)** → **下達** → 執行時對比**計畫 vs 實際**。
**計劃的本質是「分析腦力 + 把方案畫出來 + 下達」**，要考慮**地形、天氣、敵情、我軍、時間、民事**。

### 痛點 → 需求邏輯
計劃需要：① 把 METT-TC 因素**可視化在同一張圖**（地形/天氣/敵情疊層）；② 畫**計畫方案**（路線/目標/管制措施，標「計畫中」）；③ **下達**給單位；④ 執行時**計畫 vs 實際**對比。效率關鍵 = **規劃→下達→對比執行都在同一張 COP 上，不脫離態勢**。

### TAK/ICS 支援 vs **缺口（誠實）**
| 計劃要素 | TAK/ICS 支援? | 說明 |
|---|---|---|
| 地形「**顯示**」 | ✅ | MapLibre 底圖 / TAK 地圖 |
| 地形「**分析**」（坡度 / 通視 LOS / 機動性 / 關鍵地形） | **❌ 缺** | ICS 無地形分析工具；ATAK 有限(range/bearing、部分版本高程/viewshed 插件 ❓)，非完整 IPB 地形分析 |
| **天氣 / 環境**（風 / 降雨 / 能見度，影響航空·NBC 擴散·火勢·淹水） | **❌ 缺** | ICS 無氣象 feed/疊層；= 外部 data feed 缺口（同 SDR 那條）|
| **敵情疊層** | 🔶 部分 | ISR 標記(感知群)可顯示；**敵 COA templating 只能手畫、無分析** |
| **COA 友軍方案標繪**（路線/目標/管制線/相位線/named area） | ✅ | 繪圖 + **`planned` 空心框**（MIL-STD-2525 戰術圖形；P2-11b 旗標 + P2-13）|
| **兵棋推演**（action-reaction-counteraction） | **❌ 缺** | 無 wargaming 引擎；P2-19 TTX 是**演習注入**非**規劃兵推** |
| **OPORD / 命令產製**（5 段式結構化命令） | **❌ 缺** | 非命令撰寫工具；Mission 可帶圖+簡述，但**非結構化 OPORD** |
| **同步矩陣 / 作戰時間軸** | **❌ 缺** | 無；P2-20 AAR 是**事後回放**非**規劃時間軸** |
| **下達（dissemination）** | ✅(規劃中) | **Mission 下行 = P2-13**（未做）|
| **計畫 vs 實際對比** | ✅(規劃中) | `planned`(空心)/`actual`(實心)視覺 = P2-13 |

### 效率提升（TAK/ICS 真正能加值的部分）
- 計畫疊層與**即時態勢同圖** → 規劃不脫離現況（不是在另一張靜態地圖上規劃）。
- `planned` 空心框**下達** → 單位行動前在自己 ATAK 上見目標/路線。
- 執行時 **planned vs actual 一眼對比** → 偏離計畫即時可見。
- 任務派遣(Tasking) group-scoped → 各單位只見自己的任務（與 ExCheck/ICS-204、P2-18 重疊）。

### 誠實結論
**TAK/ICS = 「空間計劃畫布 + 下達 + 計畫/實際對比」，不是計劃分析套件。**
計劃的**分析腦力**（地形分析、天氣判斷、兵推、OPORD 撰寫、同步矩陣）**大多在規劃官腦中/他工具完成，或就是 ICS 缺口**。TAK/ICS 接手的是「分析完之後 → 把方案畫上 COP → 下達 → 對比執行」。

### ICS 缺口清單（誠實，未來可評估補）
- **天氣/環境 feed 疊層**（無）← 對 #4野火/#5水災/#6HazMat/航空 影響大
- **地形分析**（坡度/通視/機動）（無）
- **兵棋 / COA 比較**（無）
- **OPORD / 命令結構化產製**（無）
- **同步矩陣 / 作戰時間軸**（無；AAR 回放 ≠ 規劃時間軸）

> 註：上述「缺口」是**忠實盤點**，不代表都要做——多數計劃分析本就不在 COP 工具範疇（屬規劃官/專用工具）。**最值得評估補的是「天氣/環境 feed」**（多個動作情境都需要、且就是既有 data-feed 缺口）。

---

# 群：計劃驗證（桌上演習 TTX + AAR）

> 軍用決策循環 **情報 → 計劃 → 行動 → 評估**：計劃完成後，真實流程**通常先桌上推演（TTX）驗證計畫、找出協同/通聯/決策瓶頸，再進入行動**。TTX 同時是「行動前的彩排」與「評估(AAR)的素材來源」。**這是 ICS 的強項區**（P2-19~22 有完整 spec），恰好補上計劃群「❌ 兵棋推演」缺口的**人在迴路替代**。

## 桌上演習（TTX）＋ AAR 回放

### 作業案例（TTX 實況）
參考 **HSEEP**（演習評估）/ 軍用 **CPX（指揮所演習）** 慣例：**O/C（導調/觀察官）**依**情境腳本（MSEL，按時間軸觸發注入事件）**丟入合成態勢（敵蹤/傷患/災點），**學員在自己真實的 ATAK/儀表板上反應**（下令、派遣、通聯），全程記錄 → 演習後 **AAR 回放**逐事件檢討、量化反應時間。**核心紅線：合成資料絕不可污染實戰資料。**

### 痛點 → 需求邏輯
計畫在腦中/圖上看起來可行，**實際協同會卡**（誰先到、通聯亂、指揮鏈塞車）。需要在**零真實風險/成本**下：① 按腳本注入事件；② 學員在**真實 COP 工具**上操作（演的是真環境，不是投影片）；③ 全程錄下供**回放/AAR**；④ 演習結束**乾淨清除合成物**。

### TAK/ICS 怎麼用（四層 + 為什麼）
| 層 | 怎麼用 | 為什麼 |
|---|---|---|
| ① Client (ATAK/iTAK) | 學員用**真實** ATAK/儀表板操作；見注入的合成實體，標 **`[SIM]` + 虛線框**（與實戰物視覺區隔）| 演真環境才練得到協同；視覺區隔避免誤認 |
| ② 協定/資料 | 合成注入 `how=h-g-i-g-o` + **`simulated=True`**（P2-11b）；archive 後**整批清除** | flag 是「乾淨清除」與「不污染實戰」的資料根基 |
| ③ TAK Server | **Injectors**（Configuration 選單，server-side 注入 CoT）；exercise scoping | server 端注入讓合成態勢進得了 COP streaming |
| ④ ICS Dashboard | **情境腳本 runner**（P2-19：t_offset 時間軸、action 白名單、**sysadmin-only 注入 API**、scenario_running mutex、O/C 控制頁 `/admin/exercise-control`）；`scenario_designer.html` 設計腳本；**AAR 時間軸回放**（P2-20：tracks+events+chats+missions 合併、step mode、iPad 觸控）；**演習指標**（P2-21：MEDEVAC/Mission/EXCHECK 反應時間、通聯量 by 組）；**TTX Gateway**（P2-22）| ICS 是 O/C 的注入台 + 評估台；指標把「感覺很順」變成可量化 |

### 效率提升
- **零風險彩排**：在真 COP 上跑完計畫，行動前就抓出協同破口。
- **AAR 逐事件回放**：把「當時為何這樣決策」攤開檢討，連結時間戳 bookmark。
- **量化指標**：反應時間/完成率/通聯量 by 組 → 訓練成效可比較。

### 注意 / 失效模式
- **合成污染實戰（最大風險）**：`simulated=True` + archive 整批清除 + **TTX Gateway（P2-22）≥2 次演習驗證無殘留**才解鎖實戰層。
- **腳本 server-side 執行安全**：action **白名單 + Pydantic strict + 禁任何動態執行路徑**（P2-19；OWASP A03/A04）。
- **O/C 控制必須 sysadmin-gate**：放主 dashboard 學員會看到導調控制 → 獨立 `/admin/exercise-control`（缺口 #10）。
- **runner 並發**：兩腳本同跑 → 學員地圖混亂 → `scenario_running` mutex，第二個 run 回 409（#14）。
- **AAR = OPSEC 洩漏點**：歷史指揮決策 → COMMAND_ROLES 限定 + resolve_scope 跨演習守門 + 匯出 audit log（P2-20/21）。

### 誠實狀態 / 缺口
- **規格完整但多數 ⏳ 未建**：P2-19（注入）/ P2-20（AAR）/ P2-21（指標）/ P2-22（Gateway）目前是 spec，非已交付。
- **`scenario_designer.html` 脫離 API**（52KB 靜態工具，未接線；缺口 #11）→ 需補 export→`POST /api/exercises/{id}/scenario/upload`。
- **TTX ≠ 兵棋引擎**：這是**腳本注入 + 真人反應**的彩排，**無自動裁決 action-reaction-counteraction**（計劃群「❌ 兵棋」缺口的替代是**真人推演**，非 AI 對抗模型）——誠實說，要的是「練協同」不是「算勝負」。

---

# 群：動作 / 應變

## 都市地震搜救（SAR / Urban USAR）

### 實際案例（佐證，非杜撰 — 附來源）
> 本情境的**作業案例與流程**有實證；但須先講清楚一條**誠實邊界**——

| # | 案例 | 性質 | 來源 |
|---|---|---|---|
| ✅ A | **Bernalillo County Sheriff（新墨西哥 Sandia 山區）**：攀岩者墜崖、多處骨折瀕臨失血→ ATAK + **SOS Token**（發 URL 簡訊讓傷者回傳 GPS）+ **RapidSOS** 協調空中/地面/救護車完成救援。副警長原話：過去飛過去「人員藏在 ponderosa 松冠層下根本看不到」 | TAK 用於 **野地/山域 SAR** 的直接實證 | [Samsung Insights 2020](https://insights.samsung.com/2020/11/10/bernalillo-county-uses-atak-to-improve-search-and-rescue/) |
| ✅ B | **七場颶風（Harvey/Irma/Maria/Florence/Lane/Michael/Dorian）**：TAK 支援救出**逾 2,000 人**，DHS 數千人員使用 | TAK 用於 **水患災害 SAR** 的實證（與情境#5 颱風水災重疊，該情境再展開）| [Wikipedia: ATAK](https://en.wikipedia.org/wiki/Android_Team_Awareness_Kit) |
| ✅ C | **AFRL SAR plugin / CivTAK**：尋找失蹤者（迷途登山客、墜機飛行員），公開為 web 工具 + ATAK 外掛 | TAK SAR 工具生態實證 | [CivTAK 2026-03](https://www.civtak.org/2026/03/23/search-rescue-plugin-released/) |
| ✅ D | **INSARAG 指南**：都市倒塌建物 USAR 國際標準——5 級 ASR、分區(Sector)、worksite ID `1-1`、標記框 1.2m×1.0m | **地震 USAR 流程**的權威實務（非 TAK，是作業方法本身）| [INSARAG Vol II Man B](https://insarag.org/wp-content/uploads/2021/09/INSARAG20Guidelines20Vol20II2C20Man20B.pdf)、[ASR Levels](https://learn.pcpm.org.pl/wp-content/uploads/2020/08/Insarag-Manual-B-ch-5.7.pdf) |
| ✅ E | **Christchurch 2011 地震**：INSARAG 搜救標記系統實戰檢討 | 真實地震 USAR 標記案例 | [USAR marking review](https://www.researchgate.net/publication/317552357) |

> **❗誠實邊界（紅線）**：已查到的 **TAK SAR 實證是野地/山域（A）與水患（B）**；**「地震倒塌建物 USAR」的 TAK 實際部署，未查到公開案例**。故下文「地震 USAR 的 TAK 用法」是**從已證實 SAR 用途（A/C）＋ INSARAG 既有流程（D/E）推論**，標 ❓——**不冒充地震 USAR 已有 TAK 實戰**。

### 作業案例（實況）
兩種 SAR 形態，作業邏輯共通（搜索 → 定位 → 接觸 → 後送），但場景差異大：
- **野地/山域搜索**（案例 A/C）：人員散在大面積地形找失蹤者；地形遮蔽（樹冠/峽谷）、分隊各搜各的、找到傷者要協調空中吊掛/地面後送。
- **都市地震倒塌建物 USAR**（案例 D/E，INSARAG）：城市大面積倒塌，多國/多隊進場。先**廣域評估(ASR-1)**訂**分區(Sector A/B…以河川/大馬路切)**→ 各隊在分到的 sector 做**worksite 分檢(ASR-2)**→ 對每個有生還機會的 worksite 編 **ID（`1-1`：sector-序號，連字號分隔）**、在主入口畫 **1.2m×1.0m 標記框**（隊號/已完成 ASR 級別/日期/失蹤·已救出·罹難數，隨進度更新）→ 初級(ASR-3)/次級(ASR-4)搜救。**核心痛點是「多隊在同一片廢墟、誰搜了哪、哪些 void 還沒清、傷患在哪、別重複搜也別漏搜」**。

沒有共享數位 COP 時：分隊位置只能無線電點名（野地像案例 A「看不到人」）；worksite 標記只在實體牆上噴漆（外隊/指揮所/換班看不到全局）；傷患位置口述轉錄；指揮所(OSOCC/UCC)的 worksite 進度靠紙本回報彙整→**慢、易漏、換班斷層**。

### 痛點 → 需求邏輯
指揮（與 OSOCC）需要：① **全體搜救員即時位置**（誰在哪個 sector/worksite，不必點名）；② **worksite/分區幾何上圖**（誰負責哪塊、進度幾級）；③ **傷患/危害/已清區的標記累積成廢墟全景**（非口述即逝）；④ **找到傷患→就近後送協調（9-line）**；⑤ **搜救員問責**（誰逾時失聯＝安全）。效率關鍵 = **把 INSARAG 紙本分區/標記/回報數位化成即時共享 COP，免重複口述與紙本彙整斷層**。

### TAK/ICS 怎麼用（四層 + 為什麼）
- **① Client (ATAK/iTAK)**：搜救員連上**自動廣播 PLI**（✅ dogfood 見每分鐘；直解案例 A「看不到人」根痛）；在**傷患/危害/已清位置**放標記 + 註記；畫**分區/搜索範圍多邊形**（對映 INSARAG sector/worksite）；**SOS Token** 對可通訊的失蹤者發 URL 回收 GPS（✅ 案例 A 實證，限野地非埋壓）；拍照附到標記（廢墟災損/傷患）；GeoChat 隊內通聯。（PLI/放標記 ✅；iTAK 精確按鈕路徑 ❓）
- **② 協定/資料**：搜救員 = `a-f-*` 友軍 track；分區/路線 = `u-d-f`(多邊形)/`u-d-r`(線)（✅ dogfood 送過）；標記用 2525/NAPSG 語意色（紅=危害、傷患點）；後送 = **MEDEVAC 9-line**（埋 `<_medevac_>`，P2-09 已萃取）。*為什麼*：符號讓多國隊**一眼讀懂**免語言轉譯（INSARAG 多國場景關鍵）。
- **③ TAK Server**：搜救直通多為**零設定**；多隊/多機構用 **Groups** 分流（各隊先看自己 sector，呼應 INSARAG 分區）；**worksite 標記要「持久且重連補齊」→ 走 Mission/DataSync**（持久權威 COP），不能只靠 streaming（刪不掉/重連漏靜態標記＝廢墟標記消失，致命）；無人機俯視塌樓走 **Video Feed Manager**（✅ 選單實證）。
- **④ ICS Dashboard**：COP 渲染搜救員（P2-02~05）+ **小隊聚合**（P2-06d，按 team_color 看各隊在線/失聯/質心＝對映 INSARAG 各隊狀態）；**分區/worksite 多邊形**走 P1-16 zone / P2-08 shape；**MEDEVAC incident card**（P2-09/P2-12）做傷患後送；照片 **reference-only URI 不 proxy**（P2-14，防 SSRF）；搜救員**過 stale 變灰 = 該隊失聯**（安全告警，呼應案例 A「看不到人」）；軌跡→**AAR 回放**（P2-20）供事後問責與 INSARAG worksite 報告。

### 效率提升
- **砍掉「你在哪/搜到哪」無線電** → 頻道留給真正救援指令（最大效率點，案例 A/B 共同見效）。
- **搜救員問責 + 失聯偵測**：誰在哪 sector、誰逾時變灰＝主動關注（廢墟內救援員安全第一）。
- **worksite/傷患/危害標記累積成廢墟全景**（vs 紙本噴漆 + 口述）→ 換班/增援/外隊**秒接當前圖**，消 INSARAG 紙本彙整斷層。
- **就近派遣 + 9-line 後送**：找到傷患→看圖派最近隊 + geo-located 後送請求，免口述轉錄。
- **AAR**：逐事件回放供 INSARAG worksite 報告與訓練檢討（軌跡＝問責資料）。

### 注意 / 失效模式
- **GPS 在廢墟/都市峽谷衰減**：倒塌結構間、地下 void 收訊差 → PLI 不準或無；**埋壓傷者無法用 SOS Token**（要能操作手機）→ 數位定位**補強非取代**實體 INSARAG 標記與搜救犬/聲探。
- **倒塌結構內無訊號** → 需 mesh 中繼（Meshtastic 等，案例生態有 ❓確切部署）或人工中繼；單純依賴 cellular/server 會斷。
- **COP 完整性放大＝人命**：一個誤標「已清(cleared)」＝**廢墟裡漏救人**（blast radius 是生命，比 COP 情境更致命）→ 標記可信度與**可靠刪除/重連 resync（#161/#173）對本情境是必需非 nice-to-have**：worksite 標記**刪不乾淨或重連漏靜態標記＝共享錯圖、重搜或漏搜 void** → **P2-14 權威 resync 直接服務本情境**。
- **多隊/多機構**：INSARAG 國際隊 → Groups 分流 + 未來 **Federation（P2-15）** 跨機關交換；peer cert 治理（缺口 #8）。
- **stale ≠ 確定離線**：搜救員可能在無訊號 void 中而非掉隊 → 變灰是「不確定、主動關注」非「放棄」。
- **多日作業電力**：行動裝置續航 → 需充電/備援規劃。

---

## 颱風/水災疏散收容　_待深挖_
## 野火延燒應變　_待深挖_
## 危險物質（HazMat）洩漏　_待深挖_
## 重大傷亡後送（MCI/MEDEVAC）　_待深挖_
## 關鍵設施巡邏監控　_待深挖_

# 群：高威脅 / 多組織 / 訓練　（待深挖）
## RTF 武裝掩護搜救　_待深挖（已有對話內容：integrity=人命、敵我/IED/暖冷區、拉高 §8.3/#161/P2-14 優先級）_
## 多機構聯合災害指揮　_待深挖（federation/groups 為核心 server 設定）_

> 註：**桌上演習（TTX）+ AAR** 已上移至「計劃驗證」群（循環順序：計劃 → TTX 驗證 → 行動）。
