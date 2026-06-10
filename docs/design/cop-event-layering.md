# COP 分層模型：感知標記 / 事件 / 決策 / 下行

> **狀態**：設計 doctrine（2026-06-09 確立，#176 / P2-13 設計對話衍生）。
> 本文是 **ICS COP 資料分層的 SoT**。對應 ROADMAP 缺口項 **P2-27 ~ P2-30**（Phase 2，TTX 前）。
> 相關：[[tak-marti-authz-model]]（下行機制）、[strategy §1.1](../roadmap/tak-integration-strategy.md)（server-authoritative）。

## 一句話

ICS 是**多源 COP 的匯流 + 指揮中樞**：各路「感知標記」匯成一張共享態勢圖（可**雙向**流向 TAK client）；
指揮部把多起感知**收斂成「事件」**進入「追蹤 → 決策 → 行動 → 結案」流程——**事件留指揮部，不外流**。

## 全景（canonical，使用者 2026-06-09 定）

1. **感知標記（sensing markers）**：TAK client 的 marker、**無線電回報的手動 marker**、（未來）**WaveInk RF 足跡**——這些感知標記都顯示在 ICS map。
2. **標記可與 TAK client 共享**（雙向 COP）。
3. **多起感知標記收斂成一個需被追蹤的事件（event）**。**事件留在 ICS 指揮部，不分享出去**。
4. 事件追蹤**包含指揮官的決策（decision）**。
5. 事件**產生後續行動**（下行指令 / 派任）。
6. 指揮部**持續追蹤至結案（resolved）**。

## 全棧分層（心智圖：層次 ↔ 實作）

> 上面「全景」是敘事；本節是**結構視圖**——把資料流從邊緣到指揮拆成六層 + 三條橫切，每層標 code 落點。
> 下節「兩層」是本模型的 doctrine 核心，**即下圖的 L3（感知層）/ L4（事故層）**；其餘四層是它的上下文。
> **狀態以〈缺口〉表（P2-27~30）為唯一 SoT**，本圖不另立進度標註，只標「TAK 也做 / ICS 獨有」的能力歸屬。

```
                              OODA       能力歸屬
┌──────────────────────────────────────────────────────────┐
│ L5 指揮/決策  整理成支持決策的資訊        Decide   ICS       │ ← dashboard / filter / DCI / 告警
│    角色分層視圖、告警「何時決策」(支持≠替代)                 │   (主動告警後端為缺口)
├──────────────────────────────────────────────────────────┤
│ L4 事故層    流程性、生命週期、問責       Orient/  **ICS 獨有**│ ← events/decisions/chats
│    event→決策→行→結案、**不外流**        Decide  (TAK 無此模型)│  (四表割裂，待 P2-27 梳理)
├══════════════════════════════════════════════════════════┤
│ L3 感知層COP  位置性、即時、**雙向可共享** Observe  TAK 也做  │ ← cop_entities(單一SoT)
│    「來源無感」、進得來該出得去          (共享)             │   realtime_hub / cop_entity_tracks
├──────────────────────────────────────────────────────────┤
│ L2 正規化接縫 多源→單一 CoPEntity        —        ICS       │ ← services/cop_service.py
│    蓋章(scope/severity/防偽)+扇出(WS+軌跡)                  │   ingest_cot_event = 共用接縫
├──────────────────────────────────────────────────────────┤
│ L1 傳輸/接入  把節點資料送進來           —        TAK+ICS   │ ← :8089/:8443/:9000 ↘
│                                                           │   routers/tak·ingress·manual·cop
├──────────────────────────────────────────────────────────┤
│ L0 節點/邊緣  感測+顯示+執行(三合一)     Observe   外部      │ ← TAK client / 無線電(人) /
│    單一視角、無權威、server 不信其宣告    +Act              │    WaveInk / Pi-node / 指揮部下達
└──────────────────────────────────────────────────────────┘
   ║ L2↔L3 那條雙線 = cop_service 接縫，也是「感知層入口」（來源無感在此執行）
   ║ L3↔L4 = 感知標記 N:1 聚合成事件；doctrine 已分、code 還以 attributes JSON 黏定（P2-27 拆）

  橫切（貫穿全棧，不屬單層）：
  ├─ 治理/信任邊界 ── server-authoritative(scope 蓋章) · source 防偽 · PII TTL · SSRF URI-only · 供應鏈紅線
  ├─ 降階(3-tier) ── Tier0 全通 / Tier1 部分 / Tier2 退原生(manual 地板)；不硬依賴 TAK
  └─ 驗證/複盤 ──── TTX(借 TAK injector) → AAR(cop_entity_tracks 回放) → 指標（P2-19~22）
```

