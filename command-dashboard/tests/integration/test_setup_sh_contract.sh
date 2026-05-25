#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

grep -q 'set -euo pipefail' "$REPO_ROOT/deploy/setup.sh"
grep -q 'ICS_DB_DIR="/var/lib/ics"' "$REPO_ROOT/deploy/setup.sh"
grep -q 'sed "s|/home/ics/ics-dmas|' "$REPO_ROOT/deploy/setup.sh"
grep -q 'will not migrate DB data automatically' "$REPO_ROOT/deploy/setup.sh"
grep -q 'sudo mv' "$REPO_ROOT/deploy/setup.sh"
grep -q 'sudo chmod 600' "$REPO_ROOT/deploy/setup.sh"
grep -q 'systemctl kill -s HUP ics-command.service 2>/dev/null || true' "$REPO_ROOT/deploy/logrotate.ics"
grep -q 'Environment=ICS_DB_PATH=/var/lib/ics/ics.db' "$REPO_ROOT/systemd/ics-command.service"
# ExecStartPost 不應存在（Type=simple + Restart=on-failure 下會造成無限重啟）
if grep -q 'ExecStartPost' "$REPO_ROOT/systemd/ics-command.service"; then
  echo "ExecStartPost found in unit file — will cause infinite restart loop with Type=simple" >&2
  exit 1
fi

if grep -q 'ics-pi.service' "$REPO_ROOT/deploy/logrotate.ics"; then
  echo "unexpected ics-pi.service in logrotate config" >&2
  exit 1
fi
