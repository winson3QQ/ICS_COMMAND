"""
tests/unit/test_cop_schema_v1.py — P1-03 COP schema v1 contract test

凍結 v1 schema 的不變式：
- source enum 鎖 4 值（manual / pi-node / tak / waveink），含 pi-node 不關門
- severity enum 鎖 3 值
- 必填欄位 reject 缺項
- CoT 規格欄位（type / uid / time / start / stale / how / point + ce/le）齊備
- migration 13 (cop_v1_schema) 落地：3 表 + events lat/lon
- repo CRUD 來回一致（insert → get / list / mark_stale）
"""

import json

import pytest
from pydantic import ValidationError

from core.database import get_conn
from repositories.cop_entity_repo import (
    get_cop_entity,
    insert_cop_entity,
    insert_cop_link,
    insert_cop_track,
    list_cop_entities,
    list_cop_links,
    list_cop_tracks,
    mark_stale,
)
from schemas.cop import CoPEntity, CoPEntityLink, CoPEntityTrack


# ── 共用 fixture：產一個合法 entity payload dict ──────────────────────────────


def _valid_entity_payload(uid: str = "uid-test-001", **overrides) -> dict:
    base = {
        "uid":   uid,
        "type":  "a-f-G-U-C",          # MIL-STD-2525: friend / ground / unit / combat
        "time":  "2026-05-26T10:00:00Z",
        "start": "2026-05-26T10:00:00Z",
        "stale": "2099-01-01T00:00:00Z",  # 遠未來，list 預設 not stale
        "how":   "h-e",                # human estimated
        "lat":   25.0330,
        "lon":   121.5654,
        "source": "tak",
    }
    base.update(overrides)
    return base


# ── 1. Pydantic schema 不變式 ────────────────────────────────────────────────


class TestCoPEntitySchema:
    def test_minimal_valid_payload_passes(self):
        e = CoPEntity(**_valid_entity_payload())
        assert e.uid == "uid-test-001"
        assert e.source == "tak"
        assert e.severity == "info"            # default
        assert e.visible_to == ["all"]         # default
        assert e.version == "2.0"              # default
        assert e.version_clock == 1            # default
        assert e.attributes == {}              # default

    def test_source_enum_locked_to_4_values(self):
        """ROADMAP P1-03 必填 source: enum[manual, pi-node, tak, waveink]"""
        for src in ("manual", "pi-node", "tak", "waveink"):
            CoPEntity(**_valid_entity_payload(source=src))

        with pytest.raises(ValidationError):
            CoPEntity(**_valid_entity_payload(source="invalid"))
        with pytest.raises(ValidationError):
            CoPEntity(**_valid_entity_payload(source=""))

    def test_pi_node_source_not_locked_out(self):
        """ROADMAP 行 39：『預留 pi-node 不關門』"""
        e = CoPEntity(**_valid_entity_payload(source="pi-node"))
        assert e.source == "pi-node"

    def test_severity_enum_locked_to_3_values(self):
        for sev in ("info", "warning", "critical"):
            CoPEntity(**_valid_entity_payload(severity=sev))
        with pytest.raises(ValidationError):
            CoPEntity(**_valid_entity_payload(severity="fatal"))

    def test_required_fields_reject_missing(self):
        """CoT 規格必填欄位缺項 → ValidationError"""
        for missing in ("uid", "type", "time", "start", "stale", "how",
                        "lat", "lon", "source"):
            payload = _valid_entity_payload()
            payload.pop(missing)
            with pytest.raises(ValidationError):
                CoPEntity(**payload)

    def test_lat_lon_range_validation(self):
        with pytest.raises(ValidationError):
            CoPEntity(**_valid_entity_payload(lat=91.0))
        with pytest.raises(ValidationError):
            CoPEntity(**_valid_entity_payload(lon=-181.0))

    def test_heading_deg_range_validation(self):
        """heading_deg ∈ [0, 360)"""
        CoPEntity(**_valid_entity_payload(heading_deg=0.0))
        CoPEntity(**_valid_entity_payload(heading_deg=359.9))
        with pytest.raises(ValidationError):
            CoPEntity(**_valid_entity_payload(heading_deg=360.0))
        with pytest.raises(ValidationError):
            CoPEntity(**_valid_entity_payload(heading_deg=-1.0))

    def test_extra_fields_forbidden(self):
        """v1 凍結後，欄位定義以 schema 為準，未知欄位 reject（防 silent typo）"""
        with pytest.raises(ValidationError):
            CoPEntity(**_valid_entity_payload(unknown_field="x"))

    def test_attributes_is_escape_hatch_dict(self):
        e = CoPEntity(**_valid_entity_payload(
            attributes={"contact": {"callsign": "ALPHA-1"}, "color": "#ff0000"}
        ))
        assert e.attributes["contact"]["callsign"] == "ALPHA-1"