| 層 | 是什麼 | 能力歸屬 | code 落點 |
|---|---|---|---|
| **L0 節點** | 感測+顯示+執行，單一視角無權威 | 外部 | TAK client / WaveInk / 無線電(人) / Pi-node |
| **L1 傳輸** | 把節點送進來的管道 | TAK+ICS | TAK `:8089/:8443/:9000`；ICS `routers/tak·ingress·manual·cop.py`、`tak_service.parse_cot_xml` |
| **L2 正規化接縫** | 多源→單一 `CoPEntity`、蓋 scope/severity/防偽、扇出 WS+軌跡 | ICS | `services/cop_service.py`：`normalize_cot` + `ingest_cot_event`（共用接縫，#105） |
| **L3 感知層** | 位置性共享圖、來源無感、**雙向** | **TAK 也做** | `cop_entities`（SoT）、`cop_entity_repo`、`realtime_hub`、`routers/cop.py`、`cop_entity_tracks` |
| **L4 事故層** | event(severity/狀態/結案)+decision+tasking、問責、**不外流** | **ICS 獨有**（TAK 無此模型）| `events` · `decisions` · `chats` 三表 + 下行 tasking |
| **L5 指揮層** | 整理成支持決策的資訊、角色視圖、告警 | ICS | dashboard chrome、map filter、DCI、status-lamp；主動告警後端 |

### 每層實體網路對應（port / protocol）

> 把上表每層落到**實體網路**：跑哪個 port、走哪個 protocol（值以 code 為準，2026-06-10 核）。
> 三點先記：① **TAK 三埠是對接外部 TAK Server**，非 ICS 自身監聽；ICS 自己只開 FastAPI 一埠 + Node relay。
> ② **L2 無網路埠**——同進程 Python 呼叫，是 transport↔語意的分離面，不過 socket。
> ③ **Prod 由 nginx 反代終結 TLS**（對外 `:443` + HSTS），`:8000` 是 dev 直跑埠；憑證體系 = step-ca（mTLS）。

| 層 | port | protocol | 通道 / 備註 |
|---|---|---|---|
| **L0 節點** | —（節點側，非本機監聽） | CoT · 語音RF · WS | TAK client→CoT；無線電(人)→**RF 語音、無 IP**（頻外，靠幕僚手動上車 `manual`）；WaveInk→RF 足跡(未來)；Pi-node/PWA→WS |
| **L1 傳輸** | 外部 TAK `:8089` · `:8443` · `:9000`(/`:8444`)；ICS FastAPI `:8000`；Node relay `:8765`(prod `8775`) + admin `:8766`(prod `8776`) | `:8089` **TLS/TCP**(TAK Protocol v0 CoT-XML / v1 protobuf，mTLS) · `:8443` **HTTPS**(Marti REST `/Marti/api`，mTLS) · `:9000` **TLS**(federation transport) · `:8000` **HTTP**(dev；prod→nginx TLS) · relay **WS/WSS**(HMAC 簽章) | pytak `readuntil(b"</event>")` 處理 TCP 分幀、`use_protobuf` 自動 v0/v1；憑證 step-ca |
| **L2 接縫** | **— in-process** | —（同進程函式呼叫，無 socket） | `cop_service.normalize_cot` + `ingest_cot_event`；所有 doctrine 在此蓋章 |
| **L3 感知層** | REST `/api/cop/*`、WS `/api/cop/ws/updates`（同 `:8000`／prod `:443`）；出向 → TAK `:8089` | **HTTP(S)** · **WS/WSS**(token 走 `Sec-WebSocket-Protocol`，不進 URL) · 出向 **CoT/TLS**(`send_cot`) | 雙向：入＝串流/REST POST，出＝per-entity 閘 send_cot（P2-13/30） |
| **L4 事故層** | REST `/api/events`·`/api/decisions`·`/api/chats`（同 `:8000`／prod `:443`） | **HTTP(S)** | **對外無獨立 port**；不外流，出向只借 L3 兩閘（感知標記 + 衍生 tasking） |
| **L5 指揮層** | 瀏覽器 ↔ FastAPI `:443`(nginx；dev `:8000`) | **HTTPS** · **WS/WSS**(同源) | dashboard 靜態 + 即時推皆復用 L3 的 WS；無自有對外埠 |

