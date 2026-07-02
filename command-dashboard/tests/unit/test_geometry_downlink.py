# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""P2-30 / #180 — 線/區出向幾何序列化（vertices → CoT `<link>` + 樣式）+ build_geometry_cot。

格式 **#211 ATAK dogfood 修正**：出向改 ATAK 原生 `<link point=...>` 序列 + strokeColor/fillColor/
strokeStyle —— ATAK 不渲染無樣式 `<shape><polyline>`（iTAK 寬鬆才吃舊格式），`<link>`+樣式雙吃。

核心：**round-trip** —— `vertices_to_cot_links` 產的 XML 餵回 P2-08 入向 `extract_geometry`
+ `geojson_to_vertices`，還原同型同點（出向對稱入向，現走 `<link>` 分支）。純函式，不碰網路/DB。
"""

from __future__ import annotations

from datetime import UTC, datetime
from xml.etree import ElementTree as ET

import pytest

from services.geometry_service import (
    extract_geometry,
    geojson_to_vertices,
    hex_to_argb_int,
    vertices_to_cot_links,
)
from services.tak_downlink import build_geometry_cot

_NOW = datetime(2026, 6, 9, 5, 0, 0, tzinfo=UTC)


def _roundtrip(vertices, *, closed):
    links = vertices_to_cot_links(vertices, closed=closed)
    detail = ET.fromstring(f"<detail>{links}</detail>")
    geom = extract_geometry(detail)
    return geom, geojson_to_vertices(geom)


def test_roundtrip_polygon_closed():
    verts = [[25.0, 121.0], [25.1, 121.0], [25.1, 121.1]]
    geom, back = _roundtrip(verts, closed=True)
    assert geom["type"] == "Polygon"  # 閉合 link（首尾相同）→ Polygon
    assert back == verts  # 去閉合點後與原始一致


def test_roundtrip_line_open():
    verts = [[24.9, 121.4], [25.0, 121.5], [25.1, 121.6]]
    geom, back = _roundtrip(verts, closed=False)
    assert geom["type"] == "LineString"
    assert back == verts


def test_links_rejects_too_few_points():
    with pytest.raises(ValueError):
        vertices_to_cot_links([[25.0, 121.0]], closed=False)  # < 2
    with pytest.raises(ValueError):
        vertices_to_cot_links([[25.0, 121.0], [25.1, 121.0]], closed=True)  # closed 需 ≥3


def test_links_skips_out_of_range_garbage():
    # 超界座標被丟（與入向 _valid_lonlat 一致）；剩 2 點仍成 line（開放不補閉合）
    verts = [[25.0, 121.0], [999.0, 121.0], [25.1, 121.1]]
    links = vertices_to_cot_links(verts, closed=False)
    assert links.count("<link") == 2  # 999 緯度被剔


def test_links_closed_appends_closing_point():
    verts = [[25.0, 121.0], [25.1, 121.0], [25.1, 121.1]]
    links = vertices_to_cot_links(verts, closed=True)
    assert links.count("<link") == 4  # 3 頂點 + 閉合點（首尾相同）


def test_links_route_uses_control_points_polygon_bare():
    # #211 route dogfood：route(open) link 帶 b-m-p-c（control point）→ ATAK 不建「SP」航點 marker；
    # polygon(closed) 維持裸 link（無航點問題）。
    route_links = vertices_to_cot_links([[24.9, 121.4], [25.0, 121.5]], closed=False)
    assert 'type="b-m-p-c"' in route_links
    poly_links = vertices_to_cot_links([[25.0, 121.0], [25.1, 121.0], [25.1, 121.1]], closed=True)
    assert "b-m-p-c" not in poly_links  # polygon 不帶 control-point type（裸 link）


def test_hex_to_argb_int_symmetric_with_inbound():
    # 對稱 cop_service._argb_int_to_hex：黃 0xFFFFFF00 → -256
    assert hex_to_argb_int("#ffff00", alpha=0xFF) == -256
    assert hex_to_argb_int("#000000", alpha=0xFF) == -16777216  # 0xFF000000
    assert hex_to_argb_int("#ffffff", alpha=0x40) == 1090519039  # 0x40FFFFFF 半透明白（正數）
    assert hex_to_argb_int(None) is None
    assert hex_to_argb_int("bad") is None


def test_build_geometry_cot_polygon_structure_and_style():
    verts = [[25.0, 121.0], [25.1, 121.0], [25.1, 121.1]]
    cot = build_geometry_cot(
        uid="ICS-CMD-Z1",
        type_="u-d-f",
        vertices=verts,
        closed=True,
        callsign="禁區A",
        remarks="<禁止>進入",
        color="#e3b341",
        dashed=True,
        now=_NOW,
    )
    e = ET.fromstring(cot[cot.index("<event") :])
    assert e.get("uid") == "ICS-CMD-Z1" and e.get("type") == "u-d-f"
    assert len(e.findall("detail/link")) == 4  # 3 頂點 + 閉合
    assert e.find("detail/strokeColor").get("value") == str(hex_to_argb_int("#e3b341", alpha=0xFF))
    assert e.find("detail/fillColor") is not None  # closed → 帶填色
    assert e.find("detail/strokeStyle").get("value") == "dashed"
    assert e.find("detail/archive") is not None
    assert e.find("detail/remarks").text == "<禁止>進入"  # escape 後仍還原
    # event point = 形心（落在頂點範圍內）
    pt = e.find("point")
    assert 25.0 <= float(pt.get("lat")) <= 25.1 and 121.0 <= float(pt.get("lon")) <= 121.1


def test_build_geometry_cot_open_line_no_fill_default_stroke():
    verts = [[24.9, 121.4], [25.0, 121.5]]
    cot = build_geometry_cot(uid="ICS-CMD-R1", type_="b-m-r", vertices=verts, closed=False, now=_NOW)
    e = ET.fromstring(cot[cot.index("<event") :])
    assert e.find("detail/fillColor") is None  # 開放線無填色
    assert e.find("detail/strokeStyle").get("value") == "solid"  # 預設非 dashed
    assert e.find("detail/strokeColor").get("value") == "-1"  # 無 color → 預設白


def test_build_geometry_cot_dotted_style():
    # #214 review：strokeStyle 三態與入向對稱，不可漏 dotted（dotted > dashed > solid）
    verts = [[24.9, 121.4], [25.0, 121.5]]
    cot = build_geometry_cot(uid="ICS-CMD-D1", type_="b-m-r", vertices=verts, closed=False, dotted=True, now=_NOW)
    e = ET.fromstring(cot[cot.index("<event") :])
    assert e.find("detail/strokeStyle").get("value") == "dotted"


def test_build_geometry_cot_centroid_ignores_garbage():
    """review #180：形心須用 cleaned 點集（與 links 同一組）——含畸形/超界頂點時不崩、不歪。"""
    # 含畸形短項 [] + 超界 [999,...]：clean 後剩 2 個有效點，形心只算這 2 個
    verts = [[25.0, 121.0], [], [999.0, 121.0], [25.2, 121.0]]
    cot = build_geometry_cot(uid="ICS-CMD-G1", type_="b-m-r", vertices=verts, closed=False, now=_NOW)
    e = ET.fromstring(cot[cot.index("<event") :])
    pt = e.find("point")
    # 形心 = (25.0+25.2)/2 = 25.1（999 與 [] 被排除），未崩、未被 999 拉歪
    assert abs(float(pt.get("lat")) - 25.1) < 1e-9
    assert -90.0 <= float(pt.get("lat")) <= 90.0  # 不會因 garbage 落到非法緯度
    assert len(e.findall("detail/link")) == 2


