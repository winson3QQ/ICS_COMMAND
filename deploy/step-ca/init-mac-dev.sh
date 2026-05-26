#!/usr/bin/env bash
# init-mac-dev.sh — Mac 開發機初始化 step-ca（一次性）
#
# 結果：
#   - ~/.step/ 建立 dev CA（CA name: ICS_Command Dev CA）
#   - 啟動 ACME provisioner（acme）
#   - root CA 公鑰位於 ~/.step/certs/root_ca.crt
#   - CA password 寫入 ~/.step/secrets/password（chmod 600）
#
# 跑完後：
#   1. ./trust-root-mac.sh  把 root 加進系統信任
#   2. ./issue-cert.sh <hostname> [<ip>]  發證

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CA_NAME="ICS_Command Dev CA"
CA_DNS="localhost"
CA_ADDR=":8443"
PROVISIONER="acme"

# ── 0. 前置檢查 ──────────────────────────────────────────────
command -v step >/dev/null 2>&1 || {
  echo "✗ step CLI 未安裝。執行：brew install step" >&2
  exit 1
}

if [[ -d "$HOME/.step" ]] && [[ -f "$HOME/.step/config/ca.json" ]]; then
  echo "⚠ ~/.step/ 已存在 CA。若要重建，先跑 ./uninstall-dev.sh"
  echo "  目前 CA：$(step ca config 2>/dev/null | grep -E 'name|dnsNames' | head -2 || echo '無法讀取')"
  exit 0
fi

# ── 1. 產生 CA password（隨機 32 字元，存檔保護權限）─────────
mkdir -p "$HOME/.step/secrets"
chmod 700 "$HOME/.step/secrets"
PASSWORD_FILE="$HOME/.step/secrets/password"
if [[ ! -f "$PASSWORD_FILE" ]]; then
  openssl rand -base64 24 | tr -d '\n' > "$PASSWORD_FILE"
  chmod 600 "$PASSWORD_FILE"
  echo "✓ CA password 已產生：$PASSWORD_FILE"
fi

# ── 2. step ca init（standalone CA，含 ACME provisioner）─────
step ca init \
  --name="$CA_NAME" \
  --dns="$CA_DNS" \
  --address="$CA_ADDR" \
  --provisioner="admin@ics.local" \
  --password-file="$PASSWORD_FILE" \
  --provisioner-password-file="$PASSWORD_FILE"

# ── 3. 加 ACME provisioner（給 Pi/Command 自動申請用）────────
step ca provisioner add "$PROVISIONER" --type ACME

echo ""
echo "✓ step-ca 初始化完成"
echo "  CA:        $CA_NAME"
echo "  Root cert: $HOME/.step/certs/root_ca.crt"
echo "  Password:  $PASSWORD_FILE"
echo ""
echo "下一步："
echo "  1. ./trust-root-mac.sh         （加進 macOS 系統信任）"
echo "  2. ./start-ca.sh               （啟動 CA daemon）"
echo "  3. ./issue-cert.sh <hostname>  （發證）"
