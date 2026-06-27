# WireGuard 前置 — #280 item B（#411 enrollment 的安全配套）

> **決策（2026-06-27）**：周邊 VPN 前置選 **WireGuard 自建**（非 Tailscale SaaS、非 Headscale）。
> 理由：供應鏈最小且全自管（mainline kernel + 官方 client，無第三方控制面 / 無 SaaS coordination
> metadata）、單一公網 IP 友善（只開一個 UDP port）、且**你的拓樸有公網 server 端點 → 現場裝置漫遊
> 由 WireGuard 內建 endpoint 自動更新 + `PersistentKeepalive` 涵蓋**（Tailscale 的 DERP relay 只在
> 「兩端都無公網端點」才需要，你用不到）。完整取捨見 GitHub #280 / 上層 [`../README.md`](../README.md)。
> Headscale 已排除（control 端點對單一公網 IP 最費工）。

## 這一步在整條鏈的位置

本 runbook = **Phase A（keystone）**。落地後才做 **Phase B（#411：把 `ICS-TAK-SVC-CA` 上線 TAK 改走
signClient enrollment）**——因為 #411 要 CA 私鑰進 TAK，唯有先把 TAK 的 `:8089`/`:8446` 收進私網、
不對公網，這個「CA 私鑰上線」的 blast radius 才被壓住（攻擊者得先進 VPN 才碰得到 TAK）。**順序不可顛倒。**

## 拓樸

```
                    家用路由器（單一公網 IP）
                    forward: UDP 51820 → 主機     ← 只剩這一個對外 port
                    （移除原 TCP 443/8089/8446 forward）
                              │
                     ┌────────┴─────────┐
                     │  Windows prod 主機 │  WireGuard server（Wintun）
                     │  wg0: 10.13.13.1   │  Docker stack 照常跑在本機
                     └────────┬─────────┘
        ┌──────────────┬──────┴───────┬──────────────┐
   操作員筆電        現場 ATAK        現場 iTAK        …（每台一份 peer config）
  10.13.13.10      10.13.13.20      10.13.13.21
   → :443 mTLS      → :8089 CoT      → :8089 CoT
```

**雙層防護不變**：進得了 tailnet/WG 私網 ≠ 進得了系統；mTLS 憑證登入照舊＝第二道鎖。

## A. Server 安裝（Windows prod 主機，一次性）

### A1. 裝官方 WireGuard for Windows
- 來源：<https://www.wireguard.com/install/>（WireGuard LLC / Jason A. Donenfeld；GPLv2；Wintun driver
  同源）。**供應鏈紅線：無中國實體、無 SaaS、無外部控制面。** 正式版前釘安裝包 hash。

### A2. 產 server 金鑰 + 建 tunnel
WireGuard GUI →「Add Tunnel」→「Add empty tunnel」會自動產一組金鑰；或 PowerShell：

```powershell
# server 金鑰（GUI 已自動產可略）
wg genkey | Tee-Object -FilePath server.key | wg pubkey | Out-File server.pub -Encoding ascii
```

tunnel 設定（GUI 貼這段；`<SERVER_PRIVATE_KEY>` 用 A2 產的）：

```ini
[Interface]
Address    = 10.13.13.1/24
ListenPort = 51820
PrivateKey = <SERVER_PRIVATE_KEY>

# ── 每台現場/操作裝置一個 [Peer]（A4 為每台產），範例： ──
# [Peer]
# PublicKey  = <DEVICE_PUBLIC_KEY>
# AllowedIPs = 10.13.13.20/32
```

啟用 tunnel（「Activate」）。`wg show` 應見 interface up。

### A3. 路由器收口（**這一步才是「埠不對外」**）
1. **新增** UDP `51820` forward → 本機。
2. **移除** 原本的 TCP `443` / `8089` / `8446` forward（443 你先前已關；8089/8446 仍直曝 → 一併移除）。
   移除後，公網掃描 bot 對 TAK 串流/enrollment 埠**根本連不到**。

