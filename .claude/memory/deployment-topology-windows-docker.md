---
name: deployment-topology-windows-docker
description: 部署拓樸——Windows 跑 Docker 為公網 prod 機（ICS cmd prod + TAK server 都在此）；驗證必須在 Windows
metadata:
  node_type: memory
  type: project
  originSessionId: 11d72cd7-1f63-44a3-8fbf-5337ce696ef6
---

部署/驗證拓樸（2026-06-20 使用者確立）：

- **公網機器 = Windows**，跑 Docker。**prod 的 ICS cmd dashboard 與 TAK server 都佈署在這台 Windows 的 Docker**。
- **dev 環境**純為開發用（CLAUDE.md 寫「主要開發機：Mac」是開發脈絡，但**公網 prod 落地點是 Windows/Docker**）。
- **工作流**：dev 開發 → dev 驗過 → 佈署到（Windows 的）Docker。
- **驗證紅線**：使用者要在 Windows 上驗（因為那是真正連公網、真正跑 prod 的機器）。**不要假設驗證在 Mac 跑** —— mTLS / nginx 端到端驗證的正解是 Docker 棧（`deploy/ics-validation/`），不是 Mac 的 `deploy/step-ca/*.sh`（那些是 bash + brew + `~/.step`，Windows 跑不動）。
- **節奏**：「**不要求快，要做對**」。
- **公網存取**：home router 已開好對外固定 IP **`1.34.230.218`** + port **443** forward 到這台 Windows Docker。外網驗證走 `https://1.34.230.218/`（手機/平板 4G 測）。mTLS server 憑證 SAN 須含此 IP（`ICS_SERVER_SANS=1.34.230.218`，見 `deploy/ics-validation/mtls/`）。原始公網直曝實測 + 缺口 → `threat_model` §8.6（那次為 throwaway，事後拆 forward；正式對外前先落地 mTLS=本 #275 + §8.6 #2 硬化=wave 4）。

**對 #275 mTLS 的影響**：要讓使用者可驗，需把 mTLS 驗證做成 Windows/Docker 可跑（nginx 容器開 `ssl_verify_client on` + 餵 root CA/client cert + 後端 `ICS_MTLS_REQUIRED=true`），且**簽證路徑也要 Windows 可行**（step-ca 進 Docker 或 Git Bash），否則產不出 client p12。見 [[issue-275-mtls-waves]]。

## 2026-06-21 — `deploy/prod` 棧 LIVE + 未進 git 的 runtime patch（⚠ 重佈署前必讀）

`deploy/prod` 單機棧（ics-prod compose）**現正跑在這台 Windows**（#305 端到端通過）。docker volumes：`ca-data`（step-ca CA）、`ics-data`（DB+綁定）、`ics-tiles`（底圖 taiwan.pmtiles）、`tak-db-data`。**正常進版（rebuild image + recreate、保留 volumes）憑證/綁定照常有效**；砍 `ca-data`=換 CA 全失效、砍 `ics-data`=綁定沒了回雞生蛋。

**現役有手 patch、未進 tracked config —— 乾淨重佈署會丟，要照 `deploy/prod/README` runbook 重做**：
1. **step-ca provisioner 效期** runtime pat 成 `maxTLSCertDuration=2160h`（改 ca-data 的 ca.json）→ compose `STEP_CLIENT_CERT_DURATION` 仍 tracked=23h（單獨調 2160h 會被 24h 預設拒）。可重現化 = #279。
2. **`.env`**（gitignored）：`ICS_SERVER_SANS=1.34.230.218 192.168.50.2 10.0.1.16`（加了 LAN IP 給內網 Windows 連 `https://10.0.1.16/`）、`ICS_MTLS_REQUIRED=true`（onboarding 完上鎖；首次須先 false，#306）。
3. **TAK 憑證用獨立 `ICS-TAK-SVC-CA`**（非 step-ca，安全隔離），CA 鑰在 `deploy/prod/tak-certs/_ca/tak-ca.key`（gitignored）；step-ca 版 JKS 備份在 `tak-certs/_rollback_stepca/`。發 TAK/裝置憑證的腳本在 `tak-certs/_cascripts/`（gitignored）。
4. **裝置憑證綁定**（commander-01/iPhone、windows-01/Windows）在 `ics-data` DB；乾淨 DB 會清掉，須重綁。

**裝置憑證 / iOS / onboarding 踩坑全文** → `deploy/prod/README` 四節 + [[ios-mtls-client-cert-packaging]]。**動 prod 前先讀那份 runbook，別重新推導。**
