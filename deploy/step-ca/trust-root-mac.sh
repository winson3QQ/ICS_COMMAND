#!/usr/bin/env bash
# trust-root-mac.sh — 把 step-ca root cert 加進 macOS 系統信任
#
# 之後 Safari/Chrome/curl 對由此 CA 簽出的 cert 都會直接信任。
# 對應 uninstall：./uninstall-dev.sh

set -euo pipefail

ROOT_CRT="$HOME/.step/certs/root_ca.crt"
[[ -f "$ROOT_CRT" ]] || { echo "✗ $ROOT_CRT 不存在，先跑 init-mac-dev.sh" >&2; exit 1; }

echo "將 root CA 加進 macOS 系統 keychain（會要求輸入 sudo 密碼）..."
sudo security add-trusted-cert -d -r trustRoot \
  -k /Library/Keychains/System.keychain \
  "$ROOT_CRT"

echo "✓ root CA 已加入信任"
echo "  撤銷：sudo security delete-certificate -c 'ICS_Command Dev CA' /Library/Keychains/System.keychain"
