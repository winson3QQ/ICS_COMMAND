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
    """去 XML namespace 前綴。與 tak_service._localname 同（刻意各持一份：geometry_service 被
    tak_service import，反向複用會循環；3 行小函式重複成本低於抽共用 module，review #132）。"""
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
            if c.get("closed", "").lower() == "true" and len(coords) >= 3:
                # Polygon 需 ≥3 相異頂點（review #132：2 點 closed 是退化/自交環 → 退 LineString）
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
    # 多筆 <link point=..>（ATAK/iTAK route 或封閉繪圖）；非幾何 link（無 point 屬性）自動略過
    coords = [
        [ll[1], ll[0]]
        for c in detail_el
        if _localname(c.tag) == "link" and (ll := _latlon(c.get("point")))
    ]
    if len(coords) >= 2:
        # #159（P2-10 真機 dogfood）：iTAK 封閉繪圖（area/Zone）以**首尾相同**的 <link>
        # 序列送（非 <shape><polyline closed>）→ 應判 Polygon 非 route。對齊 _from_shape
        # 的 closed 邏輯（review #132：Polygon 需 ≥3 相異頂點 → 含閉合點 ≥4 coords）。
        if coords[0] == coords[-1] and len(coords) >= 4:
            return {"type": "Polygon", "coordinates": [coords]}
        return {"type": "LineString", "coordinates": coords}
    return None


def _valid_lonlat(c) -> tuple[float, float] | None:
    """GeoJSON coord [lon, lat, ...] → (lon, lat) float；非 list / arity<2 / 非數值 /
    超出 [-180,180]×[-90,90] → None（防不可信 REST geometry 的 garbage，review #132）。"""
    if not isinstance(c, list | tuple) or len(c) < 2:
        return None
    try:
        lon, lat = float(c[0]), float(c[1])
    except (TypeError, ValueError):
        return None
    return (lon, lat) if -180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0 else None


def geojson_to_vertices(geom: dict | None) -> list[list[float]]:
    """GeoJSON Geometry → 前端 vertices `[[lat,lng],...]`（沿用 P1-16 格式）。Polygon 取
    outer ring 去閉合末點。**防禦不可信 coordinates**（REST geometry 為任意 dict）：非法 /
    超界座標跳過（與 _latlon 回 None 風格一致），避免 garbage 讓 ingest 5xx（review #132）。"""
    if not geom:
        return []
    t = geom.get("type")
    raw = geom.get("coordinates")
    if t == "LineString":
        coords = raw if isinstance(raw, list) else []
    elif t == "Polygon":
        coords = raw[0] if isinstance(raw, list) and raw and isinstance(raw[0], list) else []
        if len(coords) >= 2 and coords[0] == coords[-1]:
            coords = coords[:-1]
    else:
        return []
    return [[v[1], v[0]] for c in coords if (v := _valid_lonlat(c))]  # lon-lat → lat-lng，garbage 跳過
