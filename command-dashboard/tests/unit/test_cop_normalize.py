# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
tests/unit/test_cop_normalize.py — P2-04（#105）：normalize_cot 純函式映射

鎖住的不變式：
- CoTEventIn → CoPEntity 欄位直通（uid/type/time/start/stale/how/version/point/access/
  callsign/remarks），source 固定 'tak'，severity 預設 'info'
- detail extensions 整包進 attributes（不自創欄位）
- <track course/speed> → heading_deg/speed_mps；超界 / 非數 → None（不炸 ingest）
- 真實管線：parse_cot_xml(fixture) → normalize_cot 不丟例外
"""

from pathlib import Path

import pytest

from schemas.tak import CoTEventIn
from services.cop_service import normalize_cot
from services.tak_service import parse_cot_xml

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cot"


@pytest.fixture(autouse=True)
def _db(tmp_db):
    """normalize_cot 會呼叫 current_exercise_id() → 查 exercises 表，需 init 過的 DB。"""
    yield


def _event(**overrides) -> CoTEventIn:
    base = {
        "uid": "TEST-1",
        "type": "a-f-G-U-C",
        "time": "2026-06-05T03:50:12Z",
        "start": "2026-06-05T03:50:12Z",
        "stale": "2026-06-05T03:55:12Z",
        "how": "m-g",
        "lat": 24.137,
        "lon": 120.687,
    }
    base.update(overrides)
    return CoTEventIn(**base)


# ── 1. 核心欄位直通 + 固定值 ───────────────────────────────────────────────


def test_core_fields_passthrough_and_source_fixed():
    ent = normalize_cot(_event(access="Unclassified", callsign="ALPHA-1", remarks="集結"))
    assert ent.uid == "TEST-1"
    assert ent.type == "a-f-G-U-C"
    assert ent.time == "2026-06-05T03:50:12Z"
    assert ent.start == "2026-06-05T03:50:12Z"
    assert ent.stale == "2026-06-05T03:55:12Z"
    assert ent.how == "m-g"
    assert ent.version == "2.0"
    assert (ent.lat, ent.lon) == (24.137, 120.687)
    assert ent.access == "Unclassified"
    assert ent.callsign == "ALPHA-1"
    assert ent.remarks == "集結"
    # 固定 / 預設
    assert ent.source == "tak"
    assert ent.severity == "info"


# ── 2. detail → attributes（不自創欄位，整包收進來）─────────────────────────


def test_detail_goes_into_attributes_verbatim():
    detail = {"__group": {"name": "Cyan", "role": "Team Member"}, "status": {"battery": "78"}}
    ent = normalize_cot(_event(detail=detail))
    assert ent.attributes == detail


# ── 3. track course/speed → heading/speed；超界與非數歸 None ─────────────────


def test_track_course_speed_extracted():
    ent = normalize_cot(_event(detail={"track": {"course": "270.0", "speed": "3.5"}}))
    assert ent.heading_deg == 270.0
    assert ent.speed_mps == 3.5


def test_track_out_of_range_or_garbage_becomes_none():
    # speed 超 1000、course 非數 → None（不讓 sensor garbage 炸掉 CoPEntity 驗證）
    ent = normalize_cot(_event(detail={"track": {"course": "abc", "speed": "9999"}}))
    assert ent.heading_deg is None
    assert ent.speed_mps is None


def test_no_track_means_none_motion():
    ent = normalize_cot(_event())
    assert ent.heading_deg is None
    assert ent.speed_mps is None


# ── 4. 真實管線：parse_cot_xml(fixture) → normalize_cot 串得起來 ─────────────


def test_pipeline_from_fixture_valid_friendly():
    event = parse_cot_xml((FIXTURES / "valid_friendly.xml").read_text(encoding="utf-8"))
    ent = normalize_cot(event)
    assert ent.uid == "ANDROID-359975090666199"
    assert ent.source == "tak"
    assert ent.callsign == "ALPHA-1"  # <contact callsign=..> 由 parse 層抽出
    assert ent.remarks == "前進至集結點"
    # __group / status 等 detail children 收進 attributes
    assert "__group" in ent.attributes


def test_pipeline_from_fixture_valid_minimal():
    event = parse_cot_xml((FIXTURES / "valid_minimal.xml").read_text(encoding="utf-8"))
    ent = normalize_cot(event)
    assert ent.uid == "HAZ-001"
    assert ent.type == "b-d"
    assert ent.source == "tak"
    assert (ent.lat, ent.lon) == (24.15, 120.65)


# ── P2-06c（#126）：小隊欄位提取 team_color / role / battery ──────────────────


def test_squad_fields_extracted():
    ent = normalize_cot(
        _event(
            detail={
                "__group": {"name": "Cyan", "role": "Team Member"},
                "status": {"battery": "78"},
            }
        )
    )
    assert ent.team_color == "Cyan"
    assert ent.role == "Team Member"
    assert ent.battery == 78
    assert ent.attributes["__group"]["name"] == "Cyan"  # 原始巢狀仍保留（CoT 忠實）


def test_squad_missing_yields_none():
    ent = normalize_cot(_event())  # 無 detail
    assert (ent.team_color, ent.role, ent.battery) == (None, None, None)


def test_team_color_normalized_preserves_multiword():
    assert normalize_cot(_event(detail={"__group": {"name": "cyan"}})).team_color == "Cyan"
    # 多字色名用 title 保留（非 capitalize 會變 'Dark blue'）
    assert normalize_cot(_event(detail={"__group": {"name": "dark blue"}})).team_color == "Dark Blue"


def test_battery_garbage_or_out_of_range_yields_none():
    assert normalize_cot(_event(detail={"status": {"battery": "??"}})).battery is None
    assert normalize_cot(_event(detail={"status": {"battery": "150"}})).battery is None  # 越界
    assert normalize_cot(_event(detail={"status": {"battery": ""}})).battery is None
    assert normalize_cot(_event(detail={"status": {"battery": "inf"}})).battery is None  # OverflowError 攔（#126-1）


def test_squad_list_takes_first_dict():
    """同 tag 多筆（_extract_detail 收成 list）→ 取首個 dict，非靜默全丟（#126-4）。"""
    ent = normalize_cot(_event(detail={"__group": [{"name": "Cyan", "role": "Lead"}, {"name": "Red"}]}))
    assert (ent.team_color, ent.role) == ("Cyan", "Lead")


