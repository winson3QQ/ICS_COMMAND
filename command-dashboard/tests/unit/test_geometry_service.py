# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
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
    el = _detail(
        '<detail><shape><polyline closed="false">'
        '<vertex point="24.1,120.6"/><vertex point="24.2,120.7"/>'
        "</polyline></shape></detail>"
    )
    assert geometry_service.extract_geometry(el) == {
        "type": "LineString",
        "coordinates": [[120.6, 24.1], [120.7, 24.2]],
    }


def test_polyline_closed_to_polygon():
    el = _detail(
        '<detail><shape><polyline closed="true">'
        '<vertex point="24.1,120.6"/><vertex point="24.2,120.6"/><vertex point="24.2,120.7"/>'
        "</polyline></shape></detail>"
    )
    geom = geometry_service.extract_geometry(el)
    assert geom["type"] == "Polygon"
    ring = geom["coordinates"][0]
    assert ring[0] == ring[-1] == [120.6, 24.1]  # 閉合環首尾相同


def test_link_route_to_linestring():
    el = _detail(
        "<detail>"
        '<link point="24.1,120.6,0"/><link point="24.2,120.7,0"/>'
        '<link type="b-m-p-w"/>'  # 非幾何 link（無 point）→ 略過
        "</detail>"
    )
    assert geometry_service.extract_geometry(el) == {
        "type": "LineString",
        "coordinates": [[120.6, 24.1], [120.7, 24.2]],
    }


def test_link_closed_ring_to_polygon():
    # #159：iTAK 封閉繪圖（area）以首尾相同的 <link> 序列送 → 應判 Polygon（非 route）。
    el = _detail(
        "<detail>"
        '<link point="24.1,120.6,0"/><link point="24.2,120.7,0"/>'
        '<link point="24.0,120.8,0"/><link point="24.1,120.6,0"/>'  # 末點==首點 → 封閉
        "</detail>"
    )
    geom = geometry_service.extract_geometry(el)
    assert geom["type"] == "Polygon"
    assert geom["coordinates"][0][0] == geom["coordinates"][0][-1]  # 環封閉


def test_link_open_three_points_stays_linestring():
    # 首尾不同 → 仍是開放 route（不誤判 polygon）。
    el = _detail(
        '<detail><link point="24.1,120.6,0"/><link point="24.2,120.7,0"/><link point="24.0,120.8,0"/></detail>'
    )
    assert geometry_service.extract_geometry(el)["type"] == "LineString"


def test_link_rectangle_with_fillcolor_to_polygon():
    # #4（P2-10 真機）：iTAK u-d-r 矩形送 4 角 <link>（首≠尾，不重複首點）+ <fillColor>
    # = 填色面 → 應判 Polygon（補閉合點），非 route。route（線）無 fillColor。
    el = _detail(
        "<detail>"
        '<link point="24.825,121.019"/><link point="24.824,121.022"/>'
        '<link point="24.822,121.020"/><link point="24.823,121.018"/>'
        '<strokeColor value="0"/><fillColor value="2130706432"/>'
        "</detail>"
    )
    geom = geometry_service.extract_geometry(el)
    assert geom["type"] == "Polygon"
    ring = geom["coordinates"][0]
    assert ring[0] == ring[-1]  # 補了閉合點成環
    assert len(ring) == 5  # 4 角 + 閉合


def test_link_fillcolor_but_open_route_no_fill():
    # 無 fillColor 的開放 4 點 link（route）→ 不誤判 polygon（首尾不同 + 無填色）。
    el = _detail(
        "<detail>"
        '<link point="24.1,120.6"/><link point="24.2,120.7"/>'
        '<link point="24.3,120.8"/><link point="24.4,120.9"/>'
        "</detail>"
    )
    assert geometry_service.extract_geometry(el)["type"] == "LineString"


def test_ellipse_without_center_returns_none():
    # 無 center（圓心）→ 無法定位 → None（extract_geometry 預設 center=None）。
    el = _detail('<detail><shape><ellipse major="100" minor="50" angle="0"/></shape></detail>')
    assert geometry_service.extract_geometry(el) is None