#### 元件視角（actor × 連線方向）

> 上表按「層」切；本表把同一條鏈按「**元件**」拆——誰監聽（listen）、誰主動連出（egress）、走什麼。先釐清三件易混的事：
> ① 本 repo 的 **command server** 可跑 Mac（`start_mac.sh`）或 **Pi**（`start_pi.sh`，headless、bind `0.0.0.0` 給區網平板）——**Pi 上跑的就是同一支 command server（FastAPI :8000），不是另一種 server**。
> ② **TAK server 是外部基礎設施**（`deploy/tak-server/`），ICS 對它是 client，三埠都是「往外連」非自身監聽。
> ③ `server/` Node relay 服務的 shelter / medical **PWA 不在本 repo COP 範圍**（CLAUDE.md），僅作 L0 邊緣節點的背景列出。

| 元件 | 角色（層） | 監聽 listen | 主動連出 egress | protocol |
|---|---|---|---|---|
| **TAK client**（ATAK/iTAK/WinTAK） | L0 邊緣：感測+顯示+收 COP | —（純 client） | TAK server `:8089`（`connectString0=<host>:8089:ssl`）；憑證註冊 `:8446` / web `:8443` | CoT over **TLS**（mTLS，v0 XML / v1 protobuf）；`u-d-*`、GeoChat `b-t-f`、`_medevac_` |
| **TAK server**（外部基礎設施） | CoT 匯流 + Mission 持久 + federation | `:8089`（CoT TLS streaming）、`:8443`（web/Marti REST HTTPS）、`:8446`（cert enroll，clientAuth=false）、`:8444`/`:9000`（federation，選配） | federation peer `:9000` / `:8444` | **TLS** / **HTTPS**；憑證 step-ca |
| **Command server**（ICS 指揮部 FastAPI；Mac 或 **Pi**） | L1–L5 主體：接入·正規化·COP·事故·指揮 | `:8000`（HTTP；Pi bind `0.0.0.0`；prod 由 nginx → `:443` TLS+HSTS） | **入向訂閱** TAK `:8089`（mTLS 串流）、**主動查** TAK Marti `:8443`（mTLS）、**出向下行** `send_cot` → TAK `:8089`（mTLS，**非 Marti**，複用訂閱 cert） | 自身 **HTTP(S)**；對 TAK **TLS/HTTPS**（mTLS） |
| **Frontend**（commander dashboard，瀏覽器） | L5 呈現 + L3 即時態勢 | —（純 browser） | command server `:443`（dev `:8000`）：靜態 + REST `/api/*` + WS `/api/cop/ws/updates` | **HTTPS** + **WS/WSS**（token 走 `Sec-WebSocket-Protocol`，不進 URL） |
| _(Pi-node / Node relay；上游 PWA 邊緣，**非本 repo COP**)_ | L0：shelter/medical PWA 中繼 | relay `:8765`/`8775`（WS）、admin `:8766`/`8776` | — | **WS/WSS**（HMAC 簽章） |

