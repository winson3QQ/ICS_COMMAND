# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
services/geometry_service.py — CoT <shape> + DataSync GeoJSON 幾何統一解析（P2-08，#132）

純函式。CoT `<shape>`/`<link>` 與（未來 P2-14）DataSync GeoJSON 兩條來源在 **GeoJSON 這層
匯流** —— `extract_geometry` 從 CoT detail element re-parse 出 GeoJSON，`geojson_to_vertices`
把 GeoJSON 轉前端既有的 `[[lat,lng],...]` 格式（P1-16 route/polygon 用），零前端改動。

**不依賴 `_extract_detail`**：後者只收 detail 直接子元素一層、不遞迴，shape 的巢狀 vertices /
多筆 link 會丟失，故本服務自己拿 raw element 遞迴解析。
"""

import logging
import math
from xml.sax.saxutils import quoteattr

log = logging.getLogger(__name__)

# 圓/橢圓以 N-gon 逼近的頂點數（夠圓滑、頂點數不爆；circle major==minor → 正 N 邊形）。
_ELLIPSE_SEGMENTS = 48


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


def _opt_float(value) -> float | None:
    """字串 → float，None/非數值 → None。"""
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _ellipse_to_polygon(center, major_m, minor_m, angle_deg) -> dict | None:
    """ATAK <ellipse major minor angle> → GeoJSON Polygon 環（N-gon 逼近，#134/真機圓形）。
    major/minor = 半長軸/半短軸（**公尺**，circle 時相等）；angle = 旋轉（度，CW）。
    center=(lat,lon)（CoT event <point>，圓心）。平面近似（數百公尺內誤差可忽略）。
    座標 GeoJSON lon-lat 序，首尾閉合。缺 center / 軸 ≤0 → None（退回不顯示）。"""
    if center is None:
        return None
    lat, lon = center
    if lat is None or lon is None or not major_m or not minor_m or major_m <= 0 or minor_m <= 0:
        return None
    phi = math.radians(angle_deg or 0.0)
    cos_p, sin_p = math.cos(phi), math.sin(phi)
    m_per_deg_lat = 111320.0
    m_per_deg_lon = 111320.0 * math.cos(math.radians(lat)) or 1e-9  # 防赤道外 cos→極點 0
    ring = []
    for i in range(_ELLIPSE_SEGMENTS):
        t = 2.0 * math.pi * i / _ELLIPSE_SEGMENTS
        x = major_m * math.cos(t)  # 局部半長軸方向（公尺）
        y = minor_m * math.sin(t)  # 局部半短軸方向
        east = x * cos_p - y * sin_p
        north = x * sin_p + y * cos_p
        ring.append([lon + east / m_per_deg_lon, lat + north / m_per_deg_lat])
    ring.append(ring[0])  # 閉合環
    return {"type": "Polygon", "coordinates": [ring]}


def _from_shape(shape_el, center=None) -> dict | None:
    """<shape> 子層解析。<polyline closed=..><vertex point="lat,lon"/>...> → GeoJSON。
    <ellipse major minor angle/>（圓/橢圓，u-d-c-c）→ N-gon Polygon 逼近（#134，需 center）。"""
    for c in shape_el:
        tag = _localname(c.tag)
        if tag in ("ellipse", "circle"):
            poly = _ellipse_to_polygon(
                center, _opt_float(c.get("major")), _opt_float(c.get("minor")), _opt_float(c.get("angle"))
            )
            if poly:
                return poly
            log.warning("[geometry] CoT shape <%s> 缺 center/軸 → 略過（無法定圓心半徑）", tag)
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


