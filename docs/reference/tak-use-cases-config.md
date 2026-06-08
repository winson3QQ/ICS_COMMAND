# TAK / COP 使用情境 ↔ 四層配置對照

> 把使用情境拆成 **① Client(ATAK/iTAK) / ② 協定 / ③ TAK Server / ④ ICS Dashboard** 四層，
> 每層列出要動什麼。供 CONOPS ↔ 配置對照、ROADMAP item 回溯「為哪個情境而做」。
> 衍生自 2026-06 真機 dogfood + admin GUI 實證 + 安全策略對話。

## ⚠️ 可信度標註（重要，不亂掰）
- **✅ 實證**：dogfood 真機（iTAK + 活 server）、admin GUI 截圖、或 ICS code 直接確認。
- **❓ 未查證**：需對 **ATAK/iTAK 官方文件 / 實機 / TAK_Server_Configuration_Guide.pdf** 確認，本文**不編造**。
- 特別：**TAK Client（ATAK/iTAK）的精確選單/按鈕路徑大多為 ❓**——dogfood 只確認「行為與送出的 CoT」，沒逐一記按鈕名。下文 client 層只寫**已實證行為**，精確操作路徑標 ❓。

## 四層定義
| 層 | 是什麼 | 配置面 |
|---|---|---|
| **① Client (ATAK/iTAK)** | 操作員在 app 的動作；server 直通轉發 | 發包(group/cert) + 操作員操作 |
| **② 協定/資料** | CoT type / MIL-STD-2525 符號 / 9-line schema | 格式，**不用配** |
| **③ TAK Server** | admin GUI / CoreConfig.xml 設定 | **真正的 server 設定** |
| **④ ICS Dashboard** | ICS 端 normalize/渲染/COP（我們的 code） | ROADMAP P1/P2 item |

---

## ③ TAK Server admin GUI 菜單（✅ 2026-06-08 實證，本文引用基準）
> 進入：`https://<host>:8443/` → 接受 Distribution Statement → 需 **admin client cert**（operator cert 看不到管理頁）。各情境下文以這些選單名引用。

- **Data**：Cot Query｜File Manager｜Send Mission Package｜Video Feed Manager｜**Mission (COP) Manager**｜ExCheck
- **Situation Awareness**：Export Mission｜KML SA Feed｜WebTAK
- **Configuration**：**Inputs and Data Feeds**｜**Federation**｜Federate Certificate Authorities｜**Injectors**｜Security and Authentication
- **Administrative**：Database｜**Data Retention**｜Manage Users｜**Client Certificates**｜Tokens｜Device Logs｜**Device Profiles**｜File Config｜VBM Configuration
- **Monitoring**：Alarms｜Metrics Dashboard｜Client Dashboard

> **實證重點**：① Client Certificates 有 `Revoke Selected`/`Show Revoked`，但只管 **enrollment 發的 cert**（離線 makeCert cert 不在清單）。② Federation 現 **DISABLED**（scaffold+fed-truststore 在、無 peer）。③ Data Retention 全 TTL 空、排程 `Never`（預設無限保留）。④ Mission Manager 可 `ADD`/`DELETE` mission，可見性 **group-scoped**。

---

## 通則：多數「動作型」情境的 server 設定其實很少
- **① Client + ④ ICS 為主、③ TAK Server 幾乎零設定**的情境：只要 :8089 通 + cert/group 對，operator 操作就直通進 COP。
- **③ TAK Server 設定吃重**的，集中在：**Federation / Mission(DataSync) / Inputs and Data Feeds / Video / ExCheck / Data Retention / Groups(Manage Users)** —— 也正好對到 ROADMAP 未做的 P2 item。

---

# 使用情境（14）

## A. 動作 / 應變類

### #1 友軍/人員即時定位（Blue Force Tracking）
- **① Client**：iTAK 連上即**自動上報自身 GPS 位置**（✅ dogfood 見 `曙豐-3QQ` 每分鐘 PLI）。精確設定路徑 ❓。
- **② 協定**：`a-f-G-U-C` 等 atom CoT + `<track>`；2525 友軍框。✅
- **③ TAK Server**：**幾乎零設定**——只需 :8089 input 開（預設）+ client cert/group。Groups 經 `Administrative → Manage Users`。
- **④ ICS**：✅ 已做（P2-02~05 串流→COP→2525 渲染）。

