#!/bin/bash
# preflight_basemap.sh — 底圖出場守門檢查
#
# 用途：確認本機 taiwan.pmtiles 存在且 SHA256 == manifest（即「現行官方底圖已就位」）。
# 場域離線零信任：這是「可下場」資格檢查，整備完該過、出場前該過。
#
# exit code：
#   0 = 底圖就緒（檔在 + SHA 符）
#   2 = 底圖缺檔（未整備）
#   3 = 底圖 SHA 不符（舊版 / 損毀 / 拿錯）
#
# 用法：./scripts/preflight_basemap.sh [--quiet]

set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_basemap_lib.sh"

QUIET=false
[ "${1:-}" = "--quiet" ] && QUIET=true
say() { [ "$QUIET" = true ] || echo "$@"; }

bm_load

if [ ! -f "$BM_TARGET" ]; then
  echo "✗ 底圖未整備：$BM_TARGET_REL 不存在（manifest 版本 $BM_VERSION）" >&2
  exit 2
fi

actual="$(lc "$(sha256_of "$BM_TARGET")")"
if [ "$actual" != "$BM_SHA" ]; then
  echo "✗ 底圖 SHA 不符（可能舊版/損毀/拿錯）" >&2
  echo "  期望（manifest $BM_VERSION）：$BM_SHA" >&2
  echo "  實際：                        $actual" >&2
  exit 3
fi

say "✓ 底圖就緒：taiwan v$BM_VERSION（SHA 符）"
exit 0
