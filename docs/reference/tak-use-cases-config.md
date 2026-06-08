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

# 深挖順序（基礎 → 進階）

| 階 | 情境 | 狀態 |
|---|---|---|
| **0 基礎** | 友軍即時定位 (BFT) | ✅ 已挖 |
| **0 基礎** | 共同作戰圖 (Shared COP) | ✅ 已挖 |
| **1 單隊作業** | 目標偵查與回報 (Recon) | 待挖 |
| **1 單隊作業** | 都市地震搜救 (SAR) | 待挖 |
| **1 單隊作業** | 重大傷亡後送 (MCI/MEDEVAC) | 待挖 |
| **2 多元素應變** | 颱風/水災疏散收容 | 待挖 |
| **2 多元素應變** | 野火延燒應變 | 待挖 |
| **2 多元素應變** | 危險物質 (HazMat) 洩漏 | 待挖 |
| **2 多元素應變** | 關鍵設施巡邏監控 | 待挖 |
| **3 進階 ISR** | 多影像情資 (IMINT) | 待挖 |
| **3 進階 ISR** | SDR/RF 感測 (→ WaveInk/P3) | 待挖 |
| **4 高威脅/多組織/訓練** | RTF 武裝掩護搜救 | 待挖（整理對話內容） |
| **4 高威脅/多組織/訓練** | 多機構聯合災害指揮 | 待挖 |
| **4 高威脅/多組織/訓練** | 桌上推演 (TTX) + AAR | 待挖 |

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

# 階 1：單隊作業　（待深挖）
## 目標偵查與回報（Recon & Report）　_待深挖_
## 都市地震搜救（Urban SAR）　_待深挖_
## 重大傷亡後送（MCI / MEDEVAC）　_待深挖_

# 階 2：多元素應變　（待深挖）
## 颱風/水災疏散收容　_待深挖_
## 野火延燒應變　_待深挖_
## 危險物質（HazMat）洩漏　_待深挖_
## 關鍵設施巡邏監控　_待深挖_

# 階 3：進階 ISR　（待深挖）
## 多影像情資（IMINT）　_待深挖_
## SDR/RF 感測（→ WaveInk / P3）　_待深挖_

# 階 4：高威脅 / 多組織 / 訓練　（待深挖）
## RTF 武裝掩護搜救　_待深挖（已有對話內容可整理：integrity=人命、敵我/IED/暖冷區、拉高 §8.3/#161/P2-14 優先級）_
## 多機構聯合災害指揮　_待深挖（federation/groups 為核心 server 設定）_
## 桌上推演（TTX）+ AAR　_待深挖（injectors/simulated/回放）_
