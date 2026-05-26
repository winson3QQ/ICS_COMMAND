#!/usr/bin/env bash
# start-ca.sh — 啟動 step-ca daemon（前景，背景請用 nohup 或 launchd）
set -euo pipefail

PASSWORD_FILE="$HOME/.step/secrets/password"
[[ -f "$PASSWORD_FILE" ]] || { echo "✗ password 檔不存在，先跑 init-mac-dev.sh" >&2; exit 1; }

echo "啟動 step-ca on :8443..."
echo "（Ctrl+C 停止；背景跑用：nohup ./start-ca.sh > ~/.step/ca.log 2>&1 &）"
exec step-ca "$HOME/.step/config/ca.json" --password-file "$PASSWORD_FILE"