### #2 共同作戰圖標繪（Shared COP）
- **① Client**：iTAK 繪圖工具畫區域/放標記（✅ dogfood 實證送 `u-d-f`/`u-d-r`/`u-d-c-c` + 顏色 + solid/dashed/dotted）。精確按鈕 ❓。
- **② 協定**：`u-d-*` + `<shape>`/`<link>` + strokeColor/strokeStyle。✅
- **③ TAK Server**：純串流標繪零設定；**要「持久共享/可靠刪除」→ `Data → Mission (COP) Manager`**（放進 mission 才權威同步）。
- **④ ICS**：🔶 上行標繪✅（P2-08 幾何）；可靠共享/刪除=P2-14。

### #3 都市地震搜救（Urban SAR）
- **① Client**：畫搜索責任區(多邊形)、標已清/發現、隊員 BFT、GeoChat 回報。✅(行為) / 按鈕 ❓
- **② 協定**：多邊形 CoT + 標記 + `b-t-f`(GeoChat)。✅
- **③ TAK Server**：基本零設定；多隊分流可用 **Groups**；責任區若要共享持久→ Mission Manager。
- **④ ICS**：🔶 幾何/GeoChat 後端✅；「責任區覆蓋率/分派」工作流未做。

### #4 野火延燒應變
- **① Client**：畫火線(會移動)、疏散區；空拍見 #13。❓按鈕
- **② 協定**：u-d-* 多邊形 + （影像見 #13）。✅
- **③ TAK Server**：火線串流零設定；**空拍影像→ `Data → Video Feed Manager`**；外部氣象/風向 feed→ `Configuration → Inputs and Data Feeds`。
- **④ ICS**：🔶 繪圖✅；影像=P2-16、外部 feed=未列(見 #14/P3)。

### #5 颱風/水災疏散收容
- **① Client**：標避難所、畫疏散路線、回報。❓按鈕
- **② 協定**：標記 + 路線 CoT。✅
- **③ TAK Server**：**外部水位/氣象 feed → `Configuration → Inputs and Data Feeds`**（核心設定點）。
- **④ ICS**：避難所/路線 COP 大致✅（P1-16/P2-08）；水位 feed 進 COP 未做。

### #6 危險物質(HazMat)洩漏
- **① Client**：標毒煙擴散/隔離區、下風疏散、追蹤處置人員。❓按鈕
- **② 協定**：多邊形(擴散) + 標記 + 2525 hazard。✅(框架)/IED-style 專用符號 ❓
- **③ TAK Server**：基本零設定。
- **④ ICS**：🔶 繪圖✅；hazard 專用符號待確認。

### #7 重大傷亡事件(MCI)後送
- **① Client**：檢傷點、MEDEVAC 9-line、後送路線。9-line 表單 iTAK 路徑 ❓。
- **② 協定**：MEDEVAC CoT（9-line in `<detail>`）+ 路線。✅(P2-09 解析過)
- **③ TAK Server**：零設定（串流直通）。
- **④ ICS**：✅ 後端(P2-09)；MEDEVAC card 面板=P2-12。

### #8 多機構聯合災害指揮（跨組織）
- **① Client**：各機構 client 各自 cert/group。發包設定 ❓細節。
- **② 協定**：CoT 跨域轉發。
- **③ TAK Server**：**`Configuration → Federation`（現 DISABLED，需開+設 peer）+ `Federate Certificate Authorities`（fed CA）+ `Manage Users`(group need-to-know)**。← server 設定最吃重的情境。
- **④ ICS**：⏳ P2-15（gated，未做）。

### #9 關鍵設施巡邏監控
- **① Client**：巡邏 BFT、geofence、入侵應變。geofence 設定 iTAK 路徑 ❓。
- **② 協定**：BFT + 標記 + （影像 #13）。
- **③ TAK Server**：監視器→ `Video Feed Manager`；感測→ `Inputs and Data Feeds`。
- **④ ICS**：🔶 BFT✅；影像/感測未做。

### #10 指揮所桌上推演(TTX) + 複盤
- **① Client**：學員 iTAK 收注入的合成情境並應變。
- **② 協定**：合成 CoT（`how="h-g-i-g-o"` → `simulated`）。
- **③ TAK Server**：**`Configuration → Injectors`**（情境注入，待查證確切用法 ❓）。
- **④ ICS**：⏳ P2-19(注入/O-C) + P2-20(AAR 回放)，未做。

### #11 RTF 武裝掩護搜救（暖區、有敵情/爆裂物）
- **① Client**：標敵方(紅 2525)、IED/UXO、熱/暖/冷區、武裝+醫護雙編組 BFT、靜默 GeoChat。✅(放敵我色/畫區/GeoChat 實證) / IED 專用符號 + 精確按鈕 ❓。
- **② 協定**：`a-h-*`(敵)、`u-d-*`(區)、2525 hostile/（IED 符號 ❓）、`b-t-f`。✅(敵我框)
- **③ TAK Server**：串流零設定；但 **integrity 要求最高**（見下）。
- **④ ICS**：✅ 2525 敵我(P2-05)+敵我篩選(P2-25)+archive/stale(#161)；**這些在 RTF 是剛需非裝飾**。
- **⚠️ 安全**：此情境 **COP 完整性=人命**（假敵標→走進埋伏、漏 IED→傷亡、刪不乾淨→誤判暖/熱區）→ 拉高 **§8.3 poisoning / #161 可靠刪除 / P2-14** 的優先級。

## B. 情報 / 監偵類（ISR）

### #12 目標偵查與回報（Recon & Report）
- **① Client**：偵查員 BFT + 標目標(2525 敵/不明) + GeoChat 回報。✅(行為)/結構化 spot report 表單 ❓。
- **② 協定**：atom CoT + `b-t-f`；spot report 可塞 `<detail>`。
- **③ TAK Server**：零設定。
- **④ ICS**：✅ ingest+GeoChat；結構化 spot report 未做。

### #13 多影像情資回傳（IMINT）
- **① Client**：拍多張照片 geo-tag 回傳。iTAK 附件路徑 ❓。
- **② 協定**：照片走 Enterprise Sync / DataSync 附件（URI reference）。
- **③ TAK Server**：**`Data → File Manager` / Mission 附件**（Enterprise Sync）。
- **④ ICS**：⏳ P2-14——**照片=reference-only URI、永不 follow（防 SSRF）**。← 本情境證明該守則有真實需求。

### #14 SDR/RF 感測回傳 radio pattern（SIGINT/RF）
- **① Client / 感測源**：SDR 裝置或 **WaveInk**（SDR 多頻 + ASR）產生 RF 資料。
- **② 協定**：RF emitter → sensor-type CoT（位置/方位，確切 type ❓）或 WaveInk 自有格式。
- **③ TAK Server**：若走 TAK→ **`Configuration → Inputs and Data Feeds`**（接 SDR feed）。
- **④ ICS**：**= WaveInk = COP 第二外部來源（Phase 3，全未做）**。對到「外部 data feed」缺口。

---

## 觀察與用途
1. **動作型情境（#1-7,9,11-12）**：③ TAK Server 多為**零/極少設定**；配置重心在 ① 發包(cert/group) + ④ ICS 渲染。
2. **③ TAK Server 設定吃重**：**#8 federation、#4/5/14 inputs/data-feed、#4/9 video、#10 injectors、#13 file/mission、retention** —— 對到 ROADMAP 未做 P2 item。
3. **#11 RTF 是 integrity 天花板**：把 COP 完整性/可靠刪除/敵我篩選從「nice-to-have」拉成「人命」。
4. **#14 SDR = WaveInk(P3)**：補上「外部感測 data feed」這條，且是 COP 第二來源。

## 待查證清單（❓，動工前補）
- TAK Client（iTAK/ATAK）各操作的**精確選單/按鈕路徑**（本文只列已實證行為）。
- IED/UXO / HazMat 的 **2525 專用符號** 是否在我們渲染管線可用。
- **Injectors** 的確切用法（#10）。
- RF/sensor CoT 的**確切 type**（#14）。
- 來源：ATAK/iTAK 官方文件、`TAK_Server_Configuration_Guide.pdf`、實機。
