"""tests/unit/test_event_markers.py — P2-27 event↔marker junction 關聯

鎖住的不變式：
- event_markers junction 取代 attributes.kind='event' JSON glue 當**權威關聯**
- link_marker idempotent（PK 撞→OR IGNORE，回傳是否新建）
- N:1 且 N:M-ready（同一 marker 可關聯多 event）
- 導航鏈 get_event_chain = {event, markers(via junction), decisions(via primary_event_id)}
- 雙向 ON DELETE CASCADE：硬刪 event / 硬刪 cop_entity → 關聯自動消
- backfill：既有 JSON glue 回填，且**孤兒守門**（event 不存在不回填，避免 FK 炸）
- events.lat/lon 死欄已由 m022 砍除
"""

import pytest

from core.database import _m021_event_markers, get_conn
from repositories import event_marker_repo
from repositories._helpers import NULL_SCOPE
from repositories.cop_entity_repo import insert_cop_entity
from repositories.decision_repo import create_decision
from repositories.event_repo import create_event
from repositories.exercise_repo import create_exercise
from schemas.cop import CoPEntity

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _db(tmp_db):
    """本模組所有測試套用 tmp_db（隔離 SQLite + init_db 建表/跑 migration）。"""


_FUTURE = "2099-01-01T00:00:00Z"


def _mk_event(desc="倒塌回報", exercise_id=None):
    return create_event(
        {
            "reported_by_unit": "forward",
            "event_type": "structural_collapse",
            "description": desc,
            "operator_name": "tester",
            "severity": "warning",
        },
        exercise_id,
    )["id"]


def _mk_marker(uid, *, kind="event", event_id=None, lat=25.0, lon=121.0):
    attrs = {"kind": kind}
    if event_id is not None:
        attrs["event_id"] = event_id
    return insert_cop_entity(
        CoPEntity(
            uid=uid,
            type="a-u-G",
            time="2026-06-09T10:00:00Z",
            start="2026-06-09T10:00:00Z",
            stale=_FUTURE,
            how="h-e",
            lat=lat,
            lon=lon,
            source="manual",
            attributes=attrs,
        )
    )


# ── link / idempotent ────────────────────────────────────────────────────────


def test_link_marker_idempotent():
    ev = _mk_event()
    _mk_marker("manual:m1")
    assert event_marker_repo.link_marker(ev, "manual:m1") is True
    assert event_marker_repo.link_marker(ev, "manual:m1") is False  # 重複→不新建
    markers = event_marker_repo.get_markers_for_event(ev)
    assert len(markers) == 1
    assert markers[0]["uid"] == "manual:m1"
    assert markers[0]["link_role"] == "primary"


def test_link_marker_fk_violation_on_missing_event():
    """event 不存在 → FK 擋（IntegrityError）。caller（router）best-effort 吞之。"""
    import sqlite3

    _mk_marker("manual:m2")
    with pytest.raises(sqlite3.IntegrityError):
        event_marker_repo.link_marker("no-such-event", "manual:m2")


# ── N:M-ready ────────────────────────────────────────────────────────────────


def test_marker_can_link_multiple_events():
    """同一 marker 關聯兩個 event（一棟樓同屬火災+搜救）→ get_events_for_marker 回 2。"""
    ev_fire = _mk_event("火災")
    ev_sar = _mk_event("搜救")
    _mk_marker("manual:building")
    event_marker_repo.link_marker(ev_fire, "manual:building")
    event_marker_repo.link_marker(ev_sar, "manual:building")
    events = event_marker_repo.get_events_for_marker("manual:building")
    assert {e["id"] for e in events} == {ev_fire, ev_sar}


# ── 導航鏈 ───────────────────────────────────────────────────────────────────


def test_get_event_chain_aggregates_markers_and_decisions():
    ev = _mk_event("複合事件")
    _mk_marker("manual:cm1")
    _mk_marker("manual:cm2")
    event_marker_repo.link_marker(ev, "manual:cm1")
    event_marker_repo.link_marker(ev, "manual:cm2")
    create_decision(
        {
            "primary_event_id": ev,
            "decision_type": "initial",
            "severity": "warning",
            "decision_title": "撤不撤",
            "impact_description": "影響東側",
            "suggested_action_a": "撤離",
            "created_by": "cmd",
        }
    )
    chain = event_marker_repo.get_event_chain(ev, NULL_SCOPE)
    assert chain["event"]["id"] == ev
    assert {m["uid"] for m in chain["markers"]} == {"manual:cm1", "manual:cm2"}
    assert len(chain["decisions"]) == 1
    assert chain["decisions"][0]["primary_event_id"] == ev


def test_get_event_chain_missing_event_returns_none():
    assert event_marker_repo.get_event_chain("nope", NULL_SCOPE) is None


# ── P1-14 exercise scope 守門（PII 不跨場洩漏）────────────────────────────────


def test_chain_scope_blocks_cross_exercise():
    """演習場 event，caller scope=實戰池(NULL_SCOPE) → 視同不存在（None），不洩 PII。"""
    ex = create_exercise({"name": "EX-A", "type": "ttx"})["id"]
    ev = _mk_event("演習傷患", exercise_id=ex)
    assert event_marker_repo.get_event_chain(ev, NULL_SCOPE) is None  # 跨場 → 擋
    assert event_marker_repo.get_event_chain(ev, ex)["event"]["id"] == ev  # 同場 → 放行