#### port 速查（每埠作用）

> 把上兩表出現的埠攤平，逐一標「這個埠在做什麼」。`:8089`–`:9000` 是**外部 TAK server** 的埠（ICS 對它是 client）；`:8000`/`:443` 是 **ICS 自身**；`:876x` 是上游 relay（非本 repo COP）。

| port | 屬於 | 作用（這個埠在做什麼） | protocol |
|---|---|---|---|
| `:8089` | TAK server | **CoT 雙向串流匯流**——ICS 訂閱（入向）+ `send_cot` 下行（出向）皆走此埠；TAK client 的 `connectString0=<host>:8089:ssl` 也連這裡 | **TLS**（mTLS），TAK Protocol v0 XML / v1 protobuf |
| `:8443` | TAK server | **web UI + Marti REST API**——人類 admin 登入 + ICS 主動查（mission / groups / presence，P2-11/14） | **HTTPS**（mTLS） |
| `:8446` | TAK server | **憑證註冊（enrollment）**——managed-cert 申領，`clientAuth=false`；ICS COP subscriber **不經此**（用 CA 簽 fullchain client cert 即可） | **HTTPS** |
| `:8444` | TAK server | **federation HTTPS（fed_https）**——多機構憑證/治理介面（選配，P2-15） | **HTTPS** |
| `:9000` | TAK server | **federation transport**——多機構 CoT 互聯傳輸（選配，P2-15） | **TLS** |
| `:8000` | command server | **ICS 指揮部 FastAPI 本體**——dashboard + REST `/api/*` + WS；dev 直跑，Pi bind `0.0.0.0` 給區網平板 | **HTTP**（prod 由 nginx 終結 TLS） |
| `:443` | nginx（prod） | **對外 HTTPS 入口**——反代終結 TLS + 注入 HSTS，轉發給 `:8000`；CSP/X-Frame 等由 FastAPI 出 | **HTTPS** |
| `:8765`/`8775` | Node relay | **PWA WebSocket 中繼**——shelter（8765）/ medical（8775）即時同步（**非本 repo COP**） | **WS/WSS**（HMAC 簽章） |
| `:8766`/`8776` | Node relay | **relay admin 介面**——首次設定/PIN/稽核（對應上欄各單位） | **WS/WSS** |

#### 憑證信任鏈（每埠：誰產出 · 誰簽 · 給誰）

> **單一信任根 = step-ca**（雙層 root→intermediate→leaf，`deploy/step-ca/`）。TAK server 棄官方自簽 CA 改用此 CA（#98 drift 2）→ dashboard / TAK / 未來 federation 同一條鏈（`deploy/tak-server/pki/issue-tak-certs.sh`）。
> mTLS 埠**雙向各一張 cert**：server 出示 server cert、client 出示 client cert，彼此靠 truststore（step-ca root+intermediate）互驗；client 一律送 **fullchain（leaf+intermediate）**，因 TAK truststore 只有 root，缺 intermediate 會 `peer not verified`（#106/#170 實證）。
> ⚠ **與 at-rest / backup 金鑰無關**：那是 FIDO2 → HKDF master→child（`p1-key-management.md`），管 DB / 備份加密，不是傳輸憑證——兩條鏈別混。

