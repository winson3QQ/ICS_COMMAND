#!/usr/bin/env python3
"""ICS_Command ROADMAP ↔ GitHub Issue 同步檢查 + 狀態報告

兩段輸出：
1. **Status Report**（Layer 3）：依 ROADMAP marker（✅⏳🚧）+ issue state，
   報「P1: X/N done」+ 每 item 一行狀態。新 session / 新接手者一行指令拿全景。
2. **Drift / Gap Report**：
   - ROADMAP item 沒對應 GitHub issue → 建議 `gh issue create`
   - GitHub issue 沒對應 ROADMAP item → 提示審閱
   - ROADMAP ✅ 但 issue 沒 closed（或反過來）→ status drift

預設不修改任何東西，純報告。加 --create-missing 才會 prompt 建立。
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# Windows console 預設 cp950，print 狀態 marker（✅⏳🚧 / ✓⚠✗ℹ）+ 中文會 UnicodeEncodeError（#148）；
# 強制 stdout/stderr UTF-8。hasattr 守門：stdout 被重導/替換時無 reconfigure。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

BASE = Path(__file__).resolve().parent.parent
ROADMAP = BASE / "docs" / "ROADMAP.md"

# ROADMAP item ID pattern：P1-01, P1-10a, P2-05, P3-00 ...
ITEM_RE = re.compile(r"\b(P[123]-\d+[a-z]?)\b")

# 狀態 marker（對應 ROADMAP〈狀態 marker 約定〉段落）
STATUS_MARKERS: dict[str, str] = {
    "✅": "done",
    "⏳": "in-progress",
    "🚧": "blocked",
}
STATUS_ICON: dict[str, str] = {
    "done":        "✅",
    "in-progress": "⏳",
    "blocked":     "🚧",
    "pending":     "  ",
}


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
            # encoding 明指 UTF-8：gh 輸出含中文 issue 標題，Windows 預設 cp950 解碼會
            # UnicodeDecodeError → out.stdout=None → json.loads 炸（#148 status.sh 崩根因）。
            capture_output=True, text=True, encoding="utf-8", check=True, timeout=15,
        )
        return json.loads(out.stdout)
    except FileNotFoundError:
        print("⚠  gh CLI 未安裝，跳過 issue 查詢", file=sys.stderr)
        return []
    except subprocess.CalledProcessError as e:
        print(f"⚠  gh issue list 失敗：{e.stderr.strip()}", file=sys.stderr)
        return []


def parse_roadmap_status() -> dict[str, str]:
    """掃 ROADMAP 每 row 開頭 marker，回傳 {item_id: status}。
    status ∈ {done, in-progress, blocked, pending}。
    """
    statuses: dict[str, str] = {}
    if not ROADMAP.exists():
        return statuses
    text = ROADMAP.read_text(encoding="utf-8")
    for line in text.splitlines():
        m = ITEM_RE.search(line)
        if not m:
            continue
        item_id = m.group(1)
        if item_id in statuses:
            continue
        # 看 item ID 出現前的 prefix 有沒有 marker
        prefix = line[:m.start()]
        status = "pending"
        for marker, label in STATUS_MARKERS.items():
            if marker in prefix:
                status = label
                break
        statuses[item_id] = status
    return statuses


def phase_of(item_id: str) -> str:
    return item_id.split("-")[0]  # "P1-01" → "P1"


def print_status_report(roadmap_items: dict[str, str],
                        statuses: dict[str, str],
                        issue_by_id: dict[str, dict]) -> int:
    """印 status report，回傳 drift 數量（0 = 無 drift）"""
    by_phase: dict[str, list[tuple[str, str, str]]] = {}
    for item_id, title in sorted(roadmap_items.items()):
        phase = phase_of(item_id)
        status = statuses.get(item_id, "pending")
        by_phase.setdefault(phase, []).append((item_id, status, title))

    print("=== ROADMAP Status Report ===\n")
    for phase in sorted(by_phase):
        items = by_phase[phase]
        counts = {k: sum(1 for _, s, _ in items if s == k)
                  for k in ("done", "in-progress", "blocked", "pending")}
        print(f"{phase}: {counts['done']}/{len(items)} done"
              f"  ·  {counts['in-progress']} in-progress"
              f"  ·  {counts['blocked']} blocked"
              f"  ·  {counts['pending']} pending")
        for item_id, status, title in items:
            icon = STATUS_ICON[status]
            iss = issue_by_id.get(item_id)
            iss_str = (f"(#{iss['number']} {iss['state']})" if iss
                       else "(no issue)")
            print(f"  {icon} {item_id:<8} {title[:48]:<48} {iss_str}")
        print()

    # Drift detection
    drift: list[str] = []
    for item_id in sorted(roadmap_items):
        st = statuses.get(item_id, "pending")
        iss = issue_by_id.get(item_id)
        if st == "done" and (not iss or iss.get("state") != "CLOSED"):
            iss_state = iss["state"] if iss else "missing"
            drift.append(f"  - {item_id}: ROADMAP ✅ 但 issue {iss_state}")
        elif st != "done" and iss and iss.get("state") == "CLOSED":
            drift.append(f"  - {item_id}: issue CLOSED 但 ROADMAP 未 ✅"
                         f"（PROCESS step 8.5 漏勾）")
    if drift:
        print(f"⚠  狀態 drift（{len(drift)}）：")
        for d in drift:
            print(d)
        print()
    return len(drift)


def issue_item_ids(issue: dict) -> list[str]:
    """從 issue title / labels 抽**所有** item ID（支援打包 issue：
    e.g. title "P1-05 + P1-06 + P1-07: ..." → ['P1-05', 'P1-06', 'P1-07']）。

    去重保序：title 先抽，再 labels；同 ID 不重複加入。
    """
    ids: list[str] = []
    seen: set[str] = set()
    for m in ITEM_RE.finditer(issue.get("title", "")):
        if m.group(1) not in seen:
            ids.append(m.group(1))
            seen.add(m.group(1))
    for label in issue.get("labels", []):
        for m in ITEM_RE.finditer(label.get("name", "")):
            if m.group(1) not in seen:
                ids.append(m.group(1))
                seen.add(m.group(1))
    return ids


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
        iids = issue_item_ids(iss)
        if iids:
            # 同一 issue 可 map 到多個 item（e.g. 打包 PR：「P1-05 + P1-06 + P1-07」）
            for iid in iids:
                issue_by_id[iid] = iss
        else:
            orphan_issues.append(iss)

    statuses = parse_roadmap_status()

    # Layer 3 — Status Report 放前面，先看全景再看細節
    drift_count = print_status_report(roadmap_items, statuses, issue_by_id)

    missing: list[tuple[str, str]] = []
    closed_not_ticked: list[tuple[str, dict]] = []

    for item_id, title in sorted(roadmap_items.items()):
        if item_id not in issue_by_id:
            missing.append((item_id, title))
            continue
        iss = issue_by_id[item_id]
        if iss.get("state") == "CLOSED" and statuses.get(item_id) != "done":
            # 與 print_status_report 的 drift 偵測互補：這裡只列「應補 tick」
            closed_not_ticked.append((item_id, iss))

    # ── Gap / Drift Report ──
    print("=== Gap Report ===")
    print(f"  ROADMAP items: {len(roadmap_items)}")
    print(f"  GitHub issues (mapped): {len(issue_by_id)}")
    print(f"  Orphan issues (no ROADMAP id): {len(orphan_issues)}")
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

    issues_found = len(missing) + len(orphan_issues) + drift_count
    if issues_found == 0:
        print("✓ ROADMAP ↔ issue 完全同步，無 status drift")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
