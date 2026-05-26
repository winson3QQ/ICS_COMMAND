#!/usr/bin/env bash
# snapshot_issues.sh — GitHub Issue/PR 結構化備份（Codeberg disaster fallback）
#
# 使用方式:
#   bash scripts/snapshot_issues.sh                 # 寫到 docs/backups/
#   REPO=other/repo bash scripts/snapshot_issues.sh # 換 repo
#
# 設計意圖（對齊 C 方案 SoT 規則 — 見 docs/disaster/ + memory）:
#   - GitHub 為 issue tracker SoT，Codeberg 為 code mirror
#   - 此腳本將 GitHub issue/PR 結構化匯出為 JSON，commit 後透過 dual push
#     自動帶到 Codeberg，達成「停權場景下 issue 內容仍可從 repo 重建」
#   - 兼作 L1 (GitHub Action) 與 L3 (人工 fallback) 共用核心邏輯
#
# 觸發來源（在 .github/workflows/issue-snapshot.yml）:
#   push / issues / issue_comment / pull_request / pull_request_review_comment
#   + schedule daily 兜底 + workflow_dispatch 手動
#
# 失敗策略: set -euo pipefail；gh 失敗即停，避免寫入半成品 JSON

set -euo pipefail

REPO="${REPO:-winson3QQ/ICS_COMMAND}"
OUT_DIR="${OUT_DIR:-docs/backups}"
LIMIT="${LIMIT:-500}"

# gh 必須已認證
if ! command -v gh >/dev/null 2>&1; then
    echo "[snapshot] ERROR: gh CLI 未安裝" >&2
    exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
    echo "[snapshot] ERROR: gh 未認證（CI 需設 GH_TOKEN）" >&2
    exit 1
fi

mkdir -p "$OUT_DIR"

# ── Issue snapshot ───────────────────────────────────────────────────────────
ISSUES_FILE="$OUT_DIR/github-issues.json"
echo "[snapshot] fetching issues from $REPO → $ISSUES_FILE"
gh issue list \
    --repo "$REPO" \
    --state all \
    --limit "$LIMIT" \
    --json number,title,state,labels,assignees,author,body,comments,createdAt,closedAt,updatedAt,milestone,url \
    > "$ISSUES_FILE.tmp"

# 正規化: 排序 + 移除 token-視角欄位
#   - 按 number 升冪 → diff 穩定（gh 預設 updatedAt 倒序）
#   - 過濾 viewer* 欄位 → 本地手動跑與 CI Action 跑輸出一致
#     (viewerDidAuthor 等欄位隨查詢 token 變動，會造成 spurious diff commits)
python3 -c "
import json
def strip_viewer(o):
    if isinstance(o, dict):
        return {k: strip_viewer(v) for k, v in o.items() if not k.startswith('viewer')}
    if isinstance(o, list):
        return [strip_viewer(x) for x in o]
    return o
with open('$ISSUES_FILE.tmp') as f:
    data = json.load(f)
data = strip_viewer(data)
data.sort(key=lambda x: x['number'])
with open('$ISSUES_FILE', 'w') as f:
    json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)
    f.write('\n')
print(f'[snapshot] issues: {len(data)} items')
"
rm -f "$ISSUES_FILE.tmp"

# ── PR snapshot ──────────────────────────────────────────────────────────────
PRS_FILE="$OUT_DIR/github-prs.json"
echo "[snapshot] fetching PRs from $REPO → $PRS_FILE"
gh pr list \
    --repo "$REPO" \
    --state all \
    --limit "$LIMIT" \
    --json number,title,state,labels,author,body,comments,createdAt,closedAt,updatedAt,mergedAt,baseRefName,headRefName,url \
    > "$PRS_FILE.tmp"

# 正規化同上（共用 strip_viewer 邏輯，inline 避免外部 module 依賴）
python3 -c "
import json
def strip_viewer(o):
    if isinstance(o, dict):
        return {k: strip_viewer(v) for k, v in o.items() if not k.startswith('viewer')}
    if isinstance(o, list):
        return [strip_viewer(x) for x in o]
    return o
with open('$PRS_FILE.tmp') as f:
    data = json.load(f)
data = strip_viewer(data)
data.sort(key=lambda x: x['number'])
with open('$PRS_FILE', 'w') as f:
    json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)
    f.write('\n')
print(f'[snapshot] PRs: {len(data)} items')
"
rm -f "$PRS_FILE.tmp"

# ── README 標頭（每次覆寫，內容固定，便於未來在 Codeberg 上一眼看懂） ──────
cat > "$OUT_DIR/README.md" <<'EOF'
# GitHub Issue/PR Snapshot

此目錄為 **GitHub issue/PR 的結構化備份**，自動由
`.github/workflows/issue-snapshot.yml` 維護。

## 觸發
- `push` / `issues` / `issue_comment` / `pull_request*` event
- 每日 00:00 UTC schedule 兜底
- 手動 `workflow_dispatch`

## 用途
- C 方案 SoT 規則：GitHub = issue tracker SoT，Codeberg = code mirror
- 若 GitHub 帳號再被停權，可從這份 JSON 把 open issue 重建至 Codeberg
- 同時為人工稽核留下機讀備份（補強 matrix.md / ROADMAP 人讀紀錄）

## 復原流程
見 `docs/disaster/` 對應 runbook（停權後手動 import 至 Codeberg）。

## 不備份
- Issue / PR 內嵌的圖片（GitHub user-content URL）
- Reactions, project board associations, release artifacts
- Wiki, discussions
EOF

echo "[snapshot] done"
