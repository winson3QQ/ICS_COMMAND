#!/usr/bin/env python3
"""ICS_Command ROADMAP ↔ GitHub Issue 同步檢查

雙向比對：
1. ROADMAP item 沒對應 GitHub issue → 建議 `gh issue create`
2. GitHub issue 沒對應 ROADMAP item → 提示審閱
3. 已關閉但 ROADMAP 未打勾 → 提示更新 ROADMAP

預設不修改任何東西，純報告。加 --create-missing 才會 prompt 建立。
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
ROADMAP = BASE / "docs" / "ROADMAP.md"

# ROADMAP item ID pattern：P1-01, P1-10a, P2-05, P3-00 ...
ITEM_RE = re.compile(r"\b(P[123]-\d+[a-z]?)\b")


def parse_roadmap_items() -> dict[str, str]:
    """從 ROADMAP.md 抽 item ID → 標題行（第一句）"""
    items: dict[str, str] = {}
    if not ROADMAP.exists():
        print(f"⚠  {ROADMAP} 不存在", file=sys.stderr)
        return items
    text = ROADMAP.read_text(encoding="utf-8")
    for line in text.splitlines():
        m = ITEM_RE.search(line)
        if not m:
            continue
        item_id = m.group(1)
        if item_id in items:
            continue  # 取第一次出現
        # 抽該行的「標題」：移除 markdown table pipe 跟前綴
        title = line.replace("|", "").strip()
        title = re.sub(r"^\W+", "", title)
        title = re.sub(r"^P[123]-\d+[a-z]?\s*[—:-]?\s*", "", title)
        items[item_id] = title[:80] if title else "(no title)"
    return items


def gh_issues() -> list[dict]:
    """gh issue list — 拿所有 open + recently closed"""
    try:
        out = subprocess.run(
            ["gh", "issue", "list",
             "--state", "all",
             "--limit", "200",
             "--json", "number,title,state,labels"],
            capture_output=True, text=True, check=True, timeout=15,
        )
        return json.loads(out.stdout)
    except FileNotFoundError:
        print("⚠  gh CLI 未安裝，跳過 issue 查詢", file=sys.stderr)
        return []
    except subprocess.CalledProcessError as e:
        print(f"⚠  gh issue list 失敗：{e.stderr.strip()}", file=sys.stderr)
        return []


def issue_item_id(issue: dict) -> str | None:
    """從 issue title / labels 抽 item ID"""
    m = ITEM_RE.search(issue.get("title", ""))
    if m:
        return m.group(1)
    for label in issue.get("labels", []):
        m = ITEM_RE.search(label.get("name", ""))
        if m:
            return m.group(1)
    return None


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--create-missing", action="store_true",
                   help="互動建立缺漏的 issue（預設只報告）")
    args = p.parse_args()

    roadmap_items = parse_roadmap_items()
    if not roadmap_items:
        print("✗ ROADMAP 沒有可解析的 item ID（格式應為 P1-01、P2-05a…）")
        return 1

    issues = gh_issues()
    issue_by_id: dict[str, dict] = {}
    orphan_issues: list[dict] = []
    for iss in issues:
        iid = issue_item_id(iss)
        if iid:
            issue_by_id[iid] = iss
        else:
            orphan_issues.append(iss)

    missing: list[tuple[str, str]] = []
    closed_not_ticked: list[tuple[str, dict]] = []

    for item_id, title in sorted(roadmap_items.items()):
        if item_id not in issue_by_id:
            missing.append((item_id, title))
            continue
        iss = issue_by_id[item_id]
        if iss.get("state") == "CLOSED":
            closed_not_ticked.append((item_id, iss))

    # ── Report ──
    print(f"=== ROADMAP items: {len(roadmap_items)} ===")
    print(f"=== GitHub issues (mapped): {len(issue_by_id)} ===")
    print(f"=== Orphan issues (no ROADMAP id): {len(orphan_issues)} ===")
    print()

    if missing:
        print(f"⚠  ROADMAP item 沒對應 issue（{len(missing)}）：")
        for item_id, title in missing:
            print(f"  - {item_id}: {title}")
            print(f"    建議：gh issue create --title \"{item_id}: {title[:50]}\" \\")
            print(f"             --body \"見 docs/ROADMAP.md 該段落\"")
        print()

    if orphan_issues:
        print(f"⚠  GitHub issue 沒 ROADMAP item ID（{len(orphan_issues)}）：")
        for iss in orphan_issues[:10]:
            print(f"  - #{iss['number']} [{iss['state']}] {iss['title'][:70]}")
        if len(orphan_issues) > 10:
            print(f"  ... +{len(orphan_issues)-10} 更多")
        print()

    if closed_not_ticked:
        print(f"ℹ  issue 已 close，記得 ROADMAP item 勾選 / 更新（{len(closed_not_ticked)}）：")
        for item_id, iss in closed_not_ticked:
            print(f"  - {item_id} → #{iss['number']} {iss['title'][:60]}")
        print()

    if args.create_missing and missing:
        print("互動建立模式尚未實作（避免一次誤建 N 個 issue）。")
        print("請複製上方 `gh issue create` 指令手動執行。")

    issues_found = len(missing) + len(orphan_issues)
    if issues_found == 0:
        print("✓ ROADMAP ↔ issue 完全同步")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