| port / 憑證 | 誰產出（命令） | 誰簽（CA） | 配給誰（出示方） | 對端怎麼驗 |
|---|---|---|---|---|
| `:8089`·`:8443`·`:8446` **server cert** | `issue-tak-certs.sh`：`step ca certificate`（**RSA 2048**，含 SAN）→ `takserver.jks` | step-ca **intermediate** | **TAK server** 對所有 TLS listener 出示 | client 用 `truststore-root.jks`（root+intermediate）驗 |
| `:8089` **client** `cop-subscriber` | `issue-tak-certs.sh`：離線 `step certificate create`（fullchain=leaf+int） | step-ca **intermediate** | **command server**（`TAK_CLIENT_CERT/KEY`；訂閱入向 + `send_cot` 下行**共用**這張） | TAK 用 `truststore-root.jks` 驗（fullchain 補鏈） |
| `:8443` **client** `ics-mission-read` / `ics-mission-write` | 同上（離線簽 fullchain，三張一起簽） | step-ca **intermediate** | **command server**（`TAK_MARTI_READ/WRITE_CERT/KEY`） | truststore 信任即可**讀**；**寫**另由 mission-role（MISSION_WRITE/owner）把關，非 cert（P2-13 待解） |
| `:8446` **enrollment**（`clientAuth=false`） | —（官方 managed-cert 申領路徑） | — | 無需 client cert | **ICS COP 不經此**（CA 簽 fullchain 即可，毋須 enroll） |
| `:8444`·`:9000` **federation peer cert** | 對端機構各自產 | 各自 CA（PoC 同 step-ca） | federation **peer** 互相出示 | `fed-truststore.jks`（step-ca root+int；對端用別的 CA 時再匯入對端 root，P2-15） |
| `:8000`（dev）**無 TLS** | —（純 HTTP，本機/區網 dev） | — | — | — |
| `:443`（prod）**nginx server cert** | 部署者佈署（**repo 未綁定**來源） | step-ca 或公開 CA（依場景） | **nginx** 對瀏覽器出示 | 瀏覽器系統信任庫；內網則裝 step-ca root |
| `:8765`/`8775` **relay TLS**（選配）+ **HMAC** | `CERT_PATH/KEY_PATH`（部署者）、同目錄 `rootCA.pem` | step-ca（同 PKI） | **relay** 出示 TLS；app 層另以 **HMAC 共享密鑰**逐筆簽 | client 用 `CA_CERT` 驗 TLS；HMAC 驗訊息完整性（非本 repo COP） |

#### 憑證鏈全圖（cert chain，傳輸層）

> 上表逐埠列「誰出示」，本圖補「**整棵樹**」：step-ca 一根長到每張 leaf。`[srv]`＝server cert（被連方出示）、`[cli]`＝client cert（主動連方出示）。SoT＝`deploy/step-ca/` + `deploy/tak-server/pki/issue-tak-certs.sh`。

```
step-ca（standalone CA，deploy/step-ca/，金鑰在 ~/.step/）
└─ root_ca.crt ───────────────────────── 信任根（長效、離線；裝進各 truststore / TAK_CAFILE）
   └─ intermediate_ca.crt ── 實際簽發層（所有 leaf 由它簽；client fullchain 補的就是它）
      provisioner：admin@ics.local（JWK，手動/腳本簽）｜ acme（ACME，Pi/Command 自動申領）
      │
      ├─[srv] CN=<TAK_HOSTNAME> RSA2048 +SAN → takserver.jks ── TAK 對 :8089/:8443/:8446 出示
      │         （RSA 非 EC：api jwkSource 寫死 RSAPublicKey，給 EC 會 8443 不綁 #101）
      ├─[cli] cop-subscriber    (fullchain) → command server ── :8089 訂閱入向 + send_cot 下行（共用）
      ├─[cli] ics-mission-read  (fullchain) → command server ── :8443 Marti 讀
      ├─[cli] ics-mission-write (fullchain) → command server ── :8443 Marti 寫（cert 給身分，role 另管）
      ├─[srv] nginx / dashboard cert        → :443 對瀏覽器出示（場景擇 step-ca 或公開 CA，repo 未綁定）
      └─[srv] relay cert                    → :8765 relay 出示（選配，非本 repo COP）

  truststore（驗對端用，內容皆 = root + intermediate）：
    · truststore-root.jks → TAK 驗 client/peer        · fed-truststore.jks → federation peer
    · TAK_CAFILE = root_ca.crt → command server 驗 TAK server 憑證
    ※ client 一律送 fullchain：truststore 只有 root，缺 intermediate → peer not verified（#106/#170）

  效期：step-ca 預設 24h（dev 夠用，renew-cert.sh 快速）；prod 須改 2160h(90d)
        （~/.step/config/ca.json 的 provisioner.claims，C3-B install.sh patch）。
        client cert 效期 = TAK_CLIENT_CERT_DURATION（預設 2160h）。
```