def test_circle_with_center_to_polygon():
    # #134/真機：<ellipse> + center（event <point>）→ N-gon Polygon 逼近。
    el = _detail('<detail><shape><ellipse major="100" minor="100" angle="0"/></shape></detail>')
    geom = geometry_service.extract_geometry(el, center=(24.0, 121.0))
    assert geom["type"] == "Polygon"
    ring = geom["coordinates"][0]
    assert ring[0] == ring[-1]  # 閉合
    assert len(ring) == 49  # 48 段 + 閉合點
    # 每個頂點離圓心約 100m（平面近似）：經度 100m≈0.000983°、緯度 100m≈0.000898°，取最大位移檢核
    import math as _m

    for lon, lat in ring:
        dlat_m = (lat - 24.0) * 111320.0
        dlon_m = (lon - 121.0) * 111320.0 * _m.cos(_m.radians(24.0))
        r = _m.hypot(dlat_m, dlon_m)
        assert 95.0 <= r <= 105.0  # ≈100m 半徑（±5% 容差）


def test_ellipse_rotated_axes_differ():
    # 橢圓（major≠minor）→ Polygon；半長軸方向位移 > 半短軸方向（粗略檢核形狀非圓）。
    geom = geometry_service.extract_geometry(
        _detail('<detail><shape><ellipse major="200" minor="50" angle="0"/></shape></detail>'),
        center=(24.0, 121.0),
    )
    assert geom["type"] == "Polygon"
    ring = geom["coordinates"][0]
    import math as _m

    lons = [abs(lon - 121.0) * 111320.0 * _m.cos(_m.radians(24.0)) for lon, _ in ring]
    lats = [abs(lat - 24.0) * 111320.0 for _, lat in ring]
    assert max(lons) > 150.0  # 半長軸 ~200m（東向，angle=0）
    assert max(lats) < 60.0  # 半短軸 ~50m（北向）


def test_no_geometry_returns_none():
    assert geometry_service.extract_geometry(_detail('<detail><contact callsign="X"/></detail>')) is None
    assert geometry_service.extract_geometry(None) is None


# ── geojson_to_vertices / centroid ───────────────────────────────────────────


def test_linestring_to_vertices():
    v = geometry_service.geojson_to_vertices({"type": "LineString", "coordinates": [[120.6, 24.1], [120.7, 24.2]]})
    assert v == [[24.1, 120.6], [24.2, 120.7]]  # lon-lat → lat-lng


def test_polygon_to_vertices_drops_closing():
    ring = [[120.6, 24.1], [120.7, 24.1], [120.7, 24.2], [120.6, 24.1]]
    v = geometry_service.geojson_to_vertices({"type": "Polygon", "coordinates": [ring]})
    assert v == [[24.1, 120.6], [24.1, 120.7], [24.2, 120.7]]  # 去閉合末點


def test_garbage_coordinates_skipped():
    """不可信 REST geometry 的非法/超界座標跳過（不 raise，避免 ingest 5xx，review #132）。
    注意 GeoJSON 3D [lon,lat,alt] 合法（取前兩個）。"""
    # 非數值 / 超界 / arity<2 / 非 list → 全跳過
    assert (
        geometry_service.geojson_to_vertices(
            {"type": "LineString", "coordinates": [["a", "b"], [999, 999], [5], "notlist"]}
        )
        == []
    )
    # 3D [lon,lat,alt] 合法保留，garbage 去
    assert geometry_service.geojson_to_vertices(
        {"type": "LineString", "coordinates": [[120.6, 24.1, 100], [999, 999], [120.7, 24.2]]}
    ) == [[24.1, 120.6], [24.2, 120.7]]


def test_polygon_degenerate_falls_back_linestring():
    """2 頂點 closed 是退化環 → 退 LineString（review #132）。"""
    el = _detail(
        '<detail><shape><polyline closed="true">'
        '<vertex point="24.1,120.6"/><vertex point="24.2,120.7"/></polyline></shape></detail>'
    )
    assert geometry_service.extract_geometry(el)["type"] == "LineString"