### A4. 為每台裝置產 peer（每台一組金鑰）
```powershell
wg genkey | Tee-Object -FilePath dev-atak.key | wg pubkey | Out-File dev-atak.pub -Encoding ascii
```
- server tunnel 加一個 `[Peer]`：`PublicKey = <dev-atak.pub 內容>`、`AllowedIPs = 10.13.13.20/32`（每台唯一 IP）。
- 改完 server tunnel 後在 GUI 重新 Activate（或 `wg syncconf`）。
- 對應的裝置端 config 見 [C 節](#c-現場裝置上手手機平板筆電)。

## B. 埠收口 enforcement（Windows Defender Firewall — 可靠機制）

> **為何不靠 Docker 綁定 host IP**：Windows Docker Desktop（vpnkit/WSL2 代理）對「publish 綁定到特定
> host IP」行為不一定可靠（未在本機實測前不寫死，Debug 規則）。**Windows 防火牆規則才是可靠手段**：
> 路由器已不轉發 → 公網已斷；防火牆再把這幾個埠限縮到 WG 子網＋localhost，擋掉 LAN / 其他介面誤觸。

> **公網之所以擋住，靠的是 A3 移除 port-forward（根本無對外入口），不是這條防火牆。** 防火牆只負責
> 處理**其他本地介面**（指揮所 LAN、host 連到的其他 WiFi）誤觸 → 所以放行可信 LAN **不會**把 TAK 重新
> 曝給公網，#411「CA 上線只在私網可達」前提不動。

PowerShell（系統管理員；放行 WG 子網 + **指揮所可信 LAN** + 本機，擋其餘）：

```powershell
# 把 <LAN_SUBNET> 換成指揮所內網（如 192.168.1.0/24）；多段就多列。沒有同網現場裝置可省略它。
$allow = @('10.13.13.0/24','<LAN_SUBNET>','127.0.0.1')
$ports = '443','8089','8446'
foreach ($p in $ports) {
  New-NetFirewallRule -DisplayName "ICS-WG-only $p" -Direction Inbound -Protocol TCP `
    -LocalPort $p -RemoteAddress $allow -Action Allow
  # 明確擋其他來源（縱深；放在 Allow 之後，Block 優先級高）
  New-NetFirewallRule -DisplayName "ICS-block-public $p" -Direction Inbound -Protocol TCP `
    -LocalPort $p -RemoteAddress 'Any' -Action Block
}
# WireGuard 對外只留這一個
New-NetFirewallRule -DisplayName "ICS-WG-listen" -Direction Inbound -Protocol UDP `
  -LocalPort 51820 -RemoteAddress 'Any' -Action Allow
```

> ⚠ Docker Desktop 有時自帶寬鬆的 inbound 規則（`com.docker.backend`）→ 上面的 Block 需確認**優先**。
> 套用後務必照 [D 節](#d-驗證) 從**外部**驗 8089/8446 真的連不到。

## 連帶調整（Phase A 一起改，否則裝置連不上新位址）

現場/操作裝置改走 `10.13.13.1` 後，幾處「位址寫死」要補 SAN / connectString：

1. **nginx server cert SAN** ＝ `ICS_SERVER_SANS` 補 `10.13.13.1`（操作員 `https://10.13.13.1/` 才不報名稱不符）。
   `deploy/prod/.env` → `up -d --force-recreate ca-bootstrap` → `restart nginx`（步驟見 [`../../prod/README.md`](../../prod/README.md) 內網存取節）。
2. **TAK server cert SAN** 必含 `10.13.13.1`（嚴格 ATAK client 否則 `IP mismatch` 斷線）。
   重簽：`deploy/tak-server/pki/issue-tak-certs.sh`（SAN 加 WG IP）。
3. **iTAK/ATAK data package 的 connectString** ＝ `10.13.13.1:8089:ssl`（取代公網 IP）。
   產包邏輯在 `command-dashboard/src/services/tak_device_cert.py`（#315）→ Phase B 一併校準。

## C. 現場裝置上手（手機/平板/筆電）

每台裝置一份 config（`<DEVICE_PRIVATE_KEY>` ＝ A4 那台的私鑰、`<PUBLIC_IP>` ＝你家用公網 IP）：

```ini
[Interface]
PrivateKey = <DEVICE_PRIVATE_KEY>
Address    = 10.13.13.20/32

[Peer]
PublicKey           = <SERVER_PUBLIC_KEY>      # A2 的 server.pub
Endpoint            = <PUBLIC_IP>:51820
AllowedIPs          = 10.13.13.0/24            # 只把 ICS/TAK 私網導進隧道（非全流量）
PersistentKeepalive = 25                       # ★ 撐住 cellular CGNAT 對應、保漫遊不斷
```

- **iOS / Android**：裝官方 **WireGuard** app（App Store / Play）→ 匯入 config（可在桌機把上面 config 轉
  QR：`wg-quick` 體系用 `qrencode`，掃碼最省事）→ 開啟隧道。
- 隧道是**系統層 VPN** → ATAK / iTAK 當一般 app 跑在其上，連 `10.13.13.1:8089` 即可。
- **操作員瀏覽器**：連 `https://10.13.13.1/static/commander_dashboard.html` → 憑證 + PIN（**iOS 仍須 Safari**）。

### 網路路徑分情境（cellular / WiFi / 同 LAN）

| 裝置所在 | 路徑 | 結果 |
|---|---|---|
| cellular | 撥出 UDP → 公網端點 | ✅ 通；漫遊自動更新 endpoint、keepalive 撐 CGNAT |
| **外部 WiFi**（與 server 不同網路） | 撥出 UDP → 公網端點 | ✅ 同 cellular。**邊角**：極端受限 WiFi 擋出向 UDP 51820 → handshake 失敗（見下） |
| **同一條 LAN**（指揮所內網，與 server 同 router） | ❌ 走 WG 連公網 IP = NAT hairpin，多數家用 router 不 loopback | **改走 LAN 直連**（見下） |

**漫遊不斷**：裝置 cellular ↔ wifi 切換換了來源 IP → 因為它**主動撥出**到公網 server 端點，server
**自動更新該 peer 的 endpoint**、連線不斷；`PersistentKeepalive = 25` 撐住 CGNAT 對應。真正卡 WireGuard
的「兩端都無公網端點」情境，在這個有公網 server 的拓樸下不存在。

**同 LAN（NAT hairpin）解法**：指揮所內、與 server 同 router 的裝置**不必走 WG**——直連 host 的 LAN IP
（`https://<host-LAN-IP>/` 或 `<host-LAN-IP>:8089`），由 [B 節防火牆](#b-埠收口-enforcementwindows-defender-firewall--可靠機制)
的 allow-list 納入指揮所 LAN 子網放行。公網仍因 A3 移除 forward 而完全擋住 → #411 containment 不變。
（該 LAN IP 須在 `ICS_SERVER_SANS` / TAK server cert SAN，見上方「連帶調整」。）

**受限 WiFi 擋 UDP 51820**：這是純 WireGuard 的弱點（Tailscale 能 DERP-over-TCP-443 繞，WG 不行）。
緩解：ListenPort 改挑寬鬆 UDP port（甚至 443/udp）；真打不通那台 fallback cellular，或單獨投 Tailscale。

> **安全網（近乎不可逆地廉價）**：Tailscale 數據面也是 WireGuard、mTLS 與裝置設定不變。萬一某電信
> 對稱 CGNAT 或受限 WiFi 個案打不通該台，事後單獨改投 Tailscale 是 drop-in，不影響 #411。

## D. 驗證

1. **WG handshake**：裝置開隧道後，server `wg show` 該 peer 有 `latest handshake`；裝置端能 `ping 10.13.13.1`。
2. **埠真的收口（從外部，非本機）**：用**不在 WG 內**的網路（如手機關 WG 走純 cellular）對公網 IP：
   ```bash
   nc -vz -w3 <PUBLIC_IP> 8089   # 期望：timeout / refused（已不對外）
   nc -vz -w3 <PUBLIC_IP> 8446   # 期望：timeout / refused
   nc -vzu -w3 <PUBLIC_IP> 51820 # 期望：通（WG 唯一對外）
   ```
3. **業務鏈**：開 WG → `https://10.13.13.1/` 憑證+PIN 登入；ATAK 連 `10.13.13.1:8089` 推 CoT → ICS `/api/tak/status` connected、COP 收到。

## 供應鏈摘要（過中國紅線）

| 元件 | 來源 | 備註 |
|---|---|---|
| WireGuard protocol | Linux mainline kernel | 極小已稽核 codebase |
| WireGuard for Windows + Wintun | WireGuard LLC（J. Donenfeld，US）GPLv2 | 官方，無 SaaS、無外部控制面 |
| 行動 client | 官方 WireGuard app（App Store / Play） | 同上 |

**無 gitee/alibaba/tencent/baidu/huawei、零 China-origin、無第三方協調 metadata。** 正式 sign-off 前
釘安裝包 hash（與既有 image digest 釘版同列供應鏈待辦）。
