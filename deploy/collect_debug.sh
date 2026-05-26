#!/usr/bin/env bash
# ================================================================
# collect_debug.sh — ICS_Command 現場 debug 資料收集工具
# 規格：logging_architecture_decision_v1.1.md §7（v1.1）
# PII scrub：v1.1 §7.6 完整函式（非 v1.0 2-line sed）
# 輸出：/home/ics/ics_debug_YYYYMMDD_HHMM.zip
# ================================================================
set -euo pipefail

TIMESTAMP="$(date -u +%Y%m%d_%H%M)"
OUTDIR="/tmp/ics_debug_${TIMESTAMP}"
ZIPFILE="/home/ics/ics_debug_${TIMESTAMP}.zip"

# ── §7.2 preconditions check ──────────────────────────────────────

# /home/ics 可寫
if [ ! -d /home/ics ] || [ ! -w /home/ics ]; then
    echo "ERROR: /home/ics not writable" >&2; exit 1
fi

# ≥ 100MB 可用空間
AVAIL=$(df -m /home/ics | awk 'NR==2 {print $4}')
if [ "$AVAIL" -lt 100 ]; then
    echo "ERROR: Insufficient disk space (<100MB available)" >&2; exit 1
fi

# 同 timestamp 不重複（秒級衝突保護）
if [ -e "$ZIPFILE" ]; then
    TIMESTAMP="${TIMESTAMP}_$(date +%S)"
    OUTDIR="/tmp/ics_debug_${TIMESTAMP}"
    ZIPFILE="/home/ics/ics_debug_${TIMESTAMP}.zip"
fi

# ── §7.1 step 1: mkdir ────────────────────────────────────────────
mkdir -p "$OUTDIR"

echo "[collect_debug] 開始收集 debug 資料..."

# ── §7.3 step 3: 收集 log（含 rotated，最近 1000 行）─────────────

# Command log
for f in /var/log/ics/command.log \
         /var/log/ics/command.log.1 \
         /var/log/ics/command.log.1.gz \
         /var/log/ics/command.log.2.gz; do
    [ -f "$f" ] || continue
    case "$f" in
        *.gz) zcat "$f" >> "$OUTDIR/command_combined.log" ;;
        *)    cat  "$f" >> "$OUTDIR/command_combined.log" ;;
    esac
done
if [ -f "$OUTDIR/command_combined.log" ]; then
    tail -1000 "$OUTDIR/command_combined.log" > "$OUTDIR/command.log"
    rm "$OUTDIR/command_combined.log"
else
    echo "no command log entries" > "$OUTDIR/command.log"
fi

# Pi log
for f in /var/log/ics/pi.log \
         /var/log/ics/pi.log.1 \
         /var/log/ics/pi.log.1.gz \
         /var/log/ics/pi.log.2.gz; do
    [ -f "$f" ] || continue
    case "$f" in
        *.gz) zcat "$f" >> "$OUTDIR/pi_combined.log" ;;
        *)    cat  "$f" >> "$OUTDIR/pi_combined.log" ;;
    esac
done
if [ -f "$OUTDIR/pi_combined.log" ]; then
    tail -1000 "$OUTDIR/pi_combined.log" > "$OUTDIR/pi.log"
    rm "$OUTDIR/pi_combined.log"
else
    echo "no pi log entries" > "$OUTDIR/pi.log"
fi

# ── §7.4 step 4: timestamps.txt ───────────────────────────────────
{
    echo "=== Log Time Coverage ==="
    echo "Collection time (UTC):    $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "Collection time (local):  $(date +%Y-%m-%dT%H:%M:%S%z)"
    echo ""
    echo "Server local timezone: $(timedatectl 2>/dev/null | grep 'Time zone' | awk '{print $3}' || echo 'unknown')"
    echo "All log timestamps are in UTC."
    echo ""
    echo "=== command.log range ==="
    head -1 "$OUTDIR/command.log" | grep -oE '"ts":"[^"]*"' || echo "no entry"
    tail -1 "$OUTDIR/command.log" | grep -oE '"ts":"[^"]*"' || echo "no entry"
    echo ""
    echo "=== pi.log range ==="
    head -1 "$OUTDIR/pi.log" | grep -oE '"ts":"[^"]*"' || echo "no entry"
    tail -1 "$OUTDIR/pi.log" | grep -oE '"ts":"[^"]*"' || echo "no entry"
} > "$OUTDIR/timestamps.txt"

# ── step 5: system_info.txt ───────────────────────────────────────
{
    echo "=== System Info ==="
    echo "Date (UTC): $(date -u)"
    echo "Hostname:   $(hostname)"
    echo ""
    echo "=== Disk ==="
    df -h /home/ics /var/log 2>/dev/null || true
    echo ""
    echo "=== Memory ==="
    free -h 2>/dev/null || true
    echo ""
    echo "=== ICS Versions ==="
    cat /home/ics/ics-command/server/package.json 2>/dev/null | grep '"version"' || echo "Pi server version unknown"
} > "$OUTDIR/system_info.txt"

# ── step 6: service_status.txt ────────────────────────────────────
{
    echo "=== systemctl status ics-* ==="
    systemctl status ics-command.service ics-pi.service 2>/dev/null || \
        echo "systemd units not found or not running"
} > "$OUTDIR/service_status.txt"

# ── §7.5 step 7: PWA log（預留點）────────────────────────────────
if [ -f /var/lib/ics/pwa_log.json ]; then
    cp /var/lib/ics/pwa_log.json "$OUTDIR/pwa_log.json"
