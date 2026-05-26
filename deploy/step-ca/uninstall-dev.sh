#!/usr/bin/env bash
# uninstall-dev.sh — 完整拆除開發機 step-ca
#
# 會做：
#   - 從 macOS 系統信任移除 root CA
#   - 刪除 ~/.step/
#   - 刪除 deploy/step-ca/certs/
#
# 注意：使用此 CA 簽出的所有憑證會立刻失效。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

read -p "⚠ 確認拆除 step-ca 與所有已簽憑證？(yes/N) " ans
[[ "$ans" == "yes" ]] || { echo "取消"; exit 0; }

echo "1. 從 macOS 信任移除 root CA..."
sudo security delete-certificate -c "ICS_Command Dev CA" /Library/Keychains/System.keychain 2>/dev/null || true

echo "2. 刪除 ~/.step/..."
rm -rf "$HOME/.step"

echo "3. 刪除 deploy/step-ca/certs/..."
rm -rf "$SCRIPT_DIR/certs"

echo "✓ 已拆除"
