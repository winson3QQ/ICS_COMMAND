---
name: issue-275-mtls-waves
metadata:
  node_type: memory
  type: project
  originSessionId: 11d72cd7-1f63-44a3-8fbf-5337ce696ef6
---

**✅ 已完成並 merge**：PR #277（merge commit 進 main，2026-06-20），#275 CLOSED。`/code-review`+`/security-review` 0 HIGH/MED。對外實機驗證（iPhone Safari 4G + mTLS）通過。Follow-up：#278 跨平台 enrollment 產品化、#279 公測憑證 23h→90 天。下方為各 wave 細節。

#275 角色分級 mTLS 鑑權（全角色 PIN + 裝置憑證 = AAL2）分波狀態（2026-06-20）：

- **Wave 1**（已在 main，PR #276 `0a3490f`）：後端消費 `X-Client-Cert-CN`，migration v28（`accounts/sessions.cert_cn`）、`ICS_MTLS_REQUIRED` 旗標、cert-bound session。
- **Wave 2**（branch `feat/issue-275-mtls-wave2` commit `937d7a8`）：nginx mTLS 單埠 443 強制版 `command-mtls.conf.disabled`（取代舊 8443 tier3 stub）、**trusted-header 剝除**（非 mTLS block 剝 `X-Client-Cert-*`）、systemd `--host 0.0.0.0`→`127.0.0.1`（後端只經 nginx 可達）。
- **Wave 3**（commit `b068cc5`）：**per-device** 裝置憑證（migration v29 `account_certs` 表，一帳號多裝置）+ App 層撤銷（撤即時失效，不靠 CRL）+ `deploy/step-ca/issue-client-cert.sh`（step-ca offline 簽 client cert+p12）+ admin API `/api/admin/accounts/{username}/certs`（GET/POST/DELETE，sysadmin only）。
- **Wave A**（commit `401bde2`）：🐞 修 wave 2 nginx 變數 bug（`$ssl_client_s_dn_cn` 不存在→emerg，改 `map $ssl_client_s_dn → $ics_client_cn`，Docker 實證）+ Windows/Docker 全角色 mTLS 端到端驗證棧（`deploy/ics-validation/docker-compose.mtls.yml` + `mtls/`：step-cli 離線簽、nginx ssl_verify_client on、ICS_MTLS_REQUIRED=true）。**本機實測矩陣全過**：綁定證+對 PIN→200／錯 PIN→401／不持證→400／未綁定 CN→401／撤銷後→401。
- **Wave B**（commit `b532959`）：admin console（系統管理員後台·帳號卡）加「🔑 裝置憑證」面板——per-device 列出/綁定/撤銷，接 wave 3 API，sysadmin only。**活 app 瀏覽器實測**（preview）全流程過。
- **Wave B-2**（commit `ecd0840`）：**面板線上發證（選項 i 安全版）**——後端不持 CA 鑰，呼叫 step-ca daemon（provisioner token）請簽 → p12 下載 + 自動綁定。`services/cert_issuance.py`（step CLI subprocess）+ `POST /certs/issue`（503 未配置/502 daemon 拒/409 重複）+ Dockerfile 帶 step CLI + 前端「發憑證/僅綁定」鈕。**驗證棧重構為單一 CA = step-ca daemon**（修缺陷：原 pki-init 另造 CA 與 daemon 不同源→線上發的證 nginx 不認；ca-bootstrap 改向 daemon 簽 server 憑證+truststore）。**Windows/Docker 端到端實測**：API 發證→200+合法 p12+自動綁定；daemon 簽的證過 nginx mTLS 登入（對 PIN 200/錯 PIN 401/不持證 400）。
  - ⚠ 發證關鍵雷：stock nginx 用 `$ssl_client_s_dn`（全 DN）+ map 抽 CN（非 `$ssl_client_s_dn_cn`，那會 emerg）；step 證 subject 有 CN；驗證棧 ics-data 是 external 持久卷，跨 run 測試資料會殘留（綁定衝突）。
- **Wave 4**（commit `8cd212f`）：§8.6 #2 硬化——PBKDF2 100k→600k（迭代數編進 hash「<iters>$<hex>」、舊裸 hex 相容、登入透明 rehash、hmac.compare_digest）、`_client_ip` XFF 信任修正（`ICS_BEHIND_PROXY` 時信任 nginx X-Real-IP、否則忽略可偽造 XFF）、鎖定-DoS 緩解（持綁定裝置證者鎖定不擋、錯 PIN 不上鎖 → 攻擊者無證可鎖、本人持裝置永不被鎖死）。test_wave4_hardening 11。

