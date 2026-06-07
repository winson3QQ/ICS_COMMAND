"""tests/unit/test_roadmap_sync.py — scripts/roadmap_issue_sync.py drift parser（#155）。

鎖住的不變式：
- issue→item 對映排除交叉引用（「P1-12 prep」「延 P2-13」「P2-13 前置」），保留主體 + bundled。
- 狀態只從 item 的「定義表格列」首格讀 marker，跳散文（reality-check blockquote）。
- 「done 但無 issue」不算 drift；「done 但 issue OPEN」才是真 drift。
"""

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import roadmap_issue_sync as ris  # noqa: E402


def _issue(title: str, labels=None) -> dict:
    return {"title": title, "labels": [{"name": n} for n in (labels or [])]}


# ── issue_item_ids：排除交叉引用，保留主體 + bundled ─────────────────────────


def test_maps_primary_not_prep_crossref():
    # #27 真實標題：主體 P1-13，「P1-12 prep」順帶 → 不對映 P1-12
    assert ris.issue_item_ids(_issue(
        "refactor(map): split map_config into tracked seed + gitignored runtime (P1-13, P1-12 prep)"
    )) == ["P1-13"]


def test_excludes_deferred_crossref():
    # #140：主體 P2-11b，「延 P2-13」順帶
    assert ris.issue_item_ids(_issue(
        "P2-11b（部分）CoPEntity 加 planned/simulated 欄位（source command 延 P2-13）"
    )) == ["P2-11b"]


def test_excludes_prerequisite_crossref():
    # #141：主體無 P-item（source command enum），「P2-13 前置」順帶 → 空
    assert ris.issue_item_ids(_issue(
        "TAK source 'command' enum + cop_entities table rebuild（P2-13 前置）"
    )) == []


def test_primary_id_kept():
    assert ris.issue_item_ids(_issue("P2-01：部署官方 TAK Server")) == ["P2-01"]


def test_bundled_all_kept():
    # 打包 issue：多主體全對映（非交叉引用，不可被誤殺）
    assert ris.issue_item_ids(
        _issue("P1-05 + P1-06 + P1-07: tests green + CI + 規格 header")
    ) == ["P1-05", "P1-06", "P1-07"]


# ── _iter_item_rows：只讀表格列首格，跳散文 ─────────────────────────────────


def test_status_from_table_row_not_prose():
    text = "\n".join([
        "> reality check：P2-01 部署全 greenfield、零實作",  # 散文先出現，無 marker
        "| ✅ P2-01 | 部署官方 TAK Server |",  # 真正定義列，有 ✅
    ])
    iid, cells, m = next(r for r in ris._iter_item_rows(text) if r[0] == "P2-01")
    assert "✅" in cells[1][:m.start()]  # marker 取自首格、ID 之前


def test_iter_skips_non_table_lines():
    text = "本文提到 P3-09 但不是表格列\n| ✅ P3-09 | 規格書補 WaveInk |"
    assert [iid for iid, _c, _m in ris._iter_item_rows(text)] == ["P3-09"]


# ── drift 判定：done+無 issue 非 drift；done+issue OPEN 才是 ──────────────────


def test_done_without_issue_is_not_drift(capsys):
    drift = ris.print_status_report({"P1-04": "federation infra"}, {"P1-04": "done"}, {})
    assert drift == 0


def test_done_with_open_issue_is_drift(capsys):
    drift = ris.print_status_report(
        {"P2-12": "panels"}, {"P2-12": "done"}, {"P2-12": {"number": 136, "state": "OPEN"}}
    )
    assert drift == 1


def test_closed_issue_pending_item_is_drift(capsys):
    drift = ris.print_status_report(
        {"P2-99": "x"}, {"P2-99": "pending"}, {"P2-99": {"number": 1, "state": "CLOSED"}}
    )
    assert drift == 1  # issue 關了但 ROADMAP 沒勾 = 真 8.5 漏勾
