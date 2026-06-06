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
    ent = normalize_cot(_event(detail={
        "__group": {"name": "Cyan", "role": "Team Member"},
        "status": {"battery": "78"},
    }))
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


def test_real_fixture_squad_extracted():
    event = parse_cot_xml((FIXTURES / "valid_friendly.xml").read_text(encoding="utf-8"))
    ent = normalize_cot(event)
    assert (ent.team_color, ent.role, ent.battery) == ("Cyan", "Team Member", 78)
