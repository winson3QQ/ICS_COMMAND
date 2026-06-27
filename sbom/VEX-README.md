# SBOM 漏洞管理 + VEX + 真實性（#417 收尾）

稽核（資安管理法 / ISO 27001 / EU CRA / NTIA）查的不是「有沒有 SBOM」，是**有沒有流程**：
每 release 掃漏洞 → 分流（修 or 說明不影響）→ 可驗真。本檔定義該流程。

---

## 1. 漏洞掃描（每 release）
release build 後（PROCESS 步驟 9.5，產 `sbom/releases/backend-v<ver>.cdx.json` 之後）：

```bash
grype sbom:./sbom/releases/backend-v<ver>.cdx.json        # Anchore，非中國
# 或：trivy sbom ./sbom/releases/backend-v<ver>.cdx.json
```

分流規則：
- **Critical / High / Medium 且可利用** → **出貨前修**（升相依版 / patch）。
- **不可利用**（漏洞代碼不在執行路徑、元件未真正使用、base-OS 非 ICS 路徑）→ 寫 **VEX statement**（見 §2），隨 SBOM 交付，掃描器即不再噪報。
- **Low / Negligible / Unknown** → 記錄、由定期 base-image 更新覆蓋，除非升級為 High。

## 2. VEX（Vulnerability Exploitability eXchange）
VEX = 對「SBOM 裡某 CVE 是否真的影響本產品」的正式聲明。格式採 **CycloneDX VEX**（`analysis.state` ∈ `resolved/exploitable/not_affected/under_investigation` + `justification`）或 OpenVEX。grype/trivy 可吃 VEX 抑制已分析項。

每筆 not_affected 至少記：CVE、元件、`state=not_affected`、`justification`（如 `code_not_present` / `vulnerable_code_not_in_execute_path` / `component_not_configured`）、判定人、日期。

## 3. 目前基線（2026-06-27，backend image `release-ea86203-0627.0933`）
grype 掃 image SBOM（773 元件）結果：
- **Critical 0 / High 0 / Medium 0 / Low 0** ✅
- Unknown 39 + Negligible 9 = **48 筆，全為 Debian base-OS 套件**（util-linux/libmount/libsmartcols 等未評分 CVE），**非 ICS Python 相依**。
- **政策**：base-OS 低危/未評分 → 由「定期 rebuild 拉新 `python:3.12-slim`」覆蓋，**不逐筆 VEX**；任一升 High 才開 VEX/修補。ICS 直接相依（`sbom/python-runtime.cdx.json`，11 個）零 Critical/High。

> 掃描結果會隨漏洞 DB 更新而變——**每 release 重掃**，勿引用本基線當永久結論。

## 4. SBOM 真實性（防竄改，cosign）
SBOM 本身不證明沒被改 → 需簽章。**現況 blocker**：
- 無容器 registry（prod 跑本機 `ics-command:dev`，未推 registry）。
- 無簽章金鑰（待 #348 key 階層 / FIDO2-HKDF 決定金鑰來源）。

計畫（待上述解除）：
- **檔級**（registry 無關，可先做）：`cosign sign-blob --key <k> sbom/releases/backend-v<ver>.cdx.json > <...>.sig`，隨交付物附 `.sig` + 公鑰。
- **映像級**（push registry 後）：`cosign attest --type cyclonedx --predicate <sbom> <image@digest>`，客戶 `cosign verify-attestation` 驗真。
- 自動化：CI 復活（修 billing / 自架 runner）後接進 release pipeline。

## 5. 給稽核 / 客戶看的（人怎麼讀）
- 漏洞報告：`grype sbom:<f>`（看 0 critical/high）。
- 持續監控：上傳 **OWASP Dependency-Track**（元件/版本/授權/CVE/跨版告警）。
- 元件/授權表：`jq -r '.components[] | "\(.name)\t\(.version)\t\(.licenses[0].license.id // \"-\")"' <f>`。

## 待辦（#417）
- [ ] 把 §1 grype 掃描接進 release 流程（PROCESS 9.5 / 可加 `scan_release.sh`，Critical/High 即 fail）。
- [ ] cosign 簽章（待 registry + 金鑰決策 + CI/自架 runner）。
- [ ] 首個正式 tagged release 起，隨 SBOM 附掃描報告 + VEX（若有）。