def test_real_fixture_squad_extracted():
    event = parse_cot_xml((FIXTURES / "valid_friendly.xml").read_text(encoding="utf-8"))
    ent = normalize_cot(event)
    assert (ent.team_color, ent.role, ent.battery) == ("Cyan", "Team Member", 78)


# ── P2-09（#135）：MEDEVAC 9-line 正規化 + severity=critical ────────────────────


def test_medevac_extracted_and_severity_critical():
    """<_medevac_> 屬性（混大小寫）→ attributes["medevac"] 乾淨摘要 + severity critical。"""
    ent = normalize_cot(
        _event(
            type="b-a-o-tbl-medevac",
            detail={
                "_medevac_": {
                    "Title": "觸雷",
                    "freq": "38.90",
                    "urgent": "2",
                    "Priority": "1",
                    "routine": "0",
                    "casevac": "false",
                    "Security": "N",
                    "hlz_marking": "Smoke - Green",
                },
            },
        )
    )
    mv = ent.attributes["medevac"]
    assert mv["title"] == "觸雷"
    assert mv["freq"] == "38.90"
    assert mv["precedence"] == {"urgent": 2, "priority": 1, "routine": 0}  # by precedence 傷亡數→int
    assert mv["casevac"] is False
    assert mv["security"] == "N"  # 大寫 Security 也取到（case-insensitive）
    assert mv["marking"] == "Smoke - Green"
    assert ent.severity == "critical"


