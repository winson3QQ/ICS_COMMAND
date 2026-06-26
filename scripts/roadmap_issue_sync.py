#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
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
    "done": "✅",
    "in-progress": "⏳",
    "blocked": "🚧",
    "pending": "  ",
}


# 交叉引用 context 詞（#155）：item ID 與這些詞相鄰時是「順帶提及」非本 issue 主體。
# e.g. 「P1-12 prep」「source command 延 P2-13」—— 該 ID 不應對映成本 issue 的工作項。
_CROSSREF_AFTER = ("prep", "前置", "前提")  # e.g. 「P1-12 prep」「P2-13 前置」
_CROSSREF_BEFORE = ("延", "依賴", "解鎖", "取代", "替代")  # e.g. 「延 P2-13」


def _is_crossref(text: str, start: int, end: int) -> bool:
    """text[start:end] 的 item ID 是否為交叉引用（相鄰 context 詞）。"""
    after = text[end : end + 10].lstrip(" :：,，)")
    if any(after.startswith(w) for w in _CROSSREF_AFTER):
        return True
    before = text[max(0, start - 6) : start]
    return any(w in before for w in _CROSSREF_BEFORE)


def _iter_item_rows(text: str):
    """逐 item yield (item_id, cells, match)：只認**表格列**（`|` 開頭）且 item ID
    出現在**首格**（其定義列）。跳過散文（reality-check blockquote 等）與其他 cell 的
    交叉引用同 ID（#155）。每個 item 取第一個符合的列。"""
    seen: set[str] = set()
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = line.split("|")
        if len(cells) < 2:
            continue
        m = ITEM_RE.search(cells[1])  # cells[0] 為首 `|` 前空字串；cells[1] = 首格
        if not m:
            continue
        item_id = m.group(1)
        if item_id in seen:
            continue
        seen.add(item_id)
        yield item_id, cells, m


def parse_roadmap_items() -> dict[str, str]:
    """從 ROADMAP.md 抽 item ID → 標題（其定義表格列的描述格）。"""
    items: dict[str, str] = {}
    if not ROADMAP.exists():
        print(f"⚠  {ROADMAP} 不存在", file=sys.stderr)
        return items
    for item_id, cells, _m in _iter_item_rows(ROADMAP.read_text(encoding="utf-8")):
        # 標題取描述格（首格是 marker+ID，描述在第二格）
        desc = cells[2].strip() if len(cells) > 2 else ""
        title = re.sub(r"^\W+", "", desc.replace("**", "")).strip()
        items[item_id] = title[:80] if title else "(no title)"
    return items


def gh_issues() -> list[dict]:
    """gh issue list — 拿所有 open + recently closed"""
    try:
        out = subprocess.run(
            ["gh", "issue", "list", "--state", "all", "--limit", "200", "--json", "number,title,state,labels"],
            # encoding 明指 UTF-8：gh 輸出含中文 issue 標題，Windows 預設 cp950 解碼會
            # UnicodeDecodeError → out.stdout=None → json.loads 炸（#148 status.sh 崩根因）。
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
            timeout=15,
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
    for item_id, cells, m in _iter_item_rows(ROADMAP.read_text(encoding="utf-8")):
        # marker 只看**首格** item ID 之前的字（不讀散文 / 其他 cell 的交叉引用，#155）
        prefix = cells[1][: m.start()]
        status = "pending"
        for marker, label in STATUS_MARKERS.items():
            if marker in prefix:
                status = label
                break
        statuses[item_id] = status
    return statuses


def phase_of(item_id: str) -> str:
    return item_id.split("-")[0]  # "P1-01" → "P1"


def print_status_report(roadmap_items: dict[str, str], statuses: dict[str, str], issue_by_id: dict[str, dict]) -> int:
    """印 status report，回傳 drift 數量（0 = 無 drift）"""
    by_phase: dict[str, list[tuple[str, str, str]]] = {}
    for item_id, title in sorted(roadmap_items.items()):
        phase = phase_of(item_id)
        status = statuses.get(item_id, "pending")
        by_phase.setdefault(phase, []).append((item_id, status, title))

    print("=== ROADMAP Status Report ===\n")
    for phase in sorted(by_phase):
        items = by_phase[phase]
        counts = {k: sum(1 for _, s, _ in items if s == k) for k in ("done", "in-progress", "blocked", "pending")}
        print(
            f"{phase}: {counts['done']}/{len(items)} done"
            f"  ·  {counts['in-progress']} in-progress"
            f"  ·  {counts['blocked']} blocked"
            f"  ·  {counts['pending']} pending"
        )
        for item_id, status, title in items:
            icon = STATUS_ICON[status]
            iss = issue_by_id.get(item_id)
            iss_str = f"(#{iss['number']} {iss['state']})" if iss else "(no issue)"
            print(f"  {icon} {item_id:<8} {title[:48]:<48} {iss_str}")
        print()

    # Drift detection（#155：只報「真矛盾」）
    drift: list[str] = []
    for item_id in sorted(roadmap_items):
        st = statuses.get(item_id, "pending")
        iss = issue_by_id.get(item_id)
        # 「done 但無 issue」**不算 drift** —— 多 PR 完成 / 無單一對映 issue 是正常
        # （per-item 行已顯示 "no issue"）；只有「done 但 issue 仍 OPEN」才是真矛盾。
        if st == "done" and iss and iss.get("state") != "CLOSED":
            drift.append(f"  - {item_id}: ROADMAP ✅ 但 issue {iss['state']}（未關閉）")
        elif st != "done" and iss and iss.get("state") == "CLOSED":
            drift.append(f"  - {item_id}: issue CLOSED 但 ROADMAP 未 ✅（PROCESS step 8.5 漏勾）")
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
    for text in [issue.get("title", "")] + [lbl.get("name", "") for lbl in issue.get("labels", [])]:
        for m in ITEM_RE.finditer(text):
            if _is_crossref(text, m.start(), m.end()):
                continue  # 順帶提及（P1-12 prep / 延 P2-13），非本 issue 主體（#155）
            if m.group(1) not in seen:
                ids.append(m.group(1))
                seen.add(m.group(1))
    return ids


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--create-missing", action="store_true", help="互動建立缺漏的 issue（預設只報告）")
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
            print(f'    建議：gh issue create --title "{item_id}: {title[:50]}" \\')
            print('             --body "見 docs/ROADMAP.md 該段落"')
        print()

    if orphan_issues:
        print(f"⚠  GitHub issue 沒 ROADMAP item ID（{len(orphan_issues)}）：")
        for iss in orphan_issues[:10]:
            print(f"  - #{iss['number']} [{iss['state']}] {iss['title'][:70]}")
        if len(orphan_issues) > 10:
            print(f"  ... +{len(orphan_issues) - 10} 更多")
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
