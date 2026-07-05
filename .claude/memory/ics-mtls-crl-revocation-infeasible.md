---
name: ics-mtls-crl-revocation-infeasible
description: "ICS 登入證握手層撤銷(nginx ssl_crl)在現行 2 層 step-ca PKI 下不可行；App 層撤銷+WG 撤 peer 為機制(#232軌1)"
metadata:
  node_type: memory
  type: reference
---

**#232軌1（ICS mTLS 登入證「網路層/握手層」撤銷）判定：現行 PKI 下不可行，S4 擱置。** 撤銷實務靠 **App 層即時失效**（`account_cert_repo.is_cert_active` 逐 request 查，撤即下個 request 失效）＋ **撤證連動撤 WG peer**（失竊裝置同時失 VPN，網路層雙斷，threat_model §8.5/§8.9）。

**S4（nginx `ssl_crl`）為何不可行（2026-07-05 端到端診斷 + 實測鎖死）**：
- PKI 為 **2 層**：Root CA → Intermediate CA → leaf(client 證)。`/pki/ca-bundle.crt` = root + intermediate。
- nginx 設 `ssl_crl` 會對驗證棧開 **`X509_V_FLAG_CRL_CHECK_ALL`＝整鏈每張非 root 證都要有對應 CRL**（nginx 既定行為）。
- 需兩份 CRL：① intermediate 簽的(蓋 leaf) ② **root 簽的(蓋 intermediate)**。step-ca `/crl`（含 `?ca=root`，實測同一份）**只發 ① intermediate 的**（cryptography 解 issuer=Intermediate CA、AKI=intermediate SKI、空名單）；step-ca **無 OCSP**（/ocsp 404）。
- → intermediate 那層無 CRL → OpenSSL `unable to get CRL` → **拒全部證(含未撤銷)**。**實測**：ssl_crl 開→合法拋棄式證 HTTP 400、關(空 include+reload)→307。＝「空 CRL 不是放行全部，是找不到就拒」。
- **不能簡單修**：`step crl` 無 create(僅 inspect)、step-ca 容器無 python/openssl；手生空 root CRL 需**動 root CA 私鑰**（step-ca 加密 secrets）→ 搬鑰在別處簽=**違反 CA 鑰隔離紅線**([[tak-device-cert-ca-topology]] / threat_model §8.3)。其餘解都是大工(單層 CA 重構全部現役證 / 架 OCSP responder)。

**合規面（非硬違規）**：外部無硬性強制 TLS 層 CRL(federal PIV/FICAM 才硬要，不適用)；NIST 800-52 是「should」best-practice、IA-5(2)「檢查憑證狀態」App 層逐 request 查可主張達標、800-207 ZT 反而偏好 App 層逐 request。§8.5 是**自認列 + #232 追蹤 + 有補償控制**＝良好稽核姿態，非隱藏違規。

**保留為 dormant 地基（業主 2026-07-05 拍板「基建先留著」）**：**S1**(撤銷同步 step-ca revoke，serial 串接，PR #500 **open 未部署**) + **S2**(step-ca ca.json `crl.enabled`+`generateOnRevoke`+`cacheDuration 168h`+`renewPeriod 24h`，**live**) + **S3**(nginx 容器 entrypoint 背景 4h 抓 CRL→base64 轉 PEM→落 `crl-data` 卷，**live 但無人用**，`deploy/prod/crl-refresh.sh`+`nginx-entrypoint.sh`)。**未來 PKI 改單層 / 加 OCSP / step-ca 能發 root CRL 時,S4 可在此地基重試。**

**S4 部署鎖死事故的處置範式（可複用）**：改握手層認證前**先用拋棄式證實打**(python `ssl.load_cert_chain` → `HTTPSConnection('nginx',443)`,看 307 vs 400)，非只 `nginx -t`(只驗 CRL 能否解析、不驗會不會擋合法證)。緊急解鎖＝寫空 `ssl_crl.conf` + `nginx -s reload`(fail-safe include 設計)。相關 [[tak-cert-access-control]](軌2 TAK 裝置證撤銷=寫 TAK DB certificate 表，走不同路且已做)。
