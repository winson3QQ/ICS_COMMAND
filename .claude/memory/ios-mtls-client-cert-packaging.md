---
name: ios-mtls-client-cert-packaging
description: iPhone 裝 ICS 儀表板 mTLS client 憑證的正確打包法（legacy p12 + mobileconfig + Safari）
metadata:
  node_type: memory
  type: reference
  originSessionId: 5dcda818-d64b-414d-a772-6051c592fa83
---

iPhone 連 ICS 儀表板（nginx mTLS :443）需 client 憑證。2026-06-21 實機（iPhone iOS 18.7）踩通的硬性要求，三條缺一不可：

1. **必用 Safari，不能用 Chrome**：iOS 的 Chrome（UA `CriOS`）**不支援 client 憑證 mTLS**，連上只會 `400 / No required SSL certificate was sent`（nginx log `verify=NONE`）。只有 Safari 會出示系統 keychain 的身分憑證。

2. **必走 .mobileconfig，別給裸 .p12**：裸 .p12 在 iOS 常被當純文字預覽（Files/Chrome/Gmail 開 → 只顯示 XML，不觸發安裝）。用 `deploy/ics-validation/mtls/make-ios-profile.py` 把 root CA + p12 包成一個 `.mobileconfig`（密碼內嵌、一鍵裝「信任根 CA + 裝置身分」）。傳檔只能走 **AirDrop（從 Mac）或 Apple 內建「郵件」附件** 才會觸發描述檔安裝；Files 預覽/Chrome/Gmail 不會。安裝後「已下載描述檔」出現在**設定最上方**或 設定→一般→VPN與裝置管理。

3. **p12 必為 legacy 格式（最關鍵的坑）**：`step certificate p12` 與 openssl 3.x 預設產 **PBES2/AES-256-CBC/SHA256** p12，**iOS 讀不了卻誤報「憑證密碼不正確」**（密碼其實是對的，是格式不相容）。必須用 `openssl pkcs12 -export -legacy ...` 重產成 **pbeWithSHA1And3-KeyTripleDES-CBC + SHA1 MAC**（舊式），iOS 才接受。驗證：`openssl pkcs12 -info -legacy` 應見 `3DES`/`SHA1`，非 `AES-256`/`SHA256`。

完整流程（commander-01 為例，憑證由 deploy/prod 的 `issue-client` 發、step-ca 簽）：
```
# 1. legacy p12（從 issue-client 產的 client.crt/key 重打包）
openssl pkcs12 -export -legacy -in client.crt -inkey client.key -name <CN> -passout pass:<PASS> -out <CN>-ios.p12
# 2. mobileconfig（容器跑，--user root 因 out/ 屬 root）
python make-ios-profile.py <CN> <CN>-ios.p12 <PASS> root_ca.crt https://1.34.230.218/ <CN>-ios.mobileconfig
# 3. AirDrop/郵件 → iPhone 安裝（輸手機解鎖密碼，p12 密碼已內嵌）→ Safari 連
```
**產品已修（backend-v2.7.2 / #307）**：面板「發憑證」`cert_issuance.issue_p12` 已加 `--legacy` → 線上發的 p12 直接是 iOS 相容格式，不必再手動 repackage。⚠ 面板發證 p12 密碼 = `STEP_CLIENT_CERT_P12_PASS`（**預設 `icsclient`**，非 CLI issue-client 的 `ICS_CLIENT_P12_PASS` hex），且面板目前不顯示密碼（#307 子缺口）。完整 onboarding/憑證/LAN/效期 runbook 已固化在 `deploy/prod/README.md` 四節（**動 prod 憑證前先讀**）。

成功訊號：nginx log `verify=SUCCESS cn="<CN>"`。部署拓樸見 [[deployment-topology-windows-docker]]，TAK 裝置包（iTAK/ATAK）打包見同棧 `gen-device-pkg.sh`。
