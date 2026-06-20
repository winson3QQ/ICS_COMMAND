# 公網直曝周邊防護（perimeter hardening） — #280

#275 mTLS 是身分核心；本目錄是 mTLS **之外**的縱深防線。對外（home router 443 forward）
長期擺放時，「讓服務不被任意人觸及 + 偵測/阻擋掃描」比硬擋更上游。

## ⚠ 先修前提：Docker port-publish 抹掉真實 client IP（2026-06-20 實證）

現行 Windows Docker 棧用 `ports: ["443:443"]` → **nginx 與後端看到的 client IP 全是 Docker
bridge gateway（`172.19.0.1`），不是真實外網 IP**（實測：iPhone、掃描 bot 全顯示同一個）。

**後果**：所有**per-IP** 控制失效——
- nginx `limit_req` / 後端 `rate_limit.py`（10 次/分 login）→ 全外網 client 共用一個桶。
- `fail2ban`（按 IP ban）→ 全部同 IP，ban 不了。
- #275 wave 4 的 XFF/X-Real-IP 修正邏輯**正確**，但此拓樸下 nginx 拿到的「真實 IP」就是 gateway。

**這是所有 per-IP 防護的前提**。三種修法：

| 方案 | 真實 IP? | 適用 |
|---|---|---|
| **Tailscale / WireGuard 前置**（見下，**C2 首選**） | ✅（tailnet/peer IP） | Windows/Linux 皆可，且埠不對外 |
| nginx `network_mode: host`（Linux 限定） | ✅ | prod Linux（Windows Docker 不支援 host net） |
| Cloudflare Tunnel | ✅（`CF-Connecting-IP`） | 但數據面過第三方，C2 慎用 |

## 防線（依 CP 值；對應 #280 checklist）

### B. VPN / Tunnel 前置（單一最高 CP，**C2 建議優先**）
不開 raw port-forward → 埠不對公網、掃描 bot 打不到、隱藏公網 IP，**同時還原真實 client IP**。

**C2 場景建議 WireGuard / Tailscale（非 Cloudflare）**：數據面點對點、不經第三方
（資料主權）。Cloudflare Tunnel 雖附 WAF/DDoS，但 TLS 終結在 Cloudflare／數據面過其網路。

- **Tailscale**（最省事，WireGuard mesh）：測試者裝 Tailscale → 連 tailnet 內網 IP，
  443 完全不對公網。`tailscale` 可跑成 sidecar 或 host daemon。**mTLS 仍保留**（雙層）。
- **WireGuard**（自管）：自架 WG server，測試者帶 peer config。

> 落地後即可移除 home router 的 443 forward（§8.6「正式對外前」收口）。

### A. nginx 連線層硬化（待真實 IP 還原後才有效）
- `limit_conn`（per-IP 並發連線上限）+ `limit_req`（per-IP 請求率）。
- access log 加 `$ssl_client_verify` / `$ssl_client_s_dn`（餵 fail2ban + 監控）。
- 明顯掃描路徑（`.env`/`.git`/`.php`/`wp-`…）→ `return 444`（drop，連 mTLS-400 都省）。
  ※ 注意：mTLS-on 時無證者已在 TLS 層被擋（400），location 規則對 bot 多屬縱深。

### C. fail2ban（prod Linux；需真實 IP）
watch nginx log，對反覆 `400`（無證探測）/ 掃描路徑的 IP 自動 ban（host iptables）。
Windows Docker 不適用 → prod Linux 部署用。jail/filter 範本見 `fail2ban/`（待補）。

### D. geo / IP allowlist
測試者已知地域（台灣）→ 只放台灣。需 nginx GeoIP2 module（alpine 預設無）或在
Tailscale/Cloudflare 端做。alpine nginx 走 module 較重 → 建議在前置層做。

### E. 真 CA 憑證 + OCSP stapling + HSTS preload
正式對外免自簽警告；`ssl-common.conf` 已備 stapling 開關（dev off）。

### F/G（已追蹤別處）
- F 網路層憑證撤銷（nginx `ssl_crl`/OCSP，握手即擋）= [#232](https://github.com/winson3QQ/ICS_COMMAND/issues/232)
- G at-rest 加密（PIN hash 離線防護）= P1-12（SQLCipher/LUKS）

### H. 安全監控告警
失敗握手 / 反覆 400 / 帳號鎖定 → 告警。依賴 A 的 verify-status logging。

## 現況建議（給「擺著測試」）
1. **最有效一步**：把 443 forward 換成 **Tailscale**（埠不對外 + 還原真實 IP），mTLS 續留＝雙層。
2. 真實 IP 還原後，A（nginx limit）/ C（fail2ban）才生效，再逐項補。
3. 公測前同步處理 [#279](https://github.com/winson3QQ/ICS_COMMAND/issues/279)（憑證 90 天）。
