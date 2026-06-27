---
name: test-artifacts-reversible
description: 測試/驗證過程產生的任何東西都要可滾回；正式實作不得遺留測試殘渣
metadata:
  node_type: memory
  type: feedback
  originSessionId: 72b22dc7-5884-409b-9af4-5bc2fe7c9a5a
---

使用者要求（2026-06-27，WireGuard/TAK 隧道實測中拍板）：**測試階段產生的所有東西（憑證、設定改動、防火牆規則、安裝物、暫存檔、容器重啟）都必須「可滾回」**；改 live 前先備份原狀、記下每一筆改動的還原方式。**正式實作（formal impl）時不得留下測試殘渣**——臨時帳號/憑證/規則/檔案要清乾淨，只留正式設計要的東西。

**Why:** 這是公網 prod 機（[[deployment-topology-windows-docker]]），測試動到 live TAK/nginx cert、防火牆、router、docker stack；留殘渣 = 安全面擴大 + 之後排查困難 + working tree 髒。

**How to apply:** 動 live 前先備份（cert/keystore/.env 複本、原 CoreConfig）；測試用的 peer config、QR、臨時 cert、firewall 規則、`C:\Users\yello\ics-wg\` 等都登記為「測試產物」待清；正式落地時把它們收斂或刪除，並在 PR/issue 留「已清理」紀錄。相關工作見 [[tak-cert-access-control]] 的 #420 umbrella（Phase A WireGuard 前置）。
