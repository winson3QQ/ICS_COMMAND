# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
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
        "uid": uid,
        "type": "a-f-G-U-C",  # MIL-STD-2525: friend / ground / unit / combat
        "time": "2026-05-26T10:00:00Z",
        "start": "2026-05-26T10:00:00Z",
        "stale": "2099-01-01T00:00:00Z",  # 遠未來，list 預設 not stale
        "how": "h-e",  # human estimated
        "lat": 25.0330,
        "lon": 121.5654,
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
        assert e.severity == "info"  # default
        assert e.visible_to == ["all"]  # default
        assert e.version == "2.0"  # default
        assert e.version_clock == 1  # default
        assert e.attributes == {}  # default

    def test_source_enum_accepts_p1_03_plus_command(self):
        """source enum：P1-03 的 4 值 + #141 加 command（P2-13 下行指令來源）。"""
        for src in ("manual", "pi-node", "tak", "waveink", "command"):
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
        for missing in ("uid", "type", "time", "start", "stale", "how", "lat", "lon", "source"):
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
        """heading_deg ∈ [0, 360]，360.0 normalize 為 0.0（TAK CoT 相容）"""
        CoPEntity(**_valid_entity_payload(heading_deg=0.0))
        CoPEntity(**_valid_entity_payload(heading_deg=359.9))
        # PR #16 /code-review finding 2：360.0 必須被接受（TAK 客戶端常送）
        e = CoPEntity(**_valid_entity_payload(heading_deg=360.0))
        assert e.heading_deg == 0.0, "360.0 應 normalize 為 0.0（同方向）"
        with pytest.raises(ValidationError):
            CoPEntity(**_valid_entity_payload(heading_deg=360.1))
        with pytest.raises(ValidationError):
            CoPEntity(**_valid_entity_payload(heading_deg=-1.0))

    def test_speed_mps_upper_bound(self):
        """PR #16 /code-review finding 3：speed_mps 上界 le=1000.0 防 km/h 誤代 m/s"""
        CoPEntity(**_valid_entity_payload(speed_mps=0.0))
        CoPEntity(**_valid_entity_payload(speed_mps=1000.0))
        with pytest.raises(ValidationError):
            CoPEntity(**_valid_entity_payload(speed_mps=1000.01))
        with pytest.raises(ValidationError):
            CoPEntity(**_valid_entity_payload(speed_mps=-0.01))

    def test_extra_fields_forbidden(self):
        """v1 凍結後，欄位定義以 schema 為準，未知欄位 reject（防 silent typo）"""
        with pytest.raises(ValidationError):
            CoPEntity(**_valid_entity_payload(unknown_field="x"))

    def test_attributes_is_escape_hatch_dict(self):
        e = CoPEntity(**_valid_entity_payload(attributes={"contact": {"callsign": "ALPHA-1"}, "color": "#ff0000"}))
        assert e.attributes["contact"]["callsign"] == "ALPHA-1"

    def test_planned_simulated_default_false(self):
        """P2-11b（#140）：planned/simulated 預設 False（既有/一般 entity = 實際、非合成）。"""
        e = CoPEntity(**_valid_entity_payload())
        assert e.planned is False
        assert e.simulated is False

    def test_planned_simulated_accept_bool(self):
        e = CoPEntity(**_valid_entity_payload(planned=True, simulated=True))
        assert e.planned is True
        assert e.simulated is True


class TestCoPEntityTrackSchema:
    def test_minimal_valid(self):
        t = CoPEntityTrack(uid="uid-1", t="2026-05-26T10:00:00Z", lat=25.0, lon=121.0)
        assert t.hae == 0.0  # default

    def test_required_fields(self):
        with pytest.raises(ValidationError):
            CoPEntityTrack(t="2026-05-26T10:00:00Z", lat=25.0, lon=121.0)  # uid missing


class TestCoPEntityLinkSchema:
    def test_minimal_valid(self):
        link = CoPEntityLink(
            src_uid="uid-1",
            relation="follows",
            target_uid="uid-2",
            target_type="a-f-G-E-V",
        )
        assert link.mime is None

    def test_external_resource_via_mime(self):
        link = CoPEntityLink(
            src_uid="uid-1",
            relation="evidence",
            target_uid="https://photos.example/x.jpg",
            target_type="external",
            mime="image/jpeg",
        )
        assert link.mime == "image/jpeg"


# ── 2. Migration 13 落地（DB-level） ─────────────────────────────────────────