#### 金鑰鏈全圖（key chain，靜態層；**非傳輸**）

> **SoT = [`docs/roadmap/p1-key-management.md`](../roadmap/p1-key-management.md)（P1-12）**，此處只放全圖 + 與憑證鏈的邊界，細節不複製（避免雙 SoT 漂移）。

```
FIDO2 token（CTAP2 hmac-secret extension；enroll N 把：主 / 備援 / 災後）
   │ service start：PIN + touch（單一 unlock，所有 key 一次推出）
   ▼
master key（32 bytes，僅 process memory；落盤＝/etc/ics/master-key.enc，hmac-secret-wrapped）
   │ HKDF-SHA256，label-based derive
   ├─ child[0] "backup-v1" → Fernet key    → backup_db.py（data/ 備份加密）
   ├─ child[1] "db-v1"     → SQLCipher key  → live DB at-rest 加密
   └─ child[2..]（未來）    → audit log signing / session token 簽章
   rescue：master key BIP-39 助記詞紙本（可手動 reenroll）
```

> **兩鏈邊界（紅線）**：① **憑證鏈**（step-ca，上）＝**傳輸層**身分 / mTLS，管「誰能連、連線會不會被竊聽」；
> ② **金鑰鏈**（FIDO2 / HKDF，本圖）＝**靜態層**機密，管「磁碟上的 DB / 備份有沒有加密」。
> 兩者**不交叉**：傳輸憑證私鑰不拿去加密 DB，HKDF child key 也不當 TLS 私鑰。唯一未來交點＝`child[2..]` 的 audit / session 簽章（仍與 mTLS 無關）。

> **三個層界要記住**：① **L2（cop_service）= 接縫**，transport 與語意在此分離、所有 doctrine 在此蓋章；
> ② **L3↔L4 = 感知層/事故層分水嶺，也是 TAK 能力天花板**——L3 以下（含軌跡、變更稽核）TAK 都會，L4 起（severity/狀態/結案/跨源聚合）TAK 結構上沒有，是「Incident **Command**」的字面本體；
> ③ **L4「不外流」= 安全邊界**，出向只有 L3 感知標記（雙向）與 L4 衍生的下行 tasking（P2-13）兩個閘。

## 兩層 + 「來源無感」原則

| | **感知層（COP 標記）** | **事故層（事件 / 決策 / 行動）** |
|---|---|---|
| 本質 | 位置性、即時態勢快照 | 流程性、有生命週期 + 問責 |
| 成員 | marker / 線 / 區 / 單位 | event / decision / tasking |
| `source` | tak · manual（無線電上車口）· waveink · command · pi-node | —（ICS 內部） |
| 對 TAK | **可共享（#2，雙向）** | **不外流（#3）** |

**來源無感（關鍵）**：感知層**對來源無感** —— 不論 `tak`（裝置送）、`manual`（幕僚聽無線電鍵入）、`waveink`（RF）、`command`（指揮部下達），**只要是感知標記就屬同一張共享圖、都可流向 TAK client**。「共 COP」不是某個功能，是**感知層本身的雙向性**：進得來（P2-04 已做）就該出得去（P2-13 + 幾何序列化）。
> ICS 因此是「**只有無線電的單位**」進入 TAK 共享圖的**手動上車口**：非 TAK 報 → 幕僚鍵入 → 全部 ATAK 看得到。

### COP 感知層的兩態：觀察 vs 指令（`planned` 區分）

感知層**同時承載兩種語意的圖元**，由 `planned` 旗標區分——這是「指令（directive）」在分層中的定位（它**既非純觀察、亦非事件**，是 COP 層的第三態）：