def test_build_geometry_cot_line_roundtrips_through_extract():
    verts = [[24.9, 121.4], [25.0, 121.5]]
    cot = build_geometry_cot(uid="ICS-CMD-R1", type_="b-m-r", vertices=verts, closed=False, now=_NOW)
    e = ET.fromstring(cot[cot.index("<event") :])
    geom = extract_geometry(e.find("detail"))
    assert geom["type"] == "LineString"
    assert geojson_to_vertices(geom) == verts


# ── #260 Slice B：出向 route 忠實 round-trip（waypoints + link_attr）───────────────


def test_route_outbound_faithful_with_waypoints_issue260():
    """#260 Slice B：route 出向有原始 waypoints → 保 waypoint callsign/type + link_attr 導航屬性
    （非光禿 control point）。"""
    waypoints = [
        {"uid": "wp1", "callsign": "Route 1 SP", "type": "b-m-p-w", "point": "24.837,121.035,44", "relation": "c"},
        {"uid": "wp2", "callsign": "TGT", "type": "b-m-p-w", "point": "24.831,121.034,54", "relation": "c"},
    ]
    link_attr = {"method": "Walking", "routetype": "Primary", "direction": "Infil"}
    cot = build_geometry_cot(
        uid="R1",
        type_="b-m-r",
        vertices=[[24.837, 121.035], [24.831, 121.034]],
        closed=False,
        waypoints=waypoints,
        link_attr=link_attr,
        now=_NOW,
    )
    e = ET.fromstring(cot[cot.index("<event") :])
    links = e.findall("detail/link")
    assert {link.get("callsign") for link in links} == {"Route 1 SP", "TGT"}  # waypoint 名字保留
    assert all(link.get("type") == "b-m-p-w" for link in links)  # waypoint（非光禿 control point）
    la = e.find("detail/link_attr")
    assert la is not None and la.get("method") == "Walking" and la.get("routetype") == "Primary"