def extract_geometry(detail_el, center=None) -> dict | None:
    """從 CoT <detail> element 抽幾何 → GeoJSON Geometry（LineString / Polygon），無則 None。

    優先 <shape> 巢狀 polyline/polygon/ellipse；否則多筆 <link point="lat,lon,hae"/>（route）。
    <ellipse>（圓/橢圓）需 center=(lat,lon)（event <point> 圓心）→ N-gon Polygon 逼近（#134）。
    座標 GeoJSON lon-lat 序。
    """
    if detail_el is None:
        return None
    shape = next((c for c in detail_el if _localname(c.tag) == "shape"), None)
    if shape is not None:
        geom = _from_shape(shape, center)
        if geom:
            return geom
    # 多筆 <link point=..>（ATAK/iTAK route 或封閉繪圖）；非幾何 link（無 point 屬性）自動略過
    coords = [[ll[1], ll[0]] for c in detail_el if _localname(c.tag) == "link" and (ll := _latlon(c.get("point")))]
    if len(coords) >= 2:
        # 是否封閉面（→ Polygon）判斷，兩種 iTAK 真機訊號（P2-10 dogfood）：
        #   (a) #159：封閉繪圖（freehand area）以**首尾相同**的 <link> 序列送（含閉合點 ≥4 coords）。
        #   (b) #4（u-d-r 矩形 / 填色面）：4 角 <link> **首尾不同**（不重複首點），但帶 <fillColor>
        #       = 有填色 = 面。靠 <fillColor> 存在判定為封閉面，補閉合點成環。route（線）無 fillColor。
        # 兩者都要 review #132 的「Polygon 需 ≥3 相異頂點」門檻（含閉合點 ≥4 coords / ≥3 distinct）。
        has_fill = any(_localname(c.tag) == "fillColor" for c in detail_el)
        closed_loop = coords[0] == coords[-1] and len(coords) >= 4
        if closed_loop:
            return {"type": "Polygon", "coordinates": [coords]}
        if has_fill and len(coords) >= 3:
            return {"type": "Polygon", "coordinates": [coords + [coords[0]]]}  # 補閉合點成環
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


def clean_vertices(vertices: list[list[float]], *, closed: bool) -> list[tuple[float, float]]:
    """過濾出有效頂點 `[(lat,lon),...]`：限 [-90,90]×[-180,180]（與入向 `_valid_lonlat` 一致，
    擋 garbage / 畸形短項）；< 2 有效點 → ValueError；closed 須 ≥3（對齊 `_from_shape` Polygon 門檻）。

    出向（P2-30 / #180）的**單一驗證點** —— shape 與 event 形心都用這同一組 cleaned 點，
    避免「shape 過濾、形心沒過濾」的分歧（review #180）。
    """
    pts: list[tuple[float, float]] = []
    for v in vertices:
        if not isinstance(v, list | tuple) or len(v) < 2:
            continue
        try:
            la, lo = float(v[0]), float(v[1])
        except (TypeError, ValueError):
            continue
        if -90.0 <= la <= 90.0 and -180.0 <= lo <= 180.0:
            pts.append((la, lo))
    if len(pts) < 2:
        raise ValueError("clean_vertices：至少需 2 個有效頂點")
    if closed and len(pts) < 3:
        raise ValueError("clean_vertices：closed polygon 至少需 3 個有效頂點")
    return pts


def hex_to_argb_int(hex_color: str | None, *, alpha: int = 0xFF) -> int | None:
    """**出向**：`'#rrggbb'` + alpha → ATAK 有號 32-bit ARGB 整數（`cop_service._argb_int_to_hex`
    的反向）。ATAK 的 strokeColor/fillColor/color 一律有號 int（如 0xFFFFFF00 黃 → -256）。
    非法/缺色 → None（呼叫端退預設）。alpha：描邊用 0xFF（不透明）、填色用半透明（如 0x40）。"""
    if not hex_color:
        return None
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return None
    try:
        rgb = int(h, 16)
    except ValueError:
        return None
    argb = ((alpha & 0xFF) << 24) | (rgb & 0xFFFFFF)
    return argb - (1 << 32) if argb >= (1 << 31) else argb  # → 有號 32-bit（ATAK 慣例）


