# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
tests/unit/test_faction_enforce.py — #343 PR-3/PR-4：角色→可見 faction 映射 + 三層強制點核心邏輯

鎖住：
- 角色映射：sysadmin（白隊）→ None 全見；commander/operator/observer（藍軍）→ {blue, neutral}
- WS _Conn.wants：只 source='tak' 受 faction 過濾（NULL=fail-closed）；非 tak 恆送；全見連線收 red
- repo list_cop_entities(visible_factions=)：SQL 層過濾，tak red/NULL 排除、blue/neutral/非 tak 保留
"""

import pytest

from auth.role_enum import visible_factions_for_session
from repositories import cop_entity_repo
from schemas.cop import CoPEntity
from services.realtime_hub import _Conn

BLUE = frozenset({"blue", "neutral"})


# ── 1. 角色 → 可見 faction 映射（PR-3）──────────────────────────────────────────


@pytest.mark.parametrize(
    "role_detail,expected",
    [
        ("sysadmin", None),  # 白隊全見
        ("commander", BLUE),
        ("operator", BLUE),
        ("observer", BLUE),
    ],
)
def test_visible_factions_mapping(role_detail, expected):
    assert visible_factions_for_session({"role_detail": role_detail}) == expected


# ── 2. WS _Conn.wants faction 過濾（PR-4）─────────────────────────────────────


def _conn(visible_factions):
    # exercise_id=None → 場域 gate 全通，隔離出 faction 邏輯單測
    return _Conn(ws=None, exercise_id=None, visible_factions=visible_factions)


@pytest.mark.parametrize(
    "vf,source,faction,expected",
    [
        (None, "tak", "red", True),  # 全見（sysadmin）→ 收 red
        (None, "tak", None, True),  # 全見 → 收未分類
        (BLUE, "tak", "blue", True),  # 藍軍 → 收 blue
        (BLUE, "tak", "neutral", True),  # 藍軍 → 收 neutral
        (BLUE, "tak", "red", False),  # 藍軍 → 擋 red
        (BLUE, "tak", None, True),  # #472：藍軍 → 未分類「平時」（無 active 演習）放行；演習中藏見下方單測
        (BLUE, "manual", None, True),  # 非 tak（指揮部自建）→ 恆送，不受 faction 限
        (BLUE, "command", None, True),  # 下行指令 → 恆送
        (BLUE, "pi-node", "red", True),  # 自有感測非 tak → 恆送（即使誤標 red）
    ],
)
def test_conn_wants_faction(vf, source, faction, expected):
    # 無 active 演習（平時）：未分類 tak 放行（#472）；本組不設 active 演習。
    assert _conn(vf).wants(None, source, faction) is expected


def test_conn_wants_unclassified_hidden_during_exercise(monkeypatch):
    """#472：演習中（有 active 演習）→ 未編隊 tak 藏（fail-closed）；紅恆藏；藍/中立照收。"""
    monkeypatch.setattr("services.exercise_service.current_exercise_id", lambda: 3)
    c = _conn(BLUE)
    assert c.wants(None, "tak", None) is False  # 未編隊 → 演習中藏
    assert c.wants(None, "tak", "red") is False  # 紅 → 恆藏
    assert c.wants(None, "tak", "blue") is True  # 藍 → 收
    assert c.wants(None, "manual", None) is True  # 自建 → 恆送


# ── 3. repo SQL 層 faction 過濾（PR-4）────────────────────────────────────────


@pytest.fixture(autouse=True)
def _db(tmp_db):
    yield


def _insert(uid, source, faction, team_color=None):
    cop_entity_repo.insert_cop_entity(
        CoPEntity(
            uid=uid,
            type="a-h-G",
            time="2026-06-22T00:00:00Z",
            start="2026-06-22T00:00:00Z",
            stale="2099-01-01T00:00:00Z",  # 遠未來，不被 stale 過濾
            how="h-e",
            lat=24.1,
            lon=120.6,
            source=source,
            faction=faction,
            team_color=team_color,
        )
    )


def test_list_filters_tak_by_faction():
    _insert("tak-blue", "tak", "blue")
    _insert("tak-red", "tak", "red")
    _insert("tak-null", "tak", None)  # 未分類
    _insert("manual-x", "manual", None)  # 自建
    _insert("tak-neutral", "tak", "neutral")

    # 藍軍視角
    blue_uids = {e["uid"] for e in cop_entity_repo.list_cop_entities(visible_factions=BLUE)}
    assert blue_uids == {"tak-blue", "tak-neutral", "manual-x"}  # red/null 排除、manual 保留

    # 全見（None）→ 全部
    all_uids = {e["uid"] for e in cop_entity_repo.list_cop_entities(visible_factions=None)}
    assert all_uids == {"tak-blue", "tak-red", "tak-null", "manual-x", "tak-neutral"}


def test_list_allow_null_faction_includes_unclassified_in_peacetime():
    # #472：平時（allow_null_faction=True）→ 藍軍額外看得到未編隊 tak；紅恆排除。
    _insert("tak-blue", "tak", "blue")
    _insert("tak-red", "tak", "red")
    _insert("tak-null", "tak", None)  # 未編隊
    _insert("manual-x", "manual", None)  # 自建恆可見

    # allow_null_faction=False（演習中）→ 未編隊藏（fail-closed，＝既有行為）
    ex = {e["uid"] for e in cop_entity_repo.list_cop_entities(visible_factions=BLUE, allow_null_faction=False)}
    assert ex == {"tak-blue", "manual-x"}
    # allow_null_faction=True（平時）→ 未編隊放行；紅仍排除
    peace = {e["uid"] for e in cop_entity_repo.list_cop_entities(visible_factions=BLUE, allow_null_faction=True)}
    assert peace == {"tak-blue", "tak-null", "manual-x"}


def test_build_dashboard_tak_squads_faction_filtered():
    # #472 安全補漏：dashboard 的 tak_squads 聚合現在套 faction 過濾（紅隊 centroid/兵力不再洩漏給 READ_ROLES）。
    from services.dashboard_service import build_dashboard

    _insert("blue-1", "tak", "blue", team_color="Cyan")
    _insert("red-1", "tak", "red", team_color="Crimson")

    # 藍軍視角 → tak_squads 只含 Cyan，**不含 Crimson（紅）**
    colors = {s["team_color"] for s in build_dashboard(visible_factions=BLUE)["tak_squads"]}
    assert "Cyan" in colors
    assert "Crimson" not in colors
    # sysadmin（vf=None）→ 全見（含紅）
    colors_all = {s["team_color"] for s in build_dashboard(visible_factions=None)["tak_squads"]}
    assert "Crimson" in colors_all
