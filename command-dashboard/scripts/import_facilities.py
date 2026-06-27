#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""P1-17 永久設施公開資料匯入（維護者用，離線優先）。

把台灣政府開放資料的永久公共設施（避難收容處所 / 消防 / 警政 / 醫療）匯入成
唯讀基準圖層 static/facilities.seed.json。**非 cop_entity、非 map_config user-data、
不受 exercise scoping / reset 影響**（見 issue #88 / ROADMAP P1-17）。

供應鏈（CLAUDE.md 紅線）：所有來源為中華民國（台灣）政府機關，非中國。
授權：政府資料開放授權條款第 1 版（相容 CC BY 4.0，須標示出處）。

紅線實作：
- **剝除 PII**：來源含「管理人姓名 / 電話」等個資者一律不寫入（只留設施名 / 位置 /
  容量 / 災害類別 / 無障礙等公共欄位；機關市話為公開資訊，可留）。
- **座標 datum**：以實際值域判定（不信欄名——消防源欄名寫 TWD97 但值其實是 WGS84）。
  WGS84 直接用；TWD97 TM2（EPSG:3826）需 pyproj 轉換（police / 待接）。

用法：
    python3 import_facilities.py --source shelter,fire        # 指定來源
    python3 import_facilities.py --all                        # 全部已接來源
    python3 import_facilities.py --all --out <path>           # 自訂輸出

下載走系統 curl（台灣政府憑證對 Python ssl 偏嚴，curl 用系統憑證庫較穩，
與 basemap 腳本一致）。
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

# command-dashboard/ 根（本檔在 command-dashboard/scripts/ 下）
CDROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = CDROOT / "static" / "facilities.seed.json"

UA = "Mozilla/5.0 (compatible; ICS_Command-facilities-import/1.0)"

# 台灣含外島經緯度合理範圍（金門 118.2 / 馬祖 119.9 / 本島 / 蘭嶼 121.5…）
LNG_MIN, LNG_MAX = 118.0, 122.5
LAT_MIN, LAT_MAX = 21.5, 26.5


def _curl(url: str) -> bytes:
    """用系統 curl 下載（-fL：失敗即非零、跟隨轉址）。"""
    out = subprocess.run(
        ["curl", "-fsSL", "-A", UA, url],
        capture_output=True,
        timeout=120,
    )
    if out.returncode != 0:
        raise RuntimeError(f"curl 失敗（exit {out.returncode}）：{url}\n{out.stderr.decode(errors='replace')[:300]}")
    return out.stdout


