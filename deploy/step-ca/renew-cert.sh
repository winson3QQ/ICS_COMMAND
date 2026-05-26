#!/usr/bin/env bash
# renew-cert.sh — 續簽指定 hostname 的憑證
#
# 用法：./renew-cert.sh <hostname>
#
# 業界實務：cert 過期前 1/3 時間（30 天）renew 一次，systemd timer 自動跑。
# C1-B 階段手動，C3-B 一鍵安裝腳本會包進 systemd timer。

set -euo pipefail

[[ $# -eq 1 ]] || { echo "用法：$0 <hostname>" >&2; exit 1; }
HOSTNAME="$1"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="$SCRIPT_DIR/certs/$HOSTNAME"

[[ -f "$OUT_DIR/cert.pem" ]] || { echo "✗ $OUT_DIR/cert.pem 不存在，請先 issue" >&2; exit 1; }

step ca renew \
  "$OUT_DIR/cert.pem" \
  "$OUT_DIR/key.pem" \
  --force

echo "✓ 憑證已續簽：$OUT_DIR/cert.pem"
echo "  注意：使用此憑證的服務（nginx / Pi server）需 reload 才會載入新憑證"
