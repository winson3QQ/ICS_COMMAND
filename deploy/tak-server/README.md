# deploy/tak-server/ — 官方 TAK Server 部署（ICS_Command P2-01）

對應 ROADMAP **P2-01** / [issue #99](https://github.com/winson3QQ/ICS_COMMAND/issues/99)。提供官方 TAK Server（Docker）在 ICS_Command 環境的部署 artifacts + **以內網 step-ca 取代自簽憑證**的 SOP。

> **為什麼需要本目錄**：官方 Docker 包（`takserver-docker-5.7-RELEASE-43.zip`）只附 2 個 Dockerfile，**不含 `docker-compose.yml` / `.env`**，且預設用自簽 CA。本目錄補上 compose、env 範本、step-ca 憑證腳本與部署 SOP。

## 本目錄內容

| 檔 | 用途 | 入版控? |
|---|---|---|
| `docker-compose.yml` | takserver + tak-database 兩 service | ✅ |
| `.env.example` | 參數範本（port / heap / 憑證 CN / keystore 密碼） | ✅ |
| `pki/issue-tak-certs.sh` | step-ca 簽 TAK 憑證 → JKS keystore | ✅ |
| `.gitignore` | 擋 `release/`（EULA 物）/ `.env` / `*.jks` 等 | ✅ |
| `release/` | 解壓的官方包（**gitignored**，自行取得） | ❌ |
| `.env` / `release/tak/certs/files/*.jks` | 執行期密鑰（**gitignored**） | ❌ |

## 環境需求

- **RAM ≥ 8GB**（官方最低；ROADMAP 舊寫 4GB 已更正）。TAK 起 5 個 JVM + Postgres（`shared_buffers=2560MB`）。
- **Docker / Docker Desktop**。Desktop 須把 VM 記憶體調到 **8–10GB**（Settings → Resources → Memory），否則 OOM。
- OS：Ubuntu 22.04 / Debian 12（官方支援）；macOS / Windows 經 Docker Desktop。
- **M1/Apple Silicon（arm64）**：兩個 base image（`eclipse-temurin:17-jammy` / `postgres:15.1`）皆 multi-arch，`docker build` 產**原生 arm64 image，不走 qemu 模擬**。已實測 16GB M1 可行（heap 壓低）。
- **Raspberry Pi**：官方 Docker image 偏 amd64 假設，Pi(arm64) **建議走原生 `.deb`**（`takserver_5.7-RELEASE43_all.deb`，noarch Java），不走本 compose。安裝見官方 `TAK_Server_Configuration_Guide.pdf`（包內 `tak/docs/`）。

## 部署步驟（Docker，iMac / Windows / x86 Linux）

```bash
cd deploy/tak-server

# 1. 取得官方包（需 tak.gov 帳號 + 接受 EULA；不可自動化）
#    https://tak.gov/products/tak-server → 抓 TAKSERVER-DOCKER-5.7-RELEASE-43.ZIP
#    驗 MD5（對 tak.gov 頁面公布值；5.7-43 = 7efb0743efecc63ce26782d713d942a8）
md5sum ~/Downloads/takserver-docker-5.7-RELEASE-43.zip   # macOS：md5 -q

# 2. 解壓到 release/（使 release/docker/ 與 release/tak/ 存在）
mkdir -p release && unzip -q ~/Downloads/takserver-docker-5.7-RELEASE-43.zip -d /tmp/tak-x
mv /tmp/tak-x/takserver-docker-5.7-RELEASE-43/* release/

# 3. 參數
cp .env.example .env && $EDITOR .env          # 改 TAK_KEYSTORE_PASS、TAK_HOSTNAME、heap

# 4. 設定檔（從範本複製 + 補必要值）—— 兩個都要，否則 boot 失敗（dogfood 驗證所得）
cp release/tak/CoreConfig.example.xml release/tak/CoreConfig.xml
cp release/tak/TAKIgniteConfig.example.xml release/tak/TAKIgniteConfig.xml   # ★ 不補→5 JVM 搶建會 race crash
#    CoreConfig.xml 必改：
#    - <repository><connection ... password="...">  ★ 留空→setup-db 跳過建 cot/martiuser，DB healthcheck 永遠 fail
#    - <tls> keystoreFile=certs/files/takserver.jks、keystorePass 與 .env 的 TAK_KEYSTORE_PASS 一致（default 'atakatak'）

# 5. 憑證（step-ca → JKS；先確保 deploy/step-ca/ 已 init 且 daemon 在跑）
#    ⚠ 先 patch step-ca 效期到 90 天（見腳本結尾警告 / reality check #98 drift 2）
../step-ca/start-ca.sh &        # 若尚未啟動
pki/issue-tak-certs.sh          # 產 release/tak/certs/files/{takserver,truststore-root}.jks
#    ★ step-ca 監聽 8443，與 TAK web UI(8443) 衝突 → 簽完憑證後停 step-ca（或把其一改埠）再 up
pkill -f 'step-ca .*ca.json'    # 簽完即停，釋放 8443

# 6. build + 啟動
docker compose up -d --build    # 首啟久（initdb + SchemaManager upgrade，~數分鐘）
docker compose logs -f takserver

# 7. 首次 admin 憑證（UserManager；容器內）
docker compose exec takserver bash -c \
  "java -jar /opt/tak/utils/UserManager.jar certmod -A /opt/tak/certs/files/admin.pem"
#    詳細 admin enroll 流程見包內 TAK_Server_Configuration_Guide.pdf
```

## 驗證（§8）

```bash
# A. compose 語法
docker compose config >/dev/null && echo "compose OK"

# B. 容器健康
docker compose ps                     # tak-database healthy、takserver up

# C. web UI（憑證走 step-ca root）
curl -sk https://localhost:8443/ -o /dev/null -w "web %{http_code}\n"   # 期望 200/302/401

# D. CoT streaming port 開（ICS_Command tak_service 連這裡）
nc -zv localhost 8089 2>&1            # 期望 succeeded
```

## 裝置 onboarding（iTAK / ATAK）與憑證 SAN doctrine

真機（iTAK/ATAK）連 `:8089` 比 dashboard 嚴格，dogfood（#163/#170）撞過兩關，已固化進 `pki/issue-tak-certs.sh`：

- **server cert SAN 必含裝置實際連的位址**（嚴格 hostname/IP 驗證；缺則 `IP address mismatch` → disconnected）：
  - **prod**：`TAK_HOSTNAME` 設**真實 FQDN**（cert 綁名、DNS 管 IP；公網 IP 浮動/failover 免重簽）。
  - **dev/LAN（無 DNS）**：`.env` 設 `TAK_EXTRA_SANS=<LAN IP>[,...]`（逗號分隔）補進 SAN。**勿在 prod 把浮動 IP 寫進 cert。**
- **truststore（root + intermediate 都要）**：step-ca 雙層，client 通常只送 leaf；truststore 缺 intermediate → `peer not verified`。腳本 section 3/4 已同時匯 root + intermediate。

裝置 data package（iTAK 匯入用 zip）內容慣例：

- `config.pref`：`connectString0=<host|IP>:8089:ssl`、protocol、display name。
- `<device>.p12`：裝置用 client 憑證（step-ca leaf；密碼慣例 `atakatak`，prod 改）。
- `truststore-root.p12`：含 step-ca **root + intermediate**（讓裝置信任 server）。

> 公網部署除憑證外另需 DNS A record + 防火牆（只開必要 port）+ CA 策略決策（封閉發證 vs 公開 CA），屬部署題，不在本腳本範圍。

> **這兩坑是「step-ca 路線」特性，不是 Mac/OS 限定**（換 Windows 接手者勿誤判為 Mac bug）：
> - **缺口 1（truststore 缺 intermediate）是 CA 結構問題**：本 repo 刻意用**內網 step-ca（雙層 root→intermediate→leaf）取代官方自簽 CA**（與 dashboard/federation 共信任鏈，見頂部說明）。雙層才需要 truststore 同時帶 intermediate；官方 `makeCert.sh` 是**單層**（root 直接簽 leaf），truststore 放 root 就夠 → 走官方那套天生沒這坑。
> - **缺口 2（SAN 缺裝置位址）是連線情境問題**：只在「真實機用 SAN 沒涵蓋的位址（如裸 LAN IP）連、且 client 嚴格驗 hostname」時才爆；只連 dashboard/localhost 或用設定好的 hostname 連則不會觸發。與 OS 無關，任何平台同情境都會撞。

## ICS_Command 整合接點

- **CoT TLS streaming = `:8089`** ← P2-02 `services/tak_service.py` 用 client cert（step-ca 簽，同 truststore）連此 port 訂閱 CoT。
- **CoT schema 參考**：包內 `release/tak/CoT_link.xsd` / `CoT_shape.xsd` / `CoreConfig.xsd` 是官方 CoT 格式定義 → P2-04 `normalize_cot` 解析 + XXE-safe 驗證的依據（**勿 commit，按路徑引用**）。
- TLS 由同一條 step-ca 信任鏈 → ICS_Command（dashboard / tak_service）與 TAK Server 互信，不必另建 CA。

### :8089 streaming 憑證 ≠ :8443 web UI 憑證（關鍵區分，實測定案 2026-06-05）

| | **:8443 web UI**（人類 admin 登入）| **:8089 CoT streaming**（`tak_service.subscribe`）|
|---|---|---|
| 需 UserManager enroll？ | **要**（`certmod -A` 綁 `ROLE_ADMIN`，見上「首次 admin 憑證」）| **不要** —— CA 信任的 **fullchain** client cert 即可 authenticate + 收流 |
| 憑證鏈 | 同 | **必 leaf+intermediate 串鏈**：leaf-only → `peer not verified`；無 cert → `PEER_DID_NOT_RETURN_A_CERTIFICATE` |

- :8089 預設串流 = **v0 明文 CoT XML**（server 開場推 `t-x-takp-v` TakControl 宣告 v1 protobuf 為**選配**升級，不強制）；v0-only subscriber 用 `pytak TAK_PROTO=0`。
- TLS **fail-closed**：`build_subscribe_config` 無 cafile 須顯式 `allow_insecure_tls=True` 才關 server 驗證（否則 raise），防靜默 MITM 注入偽造 CoT。
- 意涵：**COP subscriber cert 只需 CA 簽的 fullchain client cert，不必經 :8446 enrollment**（enroll 是 :8443 web/managed-cert 才需）。被擄裝置撤銷限制見 `docs/compliance/threat_model.md` §8.5。

### TAK 存取控制 = group membership（producer 不掛 `__ANON__`，#403/#404）

**源碼定讞**（官方 `X509Authenticator.java`）：TAK 對 **CA 信任的證架構上永不拒絕**——非名冊證無群即無條件落 `__ANON__`。改 `CoreConfig` 的 `<auth>` 匿名旗標**擋不掉**（`x509addAnonymous="false"` 只在 LDAP 分支生效、對 file-auth 死碼，對活機實證無效）。故「誰能看到/注入 ICS 資料」**只能靠 group 隔離**：

- **🔴 紅線：所有 ICS producer（尤其 `ics-cot` streaming 身分）一律走 named group（red/blue/neutral），永不掛 `__ANON__`。** 否則與**任何 CA 簽過的證（含已 deregister 的舊裝置）同頻** → 不明證可注入假 CoT 進 COP、亦可竊聽 ICS 廣播（#403 端到端實證）。
- `register-tak-fingerprint.sh` **已強制此紅線**：不再預設 `__ANON__`，須顯式指定 named group；要刻意進 `__ANON__` 須加 `--allow-anon`。
- **正規 `ics-cot` 註冊**（取代任何落 `__ANON__` 的舊作法）：
  ```bash
  ./pki/register-tak-fingerprint.sh --apply <ics-cot-cert.pem> ics-cot red blue neutral
  # 寫後須 restart TAK（File backend 不 hot-reload）；或對既有 user 線上改群：
  #   docker exec takserver sh -c 'cd /opt/tak && java -jar utils/UserManager.jar usermod -f <FP> -r -g __ANON__ ics-cot'
  #   （usermod -r -g __ANON__ = 移除 __ANON__，保留其餘群，live 生效免 restart）
  ```
- **撤銷（點名封殺特定證）= 層2**：見 `docs/compliance/threat_model.md` §8.3 與 [#318](https://github.com/winson3QQ/ICS_COMMAND/issues/318)——ICS 離線簽的證 TAK 帳本查無 → 須發證後補登 TAK `certificate` 表 + 開 `x509checkRevocation` 才撤得掉。
- 例外：`ics-tak-admin`（REST-only、不訂閱 :8089）仍在 `__ANON__` 無 streaming 洩漏，且移除其唯一群會 bounce 回 `__ANON__`，暫不動。

## 範圍與注意

- **本 task 不含**：實機 boot 驗證（需 release 在手 + 目標機，交付時做）、Federation Hub / federation（P2-07）、commercial plugin（只用 core CoT）、Pi 原生 `.deb` 實裝（僅文件指路）。
- **憑證效期**：step-ca 預設 24h；TAK 長跑前務必 patch `ca.json` 至 90 天（`maxTLSCertDuration: 2160h`），見 `pki/issue-tak-certs.sh` 結尾。
- **供應鏈**：TAK Server = tak.gov 官方（Apache-2.0 core，非中國）；base image eclipse-temurin（Adoptium）/ postgres 官方。release zip 不入版控。

## 已知問題（dogfood 2026-06-05，M1/16GB Docker 10GB 實測）

實機 boot 已跑通 **DB tier**（initdb + SchemaManager + `cot`/`martiuser` healthy）與 **CoT streaming `:8089`**；Ignite 叢集可達 `state=ACTIVE`。**web/API tier（`:8443`）：兩個根因皆已定位並修**（詳 [#101](https://github.com/winson3QQ/ICS_COMMAND/issues/101)）：

- ✅ **根因 #1（已修）：server 憑證必須 RSA**。TAK api 的 `jwkSource` bean 用 server cert 的 key 建 JWT 簽章源，**寫死 RSAPublicKey**；step-ca 預設 ECDSA → `ClassCastException` → api 死。`pki/issue-tak-certs.sh` 已改 `--kty RSA --size 2048`。**只看 `/opt/tak/logs/takserver-api.log`（api 專屬），別看合併的 takserver.log。**
- ✅ **根因 #2（已修）：缺 `fed-truststore.jks`**（**非 staggered start，非資源/timeout**）。讀 `takserver-messaging.log` 現形：messaging（Ignite **server** node）部署 `distributed-federation-manager` Ignite service 時，`SSLConfig` 載 `certs/files/fed-truststore.jks` → `FileNotFoundException` → `SSLContext is not initialized` → `ServiceDeploymentException` → messaging `Application run failed`。**messaging 一掛，Ignite server node 消失 → config/api 等 client 全 `IgniteClientDisconnectedException`**——api 卡 federation bean 是**症狀不是根因**（同根因#1 的 red herring 模式）。即使不開 federation（`CoreConfig.xml` 仍含 `<federation>` 區塊），此 service 無條件部署，故 `fed-truststore.jks` 必須存在。`pki/issue-tak-certs.sh` 已補產（內容 = step-ca root，同官方 `makeCert.sh` 的 fed-truststore）。
- **驗證指引**：messaging 起來看 `grep 'Started TAK Server messaging' takserver-messaging.log`；api 綁 8443 看 `netstat -tlnp | grep 8443`（容器內）。
- **不卡 P2-02**：整合走 `:8089`（已通），web UI 是人類 admin 介面，非整合路徑。
