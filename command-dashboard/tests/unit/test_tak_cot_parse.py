"""P2-02（#102）— tak_service.parse_cot_xml CoT 解析 unit 測試。

純解析，不碰 DB / app。fixtures 在 tests/fixtures/cot/。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services.tak_service import CoTParseError, parse_cot_xml

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cot"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parse_friendly_full_fields():
    e = parse_cot_xml(_load("valid_friendly.xml"))
    assert e.uid == "ANDROID-359975090666199"
    assert e.type == "a-f-G-U-C"
    assert e.how == "m-g"
    assert e.version == "2.0"
    assert e.access == "Unclassified"
    assert (e.lat, e.lon) == (24.137, 120.687)
    assert e.hae == 32.5 and e.ce == 9.0 and e.le == 9.0


def test_detail_callsign_remarks_extracted():
    e = parse_cot_xml(_load("valid_friendly.xml"))
    assert e.callsign == "ALPHA-1"
    assert e.remarks == "前進至集結點"


def test_detail_dict_structured_for_p2_04():
    e = parse_cot_xml(_load("valid_friendly.xml"))
    # direct children of <detail> 結構化，交 P2-04 落 attributes
    assert e.detail["contact"]["callsign"] == "ALPHA-1"
    assert e.detail["__group"]["name"] == "Cyan"
    assert e.detail["status"]["battery"] == "78"
    assert e.detail["remarks"]["_text"] == "前進至集結點"


def test_millis_stripped_to_second_precision_z():
    # CoT 送毫秒 + Z；normalize 成秒精度 Z（對齊 repo stale 字典序比較格式）
    e = parse_cot_xml(_load("valid_friendly.xml"))
    assert e.time == "2026-06-05T03:50:12Z"
    assert e.stale == "2026-06-05T03:55:12Z"


def test_offset_tz_converted_to_utc():
    # +08:00 → UTC Z
    e = parse_cot_xml(_load("offset_tz.xml"))
    assert e.time == "2026-06-05T03:50:00Z"
    assert e.stale == "2026-06-05T04:00:00Z"


def test_minimal_uses_cot_unknown_defaults():
    e = parse_cot_xml(_load("valid_minimal.xml"))
    assert e.hae == 0.0
    assert e.ce == 9999999.0 and e.le == 9999999.0
    assert e.callsign is None and e.remarks is None and e.detail == {}


def test_parsed_event_carries_copentity_required_fields():
    # contract gap 修復驗證：CoPEntity 必填 how/start，CoTEventIn 須帶得出來
    e = parse_cot_xml(_load("valid_minimal.xml"))
    assert e.how and e.start  # 非空


def test_bytes_input_accepted():
    e = parse_cot_xml(_load("valid_minimal.xml").encode("utf-8"))
    assert e.uid == "HAZ-001"


def test_callsign_only_from_direct_child_not_nested():
    # callsign 只認 <detail> 直接子元素，不撈巢狀擴充元素裡的 callsign 屬性
    xml = (
        '<event version="2.0" uid="N-1" type="a-f-G" time="2026-06-05T04:00:00Z" '
        'start="2026-06-05T04:00:00Z" stale="2026-06-05T04:10:00Z" how="m-g">'
        '<point lat="24.1" lon="120.6"/>'
        "<detail>"
        '<wrapper><contact callsign="NESTED-WRONG"/></wrapper>'
        '<contact callsign="TOP-RIGHT"/>'
        "</detail></event>"
    )
    e = parse_cot_xml(xml)
    assert e.callsign == "TOP-RIGHT"


@pytest.mark.parametrize("name", ["missing_how.xml", "missing_point.xml", "bad_coords.xml", "not_event.xml"])
def test_invalid_inputs_raise(name):
    with pytest.raises(CoTParseError):
        parse_cot_xml(_load(name))


@pytest.mark.parametrize("raw", ["", "   ", "<not-xml", b""])
def test_empty_or_malformed_raise(raw):
    with pytest.raises(CoTParseError):
        parse_cot_xml(raw)


# ── #161：CoT <archive/> 持久標記偵測（list 豁免 stale 的依據）─────────────────

_ARCH_XML = (
    '<event version="2.0" uid="A" type="a-h-G" time="2026-06-05T04:00:00Z" '
    'start="2026-06-05T04:00:00Z" stale="2026-06-05T04:05:00Z" how="h-g-i-g-o">'
    '<point lat="24.0" lon="120.0" hae="0" ce="9" le="9"/>'
    '<detail><contact callsign="M1"/>{arch}</detail></event>'
)


def test_archive_flag_detected():
    assert parse_cot_xml(_ARCH_XML.format(arch="<archive/>")).archived is True


def test_archive_absent_defaults_false():
    assert parse_cot_xml(_ARCH_XML.format(arch="")).archived is False


@pytest.mark.parametrize("body", ["false", "0", "False", " FALSE "])
def test_archive_explicit_false_not_archived(body):
    # 容錯非標準 <archive>false</archive>（post-merge review）：不誤判為 archived
    assert parse_cot_xml(_ARCH_XML.format(arch=f"<archive>{body}</archive>")).archived is False


# ── P2-10 內容層白名單（type / callsign / 座標越界）—— ingest 端最後防線 ──────

from pydantic import ValidationError  # noqa: E402

from schemas.tak import CoTEventIn  # noqa: E402

_BASE = dict(
    uid="T-1",
    type="a-f-G-U-C",
    time="2026-06-05T04:00:00Z",
    start="2026-06-05T04:00:00Z",
    stale="2026-06-05T04:05:00Z",
    how="m-g",
    lat=24.0,
    lon=120.0,
)


def test_valid_type_and_callsign_pass():
    e = CoTEventIn(**{**_BASE, "type": "b-a-o-tbl-medevac", "callsign": "ALPHA-1"})
    assert e.type == "b-a-o-tbl-medevac" and e.callsign == "ALPHA-1"


@pytest.mark.parametrize("bad", ["<script>", "a f G", "a;drop", "x", "9-a-b", "A-f-G", ""])
def test_bad_type_rejected(bad):
    with pytest.raises(ValidationError):
        CoTEventIn(**{**_BASE, "type": bad})


def test_callsign_apostrophe_allowed():
    # 紅隊 RT-M3 陷阱 1：O'Brien 等含 ' 的合法呼號不可誤殺（內容層別過濾引號）
    assert CoTEventIn(**{**_BASE, "callsign": "O'Brien-1"}).callsign == "O'Brien-1"


@pytest.mark.parametrize("bad", ["<script>", 'a"b', "a&b", "a`b", "a\x00b", "a;b", "a<b>c"])
def test_bad_callsign_rejected(bad):
    with pytest.raises(ValidationError):
        CoTEventIn(**{**_BASE, "callsign": bad})


@pytest.mark.parametrize("lat,lon", [(91.0, 0.0), (-91.0, 0.0), (0.0, 181.0), (0.0, -181.0)])
def test_out_of_bounds_coord_rejected(lat, lon):
    with pytest.raises(ValidationError):
        CoTEventIn(**{**_BASE, "lat": lat, "lon": lon})


def test_invalid_type_via_stream_becomes_parse_error():
    # :8089 路徑：parse_cot_xml 把 ValidationError 收斂成 CoTParseError（不中斷串流）。
    bad = (
        '<event version="2.0" uid="T" type="a f G" time="2026-06-05T04:00:00Z" '
        'start="2026-06-05T04:00:00Z" stale="2026-06-05T04:05:00Z" how="m-g">'
        '<point lat="24.0" lon="120.0" hae="0" ce="9" le="9"/><detail/></event>'
    )
    with pytest.raises(CoTParseError):
        parse_cot_xml(bad)
