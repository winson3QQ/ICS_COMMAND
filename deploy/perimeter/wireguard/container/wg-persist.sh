#!/usr/bin/env bash
# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
# wg-persist.sh — #434：把 wg0 當前 runtime 設定（privkey/listenport/peers）落檔，容器重啟不丟 peer。
# `wg showconf` 只 dump [Interface] 的 PrivateKey/ListenPort + [Peer] 區塊（不含 Address/PostUp，那些由
# entrypoint 另外套用），正好可被 boot 時的 `wg setconf` 原樣載回。原子寫避免讀半寫。
set -uo pipefail
IFACE="${WG_IFACE:-wg0}"
CONF="${WG_CONF:-/wg-data/${IFACE}.conf}"  # #434 fix：持久卷（同 server.key），否則 --force-recreate 丟 peer
tmp="${CONF}.tmp.$$"
if wg showconf "$IFACE" >"$tmp" 2>/dev/null; then
  mv "$tmp" "$CONF"
else
  rm -f "$tmp"; exit 1
fi