class TestMigration013Landed:
    def test_three_new_tables_exist(self, tmp_db):
        with get_conn() as c:
            tables = {
                r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'cop_%'")
            }
        assert tables == {"cop_entities", "cop_entity_tracks", "cop_entity_links"}

    def test_events_lat_lon_dropped_by_m022(self, tmp_db):
        # m013 曾加 events.lat/lon（scenario 5 預埋），但**從未被寫/讀** → P2-27 的 m022
        # 砍除（事件位置唯一 SoT = cop_entities.lat/lon 事件圖釘）。最終 schema 應無此兩欄。
        with get_conn() as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(events)")}
        assert "lat" not in cols
        assert "lon" not in cols

    def test_migration_recorded(self, tmp_db):
        with get_conn() as c:
            row = c.execute("SELECT version, name FROM schema_migrations WHERE version=13").fetchone()
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


class TestMigration017PlannedSimulated:
    """P2-11b（#140）：cop_entities 加 planned/simulated（ADD COLUMN，bool↔INTEGER 0/1）。"""

    def test_columns_exist(self, tmp_db):
        with get_conn() as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(cop_entities)")}
        assert "planned" in cols
        assert "simulated" in cols

    def test_default_false_in_db(self, tmp_db):
        """不傳 → DB DEFAULT 0 → get 回 bool False（既有 entity 語意：實際/非合成）。"""
        insert_cop_entity(CoPEntity(**_valid_entity_payload(uid="ps-default")))
        got = get_cop_entity("ps-default")
        assert got["planned"] is False
        assert got["simulated"] is False

    def test_roundtrip_bool_not_int(self, tmp_db):
        """insert bool True → DB INTEGER 1 → get 回 **bool** True（非 int 1）。"""
        insert_cop_entity(CoPEntity(**_valid_entity_payload(uid="ps-true", planned=True, simulated=True)))
        got = get_cop_entity("ps-true")
        assert got["planned"] is True
        assert got["simulated"] is True


class TestMigration018SourceCommand:
    """#141：source CHECK 加 command（table rebuild，legacy_alter_table 避 FK cascade）。"""

    def test_command_source_accepted_at_db(self, tmp_db):
        """rebuild 後 DB CHECK 接受 command（P2-13 來源）。"""
        insert_cop_entity(CoPEntity(**_valid_entity_payload(uid="cmd-1", source="command")))
        assert get_cop_entity("cmd-1")["source"] == "command"

    def test_db_check_still_blocks_bogus_after_rebuild(self, tmp_db):
        """rebuild 後 CHECK 仍擋非法 source（DB 層防線保留，即使 Pydantic 被 bypass）。"""
        import sqlite3

        with pytest.raises(sqlite3.IntegrityError), get_conn() as c:
            c.execute(
                "INSERT INTO cop_entities (uid, type, time, start, stale, how, "
                "lat, lon, source) VALUES "
                "('x','a-f-G-U-C','t','t','t','h-e',25.0,121.0,'bogus')"
            )

    def test_rebuild_preserves_data_and_cascade(self, tmp_db):
        """★ 核心安全網：rebuild 前塞 entity+tracks+links → 跑 _m018 → 資料完整 + cascade 仍運作。

        驗 legacy_alter_table 方案正確（child FK 不被 cascade 刪光）——這是 #141 整個風險所在。
        """
        from core.database import (
            _COP_SOURCES_V1,
            _m018_cop_entities_source_command,
            _rebuild_cop_entities,
        )

        # tmp_db 已 migrate 到 18（5 值）。先 rebuild 回 V1（4 值）模擬「未加 command」起點
        with get_conn() as c:
            _rebuild_cop_entities(c, _COP_SOURCES_V1)
            c.commit()
        # 塞既有資料（2 entity + 3 tracks + 1 link）
        insert_cop_entity(CoPEntity(**_valid_entity_payload(uid="keep-1")))
        insert_cop_entity(CoPEntity(**_valid_entity_payload(uid="keep-2")))
        for i in range(3):
            insert_cop_track(CoPEntityTrack(uid="keep-1", t=f"2026-01-01T00:0{i}:00Z", lat=25.0, lon=121.0))
        insert_cop_link(CoPEntityLink(src_uid="keep-1", relation="follows", target_uid="keep-2", target_type="a-f-G"))
        # 跑 _m018 rebuild（加 command）
        with get_conn() as c:
            _m018_cop_entities_source_command(c)
            c.commit()
        # ★ rebuild 後既有資料完整（tracks/links 沒被 cascade 刪光）
        assert get_cop_entity("keep-1") is not None
        assert len(list_cop_tracks("keep-1")) == 3
        assert len(list_cop_links(src_uid="keep-1")) == 1
        # ★ cascade 經 rebuild 仍運作：刪 entity → tracks/links 隨刪
        with get_conn() as c:
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("DELETE FROM cop_entities WHERE uid='keep-1'")
            c.commit()
        assert len(list_cop_tracks("keep-1")) == 0
        assert len(list_cop_links(src_uid="keep-1")) == 0

    def test_m018_idempotent(self, tmp_db):
        """重跑 _m018（schema 已含 command）→ skip 不報錯。"""
        from core.database import _m018_cop_entities_source_command

        with get_conn() as c:
            _m018_cop_entities_source_command(c)  # 已 migrate 過，應 skip
            c.commit()
        insert_cop_entity(CoPEntity(**_valid_entity_payload(uid="idem-1", source="command")))
        assert get_cop_entity("idem-1")["source"] == "command"

    def test_rebuild_preserves_all_indexes(self, tmp_db):
        """rebuild 後所有原 index 都重建（含 m015 idx_cop_entities_team_color，review #141-1：
        手列 index 會漏掉 team_color → P2-06d GROUP BY 走 full scan）。"""
        with get_conn() as c:
            idxs = {
                r[0]
                for r in c.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='cop_entities' AND sql IS NOT NULL"
                )
            }
        assert "idx_cop_entities_team_color" in idxs  # m015 的 index 不被 rebuild 漏掉
        assert {
            "idx_cop_entities_stale",
            "idx_cop_entities_source",
            "idx_cop_entities_type",
            "idx_cop_entities_exercise",
        } <= idxs

    def test_source_constant_matches_schema_literal(self):
        """_COP_SOURCES_V2（DB CHECK 來源）與 schema CoPSource Literal 集合一致（防兩處漂移，#141-3）。"""
        from typing import get_args

        from core.database import _COP_SOURCES_V2
        from schemas.cop import CoPSource

        assert set(_COP_SOURCES_V2) == set(get_args(CoPSource))