else
    echo "PWA log not collected by server side (W-C2-A pending)" \
        > "$OUTDIR/pwa_log_notice.txt"
fi

# ── §7.7 step 8: README.txt ───────────────────────────────────────
cat > "$OUTDIR/README.txt" << 'README_EOF'
ICS_Command Debug Package
======================
請將此 zip 檔傳給工程師。
傳送方式：USB / LINE / Email
聯絡工程師：[填入聯絡方式]

如有緊急狀況，請同時截圖錯誤畫面。

【時區說明 — 重要】
所有 log 時間戳記為 UTC（協調世界時）。
Server 本地時區：Asia/Taipei（UTC+8）。
查看 log 時，請將 UTC 時間 + 8 小時 = 台灣本地時間。
例：log 顯示 "2026-06-15T06:23:11Z" = 台灣時間 14:23:11

【內容清單】
- command.log：Command server log，最近 1000 行（含 rotated）
- pi.log：Pi server log，最近 1000 行（含 rotated）
- timestamps.txt：log 時間範圍
- system_info.txt：系統狀態（版本/磁碟/記憶體）
- service_status.txt：systemctl status ics-*
- pwa_log.json：PWA 端 log（若有）

【PII 保護】
此 zip 已對敏感欄位執行 mask：
- 密碼、Token、PIN、first_run_token
- HMAC 簽章（X-ICS-Signature / X-ICS-Key-Id / X-ICS-Nonce）
- 醫療 PII（症狀、過敏、藥物、patient_id）
- 內部 IP 地址（留 subnet）
- 內部路徑（/home/ics/、/var/lib/ics/）
但仍視為機密文件，請僅傳給授權工程師。
README_EOF

# ── §7.6 step 9: PII scrub（v1.1 §7.6 完整函式，非 v1.0 2-line sed）──
scrub_pii() {
    local file="$1"

    # 認證類
    sed -i 's/"pin"\s*:\s*"[^"]*"/"pin":"***"/gi' "$file"
    sed -i 's/"password"\s*:\s*"[^"]*"/"password":"***"/gi' "$file"
    sed -i 's/Authorization:\s*Bearer\s*[A-Za-z0-9._-]\+/Authorization: Bearer ***/gi' "$file"
    sed -i 's/"session_token"\s*:\s*"[^"]*"/"session_token":"***"/gi' "$file"
    sed -i 's/"first_run_token"\s*:\s*"[^"]*"/"first_run_token":"***"/gi' "$file"

    # HMAC 類（TI-01）
    sed -i 's/X-ICS-Signature:\s*[a-f0-9]\+/X-ICS-Signature: ***/gi' "$file"
    sed -i 's/X-ICS-Key-Id:\s*[A-Za-z0-9-]\+/X-ICS-Key-Id: ***/gi' "$file"
    sed -i 's/X-ICS-Nonce:\s*[A-Za-z0-9-]\+/X-ICS-Nonce: ***/gi' "$file"
    sed -i 's/"hmac_secret"\s*:\s*"[^"]*"/"hmac_secret":"***"/gi' "$file"

    # 醫療 PII
    sed -i 's/"symptom"\s*:\s*"[^"]*"/"symptom":"***"/gi' "$file"
    sed -i 's/"allergy"\s*:\s*"[^"]*"/"allergy":"***"/gi' "$file"
    sed -i 's/"medication"\s*:\s*"[^"]*"/"medication":"***"/gi' "$file"
    sed -i 's/"diagnosis"\s*:\s*"[^"]*"/"diagnosis":"***"/gi' "$file"
    sed -i 's/"patient_id"\s*:\s*"[^"]*"/"patient_id":"***"/gi' "$file"

    # 路徑類
    sed -i 's|/home/ics/[^"[:space:]]*|/home/ics/***|g' "$file"
    sed -i 's|/var/lib/ics/[^"[:space:]]*|/var/lib/ics/***|g' "$file"

    # IP 類（最後防線）
    sed -i 's/"ip"\s*:\s*"\(192\.168\.[0-9]\+\)\.[0-9]\+"/"ip":"\1.x"/g' "$file"
    sed -i 's/"ip"\s*:\s*"\(10\.[0-9]\+\.[0-9]\+\)\.[0-9]\+"/"ip":"\1.x"/g' "$file"
}

echo "[collect_debug] 執行 PII scrub（v1.1 §7.6 + P1-09 fix: 含 .txt）..."
# 原本只跑 *.log 跟 *.json，遺漏 service_status.txt / system_info.txt 等
# README 宣稱 'scrubbed' 但實際漏 → 自相矛盾。改為涵蓋所有文字檔
for f in "$OUTDIR"/*.log "$OUTDIR"/*.json "$OUTDIR"/*.txt; do
    [ -f "$f" ] || continue
    scrub_pii "$f"
done

# ── step 10: zip ──────────────────────────────────────────────────
echo "[collect_debug] 壓縮中..."
cd /tmp
zip -r "$ZIPFILE" "ics_debug_${TIMESTAMP}/" -q

# ── step 11: rm tmp dir ───────────────────────────────────────────
rm -rf "$OUTDIR"

echo "[collect_debug] 完成：$ZIPFILE"
ls -lh "$ZIPFILE"
echo ""
echo "請將此 zip 傳給工程師（USB / LINE / Email）。"
echo "Correlation ID 查詢：解壓後 grep '前8碼' command.log"