def test_route_outbound_falls_back_without_waypoints_issue260():
    """ICS 自建 route（無 attributes.link）→ 退回光禿 control point。"""
    cot = build_geometry_cot(uid="R2", type_="b-m-r", vertices=[[24.9, 121.4], [25.0, 121.5]], closed=False, now=_NOW)
    e = ET.fromstring(cot[cot.index("<event") :])
    assert all(link.get("type") == "b-m-p-c" for link in e.findall("detail/link"))  # 光禿 control point
    assert e.find("detail/link_attr") is None


def test_route_outbound_xml_injection_defended_issue260():
    """XML injection 防護：惡意 waypoint callsign / link_attr key+value → escape/過濾，不破壞 XML。"""
    waypoints = [{"callsign": '"><evil/>', "type": "b-m-p-w", "point": "24.8,121.0", "relation": "c"}]
    link_attr = {"bad key": "x", "method": '"><inject/>'}  # "bad key" 含空格 → 過濾
    cot = build_geometry_cot(
        uid="R3",
        type_="b-m-r",
        vertices=[[24.8, 121.0], [24.9, 121.1]],
        closed=False,
        waypoints=waypoints,
        link_attr=link_attr,
        now=_NOW,
    )
    e = ET.fromstring(cot[cot.index("<event") :])  # 仍合法 XML（escape 有效）
    assert "<evil" not in cot and "<inject" not in cot  # 未注入成 element
    la = e.find("detail/link_attr")
    assert la.get("bad key") is None and la.get("method") == '"><inject/>'  # 不安全 key 過濾、value escape 後忠實
    assert e.find("detail/link").get("callsign") == '"><evil/>'  # callsign escape 後還原


def test_route_outbound_has_routeinfo_polygon_none_issue260():
    """#260：route(open) 出向必帶 <__routeinfo><__navcues/> —— ATAK 認定「這是 route」的標記，
    缺則 b-m-r event 收得到卻不渲染（真機 dogfood 實證）；polygon(closed) 不帶。"""
    route = build_geometry_cot(uid="R", type_="b-m-r", vertices=[[24.9, 121.4], [25.0, 121.5]], closed=False, now=_NOW)
    e = ET.fromstring(route[route.index("<event") :])
    assert e.find("detail/__routeinfo") is not None
    assert e.find("detail/__routeinfo/__navcues") is not None
    poly = build_geometry_cot(
        uid="Z", type_="u-d-f", vertices=[[25.0, 121.0], [25.1, 121.0], [25.1, 121.1]], closed=True, now=_NOW
    )
    ep = ET.fromstring(poly[poly.index("<event") :])
    assert ep.find("detail/__routeinfo") is None  # polygon 不是 route，不帶 routeinfo


def test_route_outbound_single_link_dict_faithful_issue260():
    """review：單一 <link> 被 _extract_detail 存成 dict（非 list）→ 仍走忠實路徑（正規化為單元素 list）。"""
    wp = {"uid": "w1", "callsign": "SP", "type": "b-m-p-w", "point": "24.8,121.0", "relation": "c"}
    cot = build_geometry_cot(
        uid="R", type_="b-m-r", vertices=[[24.8, 121.0], [24.9, 121.1]], closed=False, waypoints=wp, now=_NOW
    )
    e = ET.fromstring(cot[cot.index("<event") :])
    assert e.find("detail/link").get("callsign") == "SP"  # 單 dict 忠實序列化，非光禿


def test_route_outbound_garbage_waypoints_falls_back_issue260():
    """review：waypoints 全非法（座標 garbage / 非 dict）→ route_links_to_cot 回 None → 退回光禿 control point。"""
    bad = [{"point": "999,0", "type": "b-m-p-w"}, "not-a-dict", {"point": "abc"}]
    cot = build_geometry_cot(
        uid="R", type_="b-m-r", vertices=[[24.8, 121.0], [24.9, 121.1]], closed=False, waypoints=bad, now=_NOW
    )
    e = ET.fromstring(cot[cot.index("<event") :])
    assert all(link.get("type") == "b-m-p-c" for link in e.findall("detail/link"))  # 全非法 → fallback 光禿


def test_udf_open_line_no_routeinfo_issue260():
    """review：u-d-f 開放繪圖（非 b-m-r route）不帶 __routeinfo（避免 ATAK 誤當 route）。"""
    cot = build_geometry_cot(uid="D", type_="u-d-f", vertices=[[24.8, 121.0], [24.9, 121.1]], closed=False, now=_NOW)
    e = ET.fromstring(cot[cot.index("<event") :])
    assert e.find("detail/__routeinfo") is None
