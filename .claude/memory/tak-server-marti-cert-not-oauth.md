# TAK Server = 官方 5.7 / Marti REST 走 client cert（非 OAuth2）

## 事實（有 SoT）
- 部署的是**官方 TAK Server 5.7-RELEASE-43**（tak.gov 下載，需帳號 + EULA），**非** OpenTAKServer / FreeTAKServer。
  SoT：`deploy/tak-server/README.md` 第一行「官方 TAK Server 部署」+ `docker-compose.yml` image `ics-takserver:5.7`。
- 全 PKI 走 **step-ca cert（mTLS）**：`deploy/tak-server/pki/issue-tak-certs.sh` 簽 server / client / truststore JKS，**零 OAuth2**。
- **Marti REST API（:8443）M2M 認證 = client cert（mTLS）**，官方 5.7 標準 + 與本部署一致。
  :8089 streaming 用 fullchain（leaf+intermediate），不需 UserManager enroll（enroll 只 :8443 web UI admin 要）。
- HTTP client = **aiohttp**（pytak `with_takproto` 已帶入，aio-libs / Apache-2.0 / 非中國）。pytak 本身有 `MartiTXWorker`（POST inject）/ `MartiRXWorker`（poll `GET /Marti/api/cot/sa`）先例，皆走 cert。

## ⚠️ ROADMAP P2-11 規格錯誤（已改正）
ROADMAP P2-11 原寫「Marti REST 走 **OAuth2 client credentials**（JWT Bearer），非 enrolled cert」——**錯誤**。
OAuth2 是 OpenTAKServer（第三方 fork）才有；官方 TAK Server Marti 走 cert。
P2-11（issue #138）已改正為 cert，複用既有 `TAK_CLIENT_CERT/KEY/CAFILE`，砍掉 JWT / OAuth2 SOP。

## How to apply
- 任何 TAK REST（P2-11~P2-19，`services/tak_rest_client.py`）認證一律 **client cert**
  （aiohttp `ssl.SSLContext` + `load_cert_chain`），沿用 `core/config.py` 的 `TAK_CLIENT_CERT/KEY/CAFILE`。**不要**做 OAuth2 / JWT。
- HTTP client 用 **aiohttp**，不新增 httpx。
- 未來再看到 ROADMAP / issue 提「TAK OAuth2」，記得這條 = 規格遺留錯誤，回頭核對部署（官方 5.7 = cert）。
- 相關：[[event-symbology-classification]]（同屬 TAK/COP 整合脈絡）。
