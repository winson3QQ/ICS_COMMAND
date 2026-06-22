#!/bin/sh
# step-ca-claims-wrap.sh — #279：把 CA provisioner 憑證效期 claims 寫成 reproducible（fresh deploy 自動 90 天）。
#
# 背景：`step ca init`（smallstep image fresh 初始化）建出的 provisioner 預設 maxTLSCertDuration=24h，
# 故 fresh deploy 發的證頂多 ~23h。先前線上是「手動 `step ca provisioner update` 放寬 2160h」，**只活在
# ca-data 卷裡、未隨 committed config** → 砍 ca-data 重佈署就退回 24h（#279 殘留）。
#
# 機制：official /entrypoint.sh 先跑 init（fresh 時），再 `exec "$@"`＝本 CMD。本 wrapper 因此跑在
# **init 之後、daemon 啟動之前**——jq 把全域 `authority.claims` 設成目標效期（覆蓋 fresh 的 24h 預設），
# 然後 exec 真正的 step-ca daemon（讀到已 patch 的 ca.json）。idempotent：已是目標值就跳過寫入。
#
# 效期由 ICS_CA_MAX_CERT_DURATION 控（預設 2160h＝90 天）；min 固定 5m（step 預設）。
set -e

CONFIG="${STEPCA_CONFIG:-/home/step/config/ca.json}"
MAXDUR="${ICS_CA_MAX_CERT_DURATION:-2160h}"

if [ -f "$CONFIG" ] && command -v jq >/dev/null 2>&1; then
  cur="$(jq -r '.authority.claims.maxTLSCertDuration // empty' "$CONFIG" 2>/dev/null || echo '')"
  if [ "$cur" != "$MAXDUR" ]; then
    tmp="$(mktemp)"
    # 全域 authority.claims 作用於所有 provisioner（fresh provisioner 無自帶 claims → 套全域）。
    jq --arg d "$MAXDUR" \
      '.authority.claims = ((.authority.claims // {}) + {minTLSCertDuration:"5m", maxTLSCertDuration:$d, defaultTLSCertDuration:$d})' \
      "$CONFIG" > "$tmp" && mv "$tmp" "$CONFIG"
    echo "[step-ca-wrap] #279：設 authority.claims maxTLSCertDuration=$MAXDUR（reproducible，原 $cur）"
  else
    echo "[step-ca-wrap] #279：authority.claims 已是 $MAXDUR，跳過"
  fi
fi

exec "$@"