class TestCoPEntityTrackSchema:
    def test_minimal_valid(self):
        t = CoPEntityTrack(uid="uid-1", t="2026-05-26T10:00:00Z",
                           lat=25.0, lon=121.0)
        assert t.hae == 0.0  # default

    def test_required_fields(self):
        with pytest.raises(ValidationError):
            CoPEntityTrack(t="2026-05-26T10:00:00Z", lat=25.0, lon=121.0)  # uid missing


class TestCoPEntityLinkSchema:
    def test_minimal_valid(self):
        link = CoPEntityLink(
            src_uid="uid-1", relation="follows",
            target_uid="uid-2", target_type="a-f-G-E-V",
        )
        assert link.mime is None

    def test_external_resource_via_mime(self):
        link = CoPEntityLink(
            src_uid="uid-1", relation="evidence",
            target_uid="https://photos.example/x.jpg",
            target_type="external",
            mime="image/jpeg",
        )
        assert link.mime == "image/jpeg"


# ── 2. Migration 13 落地（DB-level） ─────────────────────────────────────────


class TestMigration013Landed:
    def test_three_new_tables_exist(self, tmp_db):
        with get_conn() as c:
            tables = {r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name LIKE 'cop_%'"
            )}
        assert tables == {"cop_entities", "cop_entity_tracks", "cop_entity_links"}

    def test_events_lat_lon_added(self, tmp_db):
        with get_conn() as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(events)")}
        assert "lat" in cols
        assert "lon" in cols

    def test_migration_recorded(self, tmp_db):
        with get_conn() as c:
            row = c.execute(
                "SELECT version, name FROM schema_migrations WHERE version=13"
            ).fetchone()
        assert row is not None
        assert row[1] == "cop_v1_schema"

    def test_db_check_constraint_blocks_bad_source(self, tmp_db):
        """SQL 層 CHECK 鎖 source enum（即使 Pydantic 被 bypass）"""
        import sqlite3
        with pytest.raises(sqlite3.IntegrityError), get_conn() as c:
            c.execute(
                "INSERT INTO cop_entities (uid, type, time, start, stale, how, "
                "lat, lon, source) VALUES "
                "('x','a-f-G-U-C','t','t','t','h-e',25.0,121.0,'bogus')"
            )

    def test_db_check_constraint_blocks_bad_severity(self, tmp_db):
        import sqlite3
        with pytest.raises(sqlite3.IntegrityError), get_conn() as c:
            c.execute(
                "INSERT INTO cop_entities (uid, type, time, start, stale, how, "
                "lat, lon, source, severity) VALUES "
                "('x','a-f-G-U-C','t','t','t','h-e',25.0,121.0,'tak','fatal')"
            )


# ── 3. Repository CRUD 來回一致 ──────────────────────────────────────────────


