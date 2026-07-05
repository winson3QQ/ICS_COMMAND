#!/usr/bin/env bash
# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
# entrypoint.sh — #434 容器化 WireGuard server。bring up wg0 + wg0→TAK/dashboard 的 DNAT 路由 + 啟動
# peer 控制面 watcher。設計為「ICS 可驅動的 WG」（取代 Windows-native WG 跨 OS 邊界驅動不了的死路）。
#
# 路由 doctrine：裝置只認 connectString `10.13.13.1:8089`（沿用既有，遷移免重發證/重配）。本容器 wg0=
#   10.13.13.1，把進 wg0 的 :8089/:8446 DNAT 到 takserver、:443 DNAT 到 nginx（皆同 icsnet），回程
#   MASQUERADE。服務 IP 開機時解析；容器重啟若服務 IP 變需重跑（compose depends_on + restart 緩解）。
set -euo pipefail

IFACE="${WG_IFACE:-wg0}"
CONF="${WG_CONF:-/wg-data/${IFACE}.conf}"  # #434 fix：持久卷（同 server.key），否則 --force-recreate 丟 peer
WG_ADDRESS="${WG_ADDRESS:-10.13.13.1/24}"
WG_PORT="${WG_PORT:-51820}"
KEY_FILE="${WG_KEY_FILE:-/wg-data/server.key}"
# DNAT 目標（容器名，icsnet 內解析）+ port。
TAK_HOST="${TAK_HOST:-takserver}"; NGINX_HOST="${NGINX_HOST:-nginx}"

log() { echo "[wg-entrypoint $(date -u +%FT%TZ)] $*"; }

# ── 1. server 私鑰：持久化在 volume，跨重啟穩定（與已發裝置 config 的 server pubkey 對得上）──
mkdir -p "$(dirname "$KEY_FILE")"
if [ ! -s "$KEY_FILE" ]; then
  (umask 077; wg genkey >"$KEY_FILE")  # 子殼 scope umask，不洩漏到後續/watcher（結果檔須 ICS 可讀）
  log "產生新 server 私鑰 → $KEY_FILE（pubkey：$(wg pubkey <"$KEY_FILE")）"
else
  log "載入既有 server 私鑰（pubkey：$(wg pubkey <"$KEY_FILE")）"
fi
# 導出 server pubkey 到共享佇列卷，供部署者填 ICS 的 WG_SERVER_PUBKEY（裝置 .conf 的 [Peer] PublicKey）。
WG_QUEUE="${WG_QUEUE:-/wg-queue}"
mkdir -p "$WG_QUEUE" 2>/dev/null || true
wg pubkey <"$KEY_FILE" >"$WG_QUEUE/server.pub" 2>/dev/null || log "server.pub 導出略過（佇列卷未掛?）"

# ── 2. wg0：載入持久化的 peers（若有）＋套 interface 設定 ──
ip link add dev "$IFACE" type wireguard 2>/dev/null || true
if [ -s "$CONF" ]; then wg setconf "$IFACE" "$CONF" || log "setconf 警告（首啟無 peer 屬正常）"; fi
wg set "$IFACE" private-key "$KEY_FILE" listen-port "$WG_PORT"
ip address replace "$WG_ADDRESS" dev "$IFACE"
ip link set "$IFACE" up
log "wg0 up：address=$WG_ADDRESS port=$WG_PORT"

# ── 3. 路由：ip_forward + DNAT（wg0 進來的 :8089/:8446→TAK、:443→nginx）+ 回程 MASQUERADE ──
sysctl -q -w net.ipv4.ip_forward=1 || log "ip_forward 設定失敗（compose sysctls 應已預設；忽略續行）"
resolve() { getent hosts "$1" | awk '{print $1; exit}'; }
TAK_IP="$(resolve "$TAK_HOST" || true)"; NGINX_IP="$(resolve "$NGINX_HOST" || true)"
log "DNAT 目標：TAK=$TAK_HOST($TAK_IP) NGINX=$NGINX_HOST($NGINX_IP)"
dnat() {  # $1=dport $2=dest_ip $3=dest_port
  [ -n "$2" ] || { log "跳過 DNAT :$1（目標未解析）"; return; }
  iptables -t nat -A PREROUTING -i "$IFACE" -p tcp --dport "$1" -j DNAT --to-destination "$2:$3"
}
dnat 8089 "$TAK_IP" 8089
dnat 8446 "$TAK_IP" 8446
# 8443 = Marti REST API（mission / Data Sync / Enterprise Sync 附件上傳 / 頻道）——TAK 標準 client 面
# 埠，非純 admin。現場 ATAK 分享附件、建 Data Sync feed 皆需之（#503 上行實證：不開則附件永遠上
# 不了 server、ICS 拉不到）。安全＝三重閘：WG-only（-i wg0 非公網）+ 強制 mTLS（8443 拒無 client
# cert，實測 TLSV13_ALERT_CERTIFICATE_REQUIRED）+ Marti group/role 授權（admin 端點仍需 admin cert）。
# 與已開放的 :8089 同一種 mTLS 保護。
dnat 8443 "$TAK_IP" 8443
dnat 443  "$NGINX_IP" 443
iptables -A FORWARD -i "$IFACE" -j ACCEPT
iptables -A FORWARD -o "$IFACE" -m state --state RELATED,ESTABLISHED -j ACCEPT
iptables -t nat -A POSTROUTING ! -o "$IFACE" -j MASQUERADE
log "路由就緒（DNAT + MASQUERADE）"

# ── 4. peer 控制面 watcher（前景，PID 1 守著）──
log "啟動 peer 控制面 watcher"
exec /usr/local/bin/wg-peer-registrar.sh
