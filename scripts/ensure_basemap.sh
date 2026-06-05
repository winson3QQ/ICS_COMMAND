#!/bin/bash
# ensure_basemap.sh — 確保本 working tree 的底圖就位（**優先重用本機現成副本，免重下載**）
#
# 背景：底圖 taiwan.pmtiles 是 gitignored deploy artifact、per-machine，新 worktree 一定沒有。
# 但各 worktree / 主 repo 的底圖**完全相同**（同 manifest 版本），重下載 247MB 純浪費。
# 本腳本掃本機所有 worktree + 主 repo 找 SHA 符的副本 → provision --from（秒級、免網）。
#
# 用法：
#   ./scripts/ensure_basemap.sh                # 預設：缺則重用本機副本；本機無副本 → 只警告
#   ./scripts/ensure_basemap.sh --allow-download  # 本機無副本時才連網下載（最後手段）
#
# 設計給 .githooks/post-checkout 在新 worktree 自動呼叫（best-effort，不擋 checkout）。
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_basemap_lib.sh"
bm_load

ALLOW_DOWNLOAD=false
[ "${1:-}" = "--allow-download" ] && ALLOW_DOWNLOAD=true

# 已就緒（檔在 + SHA 符）→ no-op
if [ -f "$BM_TARGET" ] && [ "$(lc "$(sha256_of "$BM_TARGET")")" = "$BM_SHA" ]; then
  exit 0
fi

echo "[basemap] 本 working tree 底圖缺/舊（manifest v$BM_VERSION），找本機現成副本…" >&2

# 掃本機所有 worktree（含主 repo）找 SHA 符的副本
CANDIDATE=""
while IFS= read -r line; do
  [ -n "$line" ] || continue
  wt="${line%% *}"                       # git worktree list 第一欄 = 路徑
  cand="$wt/$BM_TARGET_REL"
  [ -f "$cand" ] || continue
  [ "$cand" -ef "$BM_TARGET" ] && continue   # 跳過自己（同 inode）
  if [ "$(lc "$(sha256_of "$cand")")" = "$BM_SHA" ]; then
    CANDIDATE="$cand"
    break
  fi
done < <(git worktree list 2>/dev/null)

if [ -n "$CANDIDATE" ]; then
  echo "[basemap] 用本機副本補（免下載）：$CANDIDATE" >&2
  "$REPO_ROOT/scripts/provision_basemap.sh" --from "$CANDIDATE"
elif [ "$ALLOW_DOWNLOAD" = true ]; then
  echo "[basemap] 本機無正確副本，連網下載…" >&2
  "$REPO_ROOT/scripts/provision_basemap.sh"
else
  echo "⚠ [basemap] 本機找不到正確副本；地圖會空白。整備階段請手動跑：" >&2
  echo "    ./scripts/provision_basemap.sh            # 連網下載" >&2
  echo "    ./scripts/provision_basemap.sh --from <USB路徑>   # air-gap" >&2
fi
