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
        (BLUE, "tak", None, False),  # 藍軍 → 擋未分類（fail-closed）
        (BLUE, "manual", None, True),  # 非 tak（指揮部自建）→ 恆送，不受 faction 限
        (BLUE, "command", None, True),  # 下行指令 → 恆送
        (BLUE, "pi-node", "red", True),  # 自有感測非 tak → 恆送（即使誤標 red）
    ],
)
def test_conn_wants_faction(vf, source, faction, expected):
    assert _conn(vf).wants(None, source, faction) is expected


# ── 3. repo SQL 層 faction 過濾（PR-4）────────────────────────────────────────


@pytest.fixture(autouse=True)
def _db(tmp_db):
    yield


def _insert(uid, source, faction):
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