| 態 | `planned` | 語意 | 典型 source |
|---|---|---|---|
| **觀察（observed）** | `false` | 「**已經在那**」——敵蹤、我方位置、感測足跡 | tak / manual / waveink |
| **指令（directive）** | `true` | 「**要去那 / 要發生**」——計畫指令、派任、行動點 | command |

兩態都是 COP 圖元、都走同一份下行基建外推；視覺上 `planned=true` = 2525 空心框（計畫）、`planned=false` = 實心框（實際）。P2-13(A) 下行已正確產出 `source=command, planned=true`。

## 報 → 事 → 決 → 行 → 結案 鏈

```
chats(報/通聯)  →  events(事)  →  decisions(決)  →  下行 tasking(行)  →  追蹤至 resolved(結案)
```

四者目前**散在四張表、彼此無導航**（缺口 P2-27）。模型要求可從任一節點導航全鏈（看一樁事的「報→事→決→行」完整脈絡）。

## 事件 vs 標記的梳理（核心，使用者 2026-06-09 提）

> **操作流 / 呈現設計 / founding-why 細節（2026-06-10 深化）→ [`cop-marker-event-decoupling.md`](cop-marker-event-decoupling.md)**（降級定義、triage 三態、N:1 多選聚合、長按關聯網、即時全貌視圖、兩 type 軸）。本節為摘要。

- **現狀**：建 event 時前端配一個 `cop_entity`（`attributes.kind='event'` + `event_id`），**JSON 名義綁定、無 FK、只能 1:1** —— 把「感知標記」與「工作流事件」黏在一起。
- **模型**：
  - **感知標記 = 一等公民**（感知層，可共享 #2）。
  - **事件 = 工作流物件**（事故層，留 ICS #3），**N:1 聚合**一或多個感知標記。
- **梳理方向**：把標記**分出來**當 sensing entity（可共享）；事件 **reference**（聚合）一或多個標記，不自帶 JSON-glued pin。分出來的標記即屬 #2 可共享集合。

## 下行共用基建（P2-13 / P2-30 邊界釐清）

「推 CoT 出去」有**兩種意圖**，但**共用一份基建**，不可各做一套：

```
            ┌─ P2-13：發「指令」（directive，planned=true）──「要發生的」
共用下行基建 ─┤
（send_cot + 幾何序列化 + audit + 共享閘）
            └─ P2-30：分享「既有感知標記」（observed）──「已觀察的」
```

- **共用基建**（一次建好）：`tak_downlink.send_cot`（A 已做，點）+ **`geometry_service.geometry_to_cot` / `build_geometry_cot`**（線/區，P2-30）+ audit + **per-entity 共享閘**（P2-30）。
- **P2-13** = 此基建的消費者之一（發指令）；**P2-30** = 另一消費者（分享觀察標記）+ 提供線/區序列化與閘。
- **依賴**：P2-13 要下「線/區指令」須等 P2-30 的幾何序列化器 → **故先做 P2-30 共用基建，再回頭補 P2-13 B/C**（2026-06-09 定路線 2）。
- **指令 ↔ 事件**：自事件衍生的派任（#5）其指令應可連結 originating event（依賴 P2-27 event↔marker 關聯）；P2-13 B/C 設計不得堵死此路。

## 不外流邊界（安全 doctrine）

**事件本體**（`status` / `decision` / `assigned_unit` / 問責鏈）**不推 TAK**。出向只有兩條：
1. **感知標記**（#2，含手動/衍生標記）——經 per-entity 共享閘（P2-30）。
2. **自事件衍生的派任指令**（#5 → P2-13 下行）。

## 缺口（→ ROADMAP Phase 2，TTX 前）

