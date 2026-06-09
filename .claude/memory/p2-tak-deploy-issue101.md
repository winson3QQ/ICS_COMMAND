# P2 TAK 部署 + #101（web tier 已解）— 2026-06-05

> **[2026-06-09 部分收編進 doc]** 架構級「:8089 streaming 憑證 ≠ :8443 web 憑證」區分 + #101 兩根因（RSA/fed-truststore）已在 `deploy/tak-server/README.md`（ops SoT）。本檔留 recall 指標 + **本機 dev env scratch**（路徑/重起步驟，非 doc SoT）。

## 現況（git）
- **#101 已關閉**（兩根因皆解）。main `eb87878`（三邊同步）。
- P2-01 merged（PR #100 `35ed3d8`/#99）；#101 根因#1 `14b5373`、根因#2 PR #104 `fa38200`（closes #101）。
- Releases：`cmd-v1.0.0`（前端）+ `command-v2.2.1`（後端；#101 PATCH，原 2.2.0）。

## P2-01 deliverable（`deploy/tak-server/`，in main）
官方 TAK Server 5.7 Docker 包不含 compose/.env，本目錄補：`docker-compose.yml`、`.env.example`、`pki/issue-tak-certs.sh`（step-ca→JKS）、README。

## 實機 boot 狀態（M1/16GB Docker 10GB）= 全通 ✅
DB tier healthy、Ignite ACTIVE、CoT `:8089`、**web/API `:8443` 起來**：messaging/api Microservice Started、Tomcat 綁 8443、mutual-TLS+admin RBAC 通、web GUI（Metrics Dashboard）可登入。

## #101 兩根因（**都是憑證問題，不是 Ignite 脆弱性 / 不需 staggered start**）
> 教訓：兩次都是「往上游一層才是真凶」的 red herring。**讀對 per-profile log**：api 看 `takserver-api.log`、messaging（=Ignite **server** node）看 `takserver-messaging.log`，別看合併的 `takserver.log`。

1. **server 憑證必須 RSA 非 EC**。api `jwkSource` bean 用 server cert key 建 JWT 簽章源，寫死 RSAPublicKey；step-ca 預設 EC → `ClassCastException` → api context 死。修：`issue-tak-certs.sh` 用 `step ca certificate --kty RSA --size 2048`。
2. **缺 `fed-truststore.jks`**。CoreConfig `<federation-server>` 引用它；即使 federation 沒開，messaging 仍**無條件部署** `distributed-federation-manager` Ignite service → `SSLConfig` 載檔失敗（`FileNotFoundException`→`SSLContext is not initialized`）→ service 部署失敗 → **messaging（Ignite server node）整個掛 → config/api client 全 `IgniteClientDisconnectedException`** → api 卡 `distributedFederationHttpConnectorManager` bean → :8443 不綁。api 的 disconnect 是**症狀**。修：`issue-tak-certs.sh` 補產 `fed-truststore.jks`（內容=step-ca root，同官方 `makeCert.sh`）。

## admin web UI enroll SOP（人類登入 :8443，需 client 憑證）
1. step-ca **離線**簽 client 憑證（step-ca daemon 聽 8443 會跟 TAK 撞，故離線簽）：
   `step certificate create admin admin.crt admin.key --ca ~/.step/certs/intermediate_ca.crt --ca-key ~/.step/secrets/intermediate_ca_key --ca-password-file ~/.step/secrets/password --profile leaf --not-after 2160h --no-password --insecure`
   （CN=admin；EKU 含 clientAuth）。憑證已存 `deploy/step-ca/certs/admin/`。
2. 註冊 admin：`docker cp admin.crt takserver:/opt/tak/certs/files/admin-ics.pem` → `docker exec -w /opt/tak takserver java -jar /opt/tak/utils/UserManager.jar certmod -A /opt/tak/certs/files/admin-ics.pem`（→ Role ROLE_ADMIN）。
3. p12 給瀏覽器：**用 homebrew openssl@3 不用 LibreSSL**（LibreSSL 預設 RC2，新 macOS 移除 RC2→匯入 -26276）：
   `/opt/homebrew/opt/openssl@3/bin/openssl pkcs12 -export -in admin.crt -inkey admin.key -certfile ~/.step/certs/intermediate_ca.crt -name "ICS TAK admin" -out admin.p12 -passout pass:atakatak`（AES，可匯入）。
