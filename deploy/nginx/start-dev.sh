#!/usr/bin/env bash
# start-dev.sh — Mac 開發機啟動 nginx 反代
#
# 流程：
#   1. 檢查 step-ca 簽的 Command 憑證存在
#   2. 動態替換 conf.d/command.conf 中的 cert/key 路徑
#   3. nginx -t 驗證語法
#   4. 前景啟動 nginx
#
# 前置：
#   brew install nginx
#   deploy/step-ca/init-mac-dev.sh
#   deploy/step-ca/issue-cert.sh command.ics.local 127.0.0.1 localhost
#   FastAPI 已啟動於 :8000
#
# /etc/hosts 須加：127.0.0.1 command.ics.local

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CERT_DIR="$SCRIPT_DIR/../step-ca/certs/command.ics.local"
CERT_PATH="$CERT_DIR/cert.pem"
KEY_PATH="$CERT_DIR/key.pem"

# ── 0. 前置檢查 ──────────────────────────────────────────────
command -v nginx >/dev/null 2>&1 || { echo "✗ nginx 未安裝。執行：brew install nginx" >&2; exit 1; }
[[ -f "$CERT_PATH" ]] || { echo "✗ 憑證不存在：$CERT_PATH" >&2; echo "  先跑：deploy/step-ca/issue-cert.sh command.ics.local 127.0.0.1 localhost" >&2; exit 1; }

# ── 1. /etc/hosts 提示 ──────────────────────────────────────
if ! grep -q "command.ics.local" /etc/hosts; then
  echo "⚠ /etc/hosts 缺 command.ics.local，執行下行加入後再跑本腳本："
  echo "  echo '127.0.0.1 command.ics.local' | sudo tee -a /etc/hosts"
  exit 1
fi

# ── 2. 產生 runtime conf（替換 placeholder）─────────────────
RUNTIME_DIR="/tmp/ics-nginx-runtime"
mkdir -p "$RUNTIME_DIR/conf.d"
cp "$SCRIPT_DIR/nginx.conf" "$RUNTIME_DIR/nginx.conf"
cp "$SCRIPT_DIR/conf.d/ssl-common.conf" "$RUNTIME_DIR/conf.d/"
cp "$SCRIPT_DIR/conf.d/security-headers.conf" "$RUNTIME_DIR/conf.d/"

# 替換 placeholder（用 | 作 sed 分隔符避免路徑中 / 衝突）
sed \
  -e "s|CERT_PATH_PLACEHOLDER|$CERT_PATH|g" \
  -e "s|KEY_PATH_PLACEHOLDER|$KEY_PATH|g" \
  "$SCRIPT_DIR/conf.d/command.conf" > "$RUNTIME_DIR/conf.d/command.conf"

# ── 3. 驗證語法 ──────────────────────────────────────────────
echo "驗證 nginx 設定..."
nginx -t -p "$RUNTIME_DIR" -c "$RUNTIME_DIR/nginx.conf"

# ── 4. 啟動 ─────────────────────────────────────────────────
echo ""
echo "啟動 nginx..."
echo "  HTTP  → 80  → 301 → 443"
echo "  HTTPS → 443 → 反代到 127.0.0.1:8000"
echo "  log:    /tmp/ics-nginx-access.log / error.log"
echo "  Ctrl+C 停止；reload：sudo nginx -s reload -p $RUNTIME_DIR"
echo ""

# :80 / :443 在 macOS 需 sudo
exec sudo nginx -p "$RUNTIME_DIR" -c "$RUNTIME_DIR/nginx.conf" -g "daemon off;"
