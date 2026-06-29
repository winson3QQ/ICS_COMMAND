#!/bin/sh
# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
# node.sh — 測試棧 WG 節點。ROLE=keygen|server|client。金鑰執行期產生（不入 git）。
set -eu
SHARED=/shared
IFACE=wg0
log() { echo "[wgnode ${ROLE}] $*"; }

case "${ROLE}" in
keygen)
  # 產 server 金鑰 + 一把「被複製」的 client 金鑰（兩個 client 共用 → 模擬盜鑰）。
  if [ ! -f "$SHARED/server.key" ]; then (umask 077; wg genkey >"$SHARED/server.key"); fi
  wg pubkey <"$SHARED/server.key" >"$SHARED/server.pub"
  if [ ! -f "$SHARED/client.key" ]; then (umask 077; wg genkey >"$SHARED/client.key"); fi
  wg pubkey <"$SHARED/client.key" >"$SHARED/client.pub"
  log "金鑰就緒（server + cloned-client）"
  ;;
server)
  CPUB="$(cat "$SHARED/client.pub")"
  ip link add "$IFACE" type wireguard
  wg set "$IFACE" private-key "$SHARED/server.key" listen-port 51820 peer "$CPUB" allowed-ips 10.99.0.2/32
  ip addr add 10.99.0.1/24 dev "$IFACE"
  ip link set "$IFACE" up
  log "server wg0 up（單一 peer = cloned-client）"
  exec tail -f /dev/null
  ;;
client)
  SPUB="$(cat "$SHARED/server.pub")"
  ip link add "$IFACE" type wireguard
  # 兩個 client 用同一把 key + 同 tunnel IP，各從自己的容器 IP 對 server 握手 →
  # server 看到該 peer 的 endpoint 在兩個來源間擺盪（正是 cloned-key 的真實行為）。
  wg set "$IFACE" private-key "$SHARED/client.key" peer "$SPUB" \
    endpoint "${SERVER_HOST}:51820" allowed-ips 10.99.0.1/32 persistent-keepalive 5
  ip addr add 10.99.0.2/24 dev "$IFACE"
  ip link set "$IFACE" up
  log "client wg0 up → ${SERVER_HOST}:51820（keepalive 5s）"
  exec tail -f /dev/null
  ;;
*)
  log "未知 ROLE=${ROLE}"
  exit 1
  ;;
esac