def test_shape_priority_over_link():
    """shape 與 link 並存 → shape 優先（鎖優先序，review #132）。"""
    el = _detail(
        "<detail>"
        '<shape><polyline closed="false"><vertex point="1,1"/><vertex point="2,2"/></polyline></shape>'
        '<link point="9,9"/><link point="8,8"/></detail>'
    )
    assert geometry_service.extract_geometry(el)["coordinates"] == [[1.0, 1.0], [2.0, 2.0]]


def test_vertex_lat_lon_attributes():
    """vertex 用 lat/lon 屬性（非 point=）也能解（_latlon fallback）。"""
    el = _detail(
        '<detail><shape><polyline closed="false">'
        '<vertex lat="24.1" lon="120.6"/><vertex lat="24.2" lon="120.7"/></polyline></shape></detail>'
    )
    assert geometry_service.extract_geometry(el) == {
        "type": "LineString",
        "coordinates": [[120.6, 24.1], [120.7, 24.2]],
    }


# ── normalize_cot 整合 ───────────────────────────────────────────────────────


def test_normalize_polyline_sets_route_kind_keeps_point():
    event = parse_cot_xml((FIXTURES / "shape_polyline.xml").read_text(encoding="utf-8"))
    assert event.geometry["type"] == "LineString"
    ent = normalize_cot(event)
    assert ent.attributes["kind"] == "route"
    assert ent.attributes["vertices"] == [[24.10, 120.60], [24.20, 120.70], [24.30, 120.80]]
    assert (ent.lat, ent.lon) == (24.10, 120.60)  # 沿用 CoT <point>，非 centroid（review #132）


def test_normalize_polygon_sets_polygon_kind():
    event = parse_cot_xml((FIXTURES / "shape_polygon.xml").read_text(encoding="utf-8"))
    assert event.geometry["type"] == "Polygon"
    ent = normalize_cot(event)
    assert ent.attributes["kind"] == "polygon"
    assert len(ent.attributes["vertices"]) == 4  # 4 頂點（去閉合末點）


def test_normalize_no_geometry_unchanged():
    event = parse_cot_xml((FIXTURES / "valid_friendly.xml").read_text(encoding="utf-8"))
    ent = normalize_cot(event)
    assert "kind" not in ent.attributes  # 無 shape → 不設 kind
    assert ent.lat == 24.137  # 原 point 不變


# ── P2-10 #4/#5：iTAK u-d-r 矩形（fillColor → polygon）+ CoT 顏色解析 ──────────


def test_normalize_rectangle_to_polygon_with_color():
    """真機 #4/#5：u-d-r 矩形 4 角 link（首≠尾）+ <fillColor> → kind=polygon；
    <strokeColor> 紅（-65536=0xFFFF0000）→ attributes.color='#ff0000'（非退灰）。"""
    xml = (
        '<event version="2.0" uid="RECT-1" type="u-d-r" how="h-e" '
        'time="2026-06-07T11:28:36Z" start="2026-06-07T11:28:36Z" stale="2026-06-07T11:30:26Z">'
        '<point lat="24.824" lon="121.020" hae="0" ce="0" le="0"/>'
        '<detail><contact callsign="RectangleShape.2"/>'
        '<link point="24.825,121.019"/><link point="24.824,121.022"/>'
        '<link point="24.822,121.020"/><link point="24.823,121.018"/>'
        '<strokeColor value="-65536"/><fillColor value="2130706432"/>'
        "</detail></event>"
    )
    ent = normalize_cot(parse_cot_xml(xml))
    assert ent.attributes["kind"] == "polygon"
    assert len(ent.attributes["vertices"]) == 4  # 4 角（geojson_to_vertices 去閉合末點）
    assert ent.attributes["color"] == "#ff0000"  # strokeColor 優先（外框＝使用者選色）


def test_argb_int_to_hex_cases():
    from services.cop_service import _argb_int_to_hex

    assert _argb_int_to_hex("-1") == "#ffffff"  # 0xFFFFFFFF 白
    assert _argb_int_to_hex("-65536") == "#ff0000"  # 0xFFFF0000 紅
    assert _argb_int_to_hex("2130706432") == "#000000"  # 0x7F000000 半透明黑 → 取 RGB
    assert _argb_int_to_hex("garbage") is None
    assert _argb_int_to_hex(None) is None


