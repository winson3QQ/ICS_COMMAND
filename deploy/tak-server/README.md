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

## ICS_Command 整合接點

- **CoT TLS streaming = `:8089`** ← P2-02 `services/tak_service.py` 用 client cert（step-ca 簽，同 truststore）連此 port 訂閱 CoT。
- **CoT schema 參考**：包內 `release/tak/CoT_link.xsd` / `CoT_shape.xsd` / `CoreConfig.xsd` 是官方 CoT 格式定義 → P2-04 `normalize_cot` 解析 + XXE-safe 驗證的依據（**勿 commit，按路徑引用**）。
- TLS 由同一條 step-ca 信任鏈 → ICS_Command（dashboard / tak_service）與 TAK Server 互信，不必另建 CA。

## 範圍與注意

- **本 task 不含**：實機 boot 驗證（需 release 在手 + 目標機，交付時做）、Federation Hub / federation（P2-07）、commercial plugin（只用 core CoT）、Pi 原生 `.deb` 實裝（僅文件指路）。
- **憑證效期**：step-ca 預設 24h；TAK 長跑前務必 patch `ca.json` 至 90 天（`maxTLSCertDuration: 2160h`），見 `pki/issue-tak-certs.sh` 結尾。
- **供應鏈**：TAK Server = tak.gov 官方（Apache-2.0 core，非中國）；base image eclipse-temurin（Adoptium）/ postgres 官方。release zip 不入版控。

## 已知問題（dogfood 2026-06-05，M1/16GB Docker 10GB 實測）

實機 boot 已跑通 **DB tier**（initdb + SchemaManager + `cot`/`martiuser` 建好、healthy）與 **CoT streaming `:8089`**；Ignite 叢集一度 `state=ACTIVE`。**但 web/API tier（`:8443`）尚未穩定**：

- **現象**：`api` JVM 起來、連上 DB（Hikari pool）、跑一下即退出 → `:8443` 容器內外皆未綁；無乾淨 logged 例外。
- **可能因（未定論）**：① 單容器 5-JVM 同起的脆弱性（首啟 `TAKIgniteConfig.xml` race，已用「先 cp 範本」緩解，叢集能組但 api 仍不留存）；② `.env` 的 `API_MAX_HEAP` 壓太低（dev 省記憶體設 1GB，TAK api 為大型 Spring app，可能不足）。
- **待辦（追蹤於 follow-up issue）**：(a) 對照社群 TAK docker（Cloud-RF / atakhq）的 JVM 啟動順序（config 先、生成 TAKIgniteConfig 後再起其餘）；(b) API_MAX_HEAP 調 2-4GB 重試；(c) 乾淨 `down -v` + 刪 TAKIgniteConfig.xml 重來，確認非 raced 殘留。
- **不卡 P2-02**：整合走 `:8089`（已通），web UI 是人類 admin 介面，非 ICS_Command 整合路徑。