| 項 | 缺口 | 現狀（實證） |
|---|---|---|
| **P2-27** | 感知標記 / 事件 / 決策 **分層 + 導航鏈 + 事件梳理（標記分出）** | event↔cop 靠 attributes JSON（無 FK、會孤立、僅 1:1）；decisions 又獨立；報→事→決→行 四表割裂 |
| **P2-28** | **入向升級**：現場回報自動成 tracked event | MEDEVAC/GeoChat → COP/chats，**不進 events**；重大回報只在地圖閃 icon，無流程追蹤 |
| **P2-29** | **角色 / 分層感知地圖視圖** | map filter 僅 3 維（來源-kind / TAK affiliation / stale）；`cop_entities.visible_to` 預留未用；指揮官與幕僚看同一張圖 |
| **P2-30** | **感知標記共享閘 + 手動感知標記（無線電上車口）+ 線/區出向幾何序列化** | 無 per-entity「推 TAK」flag；手動感知標記非命名能力；`geometry_service` 只有入向 `extract_geometry`，無出向 `geometry_to_cot` |

> 位置 SoT 三份（events.lat/lon 死 + cop_entity.lat/lon 活 + location_zone_id）併入 P2-27 一起清。

## 19 情境 × 五層 壓力測試（模型驗證，2026-06-09）

> 把 [`tak-use-cases-config.md`](../reference/tak-use-cases-config.md) 的 19 情境逐一跑過五個概念層（L0 節點 / L2 接縫 / L3 感知 / L4 事故 / L5 指揮；L1 傳輸為純管路）後的**結構結論**。逐情境追蹤見 use-cases doc；本節只收「模型撐不撐得住」的發現。

**1. L4 觸發 = 分水嶺**（情境分兩類，判定權在幕僚收斂或 P2-28 入向升級）：
- **停 L3（純感知，不升事故）**：BFT、COP 標繪、UAV/IMINT 位置、RF emitter、`planned` 計劃標繪。
- **升 L4（事故）**：SAR 受困點、火場、HazMat 洩漏、MCI、geofence 闖入、Recon 疑敵。
- **唯一全鏈落地的 L4 範例 = MEDEVAC**（L2 `_extract_medevac` 蓋 critical → L4 incident → L5 pulse）；其餘 L4 路徑多卡 P2-27/28。

**2. 反覆撞到的結構缺角**（非單情境，跨多情境重現）：

| 缺角 | 哪些情境逼出 | 在不在上面缺口表 |
|---|---|---|
| **天氣/環境 feed**（風/雨/能見度，影響火勢·擴散·航空·淹水）| 軍用計劃·野火·HazMat | ❌ 不在（屬外部 data-feed 缺口，strategy 候選）|
| **L5 主動告警後端**（geofence/門檻 → 提示「何時決策」）| OODA Decide·設施巡邏 | ❌ 不在（L5 主動性整片空白，strategy 候選）|
| **N:1 聚合（標記→事件）** | SAR 多隊標同點·AAR 時間軸對齊 | ✅ = P2-27 |
| **接縫 L2 source stub** | SDR/WaveInk·收容 PWA·HazMat 感測器 | ✅ = `normalize_*` stub（L2 來源無感未兌現）|

**3. 模型「結構不足」的唯一一條 → 多機構（需補）**：
- 五層是**縱向、單指揮部**棧。聯合指揮（Unified Command）= 多個對等 L4 事故層。
- **缺口**：模型沒有「**L4 跨指揮部水平通道**」（事故層在對等指揮部間共享 ≠ 推給 TAK client）。
- 19 情境裡**唯一逼出「模型結構不足」的**（其餘都是「某層實作未做」）→ 對應〈不外流邊界〉需補「單指揮部 vs 對等多指揮部」界定。

**4. 橫切壓過縱向（不屬任何單層，但決定整棧能不能用）**：
- **RTF**：治理/信任邊界橫切（OPSEC 加密 L0 → 敵標可信度 L4）壓過縱向流。
- **室內結構火災**：L0 GPS-denied → **L0 感測品質是全棧下限**；L0 崩，上面四層再完整也無基底。