# ── 3. Repository CRUD 來回一致 ──────────────────────────────────────────────


class TestCopEntityRepo:
    def test_insert_and_get_roundtrip(self, tmp_db):
        e = CoPEntity(**_valid_entity_payload(uid="rt-001"))
        insert_cop_entity(e)
        got = get_cop_entity("rt-001")
        assert got is not None
        assert got["uid"] == "rt-001"
        assert got["source"] == "tak"
        assert got["visible_to"] == ["all"]  # JSON decoded
        assert got["attributes"] == {}

    def test_insert_returns_persisted_row(self, tmp_db):
        """Regression for PR #16 /verify finding 1：insert_cop_entity 必須回傳
        dict（含 DB DEFAULT 填值），不是 None。

        Bug：原本在 with get_conn() 內呼叫 get_cop_entity，second connection
        看不到 uncommitted row → 回傳 None。Contract test 鎖住 return type。
        """
        e = CoPEntity(**_valid_entity_payload(uid="ret-001"))
        ret = insert_cop_entity(e)
        assert ret is not None, "insert_cop_entity 必須回傳 dict 而非 None"
        assert isinstance(ret, dict)
        assert ret["uid"] == "ret-001"
        assert ret["source"] == "tak"
        # DB DEFAULT 必須在回傳值中已填好
        assert ret["received_at"] is not None
        assert ret["received_at"].endswith("Z")
        # JSON 欄位必須 decode 後回傳（不是 raw 字串）
        assert ret["visible_to"] == ["all"]
        assert ret["attributes"] == {}

    def test_insert_with_attributes_and_visible_to(self, tmp_db):
        e = CoPEntity(
            **_valid_entity_payload(
                uid="rt-002",
                visible_to=["command", "forward"],
                attributes={"channel": "NetA", "transcript": "test"},
            )
        )
        insert_cop_entity(e)
        got = get_cop_entity("rt-002")
        assert got["visible_to"] == ["command", "forward"]
        assert got["attributes"]["channel"] == "NetA"

    def test_list_filters_stale_by_default(self, tmp_db):
        # 新鮮度治理（#161 reality check 2026-06-08，TAK 原生 honor stale + honor <archive/>；取代 WIP
        # last-heard 時窗）：外部 TAK entity —— archived=1（CoT <archive/>）→ 持久豁免 stale；
        # 無 archive → 依 stale>now 過期。預設 payload stale=遠過去（除非顯式覆寫）。
        # 'valid_future'：無 archive，stale 遠未來 → 保留。
        insert_cop_entity(CoPEntity(**_valid_entity_payload(uid="valid_future", stale="2099-01-01T00:00:00Z")))
        # 'archived_persist'：archived=1（放置標記）+ stale 過去 → 保留（archive 豁免 stale）。
        insert_cop_entity(
            CoPEntity(
                **_valid_entity_payload(
                    uid="archived_persist", how="h-g-i-g-o", archived=True, stale="2000-01-01T00:00:00Z"
                )
            )
        )
        # 'gone'：無 archive 且 stale 過去 → 移除（原生 deleteStaleAfter）。
        insert_cop_entity(CoPEntity(**_valid_entity_payload(uid="gone", how="m-g", stale="2000-01-01T00:00:00Z")))
        uids = {e["uid"] for e in list_cop_entities()}
        assert "valid_future" in uids
        assert "archived_persist" in uids  # <archive/> 持久 → 過 stale 也保留
        assert "gone" not in uids  # 無 archive + 過期 → 移除

        uids_all = {e["uid"] for e in list_cop_entities(include_stale=True)}
        assert "gone" in uids_all  # include_stale（audit/回放）仍撈得到

    def test_list_filters_by_source(self, tmp_db):
        insert_cop_entity(CoPEntity(**_valid_entity_payload(uid="t1", source="tak")))
        insert_cop_entity(CoPEntity(**_valid_entity_payload(uid="m1", source="manual")))
        tak_uids = {e["uid"] for e in list_cop_entities(source="tak")}
        assert tak_uids == {"t1"}

    def test_mark_stale(self, tmp_db):
        # 標 stale（遠過去，超出移除窗口）後預設 list 過濾。TAK parity 後 h-*/m-* 同規則，
        # 此處用 m-g 驗 mark_stale → list 過濾的不變式。
        insert_cop_entity(CoPEntity(**_valid_entity_payload(uid="s1", how="m-g")))
        assert mark_stale("s1", "2000-01-01T00:00:00Z") is True
        assert mark_stale("nonexistent", "2000-01-01T00:00:00Z") is False
        # 標 stale 後預設 list 應該過濾掉
        assert "s1" not in {e["uid"] for e in list_cop_entities()}

    def test_mark_stale_rejects_bad_iso_format(self, tmp_db):
        """PR #16 /code-review finding 5：mark_stale 必須驗證 ISO 8601 格式，
        否則錯格式（如 '2026/05/26'）會因 lexicographic compare 永遠 'live'
        """
        insert_cop_entity(CoPEntity(**_valid_entity_payload(uid="bad-fmt")))
        with pytest.raises(ValueError, match="ISO 8601"):
            mark_stale("bad-fmt", "2026/05/26 10:00:00")
        with pytest.raises(ValueError, match="ISO 8601"):
            mark_stale("bad-fmt", "not-a-date")
        # 合法格式（Z / +00:00 / 純日期 / 微秒）都應通過
        for ok in ("2026-01-01T00:00:00Z", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00", "2026-01-01"):
            assert mark_stale("bad-fmt", ok) is True

    def test_visible_to_corrupt_json_fail_closed(self, tmp_db):
        """PR #16 /code-review finding 1 [HIGH]：visible_to JSON 損毀
        必須 fail-closed (= [])，絕不可預設成 ['all'] 變 ACL fail-open。
        """
        # 先正常 insert（visible_to=['team-medical']）
        insert_cop_entity(CoPEntity(**_valid_entity_payload(uid="acl-1", visible_to=["team-medical"])))
        # 直接寫 raw 損毀 JSON 進 DB（模擬磁碟錯誤/手動誤改）
        with get_conn() as c:
            c.execute(
                "UPDATE cop_entities SET visible_to = ? WHERE uid = ?",
                ("{not valid json", "acl-1"),
            )
        got = get_cop_entity("acl-1")
        assert got["visible_to"] == [], (
            f"FAIL-OPEN REGRESSION: visible_to 損毀後應 fail-closed 成 []，"
            f"實際 {got['visible_to']!r}（絕不能是 ['all']）"
        )

    def test_list_cop_entities_stable_order_by_uid_tiebreak(self, tmp_db):
        """PR #16 /code-review finding 6：received_at DB DEFAULT 只到秒，
        burst ingest 同秒會 tie；ORDER BY 加 uid 當 stable tiebreak。
        """
        # 用同 received_at 顯式插 3 筆
        same_ts = "2026-05-26T10:00:00Z"
        for u in ("zzz", "aaa", "mmm"):
            insert_cop_entity(
                CoPEntity(
                    **_valid_entity_payload(
                        uid=u,
                        received_at=same_ts,
                    )
                )
            )
        # received_at DESC, uid DESC → uid 'zzz' > 'mmm' > 'aaa'
        uids = [e["uid"] for e in list_cop_entities()]
        assert uids == ["zzz", "mmm", "aaa"], f"Expected stable tiebreak by uid DESC，實際 {uids}"


class TestCopTrackRepo:
    def test_track_insert_and_list_ordered(self, tmp_db):
        insert_cop_entity(CoPEntity(**_valid_entity_payload(uid="moving-1")))
        for i in range(3):
            insert_cop_track(
                CoPEntityTrack(
                    uid="moving-1",
                    t=f"2026-05-26T10:0{i}:00Z",
                    lat=25.0 + i * 0.001,
                    lon=121.0 + i * 0.001,
                )
            )
        tracks = list_cop_tracks("moving-1")
        assert len(tracks) == 3
        # 時間正序
        assert tracks[0]["t"] < tracks[1]["t"] < tracks[2]["t"]


class TestCopLinkRepo:
    def test_link_insert_and_bidirectional_query(self, tmp_db):
        insert_cop_entity(CoPEntity(**_valid_entity_payload(uid="A")))
        insert_cop_entity(CoPEntity(**_valid_entity_payload(uid="B")))
        insert_cop_link(
            CoPEntityLink(
                src_uid="A",
                relation="follows",
                target_uid="B",
                target_type="a-f-G-E-V",
            )
        )
        # 查 src
        out_a = list_cop_links(src_uid="A")
        assert len(out_a) == 1
        assert out_a[0]["target_uid"] == "B"
        # 查 target（反向）
        in_b = list_cop_links(target_uid="B")
        assert len(in_b) == 1
        assert in_b[0]["src_uid"] == "A"

    def test_list_cop_links_respects_limit(self, tmp_db):
        """PR #16 /code-review finding 4：list_cop_links 必須有 limit 參數
        防 federation 累積後 unbounded query OOM。
        """
        insert_cop_entity(CoPEntity(**_valid_entity_payload(uid="hub")))
        for i in range(5):
            insert_cop_entity(CoPEntity(**_valid_entity_payload(uid=f"t{i}")))
            insert_cop_link(
                CoPEntityLink(
                    src_uid="hub",
                    relation="watch",
                    target_uid=f"t{i}",
                    target_type="a-f-G-U-C",
                )
            )
        # 預設 1000 → 全 5 筆
        assert len(list_cop_links(src_uid="hub")) == 5
        # 顯式 limit=2 → 截斷到 2 筆
        assert len(list_cop_links(src_uid="hub", limit=2)) == 2


# ── PR #16 /code-review finding 7：__init__.py 規約一致性 ───────────────────


class TestInitExports:
    """確認 P1-03 新模組已加進 __init__.py，與既有 schema/repo 規約一致。"""

    def test_schemas_init_exports_cop(self):
        from schemas import (
            CoPEntity,
        )

        assert CoPEntity is not None

    def test_repositories_init_includes_cop_entity_repo(self):
        from repositories import cop_entity_repo

        assert hasattr(cop_entity_repo, "insert_cop_entity")
        assert hasattr(cop_entity_repo, "list_cop_entities")


# ── 4. services/cop_service 4 個 normalize stub 存在且 raise NotImplementedError ──


class TestCopServiceStubs:
    def test_get_cop_summary_still_works(self, tmp_db):
        """既有 read API 在 P1-03 必須保留（無 regression）"""
        from services.cop_service import get_cop_summary

        result = get_cop_summary()
        assert set(result.keys()) == {"medical", "shelter", "forward", "security"}

    def test_remaining_normalize_stubs_raise_not_implemented(self):
        """P2-04（#105）起 normalize_cot 已落地（見 test_cop_normalize.py）；
        其餘三個來源 stub 仍未實作，待各自 source 接入時落地。"""
        from services.cop_service import (
            normalize_manual,
            normalize_pi_node,
            normalize_waveink,
        )

        with pytest.raises(NotImplementedError):
            normalize_pi_node("medical", {})
        with pytest.raises(NotImplementedError):
            normalize_waveink({})
        with pytest.raises(NotImplementedError):
            normalize_manual(None)
