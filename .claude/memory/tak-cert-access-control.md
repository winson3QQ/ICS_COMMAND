---
name: tak-cert-access-control
description: "TAK 裝置證生命週期/存取控制（#398/#401 已上、#403 進行中）+ 關鍵 TAK 認證事實"
metadata:
  node_type: memory
  type: project
  originSessionId: 0ce2986b-a122-4dc9-a757-37f02a55d890
---

ICS↔TAK 裝置證管理現況（2026-06-25 dogfood 大進展）。**[[tak-cert-ca-topology]]** / [[tak-outbound-geochat-dm]] 相關。

**已上 prod（backend-v2.19.0 / frontend-v1.15.0）**：
- #398（CLOSED）：cert 清單顯示 TAK 同步狀態/fingerprint + 發證防呆（Slice1）；撤銷連動 deregister（usermod -D）+ 對帳端點 reconcile（讀 UserAuthenticationFile 比對 ICS vs TAK）（Slice2）。registrar 協定加 op（deregister/reconcile，向後相容第 4 行）。
- #401（CLOSED）：cert 面板**改以 TAK server 為 SoT**——列 TAK 上所有 managed user，殭屍可「從 TAK 移除」。新端點 `POST /api/admin/tak/users/{callsign}/deregister`。infra 防護 `_is_infra_callsign`（大小寫不敏感，ics-cot/ics-tak-admin 不可刪）+ registrar `is_infra_user`。

**🔑 關鍵 TAK 事實（dogfood 實證，多條推翻先前認知）**：
1. **TAK 信任整張 CA（ICS-TAK-SVC-CA）** → 任何 ICS 簽過的證都能建 TLS。**刪 managed user ≠ 擋裝置**：deregister 後該證仍**匿名連入（`__ANON__`）**，只是沒陣營身分。真擋要 #403 或 #318。
2. **中文 callsign 的證「連得上」**（我先前說「死證/連不上」是**錯的**）——CA 信任 → 匿名進、server 端 username mojibake、無陣營群。只是 enroll 不進名冊（usermod 拒非 ASCII）。
3. **TAK input `auth="file"` = 帳號/密碼認證，不是「憑證 CN 要在名冊」**——設它會**打死所有純憑證 client（含 ICS 自己 ics-cot）**。2026-06-25 #403 翻它實測 ICS↔TAK 斷、已 rollback。**別再用 auth="file" 當鎖匿名的槓桿。**
4. **TAK config 行為 ≠ CoreConfig.xsd 文件**：`x509addAnonymous` schema 寫預設 false 卻仍匿名 → 任何 CoreConfig 改動都要對活機實測、別憑文件。
5. dashboard TAK **綠燈 = ICS 端 socket 連著 + last_cot 新鮮度**（`tak_service.py _tak_status["connected"]`=「:8089 socket 是否連著」），**不等於 TAK server 真接受訂閱**——auth=file 下 socket 連、TAK 拒訂閱，燈仍綠但 subscriptions/all 空（小 gap，沾 #164「燈號不謊報」，待修）。權威看 TAK 端 `Marti /api/subscriptions/all`（用 admin 證 `ics-tak-admin`）。

**查在線/對帳指令**（admin 證在 ics-command 容器 `/tak-certs/ics-tak-admin.fullchain.pem`+`.key`）：`GET https://takserver:8443/Marti/api/subscriptions/all`（權威在線）；reconcile op 走 registrar 共享卷佇列讀 UserAuthenticationFile。

**#403（2026-06-26 大破解，兩層解法皆實證）= TAK 存取控制收歸 ICS**：原目標「非名冊證直接拒」。**[源碼定讞 — 推翻整個 CoreConfig 槓桿前提]** 讀 TAK 官方 `X509Authenticator.java`（`TAK-Product-Center/Server` main）：對 CA 信任的證，`auth()` **架構上永不拒絕**——無群就無條件 `doAnonAssignment(user)` 落 `__ANON__`（line 363–385）；`isX509AddAnonymous()`（line 351）只在 **LDAP 分支**內，對 file-auth 是死碼 → **`x509addAnonymous="false"` 對 file-auth 完全無效**（解釋 6/26 翻開關失敗）。**唯一真拒絕 = 撤銷**（line 133–141：`isX509CheckRevocation()` → `takCertRepository.findOneByHash(fp)` 命中且有 `revocationDate` → `RevokedException`）。**故「CoreConfig 白名單擋連」與 TAK 架構衝突、死路定讞**，下次別再翻 `<auth>` 匿名旗標。