4. **curl 驗（最省事，免瀏覽器/Keychain）**：`cat admin.crt intermediate_ca.crt > admin-chain.crt`；`curl --cert admin-chain.crt --key admin.key -k https://localhost:8443/Marti/api/version`（→200）。⚠ curl `--cert` 只送 leaf，須用 leaf+intermediate 串成的 chain，否則 server 缺中間層→`certificate unknown`。
5. 瀏覽器 GUI：Chrome/Safari 用系統鑰匙圈（會問 Mac 登入密碼）；想免密碼用 Firefox 並把 `security.osclientcerts.autoload` 設 false + 匯 p12 進 softoken。

## 本機 dev env（gitignored，下次重起免重弄）
- `deploy/tak-server/release/`（官方包）、`.env`（heap API=2048/MSG=1536/CONFIG=768）、`release/tak/certs/files/{takserver,truststore-root,fed-truststore}.jks`（**RSA + fed-truststore 已補**）、CoreConfig.xml、TAKIgniteConfig.xml
- Docker image `ics-takserver:5.7` + `ics-takserver-db:5.7`（arm64）、volume `ics-tak_tak-db-data`（DB 已 init）皆保留
- **重起最簡**（憑證已齊、不需重簽）：開 Docker Desktop → `cd deploy/tak-server && docker compose up -d`，盯 `takserver-messaging.log` 的 `Started TAK Server messaging` + `takserver-api.log` 的 `Tomcat started on ports 8443`。
- 重簽憑證才需 step-ca：`pki/issue-tak-certs.sh`（先確保 ~/.step root/intermediate 在；它現在會一次產三個 JKS 含 fed-truststore）。

## P2-02 ✅ 完成（2026-06-05，#102/#103 + #106/#109）+ P2-04 ✅（#105/#108）
- **Wave 0+1**（PR #103 `d5deaad`）：`parse_cot_xml()` XXE-safe（defusedxml）+ `CoTEventIn`→`schemas/tak.py` 補 CoT 必填 start/how/version。
- **Wave 2**（PR #109 `e7358fb`）：`tak_service.subscribe()` pytak[with-takproto] mTLS 連 :8089、`readuntil(</event>)` 分幀、濾 `t-x-takp-v`、斷線退避重連、只呼叫 `ingest_cot_event`。
- **活 server 端到端閉環實測 PASS**：推 CoT → subscribe → 真 `ingest_cot_event`（#105 `28490cf`）落 `cop_entities`。

### :8089 streaming 憑證 ≠ :8443 web UI 憑證（**關鍵區分，實測定案**）
| | :8443 web UI（人類 admin 登入）| :8089 CoT streaming（subscribe）|
|---|---|---|
| 需 UserManager enroll？ | **要**（`certmod -A` 綁 ROLE_ADMIN，見上 enroll SOP）| **不要** —— CA 信任的 fullchain client cert 即可 authenticate + 收流 |
| 證據 | — | log：無 cert→`PEER_DID_NOT_RETURN_A_CERTIFICATE`；只送 leaf→`peer not verified`；送 **leaf+intermediate fullchain**→握手過 + 開始收 CoT，全程無 enroll |
- 8089 預設串流 = **v0 明文 CoT XML**（server 開場推 `t-x-takp-v` TakControl 宣告 `TakProtocolSupport version="1"`，v1 protobuf 是選配升級，不強制）。v0-only subscriber 用 pytak `TAK_PROTO=0` 即可。
- fullchain 教訓同 line 27 curl `--cert`：leaf-only 必 `peer not verified`，要 leaf+intermediate 串鏈。
- 供應鏈：pytak/takproto = snstac/Greg Albrecht（美國），Apache-2.0/MIT，非中國。
- TLS **fail-closed**：`build_subscribe_config` 無 cafile 須顯式 `allow_insecure_tls=True` 才關 server 驗證（否則 raise），防靜默 MITM 注入偽造 CoT。

## 下一步（P2-03，ROADMAP 已記）
- **lifespan wiring**：`subscribe()` 只是函式，app 啟動沒人跑它 → main.py FastAPI lifespan launch 成背景 task + shutdown cancel。沒這步 CoT 不會在 production 流入。
- **client cert 正式簽發**：擴充 `pki/issue-tak-certs.sh` 出 COP subscriber 的 fullchain client cert（現只簽 server/truststore/fed-truststore）。實測時是臨時手簽丟 /tmp（已刪）。
- **config plumbing**：`core/config.py` 加 env-driven cot_url + cert 路徑。
- 下游落地層 P1 已建好（CoPEntity v1 / cop_entity_repo / cop_service.ingest_cot_event）。
