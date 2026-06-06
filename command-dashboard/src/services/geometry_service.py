"""
services/geometry_service.py — CoT <shape> + DataSync GeoJSON 幾何統一解析（P2-08，#132）

純函式。CoT `<shape>`/`<link>` 與（未來 P2-14）DataSync GeoJSON 兩條來源在 **GeoJSON 這層
匯流** —— `extract_geometry` 從 CoT detail element re-parse 出 GeoJSON，`geojson_to_vertices`
把 GeoJSON 轉前端既有的 `[[lat,lng],...]` 格式（P1-16 route/polygon 用），零前端改動。

**不依賴 `_extract_detail`**：後者只收 detail 直接子元素一層、不遞迴，shape 的巢狀 vertices /
多筆 link 會丟失，故本服務自己拿 raw element 遞迴解析。
"""

import logging

log = logging.getLogger(__name__)


def _localname(tag: str) -> str:
    """去 XML namespace 前綴。"""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _latlon(point: str | None, el=None) -> tuple[float, float] | None:
    """ATAK 座標 'lat,lon[,hae]' 字串 → (lat, lon)；缺則退而取 el 的 lat/lon 屬性。失敗 None。"""
    if point:
        parts = point.split(",")
        if len(parts) >= 2:
            try:
                return float(parts[0]), float(parts[1])
            except ValueError:
                pass
    if el is not None:
        try:
            lat, lon = el.get("lat"), el.get("lon")
            if lat is not None and lon is not None:
                return float(lat), float(lon)
        except (TypeError, ValueError):
            pass
    return None


def _from_shape(shape_el) -> dict | None:
    """<shape> 子層解析。<polyline closed=..><vertex point="lat,lon"/>...> → GeoJSON。
    circle/ellipse 前端無管線 → None + warning（P2-08 已知限制）。"""
    for c in shape_el:
        tag = _localname(c.tag)
        if tag in ("ellipse", "circle"):
            log.warning("[geometry] CoT shape <%s> 暫不支援（前端無圓形管線，P2-08 限制），略過", tag)
            return None
        if tag == "polyline":
            coords = [
                [ll[1], ll[0]]  # GeoJSON lon-lat 序
                for v in c
                if _localname(v.tag) == "vertex" and (ll := _latlon(v.get("point"), v))
            ]
            if len(coords) < 2:
                return None
            if c.get("closed", "").lower() == "true":
                ring = coords + ([coords[0]] if coords[0] != coords[-1] else [])
                return {"type": "Polygon", "coordinates": [ring]}
            return {"type": "LineString", "coordinates": coords}
    return None


def extract_geometry(detail_el) -> dict | None:
    """從 CoT <detail> element 抽幾何 → GeoJSON Geometry（LineString / Polygon），無則 None。

    優先 <shape> 巢狀 polyline/polygon；否則多筆 <link point="lat,lon,hae"/>（route）。
    circle/ellipse 不支援（_from_shape 回 None）。座標 GeoJSON lon-lat 序。
    """
    if detail_el is None:
        return None
    shape = next((c for c in detail_el if _localname(c.tag) == "shape"), None)
    if shape is not None:
        geom = _from_shape(shape)
        if geom:
            return geom
    # 多筆 <link point=..>（ATAK route）；非幾何 link（無 point 屬性）自動略過
    coords = [
        [ll[1], ll[0]]
        for c in detail_el
        if _localname(c.tag) == "link" and (ll := _latlon(c.get("point")))
    ]
    if len(coords) >= 2:
        return {"type": "LineString", "coordinates": coords}
    return None


def geojson_to_vertices(geom: dict | None) -> list[list[float]]:
    """GeoJSON Geometry → 前端 vertices `[[lat,lng],...]`（沿用 P1-16 格式）。
    Polygon 取 outer ring 並去掉閉合重複末點（前端 vertices 不重複首尾）。"""
    if not geom:
        return []
    t = geom.get("type")
    if t == "LineString":
        coords = geom.get("coordinates", [])
    elif t == "Polygon":
        rings = geom.get("coordinates") or [[]]
        coords = rings[0]
        if len(coords) >= 2 and coords[0] == coords[-1]:
            coords = coords[:-1]
    else:
        return []
    return [[lat, lon] for lon, lat in coords]  # GeoJSON lon-lat → 前端 lat-lng


def centroid(vertices: list[list[float]]) -> tuple[float, float] | None:
    """vertices `[[lat,lng],...]` → 算術平均 centroid (lat, lon)。
    簡單平均（小範圍演習場足夠；跨換日線/極區失真，本專案場景可接受）。"""
    if not vertices:
        return None
    n = len(vertices)
    return sum(v[0] for v in vertices) / n, sum(v[1] for v in vertices) / n