class TestCopEntityRepo:
    def test_insert_and_get_roundtrip(self, tmp_db):
        e = CoPEntity(**_valid_entity_payload(uid="rt-001"))
        insert_cop_entity(e)
        got = get_cop_entity("rt-001")
        assert got is not None
        assert got["uid"] == "rt-001"
        assert got["source"] == "tak"
        assert got["visible_to"] == ["all"]      # JSON decoded
        assert got["attributes"] == {}

    def test_insert_with_attributes_and_visible_to(self, tmp_db):
        e = CoPEntity(**_valid_entity_payload(
            uid="rt-002",
            visible_to=["command", "forward"],
            attributes={"channel": "NetA", "transcript": "test"},
        ))
        insert_cop_entity(e)
        got = get_cop_entity("rt-002")
        assert got["visible_to"] == ["command", "forward"]
        assert got["attributes"]["channel"] == "NetA"

    def test_list_filters_stale_by_default(self, tmp_db):
        insert_cop_entity(CoPEntity(**_valid_entity_payload(
            uid="alive", stale="2099-01-01T00:00:00Z",
        )))
        insert_cop_entity(CoPEntity(**_valid_entity_payload(
            uid="dead", stale="2000-01-01T00:00:00Z",
        )))
        uids = {e["uid"] for e in list_cop_entities()}
        assert "alive" in uids
        assert "dead" not in uids

        uids_all = {e["uid"] for e in list_cop_entities(include_stale=True)}
        assert "dead" in uids_all

    def test_list_filters_by_source(self, tmp_db):
        insert_cop_entity(CoPEntity(**_valid_entity_payload(uid="t1", source="tak")))
        insert_cop_entity(CoPEntity(**_valid_entity_payload(uid="m1", source="manual")))
        tak_uids = {e["uid"] for e in list_cop_entities(source="tak")}
        assert tak_uids == {"t1"}

    def test_mark_stale(self, tmp_db):
        insert_cop_entity(CoPEntity(**_valid_entity_payload(uid="s1")))
        assert mark_stale("s1", "2000-01-01T00:00:00Z") is True
        assert mark_stale("nonexistent", "2000-01-01T00:00:00Z") is False
        # 標 stale 後預設 list 應該過濾掉
        assert "s1" not in {e["uid"] for e in list_cop_entities()}


class TestCopTrackRepo:
    def test_track_insert_and_list_ordered(self, tmp_db):
        insert_cop_entity(CoPEntity(**_valid_entity_payload(uid="moving-1")))
        for i in range(3):
            insert_cop_track(CoPEntityTrack(
                uid="moving-1",
                t=f"2026-05-26T10:0{i}:00Z",
                lat=25.0 + i * 0.001,
                lon=121.0 + i * 0.001,
            ))
        tracks = list_cop_tracks("moving-1")
        assert len(tracks) == 3
        # 時間正序
        assert tracks[0]["t"] < tracks[1]["t"] < tracks[2]["t"]


class TestCopLinkRepo:
    def test_link_insert_and_bidirectional_query(self, tmp_db):
        insert_cop_entity(CoPEntity(**_valid_entity_payload(uid="A")))
        insert_cop_entity(CoPEntity(**_valid_entity_payload(uid="B")))
        insert_cop_link(CoPEntityLink(
            src_uid="A", relation="follows",
            target_uid="B", target_type="a-f-G-E-V",
        ))
        # 查 src
        out_a = list_cop_links(src_uid="A")
        assert len(out_a) == 1
        assert out_a[0]["target_uid"] == "B"
        # 查 target（反向）
        in_b = list_cop_links(target_uid="B")
        assert len(in_b) == 1
        assert in_b[0]["src_uid"] == "A"


# ── 4. services/cop_service 4 個 normalize stub 存在且 raise NotImplementedError ──


class TestCopServiceStubs:
    def test_get_cop_summary_still_works(self, tmp_db):
        """既有 read API 在 P1-03 必須保留（無 regression）"""
        from services.cop_service import get_cop_summary
        result = get_cop_summary()
        assert set(result.keys()) == {"medical", "shelter", "forward", "security"}

    def test_normalize_stubs_raise_not_implemented(self):
        from services.cop_service import (
            normalize_cot,
            normalize_manual,
            normalize_pi_node,
            normalize_waveink,
        )
        with pytest.raises(NotImplementedError):
            normalize_cot(object())
        with pytest.raises(NotImplementedError):
            normalize_pi_node("medical", {})
        with pytest.raises(NotImplementedError):
            normalize_waveink({})
        with pytest.raises(NotImplementedError):
            normalize_manual(None)