**✅ 對外實機驗證通過（2026-06-20）**：iPhone Safari 走 4G 連 `https://1.34.230.218/`，mTLS 全鏈通——`/api/dashboard` 200、COP entities 200、basemap 206、**WebSocket 101（即時串流）**。公網→路由 443→nginx mTLS→持證+CN 綁定+PIN→進儀表板。
**⚠ 產品關鍵雷（iOS）**：**iOS 上 Chrome/Firefox/Edge（UA=CriOS 等，被 Apple 強制用 WebKit 殼）拿不到系統鑰匙圈的 client 憑證，mTLS 出不了證 → 一律 400**。**只有原生 Safari 能做 client-cert mTLS**。現場 iOS 人員必須用 Safari。Android/桌機（Edge/Chrome 用 OS 憑證庫）正常。
**⚠ 公網曝險實證**：曝出去一小時內即有外網掃描 bot 打 `/config/.env`、`/ecosystem.config.js`、`PROPFIND /`（找洩漏密鑰），全被 mTLS 擋 400 → mTLS 有效，但驗完須依 §8.6 收掉 443 forward。
- **iOS enrollment 工具**：`deploy/ics-validation/mtls/make-ios-profile.py` 把 root CA + p12 包成 `.mobileconfig`（清楚名稱/說明、內嵌密碼免打、CA 自動信任）。跨平台 enrollment 安裝包（接進面板發證、依平台吐對應包、CN 有意義命名、簽章 .mobileconfig）= 待做 follow-up。
- **Wave 5 ✅ 完成**：docs 收尾 + ROADMAP tick + 關 issue 全做完。
- **✅ 全部已 merge 進 main**（PR #276 wave 1 + #277 wave 2/3/4/A/B/B-2；#275 CLOSED）。後續衍生 issue 皆已開：#278（跨平台 enrollment 產品化，OPEN）、#279（憑證效期 23h→90 天，OPEN）、#280（公網周邊防護硬化，OPEN，紅隊修補系列已 merge：nginx↔後端共享密鑰堵 header 偽造 + 安全監控告警 + fail2ban/限速）、#232（憑證撤銷/CRL/被擄裝置 SOP，OPEN）。

**設計決策**：
- 身份模型 = **per-device**：一台裝置 = 一張憑證；一帳號可註冊多台裝置（多張，各自獨立簽發/撤銷）。沒有「一台多張」。業界主流，TAK 自己也是。
- 撤銷 = App 層綁定撤銷（DB status='revoked'，login + check_session 查表，即時失效；CRL 留作後續加固）。
- **發證模型（2026-06-20 決定）= (ii) 離線簽 + 面板綁定**：憑證由 step-ca **離線簽**（`issue-client-cert.sh`，CA 簽發鑰**不進 ICS 後端**）；admin 面板只做**綁定 CN↔帳號 / 列裝置 / 撤銷**（授權決策，app 該管的），**不在後端簽證**。
  - 理由：跟 TAK `gen-device-dp.sh` 同模式 + 瀏覽器 mTLS 實務（p12 匯入）一致；CA 鑰留 step-ca → blast radius 最小（後端被打穿也不能亂簽證）。
  - ~~否決 (i)~~ **2026-06-20 改採 (i) 安全版**（使用者：交付給縣市政府，現場部署者不會跑 docker/bash → 發證必須 GUI 化）。安全版 = 後端**不持 CA 鑰**，改呼叫 **step-ca daemon**（provisioner token）請它簽：面板「發憑證」鈕 → 後端產 key+CSR → step-ca 簽 → 回傳 p12 下載 + 自動綁定 CN。CA 鑰始終只在 step-ca daemon（被打穿賠的是可撤銷的 provisioner，非 CA 鑰）。代價：棧要加 step-ca daemon + 後端串 step CLI/JWK。Wave B-2。
  - 通用 IT 黃金標準其實是「裝置自產私鑰 + CSR + CA 簽（ACME/SCEP/EST），私鑰永不離裝置」——本案兩選項都弱一階（server 生 p12 再傳）；**車隊規模再升級 step-ca ACME / MDM**，現在不需要。
- **憑證管理 UI 落點（2026-06-20 改定 = 2a）= 統一 admin console（P2-26 的家）**：
  - 建一個 admin console 殼，**ICS 登入裝置證**那塊做完做對（綁定/列裝置/撤銷，Wave 3 API 已備）。
  - **TAK cert/role 分頁 = 只留接縫/骨架**，內容待解鎖再填——**卡 P1-12**（TAK cert 私鑰 at-rest 儲存對齊 P1-12 邊界，#177 明寫；[[issue-275-mtls-waves]]）。
  - 撞車盤點（為何不現在硬做 TAK 那側）：[#255](https://github.com/winson3QQ/ICS_COMMAND/issues/255)=P2-26 L2「TAK 連線設定 dashboard 化」進行中（另一 session，connection 設定切片）；[#232](https://github.com/winson3QQ/ICS_COMMAND/issues/232)=憑證撤銷/CRL/被擄裝置撤銷 SOP（跟 #275 撤銷重疊）；[#226](https://github.com/winson3QQ/ICS_COMMAND/issues/226)+=P1-12 key mgmt（擋 TAK cert 金鑰儲存）。
  - **A（Windows/Docker mTLS 驗證棧）**：step-ca 進 Docker 供簽證 + validation nginx 開 `ssl_verify_client on` + 後端 `ICS_MTLS_REQUIRED=true` → runbook 讓使用者在 Windows 驗「持證進/不持證擋」。

**可驗缺口（關鍵）**：mTLS 端到端**還不能在 Windows 驗**。缺 (A) Docker 驗證棧接 mTLS、(B) Windows 簽證路徑、(C 可選) 憑證管理前端面板。登入頁前端**不需改**（mTLS 在 TLS 握手層，瀏覽器出示 p12 後照常 PIN 登入）。見 [[deployment-topology-windows-docker]]。

**憑證 UI 與 P2-26 的區分**：[[issue-275-mtls-waves]] 的憑證 = **ICS dashboard 登入**的裝置證；**P2-26（#177）= TAK Server 端**的 cert/role/group 管理（Marti `/federatecertificates`、`/groups`），兩者不同子系統但為「ICS 交付產品·sysadmin·每變更 audit」同地兄弟。#275 的 cert 管理 UI 目前**非獨立 roadmap item**，自然落點是 account-admin 面板旁，或與 P2-26 admin console 並置。