def vertices_to_cot_links(vertices: list[list[float]], *, closed: bool) -> str:
    """**出向**：前端 vertices `[[lat,lng],...]` → ATAK 原生 `<link point="lat,lon,hae"/>` 序列。

    **取代舊 `vertices_to_cot_shape`**（`<shape><polyline>`）——#211 ATAK dogfood 實證：ATAK 只
    渲染 `<link>` 序列 + stroke/fill 樣式的繪圖，不認無樣式 `<shape><polyline>`（iTAK 寬鬆兩種皆吃）。

    **polygon（closed）**：裸 `<link point>` + 補閉合 link（首點重複），對齊 `extract_geometry`
    以「首尾相同」判 Polygon（round-trip 仍通）。
    **route（open）**：link 帶 **`type="b-m-p-c"`（control point）**——#211 route dogfood 實證：裸
    `<link>` 會被 ATAK 當 **waypoint** 自動生「<route名> SP」起點 marker（雜訊、archived 累積）；
    control point 只塑線、無航點 marker。`relation="c"` 對齊 ATAK 真機 route 格式。
    驗證走 `clean_vertices`（座標範圍 + 點數門檻）。樣式/event 包裝在 `tak_downlink.build_geometry_cot`。
    """
    pts = clean_vertices(vertices, closed=closed)
    if closed:
        links = "".join(f'<link point="{la},{lo},0"/>' for la, lo in pts)
        la0, lo0 = pts[0]
        return links + f'<link point="{la0},{lo0},0"/>'  # 閉合點（首尾相同 → Polygon）
    # route：control point，ATAK 不建航點 marker（裸 link → waypoint → 自動「SP」起點，#211）
    return "".join(f'<link type="b-m-p-c" relation="c" point="{la},{lo},0"/>' for la, lo in pts)


# ── #260 Slice B：出向 route 忠實序列化（保 waypoint 名字/type + 導航屬性）────────────


def _valid_cot_point(point) -> str | None:
    """CoT <link> 的 point 字串 `"lat,lon[,hae]"` 驗證：lat/lon 在範圍 → 回原字串，否則 None
    （防不可信 attributes 的座標 garbage，同 `_valid_lonlat` 風格）。"""
    if not isinstance(point, str):
        return None
    parts = point.split(",")
    if len(parts) < 2:
        return None
    try:
        lat, lon = float(parts[0]), float(parts[1])
    except (TypeError, ValueError):
        return None
    return point if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0 else None


def _safe_attr_name(name) -> bool:
    """XML attribute 名安全性：只允許字母/數字/底線/連字號（擋 attributes 來的 key 注入額外 XML）。"""
    return isinstance(name, str) and bool(name) and all(c.isalnum() or c in "_-" for c in name)


def route_links_to_cot(link_list) -> str | None:
    """**出向忠實**（#260 Slice B）：cop_entity `attributes.link`（原始 route waypoints）→ ATAK 原生
    `<link>` 序列，保 waypoint `uid/callsign/type/relation`（vs `vertices_to_cot_links` 光禿 control point）。
    值全走 `quoteattr` escape、座標驗證；無任何合法 waypoint → None（呼叫端退回 vertices 光禿路徑）。"""
    if not isinstance(link_list, list):
        return None
    segs: list[str] = []
    for link in link_list:
        if not isinstance(link, dict):
            continue
        pt = _valid_cot_point(link.get("point"))
        if pt is None:
            continue
        attrs = (
            f"point={quoteattr(pt)} type={quoteattr(str(link.get('type') or 'b-m-p-c'))} "
            f"relation={quoteattr(str(link.get('relation') or 'c'))}"
        )
        if link.get("uid"):
            attrs += f" uid={quoteattr(str(link['uid']))}"
        if link.get("callsign"):
            attrs += f" callsign={quoteattr(str(link['callsign']))}"
        segs.append(f"<link {attrs}/>")
    return "".join(segs) if segs else None


def link_attr_to_cot(link_attr) -> str:
    """route 導航屬性 dict（planningmethod/method/routetype/direction…）→ `<link_attr .../>`（#260 Slice B）。
    key 過 `_safe_attr_name`（擋注入）、value 走 `quoteattr`；純量值才收（skip nested dict/list）；空 → 空字串。"""
    if not isinstance(link_attr, dict):
        return ""
    parts = "".join(
        f" {k}={quoteattr(str(v))}"
        for k, v in link_attr.items()
        if _safe_attr_name(k) and not isinstance(v, dict | list)
    )
    return f"<link_attr{parts}/>" if parts else ""