def _decode(raw: bytes) -> str:
    """政府 CSV 多為 UTF-8（偶 BIG5）。逐一嘗試。"""
    for enc in ("utf-8-sig", "utf-8", "big5"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise RuntimeError("無法解碼 CSV（非 UTF-8 / BIG5）")


def _rows(raw: bytes) -> list[dict]:
    """CSV bytes → list[dict]（以 header 為 key）。"""
    return list(csv.DictReader(io.StringIO(_decode(raw))))


def _rows_from_zip(raw: bytes, inner_prefix: str) -> list[dict]:
    """ZIP bytes → 內含某 CSV（檔名以 inner_prefix 開頭）→ list[dict]。"""
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = [n for n in zf.namelist() if n.startswith(inner_prefix) and n.lower().endswith(".csv")]
        if not names:
            raise RuntimeError(f"zip 內找不到 {inner_prefix}*.csv（內容：{zf.namelist()}）")
        return _rows(zf.read(names[0]))


# TWD97 TM2 (EPSG:3826) → WGS84 (EPSG:4326) 轉換器（lazy；pyproj 僅維護者匯入時需要，
# **不進 runtime requirements**——server 只讀已是 WGS84 的 seed）。
_twd97_tf = None


def _twd97_to_wgs84(x: float, y: float) -> tuple[float, float]:
    global _twd97_tf
    if _twd97_tf is None:
        try:
            from pyproj import Transformer
        except ImportError:
            sys.exit(
                "✗ 警政源需 pyproj（TWD97→WGS84）。請在維護者 venv 裝：pip install pyproj\n"
                "  （pyproj=MIT/NOAA、PROJ=OSGeo，非中國；僅匯入時用，不進 runtime）"
            )
        _twd97_tf = Transformer.from_crs("EPSG:3826", "EPSG:4326", always_xy=True)
    lng, lat = _twd97_tf.transform(x, y)
    return lng, lat


def _valid_wgs84(lat: float, lng: float) -> bool:
    return LAT_MIN <= lat <= LAT_MAX and LNG_MIN <= lng <= LNG_MAX


# 縣市粗略 bbox（lng_min, lng_max, lat_min, lat_max；含外島）。普查用：座標須落在其縣市
# 範圍（+margin）內，否則視為來源座標錯誤（如「縣市=高雄但經度 119.46 飄到海上」）。
# 範圍從寬（margin 0.1）避免誤殺山區/邊界鄉鎮。
_COUNTY_BBOX = {
    "臺北市": (121.44, 121.68, 24.94, 25.22),
    "新北市": (121.23, 122.05, 24.65, 25.32),
    "基隆市": (121.66, 121.82, 25.05, 25.22),
    "桃園市": (120.93, 121.47, 24.55, 25.14),
    "新竹市": (120.83, 121.05, 24.72, 24.88),
    "新竹縣": (120.88, 121.45, 24.48, 24.88),
    "苗栗縣": (120.58, 121.30, 24.28, 24.75),
    "臺中市": (120.40, 121.48, 23.95, 24.48),
    "彰化縣": (120.22, 120.70, 23.78, 24.18),
    "南投縣": (120.55, 121.36, 23.38, 24.22),
    "雲林縣": (120.08, 120.75, 23.50, 23.92),
    "嘉義市": (120.38, 120.52, 23.42, 23.54),
    "嘉義縣": (120.07, 120.90, 23.18, 23.65),
    "臺南市": (120.00, 120.66, 22.85, 23.42),
    "高雄市": (120.18, 121.10, 22.45, 23.50),
    "屏東縣": (120.40, 120.95, 21.88, 22.90),
    "宜蘭縣": (121.28, 121.90, 24.30, 25.02),
    "花蓮縣": (121.00, 121.70, 23.05, 24.40),
    "臺東縣": (120.72, 121.65, 21.95, 23.48),
    "澎湖縣": (119.28, 119.75, 23.15, 23.83),
    "金門縣": (118.12, 118.52, 24.37, 24.52),
    "連江縣": (119.82, 120.55, 25.90, 26.42),
}
# 烏坵（金門縣）地理上在 119.45/24.99，遠離金門本島 → 單獨放行（屬金門縣但 bbox 不含）。
_COUNTY_EXTRA = {"金門縣": [(119.40, 119.50, 24.95, 25.02)]}
_COUNTY_BBOX_MARGIN = 0.1
# 異體字正規化（台↔臺）後比對。
_COUNTY_KEYS = sorted(_COUNTY_BBOX, key=len, reverse=True)


def _norm_county(s: str) -> str:
    return (s or "").replace("台", "臺")


def extract_county(s: str) -> str | None:
    """從『縣市及鄉鎮市區』或地址字串取縣市（臺/台 正規化）。

    先剝前置郵遞區號（如 '23741新北市…'），優先 startswith；否則取**最前出現**的縣市
    （避免把後面路名裡的縣市字（如『臺中路』）誤判成縣市）。
    """
    s = _norm_county(s).lstrip("0123456789 -")
    for c in _COUNTY_KEYS:
        if s.startswith(c):
            return c
    best, best_pos = None, len(s)
    for c in _COUNTY_KEYS:
        p = s.find(c)
        if 0 <= p < best_pos:
            best, best_pos = c, p
    return best


def county_coord_ok(county: str | None, lat: float, lng: float) -> bool:
    """普查：座標是否落在 county 的 bbox(+margin) 內。county 無法判 → 放行（不誤殺）。"""
    if not county or county not in _COUNTY_BBOX:
        return True
    m = _COUNTY_BBOX_MARGIN
    for x0, x1, y0, y1 in [_COUNTY_BBOX[county], *_COUNTY_EXTRA.get(county, [])]:
        if x0 - m <= lng <= x1 + m and y0 - m <= lat <= y1 + m:
            return True
    return False


def _num(s) -> float | None:
    try:
        return float(str(s).strip())
    except (TypeError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────────
# Adapters：原始列 → 統一 facility dict 或 None（座標無效則丟棄）。
#   schema: {type, name, lat, lng, addr, extra:{...}}
#   一律不含 PII（管理人姓名/電話）。
# ─────────────────────────────────────────────────────────────────────────


def adapt_shelter(row: dict) -> dict | None:
    """避難收容處所 #73242（內政部）。WGS84。剝管理人姓名/電話 PII。"""
    lat, lng = _num(row.get("緯度")), _num(row.get("經度"))
    if lat is None or lng is None or not _valid_wgs84(lat, lng):
        return None
    name = (row.get("避難收容處所名稱") or "").strip()
    if not name:
        return None
    return {
        "type": "shelter",
        "name": name,
        "lat": round(lat, 6),
        "lng": round(lng, 6),
        "county": extract_county(row.get("縣市及鄉鎮市區") or ""),
        "addr": (row.get("避難收容處所地址") or "").strip(),
        "extra": {
            "capacity": (row.get("預計收容人數") or "").strip(),
            "disasters": (row.get("適用災害類別") or "").strip(),
            "indoor": (row.get("室內") or "").strip(),
            "outdoor": (row.get("室外") or "").strip(),
            "accessible": (row.get("適合避難弱者安置") or "").strip(),
        },
        # 不寫入：管理人姓名 / 管理人電話（PII）
    }


def adapt_fire(row: dict) -> dict | None:
    """救援單位點位 #5969（消防署）。**欄名寫 TWD97 但值為 WGS84**（信值域）。"""
    # X座標_TWD97TM121 = 經度、Y座標_TWD97TM121 = 緯度（實測值 121.x / 24.x）
    lng, lat = _num(row.get("X座標_TWD97TM121")), _num(row.get("Y座標_TWD97TM121"))
    if lat is None or lng is None or not _valid_wgs84(lat, lng):
        return None
    name = (row.get("消防隊名稱") or "").strip()
    if not name:
        return None
    addr = (row.get("地址") or "").strip()
    return {
        "type": "fire",
        "name": name,
        "lat": round(lat, 6),
        "lng": round(lng, 6),
        "county": extract_county(addr),
        "addr": addr,
        "extra": {"phone": (row.get("聯絡電話") or "").strip()},  # 機關市話＝公開資訊
    }


def adapt_police(row: dict) -> dict | None:
    """各縣市警察分局/派出所 #5958（警政署）。**TWD97 TM2 → WGS84（pyproj）**。"""
    x, y = _num(row.get("POINT_X")), _num(row.get("POINT_Y"))
    if x is None or y is None:
        return None
    lng, lat = _twd97_to_wgs84(x, y)
    if not _valid_wgs84(lat, lng):
        return None
    name = (row.get("中文單位名稱") or "").strip()
    if not name:
        return None
    addr = (row.get("地址") or "").strip()
    return {
        "type": "police",
        "name": name,
        "lat": round(lat, 6),
        "lng": round(lng, 6),
        "county": extract_county(addr),
        "addr": addr,
        "extra": {"phone": (row.get("電話") or "").strip()},  # 機關市話＝公開資訊
    }


SOURCES = {
    "shelter": {
        "agency": "內政部",
        "dataset": "data.gov.tw #73242 避難收容處所點位檔",
        "url": (
            "https://opdadm.moi.gov.tw/api/v1/no-auth/resource/api/dataset/"
            "ED6CF735-6C03-4573-A882-72C1BEC799CB/resource/"
            "54550E2F-4567-4C8F-BD2E-E54E9D0386B8/download"
        ),
        "datum": "wgs84",
        "adapter": adapt_shelter,
    },
    "fire": {
        "agency": "內政部消防署",
        "dataset": "data.gov.tw #5969 救援與應變單位點位（救援單位）",
        "url": (
            "https://opdadm.moi.gov.tw/api/v1/no-auth/resource/api/dataset/"
            "57F3DD1D-A40E-49A6-8410-57303B2FF87E/resource/"
            "C38B7AC2-E7F3-4DD5-A3F3-88E623B55924/download"
        ),
        "datum": "wgs84",
        "adapter": adapt_fire,
    },
    "police": {
        "agency": "內政部警政署",
        "dataset": "data.gov.tw #5958 各縣市警察分局暨派出所地址資料",
        "url": "https://www.tgos.tw/tgos/VirtualDir/Product/9927eb8a-efed-40c0-8bc4-83121ad6834a/1150528.zip",
        "fetch": "zip",
        "inner_prefix": "PoliceAddress",
        "datum": "twd97tm2→wgs84",
        "adapter": adapt_police,
    },
    # hospital / clinic（TGOS geocode，需 API key）後續接入。
}


def run(sources: list[str], out_path: Path) -> int:
    facilities: list[dict] = []
    used_sources: list[dict] = []
    audit_dropped: list[dict] = []  # 普查丟棄（縣市↔座標不符 = 來源座標錯）
    for key in sources:
        spec = SOURCES.get(key)
        if spec is None:
            print(f"[skip] 未知/未接來源：{key}", file=sys.stderr)
            continue
        print(f"[{key}] 下載 {spec['dataset']} …")
        raw = _curl(spec["url"])
        if spec.get("fetch") == "zip":
            rows = _rows_from_zip(raw, spec["inner_prefix"])
        else:
            rows = _rows(raw)
        adapted = [f for f in (spec["adapter"](r) for r in rows) if f is not None]
        # schema drift 防呆：有列卻全部解析失敗 = 來源欄位改名/格式變 → 中止（不靜默寫空 seed）
        if rows and not adapted:
            sys.exit(f"✗ [{key}] {len(rows)} 列全部解析失敗（0 筆）—— 疑似來源欄位改名/schema drift，中止")
        bad_coord = len(rows) - len(adapted)
        # 普查：縣市↔座標一致性。落在其縣市範圍外 → 丟棄並留痕（來源座標錯，如海上點）。
        kept = []
        for f in adapted:
            if county_coord_ok(f.get("county"), f["lat"], f["lng"]):
                kept.append(f)
            else:
                audit_dropped.append(
                    {
                        "name": f["name"],
                        "county": f.get("county"),
                        "lng": f["lng"],
                        "lat": f["lat"],
                    }
                )
        print(
            f"[{key}] {len(rows)} 列 → {len(kept)} 筆"
            f"（丟棄 {bad_coord} 座標無效/缺名 + {len(adapted) - len(kept)} 普查縣市↔座標不符）"
        )
        facilities.extend(kept)
        used_sources.append(
            {
                "key": key,
                "agency": spec["agency"],
                "dataset": spec["dataset"],
                "datum": spec["datum"],
                "count": len(kept),
            }
        )

    if audit_dropped:
        print(f"\n[普查] 縣市↔座標不符共丟棄 {len(audit_dropped)} 筆（來源座標疑誤），樣本：")
        for a in audit_dropped[:15]:
            print(f"  ✗ {a['name'][:26]}  縣市={a['county']}  lng={a['lng']} lat={a['lat']}")

    doc = {
        "_comment": (
            "P1-17 永久設施公開資料底圖層（唯讀基準，不隨演習；非 cop_entity、"
            "非 user-data、不受 exercise reset 影響）。授權：政府資料開放授權條款"
            "第1版（CC BY 4.0 相容）。來源皆台灣政府（非中國）。已剝除 PII。"
            "重建：command-dashboard/scripts/import_facilities.py。見 issue #88。"
        ),
        "license": "政府資料開放授權條款第1版 (CC BY 4.0 compatible)",
        "attribution": "資料來源：中華民國（台灣）政府開放資料",
        "sources": used_sources,
        "count": len(facilities),
        "facilities": facilities,
    }
    out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\n✓ 寫出 {len(facilities)} 筆設施 → {out_path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="P1-17 永久設施匯入")
    ap.add_argument("--source", help="逗號分隔來源（shelter,fire,…）")
    ap.add_argument("--all", action="store_true", help="全部已接來源")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="輸出路徑")
    args = ap.parse_args()

    if args.all:
        sources = list(SOURCES.keys())
    elif args.source:
        sources = [s.strip() for s in args.source.split(",") if s.strip()]
    else:
        ap.error("需 --all 或 --source")
    return run(sources, args.out)


if __name__ == "__main__":
    sys.exit(main())
