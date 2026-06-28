#!/usr/bin/env bash
# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
# wg-peer-registrar.sh — #434 容器化 WireGuard 的 peer 控制面（ICS 驅動 WG 的接縫）。
#
# 為什麼存在（對照 TAK registrar 的 netns 難題）：
#   裝置證 ICS 已能全生命週期管（發/綁/撤），但連線那半（WG peer）原本只能手動。Windows-native
#   WG 跨 OS 邊界，容器內的 ICS 驅動不了（#434 reality check 實證：wg.exe 要 host admin、無 netns 捷徑）。
#   改把 WG 跑進 Linux 容器後，ICS 就能比照 TAK registrar 模式——經**共享卷檔佇列**下指令，本 watcher
#   在 WG 容器內（持有 wg0、有 NET_ADMIN）跑 `wg set` 即時加/刪 peer + 持久化，免 docker.sock、免 host admin。
#
# 協定（無外部依賴、injection-safe；純文字行，bash 解，不用 JSON）：
#   請求：$QUEUE/requests/<id>.req  四行＝pubkey / allowed_ip / op / label
#         （ICS 先寫 .tmp 再 rename，避免讀半寫；op = add|remove；label 僅記錄用）。
#   結果：$QUEUE/results/<id>.res    首 token＝OK|ERR，其後訊息。
#   watcher 處理後刪請求檔；ICS 輪詢結果檔（逾時＝best-effort）。
#
# 安全：pubkey / allowed_ip 一律格式驗證後才餵 wg（defense-in-depth，即使 ICS 已驗）；變數全 quote、
#   無 eval、wg 參數走 argv 不走 shell 字串。佇列僅 ICS 與本容器掛載。

set -uo pipefail
# 結果檔須讓**不同 uid 的 ICS 容器**讀得到（佇列跨容器共享）。重設 umask（entrypoint 為 server key
# 設過 077，會洩漏到此 exec 的子行程 → 結果檔變 600、ICS 讀不到 → 誤判 registrar 失敗）。
umask 022

QUEUE="${WG_QUEUE:-/wg-queue}"
REQ_DIR="$QUEUE/requests"
RES_DIR="$QUEUE/results"
IFACE="${WG_IFACE:-wg0}"
CONF="${WG_CONF:-/etc/wireguard/${IFACE}.conf}"
POLL_S="${WG_POLL_S:-1}"
# 允許的 peer IP 段（fail-closed：只接受本 VPN 子網內的 /32，擋亂配導致路由污染）。
# 注意：不可寫成 `${WG_SUBNET_RE:-...{1,3}...}`——預設值內的 `{1,3}` 大括號會提前截斷 parameter
# expansion（bash 坑）。故分兩步：先取 env（無 brace），未設才套含 `{1,3}` 的預設。
SUBNET_RE="${WG_SUBNET_RE:-}"
[ -n "$SUBNET_RE" ] || SUBNET_RE='^10\.13\.13\.([0-9]{1,3})/32$'

# WireGuard public key＝base64 32 bytes → 43 字 + '='（標準 wg genkey | wg pubkey 輸出）。
PUBKEY_RE='^[A-Za-z0-9+/]{43}=$'
OP_RE='^(add|remove)$'

log() { echo "[wg-registrar $(date -u +%FT%TZ)] $*"; }

# 持久化：把當前 runtime 狀態寫回 conf，使容器重啟後 peer 不丟（wg-quick save 會 strip 掉 PostUp/PostDown，
# 故改用 `wg showconf` 直接 dump，再以原 [Interface]（含 PostUp）+ 新 [Peer] 區塊重組——交給 entrypoint 的
# helper；此處只負責即時 `wg set`，並呼叫 persist hook 落檔。
persist() {
  # `wg-quick strip` 不存在；用 wg showconf 取目前 peer，附加到由 entrypoint 保留的 interface-only 範本後。
  if [ -x /usr/local/bin/wg-persist.sh ]; then /usr/local/bin/wg-persist.sh || log "persist 失敗（非致命，runtime 仍生效）"; fi
}

mkdir -p "$REQ_DIR" "$RES_DIR"
chmod 0777 "$QUEUE" "$REQ_DIR" "$RES_DIR" 2>/dev/null || true
log "watcher up; queue=$QUEUE iface=$IFACE poll=${POLL_S}s"

while true; do
  shopt -s nullglob
  for f in "$REQ_DIR"/*.req; do
    id="$(basename "$f" .req)"
    mapfile -t L <"$f" || true
    pubkey="${L[0]:-}"; allowed_ip="${L[1]:-}"; op="${L[2]:-add}"; label="${L[3]:-}"
    op="${op%$'\r'}"

    write_res() { echo "$1" >"$RES_DIR/$id.res.tmp" && mv "$RES_DIR/$id.res.tmp" "$RES_DIR/$id.res"; }

    if ! [[ "$op" =~ $OP_RE ]]; then
      log "reject $id: bad-op '$op'"; write_res "ERR bad-op"; rm -f "$f"; continue
    fi
    if ! [[ "$pubkey" =~ $PUBKEY_RE ]]; then
      log "reject $id: bad-pubkey"; write_res "ERR bad-pubkey"; rm -f "$f"; continue
    fi

    if [ "$op" = "add" ]; then
      if ! [[ "$allowed_ip" =~ $SUBNET_RE ]]; then
        log "reject $id: bad-ip '$allowed_ip'（須 ${SUBNET_RE} 內 /32）"; write_res "ERR bad-ip"; rm -f "$f"; continue
      fi
      out="$(wg set "$IFACE" peer "$pubkey" allowed-ips "$allowed_ip" 2>&1)"; rc=$?
      if [ "$rc" -eq 0 ]; then
        persist; log "added peer ${pubkey:0:12}… → $allowed_ip (${label})"; write_res "OK add $allowed_ip"
      else
        log "wg set add FAIL $id rc=$rc: ${out//$'\n'/ }"; write_res "ERR rc=$rc ${out//$'\n'/ }"
      fi
    else  # remove
      out="$(wg set "$IFACE" peer "$pubkey" remove 2>&1)"; rc=$?
      if [ "$rc" -eq 0 ]; then
        persist; log "removed peer ${pubkey:0:12}… (${label})"; write_res "OK remove"
      else
        log "wg set remove FAIL $id rc=$rc: ${out//$'\n'/ }"; write_res "ERR rc=$rc ${out//$'\n'/ }"
      fi
    fi
    rm -f "$f"  # 結果已落地，刪請求（at-least-once：crash 重跑冪等，wg set 本身冪等）
  done
  shopt -u nullglob
  sleep "$POLL_S"
done
