"""P2-30 / #180 — 線/區出向幾何序列化（vertices → CoT shape）+ build_geometry_cot。

核心：**round-trip** —— `vertices_to_cot_shape` 產的 XML 餵回 P2-08 入向 `extract_geometry`
+ `geojson_to_vertices`，必須還原同型同點（出向對稱入向）。純函式，不碰網路/DB。
"""

from __future__ import annotations

from datetime import UTC, datetime
from xml.etree import ElementTree as ET

import pytest

from services.geometry_service import extract_geometry, geojson_to_vertices, vertices_to_cot_shape
from services.tak_downlink import build_geometry_cot

_NOW = datetime(2026, 6, 9, 5, 0, 0, tzinfo=UTC)


def _roundtrip(vertices, *, closed):
    shape_xml = vertices_to_cot_shape(vertices, closed=closed)
    detail = ET.fromstring(f"<detail>{shape_xml}</detail>")
    geom = extract_geometry(detail)
    return geom, geojson_to_vertices(geom)


def test_roundtrip_polygon_closed():
    verts = [[25.0, 121.0], [25.1, 121.0], [25.1, 121.1]]
    geom, back = _roundtrip(verts, closed=True)
    assert geom["type"] == "Polygon"
    assert back == verts  # 去閉合點後與原始一致


def test_roundtrip_line_open():
    verts = [[24.9, 121.4], [25.0, 121.5], [25.1, 121.6]]
    geom, back = _roundtrip(verts, closed=False)
    assert geom["type"] == "LineString"
    assert back == verts


def test_shape_rejects_too_few_points():
    with pytest.raises(ValueError):
        vertices_to_cot_shape([[25.0, 121.0]], closed=False)  # < 2
    with pytest.raises(ValueError):
        vertices_to_cot_shape([[25.0, 121.0], [25.1, 121.0]], closed=True)  # closed 需 ≥3


def test_shape_skips_out_of_range_garbage():
    # 超界座標被丟（與入向 _valid_lonlat 一致）；剩 2 點仍成 line
    verts = [[25.0, 121.0], [999.0, 121.0], [25.1, 121.1]]
    shape = vertices_to_cot_shape(verts, closed=False)
    assert shape.count("<vertex") == 2  # 999 緯度被剔


def test_build_geometry_cot_polygon_structure():
    verts = [[25.0, 121.0], [25.1, 121.0], [25.1, 121.1]]
    cot = build_geometry_cot(
        uid="ICS-CMD-Z1",
        type_="u-d-f",
        vertices=verts,
        closed=True,
        callsign="禁區A",
        remarks="<禁止>進入",
        now=_NOW,
    )
    e = ET.fromstring(cot[cot.index("<event") :])
    assert e.get("uid") == "ICS-CMD-Z1" and e.get("type") == "u-d-f"
    assert e.find("detail/shape/polyline").get("closed") == "true"
    assert len(e.findall("detail/shape/polyline/vertex")) == 3
    assert e.find("detail/archive") is not None
    assert e.find("detail/remarks").text == "<禁止>進入"  # escape 後仍還原
    # event point = 形心（落在頂點範圍內）
    pt = e.find("point")
    assert 25.0 <= float(pt.get("lat")) <= 25.1 and 121.0 <= float(pt.get("lon")) <= 121.1


def test_build_geometry_cot_centroid_ignores_garbage(monkeypatch):
    """review #180：形心須用 cleaned 點集（與 shape 同一組）——含畸形/超界頂點時不崩、不歪。"""
    # 含畸形短項 [] + 超界 [999,...]：clean 後剩 2 個有效點，形心只算這 2 個
    verts = [[25.0, 121.0], [], [999.0, 121.0], [25.2, 121.0]]
    cot = build_geometry_cot(uid="ICS-CMD-G1", type_="b-m-r", vertices=verts, closed=False, now=_NOW)
    e = ET.fromstring(cot[cot.index("<event") :])
    pt = e.find("point")
    # 形心 = (25.0+25.2)/2 = 25.1（999 與 [] 被排除），未崩、未被 999 拉歪
    assert abs(float(pt.get("lat")) - 25.1) < 1e-9
    assert -90.0 <= float(pt.get("lat")) <= 90.0  # 不會因 garbage 落到非法緯度
    assert len(e.findall("detail/shape/polyline/vertex")) == 2


def test_build_geometry_cot_line_roundtrips_through_extract():
    verts = [[24.9, 121.4], [25.0, 121.5]]
    cot = build_geometry_cot(uid="ICS-CMD-R1", type_="b-m-r", vertices=verts, closed=False, now=_NOW)
    e = ET.fromstring(cot[cot.index("<event") :])
    geom = extract_geometry(e.find("detail"))
    assert geom["type"] == "LineString"
    assert geojson_to_vertices(geom) == verts
