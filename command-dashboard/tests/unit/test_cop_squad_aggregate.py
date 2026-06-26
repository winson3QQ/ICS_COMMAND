"""
tests/unit/test_cop_squad_aggregate.py — P2-06d（issue #128）小隊聚合 SQL

鎖住的不變式（cop_entity_repo.aggregate_squads）：
- 按 team_color 分組，team_color 升序（NULL 組排首＝未分隊）
- total = COUNT(*)（含 stale entity，不過濾）
- online = stale > now 的數量；offline = total - online（邊界：剛好過期算 offline）
- avg_battery = AVG(battery)，NULL battery 不計入；全組無 battery → None
- centroid_lat/lon = AVG(lat)/AVG(lon) 簡單算術平均
- team_color IS NULL 保留為「未分隊」組（key=None）
- exercise_id 三態（int / NULL_SCOPE / None）
"""

import pytest

from repositories._helpers import NULL_SCOPE
from repositories.cop_entity_repo import aggregate_squads, insert_cop_entity
from repositories.exercise_repo import create_exercise
from schemas.cop import CoPEntity

pytestmark = pytest.mark.unit


def _mk_exercise(name="EX"):
    """建一筆 exercise 滿足 cop_entities.exercise_id FK，回傳 id。"""
    return create_exercise({"name": name, "type": "ttx"})["id"]


_FUTURE = "2099-01-01T00:00:00Z"  # 遠未來 → online
_PAST = "2000-01-01T00:00:00Z"  # 已過期 → offline


def _insert(uid, *, stale=_FUTURE, lat=25.0, lon=121.0, team_color="Cyan", battery=None, exercise_id=None):
    payload = {
        "uid": uid,
        "type": "a-f-G-U-C",
        "time": "2026-05-29T10:00:00Z",
        "start": "2026-05-29T10:00:00Z",
        "stale": stale,
        "how": "h-e",
        "lat": lat,
        "lon": lon,
        "source": "manual",
        "team_color": team_color,
        "battery": battery,
        "exercise_id": exercise_id,
    }
    return insert_cop_entity(CoPEntity(**payload))


def _by_color(rows):
    return {r["team_color"]: r for r in rows}


# ── 分組 + 升序 ───────────────────────────────────────────────────────────────


def test_groups_by_team_color_ascending(tmp_db):
    _insert("c1", team_color="Cyan")
    _insert("c2", team_color="Cyan")
    _insert("b1", team_color="Blue")
    rows = aggregate_squads()
    assert [r["team_color"] for r in rows] == ["Blue", "Cyan"]  # 升序
    by = _by_color(rows)
    assert by["Cyan"]["total"] == 2
    assert by["Blue"]["total"] == 1


# ── online / offline 邊界 ─────────────────────────────────────────────────────


def test_online_offline_split(tmp_db):
    _insert("live1", team_color="Red", stale=_FUTURE)
    _insert("live2", team_color="Red", stale=_FUTURE)
    _insert("dead1", team_color="Red", stale=_PAST)
    rows = aggregate_squads()
    red = _by_color(rows)["Red"]
    assert red["total"] == 3  # total 含 stale entity
    assert red["online"] == 2
    assert red["offline"] == 1


def test_all_offline_squad(tmp_db):
    _insert("d1", team_color="Gray", stale=_PAST)
    rows = aggregate_squads()
    gray = _by_color(rows)["Gray"]
    assert gray["total"] == 1
    assert gray["online"] == 0
    assert gray["offline"] == 1


# ── centroid（算術平均）──────────────────────────────────────────────────────


def test_centroid_is_arithmetic_mean(tmp_db):
    _insert("p1", team_color="Green", lat=10.0, lon=100.0)
    _insert("p2", team_color="Green", lat=20.0, lon=120.0)
    rows = aggregate_squads()
    green = _by_color(rows)["Green"]
    assert green["centroid_lat"] == pytest.approx(15.0)
    assert green["centroid_lon"] == pytest.approx(110.0)


# ── avg_battery（NULL 不計入 / 全 NULL → None）────────────────────────────────


def test_avg_battery_skips_null(tmp_db):
    _insert("e1", team_color="Teal", battery=80)
    _insert("e2", team_color="Teal", battery=40)
    _insert("e3", team_color="Teal", battery=None)  # NULL 不計入 AVG
    rows = aggregate_squads()
    teal = _by_color(rows)["Teal"]
    assert teal["avg_battery"] == pytest.approx(60.0)  # (80+40)/2，非 /3


def test_avg_battery_all_null_is_none(tmp_db):
    _insert("n1", team_color="Olive", battery=None)
    _insert("n2", team_color="Olive", battery=None)
    rows = aggregate_squads()
    olive = _by_color(rows)["Olive"]
    assert olive["avg_battery"] is None


# ── 未分隊（team_color IS NULL）保留為一組 ────────────────────────────────────


def test_null_team_color_kept_as_unassigned_group(tmp_db):
    _insert("u1", team_color=None)
    _insert("u2", team_color=None)
    _insert("a1", team_color="Cyan")
    rows = aggregate_squads()
    by = _by_color(rows)
    assert None in by  # 未分隊組存在，未被過濾
    assert by[None]["total"] == 2
    # NULL 在 SQLite 升序排首
    assert rows[0]["team_color"] is None


# ── exercise_id 三態 ──────────────────────────────────────────────────────────


def test_exercise_id_int_filters_exact(tmp_db):
    ex_a = _mk_exercise("A")
    ex_b = _mk_exercise("B")
    _insert("x1", team_color="Red", exercise_id=ex_a)
    _insert("x2", team_color="Red", exercise_id=ex_b)
    rows = aggregate_squads(exercise_id=ex_a)
    assert len(rows) == 1
    assert rows[0]["total"] == 1


def test_exercise_id_null_scope_filters_is_null(tmp_db):
    ex_a = _mk_exercise("A")
    _insert("pool1", team_color="Red", exercise_id=None)  # 實戰池
    _insert("ex1", team_color="Red", exercise_id=ex_a)
    rows = aggregate_squads(exercise_id=NULL_SCOPE)
    assert len(rows) == 1
    assert rows[0]["total"] == 1


def test_exercise_id_none_no_filter(tmp_db):
    ex_a = _mk_exercise("A")
    _insert("a1", team_color="Red", exercise_id=ex_a)
    _insert("a2", team_color="Red", exercise_id=None)
    rows = aggregate_squads(exercise_id=None)  # 不過濾
    assert rows[0]["total"] == 2


def test_empty_db_returns_empty_list(tmp_db):
    assert aggregate_squads() == []
