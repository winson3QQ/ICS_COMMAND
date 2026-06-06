"""
tests/unit/test_geometry_service.py — P2-08（#132）：CoT shape 幾何萃取

鎖住的不變式：
- <shape><polyline closed=false> → GeoJSON LineString；closed=true → Polygon
- 多筆 <link point=..> → LineString（route）；非幾何 link 略過
- circle/ellipse → None（前端無管線，已知限制）
- GeoJSON → 前端 vertices [[lat,lng]]（Polygon 去閉合末點）
- normalize_cot：geometry → attributes.kind/vertices + lat/lon 重算 centroid
"""

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from services import geometry_service
from services.cop_service import normalize_cot
from services.tak_service import parse_cot_xml

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cot"


@pytest.fixture(autouse=True)
def _db(tmp_db):
    """normalize_cot 呼叫 current_exercise_id() 查 DB。"""
    yield


def _detail(xml: str):
    return ET.fromstring(xml)


# ── extract_geometry ─────────────────────────────────────────────────────────


def test_polyline_open_to_linestring():
    el = _detail('<detail><shape><polyline closed="false">'
                 '<vertex point="24.1,120.6"/><vertex point="24.2,120.7"/>'
                 '</polyline></shape></detail>')
    assert geometry_service.extract_geometry(el) == {
        "type": "LineString", "coordinates": [[120.6, 24.1], [120.7, 24.2]],
    }


def test_polyline_closed_to_polygon():
    el = _detail('<detail><shape><polyline closed="true">'
                 '<vertex point="24.1,120.6"/><vertex point="24.2,120.6"/><vertex point="24.2,120.7"/>'
                 '</polyline></shape></detail>')
    geom = geometry_service.extract_geometry(el)
    assert geom["type"] == "Polygon"
    ring = geom["coordinates"][0]
    assert ring[0] == ring[-1] == [120.6, 24.1]   # 閉合環首尾相同


def test_link_route_to_linestring():
    el = _detail('<detail>'
                 '<link point="24.1,120.6,0"/><link point="24.2,120.7,0"/>'
                 '<link type="b-m-p-w"/>'          # 非幾何 link（無 point）→ 略過
                 '</detail>')
    assert geometry_service.extract_geometry(el) == {
        "type": "LineString", "coordinates": [[120.6, 24.1], [120.7, 24.2]],
    }


def test_circle_unsupported_returns_none():
    el = _detail('<detail><shape><ellipse major="100" minor="50" angle="0"/></shape></detail>')
    assert geometry_service.extract_geometry(el) is None


def test_no_geometry_returns_none():
    assert geometry_service.extract_geometry(_detail('<detail><contact callsign="X"/></detail>')) is None
    assert geometry_service.extract_geometry(None) is None


# ── geojson_to_vertices / centroid ───────────────────────────────────────────


def test_linestring_to_vertices():
    v = geometry_service.geojson_to_vertices({"type": "LineString", "coordinates": [[120.6, 24.1], [120.7, 24.2]]})
    assert v == [[24.1, 120.6], [24.2, 120.7]]   # lon-lat → lat-lng


def test_polygon_to_vertices_drops_closing():
    ring = [[120.6, 24.1], [120.7, 24.1], [120.7, 24.2], [120.6, 24.1]]
    v = geometry_service.geojson_to_vertices({"type": "Polygon", "coordinates": [ring]})
    assert v == [[24.1, 120.6], [24.1, 120.7], [24.2, 120.7]]   # 去閉合末點


def test_centroid():
    assert geometry_service.centroid([[0.0, 0.0], [2.0, 4.0]]) == (1.0, 2.0)
    assert geometry_service.centroid([]) is None


# ── normalize_cot 整合 ───────────────────────────────────────────────────────


def test_normalize_polyline_sets_route_kind_and_centroid():
    event = parse_cot_xml((FIXTURES / "shape_polyline.xml").read_text(encoding="utf-8"))
    assert event.geometry["type"] == "LineString"
    ent = normalize_cot(event)
    assert ent.attributes["kind"] == "route"
    assert ent.attributes["vertices"] == [[24.10, 120.60], [24.20, 120.70], [24.30, 120.80]]
    assert ent.lat == pytest.approx(24.20)   # centroid，非原 point 24.10
    assert ent.lon == pytest.approx(120.70)


def test_normalize_polygon_sets_polygon_kind():
    event = parse_cot_xml((FIXTURES / "shape_polygon.xml").read_text(encoding="utf-8"))
    assert event.geometry["type"] == "Polygon"
    ent = normalize_cot(event)
    assert ent.attributes["kind"] == "polygon"
    assert len(ent.attributes["vertices"]) == 4   # 4 頂點（去閉合末點）


def test_normalize_no_geometry_unchanged():
    event = parse_cot_xml((FIXTURES / "valid_friendly.xml").read_text(encoding="utf-8"))
    ent = normalize_cot(event)
    assert "kind" not in ent.attributes          # 無 shape → 不設 kind
    assert ent.lat == 24.137                      # 原 point 不變