def test_chain_scope_blocks_realworld_from_exercise_scope():
    """實戰 event(exercise_id=NULL)，caller scope=某演習 int → 擋（None）。"""
    ex = create_exercise({"name": "EX-B", "type": "ttx"})["id"]
    ev = _mk_event("實戰傷患", exercise_id=None)  # 實戰池
    assert event_marker_repo.get_event_chain(ev, ex) is None  # 拿演習 scope 看實戰 → 擋
    assert event_marker_repo.get_event_chain(ev, NULL_SCOPE)["event"]["id"] == ev  # 實戰 scope → 放行


# ── 雙向 cascade ─────────────────────────────────────────────────────────────


def test_cascade_on_hard_delete_event():
    ev = _mk_event()
    _mk_marker("manual:cd1")
    event_marker_repo.link_marker(ev, "manual:cd1")
    with get_conn() as conn:  # get_conn 設 foreign_keys=ON → 硬刪 event cascade
        conn.execute("DELETE FROM events WHERE id=?", (ev,))
        left = conn.execute("SELECT COUNT(*) FROM event_markers WHERE event_id=?", (ev,)).fetchone()[0]
    assert left == 0


def test_cascade_on_hard_delete_marker():
    ev = _mk_event()
    _mk_marker("manual:cd2")
    event_marker_repo.link_marker(ev, "manual:cd2")
    with get_conn() as conn:
        conn.execute("DELETE FROM cop_entities WHERE uid=?", ("manual:cd2",))
        left = conn.execute("SELECT COUNT(*) FROM event_markers WHERE cop_entity_uid=?", ("manual:cd2",)).fetchone()[0]
    assert left == 0


# ── backfill（m021）+ 孤兒守門 ────────────────────────────────────────────────


def test_backfill_picks_up_json_glue():
    """既有 kind='event'+event_id 的 cop_entity（無 junction）→ 重跑 m021 回填。"""
    ev = _mk_event()
    _mk_marker("manual:bf1", event_id=ev)  # 直接 insert（不走 router linking）→ 無 junction
    assert event_marker_repo.get_markers_for_event(ev) == []  # 確認重構前無關聯
    with get_conn() as conn:
        _m021_event_markers(conn)  # idempotent 重跑 → backfill
    markers = event_marker_repo.get_markers_for_event(ev)
    assert len(markers) == 1 and markers[0]["uid"] == "manual:bf1"


def test_backfill_skips_orphan_event_id():
    """attributes.event_id 指向不存在的 event → 守門不回填（避免孤兒 FK）。"""
    _mk_marker("manual:orphan", event_id="ghost-event")
    with get_conn() as conn:
        _m021_event_markers(conn)
        cnt = conn.execute("SELECT COUNT(*) FROM event_markers WHERE cop_entity_uid=?", ("manual:orphan",)).fetchone()[
            0
        ]
    assert cnt == 0


# ── 位置 SoT 清理（m022）─────────────────────────────────────────────────────


def test_events_lat_lon_columns_dropped():
    with get_conn() as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
    assert "lat" not in cols and "lon" not in cols


# ── P2-33b step ①a：序列化帶入 junction primary event_id（glue 退役地基）──────────


class TestPrimaryEventIdSerialization:
    """get/list cop_entity 頂層 `event_id` 來自 **junction**（非 attributes glue），
    為前端 `copEntityToEventZone` 改吃 junction-fed event_id 鋪路（#196 step ①a）。"""

    def test_get_carries_junction_primary_event_id(self):
        from repositories.cop_entity_repo import get_cop_entity

        ev = _mk_event()
        _mk_marker("evt:p1", kind="event", event_id=ev)
        event_marker_repo.link_marker(ev, "evt:p1", "primary")
        assert get_cop_entity("evt:p1")["event_id"] == ev

    def test_list_carries_junction_primary_event_id(self):
        from repositories.cop_entity_repo import list_cop_entities

        ev = _mk_event()
        _mk_marker("evt:p2", kind="event", event_id=ev)
        event_marker_repo.link_marker(ev, "evt:p2", "primary")
        d = next(e for e in list_cop_entities() if e["uid"] == "evt:p2")
        assert d["event_id"] == ev

    def test_non_event_entity_has_none(self):
        from repositories.cop_entity_repo import get_cop_entity

        _mk_marker("zone:z1", kind="zone")  # 無 junction link
        assert get_cop_entity("zone:z1")["event_id"] is None

    def test_junction_is_source_not_attributes_glue(self):
        """marker 帶 attributes.event_id glue 但**無 junction link** → 頂層 event_id 仍 None。
        證明權威來源 = junction，非 attributes（glue 退役後此行為即正解）。"""
        from repositories.cop_entity_repo import get_cop_entity

        ev = _mk_event()
        _mk_marker("evt:glueonly", kind="event", event_id=ev)  # 只塞 attributes，不 link
        assert get_cop_entity("evt:glueonly")["event_id"] is None
