"""
integration/test_events_drop_location_zone.py — P2-32（#186）砍 events.location_zone_id 死欄

驗證 m025 後：
- events 表不再有 location_zone_id 欄
- create_event 不再寫該欄（即使 data dict 殘留也忽略，不報錯）
- EventIn schema 對前端仍送的 location_zone_id 容忍（extra ignored，不 422）——
  前端 map.js 尚未停送（後端半先行，前端留 P2-33 cutover）
- 重跑 init_db 仍 idempotent
"""

import pytest

from core.database import get_conn, init_db

pytestmark = pytest.mark.integration

_BASE = {
    "reported_by_unit": "shelter",
    "event_type": "fire",
    "severity": "critical",
    "description": "測試事件",
    "operator_name": "admin",
}


def _event_cols(conn):
    return {row[1] for row in conn.execute("PRAGMA table_info(events)")}


class TestColumnDropped:
    def test_events_table_has_no_location_zone_id(self, tmp_db):
        with get_conn() as conn:
            assert "location_zone_id" not in _event_cols(conn)

    def test_migration_idempotent(self, tmp_db):
        init_db()  # 再跑一次不應出錯
        with get_conn() as conn:
            assert "location_zone_id" not in _event_cols(conn)


class TestCreateEventNoZoneCol:
    def test_create_ignores_stray_location_zone_id(self, tmp_db):
        """data dict 殘留 location_zone_id（前端尚未停送）→ create 忽略、不炸。"""
        from repositories.event_repo import create_event, get_events
        ev = create_event({**_BASE, "location_zone_id": "evt_123456"})
        assert ev["event_code"].startswith("EV-")
        found = next((e for e in get_events() if e["id"] == ev["id"]), None)
        assert found is not None
        assert "location_zone_id" not in found  # 欄已不存在於回傳

    def test_patch_ignores_location_zone_id(self, tmp_db):
        """patch_event allowed set 已移除 location_zone_id → 該 key 被濾掉、不寫。"""
        from repositories.event_repo import create_event, patch_event, get_events
        ev = create_event(_BASE.copy())
        patch_event(ev["id"], {"location_zone_id": "evt_x", "location_desc": "A 區"})
        found = next(e for e in get_events() if e["id"] == ev["id"])
        assert found["location_desc"] == "A 區"  # 合法欄有更新
        assert "location_zone_id" not in found


class TestSchemaBackCompat:
    def test_eventin_ignores_extra_location_zone_id(self, tmp_db):
        """前端 map.js 仍在 POST body 送 location_zone_id（P2-33 才停送）→
        EventIn 須容忍 extra（Pydantic 預設 ignore），不得 422。"""
        from schemas.event import EventIn
        m = EventIn(**{**_BASE, "location_zone_id": "evt_999"})
        assert not hasattr(m, "location_zone_id")