def test_medevac_casevac_bool_variants():
    """casevac → bool；缺欄位 → None（未知，非 False）。"""
    assert normalize_cot(_event(detail={"_medevac_": {"casevac": "true"}})).attributes["medevac"]["casevac"] is True
    assert normalize_cot(_event(detail={"_medevac_": {"casevac": "false"}})).attributes["medevac"]["casevac"] is False
    assert normalize_cot(_event(detail={"_medevac_": {"freq": "30"}})).attributes["medevac"]["casevac"] is None


def test_non_medevac_severity_info():
    """無 <_medevac_> → severity 維持 info，attributes 無 medevac 鍵（不誤升 critical）。"""
    ent = normalize_cot(_event())
    assert ent.severity == "info"
    assert "medevac" not in ent.attributes


def test_medevac_raw_preserved():
    """原始 _medevac_ 屬性完整保留在 attributes（CoT 忠實，真機屬性名小差時不漏資料）。"""
    ent = normalize_cot(_event(detail={"_medevac_": {"Title": "X", "urgent": "1", "foo_unknown": "bar"}}))
    assert ent.attributes["_medevac_"]["Title"] == "X"
    assert ent.attributes["_medevac_"]["foo_unknown"] == "bar"  # 未知屬性也留著


def test_medevac_garbage_precedence_yields_none():
    """precedence 非數 / 空 / 越界（>9999）→ None（防 garbage，不炸 ingest）。"""
    mv = normalize_cot(
        _event(
            detail={
                "_medevac_": {"urgent": "??", "priority": "", "routine": "99999"},
            }
        )
    ).attributes["medevac"]
    assert mv["precedence"] == {"urgent": None, "priority": None, "routine": None}


def test_real_fixture_medevac_extracted():
    event = parse_cot_xml((FIXTURES / "medevac_9line.xml").read_text(encoding="utf-8"))
    ent = normalize_cot(event)
    assert ent.severity == "critical"
    mv = ent.attributes["medevac"]
    assert mv["title"] == "集結點北側觸雷"
    assert mv["freq"] == "38.90"
    assert mv["precedence"] == {"urgent": 2, "priority": 1, "routine": 0}
    assert mv["casevac"] is False
    assert mv["marking"] == "Smoke - Green"
    # 原始 _medevac_ 仍保留（含未提取進摘要的 medline_remarks）
    assert ent.attributes["_medevac_"]["medline_remarks"] == "2 lower-limb amputations"


# ── 4. #260 Slice A：route/繪圖 <point>=0,0 佔位 → 取首頂點當代表座標 ──────────────


def test_route_zero_point_uses_first_vertex_issue260():
    """#260 Slice A：route 的 <point>=0,0（錨點在 waypoints、非單點）→ 主座標退化 (0,0)。
    無效 point + 有頂點 → 取首頂點（route 起點）當代表座標，非留 0,0。"""
    geom = {"type": "LineString", "coordinates": [[121.0353, 24.8377], [121.0382, 24.8351]]}  # [lon,lat]
    ent = normalize_cot(_event(uid="R1", type="b-m-r", lat=0.0, lon=0.0, geometry=geom))
    assert (round(ent.lat, 4), round(ent.lon, 4)) == (24.8377, 121.0353)  # 首頂點 [lat,lon]，非 0,0
    assert ent.attributes.get("kind") == "route"
    assert ent.attributes.get("vertices")  # 頂點保留供渲染


def test_valid_point_not_overwritten_by_vertex_issue260():
    """有效 <point>（非 0,0）+ geometry → lat/lon 保留原 point，不被首頂點覆寫（守 review #132）。"""
    geom = {"type": "LineString", "coordinates": [[121.0353, 24.8377], [121.0382, 24.8351]]}
    ent = normalize_cot(_event(uid="R2", type="b-m-r", lat=24.5, lon=120.5, geometry=geom))
    assert (ent.lat, ent.lon) == (24.5, 120.5)  # 保留原 point
