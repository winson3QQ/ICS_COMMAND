# step-ca 內網 PKI（C1-B）

ICS_Command 內網 HTTPS 憑證的 ACME server。取代 mkcert 的長期方案。

## 為何選 step-ca

| 需求 | mkcert | step-ca |
|---|---|---|
| 開發機自簽 | ✅ 一行搞定 | ⚠️ 需先 init |
| 量產 22 縣市 | ❌ 每台手動 trust | ✅ 標準 ACME，systemd 自動申請 |
| 自動續期 | ❌ | ✅ ACME renew |
| Per-customer CA 隔離 | ❌ | ✅ 每客戶獨立 instance |

## Per-customer CA 隔離原則（重要）

**每個交付給縣市的部署，獨立一個 step-ca instance**：

- 縣市 A 的 CA 私鑰外洩 → 只影響縣市 A，不影響縣市 B/C
- 對應 commercialization §C6 ISO 27001 的「資安事件影響範圍最小化」
- step-ca instance 跑在客戶的 Command Console 上（同一台 N100），不是廠商雲端

## 三種使用情境

| 情境 | 腳本 | CA 位置 | 信任範圍 |
|---|---|---|---|
| 開發機（Mac）| `init-mac-dev.sh` | `~/.step/` | 開發者本機 |
| 演練前測試 | `init-mac-dev.sh` | 同上 | 演練網段所有裝置 |
| 客戶部署 | `init-customer.sh`（C3 階段交付腳本） | `/etc/step-ca/` | 該客戶內網 |

C1-B 階段只做開發機與演練測試。客戶部署腳本是 C3-B「一鍵安裝腳本」的一部分，留待 C3。

## 開發機 setup（一次性）

```bash
# 1. 安裝 step CLI（含 step-ca）
brew install step

# 2. 初始化開發 CA + 啟動 ACME provisioner
deploy/step-ca/init-mac-dev.sh

# 3. 把 root CA 加進 macOS 系統信任（瀏覽器、curl 都會信）
deploy/step-ca/trust-root-mac.sh

# 4. 發證給 Command 與 Pi
deploy/step-ca/issue-cert.sh command.ics.local 127.0.0.1
deploy/step-ca/issue-cert.sh shelter.ics.local 192.168.100.10
deploy/step-ca/issue-cert.sh medical.ics.local 192.168.100.10
```

證會產在 `deploy/step-ca/certs/<hostname>/`，含 `cert.pem` + `key.pem`。

## 與既有 mkcert 憑證的關係

`certs/192.168.100.10+2.pem` 等 mkcert 產物**保留**，作為 fallback：

- step-ca 故障時可手動切回 mkcert（`server/config.js` 的 `CERT_PATH` env 改指）
- C1-B 完成驗收後，演練成功一次再考慮淘汰 mkcert

## Cleanup

```bash
deploy/step-ca/uninstall-dev.sh   # 拆 CA、撤系統信任、清憑證
```

## 參考

- step-ca 官方文件：https://smallstep.com/docs/step-ca
- 為何不用 Let's Encrypt：內網無公網，無法做 HTTP-01/DNS-01 challenge
- 為何不用 HashiCorp Vault PKI：對 22 縣市規模過重，需專人維運