def test_extract_color_priority_stroke_over_fill():
    from services.cop_service import _extract_color

    # strokeColor（外框）優先於 fillColor（填色）
    assert _extract_color({"strokeColor": {"value": "-65536"}, "fillColor": {"value": "-16776961"}}) == "#ff0000"
    # 無 strokeColor → 退 marker <color argb>
    assert _extract_color({"color": {"argb": "-16711936"}}) == "#00ff00"  # 0xFF00FF00 綠
    # 皆無 → None（前端退預設）
    assert _extract_color({"contact": {"callsign": "X"}}) is None


def test_normalize_circle_to_polygon_with_color():
    """真機圓形 u-d-c-c：<shape><ellipse> + event <point>（圓心）→ kind=polygon（N-gon）；
    strokeColor 解析成 color。對齊抓到的封包結構。"""
    xml = (
        '<event version="2.0" uid="CIRC-1" type="u-d-c-c" how="h-e" '
        'time="2026-06-07T11:36:12Z" start="2026-06-07T11:36:12Z" stale="2026-06-07T11:37:57Z">'
        '<point lat="24.8247" lon="121.0165" hae="0" ce="0" le="0"/>'
        '<detail><contact callsign="Circle.2"/>'
        '<strokeColor value="-48571"/><fillColor value="2147435077"/><strokeStyle value="solid"/>'
        '<shape><ellipse minor="166.97" angle="360.0" major="166.97"/></shape>'
        "</detail></event>"
    )
    ent = normalize_cot(parse_cot_xml(xml))
    assert ent.attributes["kind"] == "polygon"
    assert len(ent.attributes["vertices"]) == 48  # N-gon（去閉合末點）
    assert ent.attributes["color"] == _argb_hex("-48571")
    assert "dash" not in ent.attributes  # solid → 不設 dash


def test_normalize_dashed_stroke_sets_dash():
    """<strokeStyle value="dashed"> → attributes.dash=True（前端虛線渲染）。"""
    xml = (
        '<event version="2.0" uid="RECT-DASH" type="u-d-r" how="h-e" '
        'time="2026-06-07T11:41:01Z" start="2026-06-07T11:41:01Z" stale="2026-06-07T11:42:09Z">'
        '<point lat="24.827" lon="121.028" hae="0" ce="0" le="0"/>'
        "<detail>"
        '<link point="24.831,121.026"/><link point="24.830,121.031"/>'
        '<link point="24.823,121.030"/><link point="24.824,121.024"/>'
        '<strokeColor value="-16739841"/><fillColor value="2130743807"/><strokeStyle value="dashed"/>'
        "</detail></event>"
    )
    ent = normalize_cot(parse_cot_xml(xml))
    assert ent.attributes["kind"] == "polygon"
    assert ent.attributes["dash"] is True


def test_normalize_dotted_stroke_sets_dotted():
    """<strokeStyle value="dotted"> → attributes.dotted=True（小圓點，與 dash 互斥）。"""
    xml = (
        '<event version="2.0" uid="POLY-DOT" type="u-d-f" how="h-e" '
        'time="2026-06-07T11:54:28Z" start="2026-06-07T11:54:28Z" stale="2026-06-07T11:56:03Z">'
        '<point lat="24.808" lon="121.049" hae="0" ce="0" le="0"/>'
        "<detail>"
        '<link point="24.8079,121.0433"/><link point="24.8100,121.0502"/>'
        '<link point="24.8155,121.0517"/><link point="24.8079,121.0433"/>'
        '<strokeColor value="-13577896"/><fillColor value="2133905752"/><strokeStyle value="dotted"/>'
        "</detail></event>"
    )
    ent = normalize_cot(parse_cot_xml(xml))
    assert ent.attributes["kind"] == "polygon"
    assert ent.attributes["dotted"] is True
    assert "dash" not in ent.attributes  # dotted 與 dash 互斥


def _argb_hex(v):
    from services.cop_service import _argb_int_to_hex

    return _argb_int_to_hex(v)
