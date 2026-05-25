#!/bin/bash
# status.sh — ICS_Command 一鍵 orientation
# 用途：新 session / 新接手者跑一行拿到「當前位置」全景
# 設計：見 CLAUDE.md 「新 session START HERE」、README.md 「Onboarding」

set -e
REPO="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO"

echo "═══════════════════════════════════════════════════════════════"
echo " ICS_Command — Orientation"
echo "═══════════════════════════════════════════════════════════════"
echo ""

# 當前 git 狀態
echo "▸ Git"
BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "?")
HEAD=$(git rev-parse --short HEAD 2>/dev/null || echo "?")
echo "  branch:    $BRANCH"
echo "  HEAD:      $HEAD"
echo "  last:      $(git log -1 --pretty=format:'%h %s (%cr)' 2>/dev/null)"
echo ""

# 最近 5 個 commit
echo "▸ Recent commits"
git log -5 --pretty=format:"  %h %s" 2>/dev/null || echo "  (no commits)"
echo ""
echo ""

# 三邊同步狀態（C 方案 SoT）
echo "▸ Remote sync (C 方案：local / GitHub / Codeberg 三邊應一致)"
LOCAL=$(git rev-parse main 2>/dev/null || echo "?")
GH=$(git rev-parse origin/main 2>/dev/null || echo "?")
CB=$(git ls-remote codeberg refs/heads/main 2>/dev/null | awk '{print $1}' || echo "?")
echo "  local:     $LOCAL"
echo "  github:    $GH"
echo "  codeberg:  $CB"
if [ "$LOCAL" = "$GH" ] && [ "$GH" = "$CB" ]; then
  echo "  ✓ 三邊同步"
else
  echo "  ⚠ 分歧！見 docs/PROCESS.md step 8 處理"
fi
echo ""

# ROADMAP status + GitHub issue 對照
echo "▸ ROADMAP status"
if [ -f scripts/roadmap_issue_sync.py ]; then
  python3 scripts/roadmap_issue_sync.py 2>&1 | head -25
else
  echo "  (scripts/roadmap_issue_sync.py 不存在)"
fi
echo ""
echo "───────────────────────────────────────────────────────────────"
echo " 下一步建議："
echo "   docs/PROCESS.md   — task lifecycle 怎麼走"
echo "   docs/ROADMAP.md   — 完整 38 items"
echo "   CLAUDE.md         — 專案規則"
echo "───────────────────────────────────────────────────────────────"
