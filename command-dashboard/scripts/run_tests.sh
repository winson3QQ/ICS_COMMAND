#!/usr/bin/env bash
# scripts/run_tests.sh — 執行全套測試並產生報告
#
# 使用方式（從 command-dashboard/ 根目錄執行）：
#   bash scripts/run_tests.sh
#   bash scripts/run_tests.sh unit          # 只跑 unit tests
#   bash scripts/run_tests.sh integration   # 只跑 integration tests
#   bash scripts/run_tests.sh api           # 只跑 api tests

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
SRC_DIR="$ROOT_DIR/src"
REPORT_DIR="$ROOT_DIR/tests/reports"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")

cd "$ROOT_DIR"
mkdir -p "$REPORT_DIR"

# ── 標記篩選 ────────────────────────────────────────────────────────────────
MARKER=""
LABEL="all"
if [ -n "$1" ]; then
    MARKER="-m $1"
    LABEL="$1"
fi

HTML_REPORT="$REPORT_DIR/report_${LABEL}_${TIMESTAMP}.html"
COVERAGE_XML="$REPORT_DIR/coverage_${TIMESTAMP}.xml"

echo "======================================================"
echo "  ICS DMAS 測試套件"
echo "  範圍：${LABEL}"
echo "  時間：$(date '+%Y-%m-%d %H:%M:%S')"
echo "======================================================"
echo ""

# ── 執行測試 ────────────────────────────────────────────────────────────────
python3 -m pytest \
    $MARKER \
    --html="$HTML_REPORT" \
    --self-contained-html \
    --cov="$SRC_DIR" \
    --cov-report=term-missing \
    --cov-report="xml:$COVERAGE_XML" \
    --cov-fail-under=0 \
    -v \
    tests/

EXIT_CODE=$?

echo ""
echo "======================================================"
echo "  報告已儲存："
echo "  HTML  → tests/reports/$(basename "$HTML_REPORT")"
echo "  XML   → tests/reports/$(basename "$COVERAGE_XML")"
echo "======================================================"

exit $EXIT_CODE
