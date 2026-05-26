#!/usr/bin/env bash
# issue-cert.sh — 簽一張伺服器憑證
#
# 用法：
#   ./issue-cert.sh <hostname> [<additional-san>...]
#
# 範例：
#   ./issue-cert.sh command.ics.local 127.0.0.1 localhost
#   ./issue-cert.sh shelter.ics.local 192.168.100.10 ics-pi.local
#
# 結果：
#   deploy/step-ca/certs/<hostname>/cert.pem
#   deploy/step-ca/certs/<hostname>/key.pem

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "用法：$0 <hostname> [<additional-san>...]" >&2
  exit 1
fi

HOSTNAME="$1"
shift
SANS=("$HOSTNAME" "$@")

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="$SCRIPT_DIR/certs/$HOSTNAME"
PASSWORD_FILE="$HOME/.step/secrets/password"

[[ -f "$PASSWORD_FILE" ]] || { echo "✗ password 檔不存在，先跑 init-mac-dev.sh" >&2; exit 1; }

mkdir -p "$OUT_DIR"

# step ca certificate 接受多個 SAN
SAN_ARGS=()
for san in "${SANS[@]}"; do
  SAN_ARGS+=("--san=$san")
done

step ca certificate \
  "$HOSTNAME" \
  "$OUT_DIR/cert.pem" \
  "$OUT_DIR/key.pem" \
  --provisioner="admin@ics.local" \
  --password-file="$PASSWORD_FILE" \
  "${SAN_ARGS[@]}"
# 注意：預設 24h（step-ca 預設 max）。
# 生產環境改 90 天需在 ~/.step/config/ca.json 的 provisioner.claims 加：
#   "maxTLSCertDuration": "2160h", "defaultTLSCertDuration": "2160h"
# C3-B install.sh 會 patch 這段；dev 階段 24h 夠用（renew 腳本快速）

chmod 600 "$OUT_DIR/key.pem"
chmod 644 "$OUT_DIR/cert.pem"

echo ""
echo "✓ 憑證已產生："
echo "  cert: $OUT_DIR/cert.pem"
echo "  key:  $OUT_DIR/key.pem"
echo "  SAN:  ${SANS[*]}"
echo "  有效期：90 天（renew：./renew-cert.sh $HOSTNAME）"