**兩層解法（皆對活機端到端實證）**：
- **層1 = group 隔離（默認全擋，立即可用、免 restart）**：非名冊證落 `__ANON__` 擋不掉，但只要 **`__ANON__` 是死群** 就無害。漏洞根因＝`ics-cot` 掛在 `__ANON__`（與任何 CA 證同頻）。**實證**：非名冊 `ics-marti-read` 注入 `__ANON__` self-SA → ICS `cop_entities` **真的 ingest**（漏洞）；`UserManager usermod -f <fp> -r -g __ANON__ ics-cot`（保 red/blue/neutral、live 生效免 restart）後 → 同注入 **ICS 收不到**（漏洞關閉）。**已套 prod 保留**（ics-cot 現群＝red/blue/neutral）。`ics-tak-admin` 仍 `__ANON__`（REST-only 不 stream，無 streaming 洩漏；移除會 bounce 回 __ANON__）。enroll 預設群＝neutral（裝置本就不落 __ANON__）。
- **層2 = 撤銷（點名封殺，= #318 正解）**：**TAK `certificate` 表空的** → ICS **離線簽**證（ICS-TAK-SVC-CA，TAK 不經手不記帳）TAK 不認得 → 預設**撤不掉**（findOneByHash 撲空）。**實證**：手插 `certificate`(hash=證 SHA-256 冒號大寫, revocation_date=now) + `<auth x509checkRevocation="true">` + restart → 該證連 :8089 **TLS 過但 server 立刻踢斷（recv 0）**、REST **500**、subscriptions 不在線；`ics-cot`（不在表）正常。→ **撤銷有效，前提＝ICS 發證後補登進 TAK 帳本（別繞過 TAK）**。已完整 rollback（刪 row + 還原 config）。

**架構定論**：ICS 要無漏洞管 TAK＝層1（producer 不掛 `__ANON__`、隔離為地基）＋ 層2（證補登 TAK DB + checkRevocation、逐證封殺）。離線簽＝握發證權但繞過 TAK 是撤不掉的根源；補登可兼得。**落地 TODO**：①「ics-cot/producer 不掛 __ANON__」寫進 provisioning（否則 TAK rebuild 回退）②發證流程補登 `certificate` 表 + 開 checkRevocation（#318）。

**[2026-06-26 #318 reality check — 推翻 issue 的 CRL 規劃，方案 C(DB) 定案]**：兩條撤銷路活機實證。**CRL（`<security><tls><crl crlFile>`）只擋 :8443 Tomcat、不擋 :8089 串流**（source 兩接點：ServerConfiguration→Tomcat 認、SSLConfig→messaging 不認此掛點；實測撤銷證 :8443 被拒但 :8089 照連，連開 x509checkRevocation 也一樣——**checkRevocation 只查 DB 不查 CRL 檔**）。**DB 撤銷（INSERT `certificate`(hash+revocation_date) + x509checkRevocation）擋 :8089(主威脅)+:8443、且 live 免重啟**（flag 一次性開需 restart，之後 INSERT 即時生效，findOneByHash per-auth query）。CRL 還缺 CA DB（ICS 裸 `x509 -req` 簽、只有 .srl 無 index.txt）+ 無 REST 補登端點（certadmin 只管 TAK 自發證）→ 只能直寫 postgres。**方向＝方案 C**（唯一覆蓋 :8089）。耦合代價：ICS 直寫 TAK postgres（汰 dev pw `takdevpass123`+schema 耦合→隔離成 adapter）。tak_device_certs 已記 fingerprint=certificate.hash 鍵。全文 GitHub #318 comment。CoreConfig 備份 `pre403.bak`/`pre403flip-0626`/`pre403revtest`（皆已還原至裸 `<auth>`）。**reality-check 全文在 GitHub #403 comments**。**殘留**：誤建空帳號 `--help`（無證不可用，UserManager dash-parse 刪不掉，待清）。

**現況**：TAK 名冊只剩 ics-cot + ics-tak-admin（2 命脈）+ 殘留空帳號 `--help`；裝置都未 enroll（要用前重發 ASCII + enroll）。**ics-cot 群＝red/blue/neutral（已移除 __ANON__，層1 修復保留中）**；ics-tak-admin 仍 __ANON__。**#397**（入向 DM→ICS，OPEN）。branch `claude/ecstatic-knuth-295684` 有 2 個未 merge commit（#4 health 收口 + 容器化），待決留/丟。
