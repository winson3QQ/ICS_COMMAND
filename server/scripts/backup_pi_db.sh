#!/usr/bin/env bash
#
# backup_pi_db.sh — Pi (shelter / medical) DB backup (#41 B-2 manual)
#
# 對齊 #41 Step A Approval frozen decisions:
#   B-2  Pi backup: manual only (由演練前 SOP step 觸發)
#   B-3  Target path: /var/backups/pi/
#   B-5  Retention: 7 天本地刪舊 (Sync v2)
#   B-7  File naming: {shelter|medical}_accounts-<YYYY-MM-DD>.db.gz
#   B-8  gzip 壓縮
#   E-2  Pi backup 不加密 (LUKS disk-level 已加密, 對齊 Sync v4 production 前提)
#
# 使用方式 (production Pi 500):
#   1. 演練前 SOP: 開機 → FIDO2 unlock LUKS container → mount /var/lib/ics-data
#   2. /usr/local/bin/backup_pi_db.sh shelter   # 或 medical
#
# 使用方式 (dev Mac, 無 LUKS):
#   1. ./server/scripts/backup_pi_db.sh shelter --db-path ./shelter-pwa/shelter_accounts.db
#
# Exit codes:
#   0  success
#   1  invalid args
#   2  DB file not found
#   3  backup creation failed
#

set -euo pipefail

PROG="$(basename "$0")"

usage() {
  cat <<EOF
Usage: $PROG <shelter|medical> [--db-path PATH] [--backup-dir PATH] [--retain-days N]

Default paths (production):
  shelter:  /var/lib/ics-data/shelter-pwa/shelter_accounts.db  →  /var/backups/pi/
  medical:  /var/lib/ics-data/medical-pwa/medical_accounts.db  →  /var/backups/pi/

Default paths (dev):
  --db-path 顯式指定 / --backup-dir 顯式指定

Options:
  --db-path PATH       Override 預設 DB 路徑
  --backup-dir PATH    Override 預設 backup 目錄 (default /var/backups/pi/)
  --retain-days N      保留天數 (default 7, B-5 Sync v2)
  --help               顯示此訊息

Note (Sync v4): production 必須先 LUKS unlock; dev (Mac) 無 LUKS 直接讀.
EOF
}

# ── Parse args ──────────────────────────────────────────────────────────────
if [[ $# -lt 1 ]]; then
  usage; exit 1
fi

UNIT="$1"; shift
DB_PATH=""
BACKUP_DIR="/var/backups/pi"
RETAIN_DAYS=7

while [[ $# -gt 0 ]]; do
  case "$1" in
    --db-path)      DB_PATH="$2"; shift 2 ;;
    --backup-dir)   BACKUP_DIR="$2"; shift 2 ;;
    --retain-days)  RETAIN_DAYS="$2"; shift 2 ;;
    --help|-h)      usage; exit 0 ;;
    *) echo "[$PROG] Unknown arg: $1" >&2; usage; exit 1 ;;
  esac
done

case "$UNIT" in
  shelter|medical) ;;
  *) echo "[$PROG] Invalid unit: $UNIT (must be shelter or medical)" >&2; exit 1 ;;
esac

# ── Resolve default DB path ─────────────────────────────────────────────────
if [[ -z "$DB_PATH" ]]; then
  if [[ "$UNIT" == "shelter" ]]; then
    DB_PATH="/var/lib/ics-data/shelter-pwa/shelter_accounts.db"
  else
    DB_PATH="/var/lib/ics-data/medical-pwa/medical_accounts.db"
  fi
fi

# ── Sanity checks ───────────────────────────────────────────────────────────
if [[ ! -f "$DB_PATH" ]]; then
  echo "[$PROG] ERROR: DB file not found: $DB_PATH" >&2
  echo "[$PROG] HINT: production 需先 LUKS unlock; dev 用 --db-path 指定 local DB" >&2
  exit 2
fi

mkdir -p "$BACKUP_DIR"

# ── Build backup filename (B-7 Sync v3: ISO 8601 date, no time for Pi daily) ──
TIMESTAMP=$(date -u +%Y-%m-%d)  # YYYY-MM-DD UTC
BACKUP_NAME="${UNIT}_accounts-${TIMESTAMP}.db.gz"
BACKUP_PATH="${BACKUP_DIR}/${BACKUP_NAME}"
TMP_BACKUP="${BACKUP_PATH}.tmp"

# ── SQLite online backup (consistent snapshot, WAL-safe) + gzip ─────────────
echo "[$PROG] Creating backup: $DB_PATH → $BACKUP_PATH"
START_MS=$(($(date +%s%N) / 1000000))

# SQLite VACUUM INTO 是 atomic snapshot (alternative: sqlite3 .backup)
TMP_DB=$(mktemp --suffix=.db)
trap 'rm -f "$TMP_DB" "$TMP_BACKUP"' EXIT

if ! sqlite3 "$DB_PATH" ".backup '$TMP_DB'"; then
  echo "[$PROG] ERROR: SQLite backup failed" >&2
  exit 3
fi

# gzip 壓縮 → tmp file → atomic rename (B-8)
if ! gzip -c "$TMP_DB" > "$TMP_BACKUP"; then
  echo "[$PROG] ERROR: gzip failed" >&2
  exit 3
fi

mv "$TMP_BACKUP" "$BACKUP_PATH"  # atomic
SIZE=$(stat -f%z "$BACKUP_PATH" 2>/dev/null || stat -c%s "$BACKUP_PATH" 2>/dev/null || echo "?")
DURATION_MS=$(($(date +%s%N) / 1000000 - START_MS))

echo "[$PROG] backup_done unit=$UNIT path=$BACKUP_PATH size=$SIZE duration_ms=$DURATION_MS"

# ── Cleanup old backups (B-5 Sync v2: 7 days retention) ─────────────────────
DELETED=0
if [[ "$RETAIN_DAYS" -gt 0 ]]; then
  # 找超過 retain days 的 ${UNIT}_accounts-YYYY-MM-DD.db.gz
  while IFS= read -r -d '' OLDFILE; do
    rm -f "$OLDFILE"
    DELETED=$((DELETED + 1))
    echo "[$PROG] cleanup_deleted path=$OLDFILE"
  done < <(find "$BACKUP_DIR" -maxdepth 1 -type f -name "${UNIT}_accounts-*.db.gz" -mtime +"$RETAIN_DAYS" -print0 2>/dev/null)
fi

# ── Summary ─────────────────────────────────────────────────────────────────
TOTAL=$(find "$BACKUP_DIR" -maxdepth 1 -type f -name "${UNIT}_accounts-*.db.gz" 2>/dev/null | wc -l | tr -d ' ')
echo "[$PROG] backup_summary unit=$UNIT total=$TOTAL deleted=$DELETED retain_days=$RETAIN_DAYS"

exit 0
