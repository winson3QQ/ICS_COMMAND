---
name: tak-enrollment-working
description: TAK Certificate Enrollment(:8446)端到端打通的配方 — TAK Aware data package 死路的正解
metadata:
  node_type: memory
  type: project
  originSessionId: 72b22dc7-5884-409b-9af4-5bc2fe7c9a5a
---

**2026-06-27 實證打通**：TAK Aware 1.8.1 改走 **Certificate Enrollment(:8446 CSR)** 連上 TAK(callsign 3QQ-takaware 連上 :8089),繞過 data-package 的 `-25300` app 缺陷（見 [[tak-aware-datapackage]]，二度確認 package 路全死）。這是 [[tak-cert-access-control]] #420 umbrella 的 #411 Phase B 落地。

## 配套順序（VPN 先、CA 後）
WireGuard 前置（[[deployment-topology-windows-docker]] 本機）先把 TAK 公網入口關掉，CA 才上線：
- router 刪 `:8089` forward；`:8446` 本來就**沒** forward（docker `0.0.0.0` publish ≠ 公網可達，路由器才是真相）；`:443`/`:51820` 留。
- 手機 WG split-tunnel(`AllowedIPs=10.13.13.0/24`)連 `10.13.13.1`。
- TAK server cert 重簽加 `IP:10.13.13.1` SAN（用 **ICS-TAK-SVC-CA** 重簽，非 step-ca；issuer 不變裝置才信，見 [[tak-device-cert-ca-topology]]）。

## Server 端 enrollment 設定（live：deploy/tak-server/release/tak/）
1. **CoreConfig.xml** 加 `<certificateSigning CA="TAKServer">` + `<TAKServerCAConfig keystore="JKS" keystoreFile="/opt/tak/certs/files/signing-ca.jks" keystorePass="atakatak" validityDays="365" signatureAlg="SHA256WithRSA"/>` + `<nameEntries><nameEntry name="O" value="ICS"/><nameEntry name="OU" value="COP"/></nameEntries>`。語法照 `/opt/tak/CoreConfig.example.xml`（別猜）。
2. **signing-ca.jks** = ICS-TAK-SVC-CA(`_ca/tak-ca.pem`+`tak-ca.key`)→ openssl p12 → keytool JKS。簽出證被 :8089 truststore(=同 CA)信任。CA 私鑰上線進 TAK = blast radius，靠 VPN 收公網壓住。
3. **密碼帳號**：`UserManager.jar usermod -p '<pw>' <user>`（會 bcrypt + 熱套用免 restart）。

## 三個會卡死的坑（都實際踩過）
- **別手寫 UserAuthenticationFile 的密碼帳號**：`password="..." passwordHashed="false"` 明文 → auth 直接 401；之後 usermod 去**更新**它會 `IllegalArgumentException: Invalid salt`（把明文當 bcrypt 讀）。**解：先 `usermod -D <user>` 刪掉壞的，再 `usermod -p` 全新建**（passwordHashed 變 true）。
- **密碼複雜度**：UserManager 要求 **≥15 字元 + 大寫+小寫+數字+特殊符號**（如 `IcsTakEnroll2026!`），不合格 usermod 靜默不生效。
- **CSR 必含 nameEntries DN**：signClient 驗 CSR 的 subject 要 `O=ICS/OU=COP/CN=<username>`，只給 CN → `CSR validation failed!` 500。TAK Aware 會先 `GET /Marti/api/tls/config` 拿 nameEntries 再建 CSR 故合格；**手測 curl 要自己補 O/OU**。

## 自測指令（server 端驗，免動手機）
`GET /Marti/api/tls/config`(Basic auth)應 200 回 nameEntries；`POST /Marti/api/tls/signClient/v2?clientUid=x&version=1.8` 帶 `O=ICS/OU=COP/CN=<user>` 的 CSR 應 200 回憑證。

## 測試殘渣待清（[[test-artifacts-reversible]]）
aware1 為**測試帳號**（明文記 `IcsTakEnroll2026!`，正式要換/RBAC）；signClient 自測產的 `enroll-selftest` 證進了 certadmin 帳本；`_devicepkgs/wg-itak-test*`、`wgtest2*`、桌面 `ICS-WG*.zip`、`C:\Users\yello\ics-wg\` peer configs 皆待清。rollback 備份在 `deploy/prod/tak-certs/_bak-wg-ea86203/`。

## [2026-06-27] #429 面板主導發證落地（PR #430，真機 PASS）
ICS dashboard「發裝置證」改走**代理 enrollment**（`services/tak_enrollment.py`）：admin cert REST `new-user` 建 managed user → CSR → `:8446 signClient` → 證進帳本。iTAK 經面板發證走 WG 連 :8089 成 **managed user(neutral)** 實證。`TAK_ENROLL_URL` 設了走 enrollment、否則回退 #315 offline（零破壞）。
**4 個 dogfood 坑（都實證）**：① new-user body 漏 `groupListIN/OUT` → TAK `Arrays.asList(null)` NPE 500（同 #344 三欄坑，三欄都帶 group）② **signClient/v2 回 JSON `{"signedCert": base64-DER}` 非裸 PEM** → 解 JSON 抽 signedCert 包 PEM ③ `enroll_status` 要 `"ok"`（前端認的成功值，非 "enrolled" 否則誤報未同步）④ package connectString 用 `TAK_DEVICE_CONNECT_HOST`，cutover 關公網後要設 **WG IP `10.13.13.1`**（裝置走隧道連 :8089，否則連公網被關的埠）。dogfood orphan 帳號 3QQ-iTAK/iTak/iTAK33 待 delete-user 清。
