# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""import_facilities adapters — P1-17 匯入正規化（issue #88）。

驗紅線/易錯邏輯（不碰網路）：
- PII 剝除（shelter 管理人姓名/電話不得進輸出）
- 消防欄名誤導（X座標_TWD97TM121 其實是 WGS84 經度，信值不信欄名）
- 座標範圍守門（台灣含外島；境外/缺值丟棄）
- 警政 TWD97 TM2 → WGS84（需 pyproj；無則 skip）
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# 從 scripts/ 路徑載入維護者腳本（非套件，用 file loader）。
_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "import_facilities.py"
_spec = importlib.util.spec_from_file_location("import_facilities", _SCRIPT)
imp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(imp)


def test_shelter_strips_pii_keeps_public_fields():
    row = {
        "避難收容處所名稱": "五峰活動中心",
        "避難收容處所地址": "新竹縣…",
        "經度": "121.073",
        "緯度": "24.386",
        "預計收容人數": "110",
        "適用災害類別": "水災,震災",
        "室內": "是",
        "室外": "否",
        "適合避難弱者安置": "是",
        "管理人姓名": "張某某",
        "管理人電話": "03-5851001",
    }
    f = imp.adapt_shelter(row)
    assert f["type"] == "shelter" and f["name"] == "五峰活動中心"
    assert f["lat"] == 24.386 and f["lng"] == 121.073
    assert f["extra"]["capacity"] == "110"
    # 🔴 PII 不得出現在任何欄位
    blob = repr(f)
    assert "張某某" not in blob and "管理人" not in blob


def test_fire_colname_misleading_value_is_wgs84():
    # 欄名寫 TWD97 但值是經緯度（121.x / 24.x）—— 信值不信欄名
    row = {
        "消防隊名稱": "七美分隊",
        "地址": "澎湖縣…",
        "聯絡電話": "0928",
        "X座標_TWD97TM121": "119.419144",
        "Y座標_TWD97TM121": "23.196575",
    }
    f = imp.adapt_fire(row)
    assert f["lng"] == 119.419144 and f["lat"] == 23.196575
    assert f["type"] == "fire"


def test_out_of_range_coords_dropped():
    # 境外座標（東京）應丟棄
    assert imp.adapt_fire({"消防隊名稱": "x", "X座標_TWD97TM121": "139.7", "Y座標_TWD97TM121": "35.6"}) is None
    # 缺座標丟棄
    assert imp.adapt_shelter({"避難收容處所名稱": "x", "經度": "", "緯度": ""}) is None


def test_kinmen_offshore_in_range():
    # 金門 118.2 必須通過（外島不可被範圍誤殺）
    row = {"避難收容處所名稱": "烈嶼辦公處", "避難收容處所地址": "東林24號", "經度": "118.248571", "緯度": "24.428328"}
    assert imp.adapt_shelter(row) is not None


def test_extract_county():
    assert imp.extract_county("高雄市大樹區") == "高雄市"
    assert imp.extract_county("臺北市中正區延平南路96號") == "臺北市"
    assert imp.extract_county("台中市西區") == "臺中市"  # 台↔臺 正規化
    assert imp.extract_county("無縣市字串") is None


def test_county_coord_audit_catches_source_error():
    # 姑山國小：縣市=高雄但經度 119.46（飄海上）→ 普查應判 False
    assert imp.county_coord_ok("高雄市", 22.702543, 119.464848) is False
    # 同筆若座標正確（大樹區 ~120.43）→ True
    assert imp.county_coord_ok("高雄市", 22.6964, 120.4321) is True
    # 無法判縣市 → 放行（不誤殺）
    assert imp.county_coord_ok(None, 24.0, 121.0) is True
    # 烏坵（金門縣 extra bbox）→ True
    assert imp.county_coord_ok("金門縣", 24.9887, 119.4532) is True


def test_shelter_includes_county():
    row = {"避難收容處所名稱": "姑山國小", "縣市及鄉鎮市區": "高雄市大樹區", "經度": "120.4321", "緯度": "22.6964"}
    assert imp.adapt_shelter(row)["county"] == "高雄市"


def test_police_twd97_to_wgs84():
    pytest.importorskip("pyproj")  # 警政轉換需 pyproj（維護者環境才有）
    # 台北市警局 TWD97 TM2 → 應得 ~121.51 / 25.04
    row = {
        "中文單位名稱": "臺北市政府警察局",
        "地址": "延平南路96號",
        "電話": "02",
        "POINT_X": "301442.6468",
        "POINT_Y": "2770733.688",
    }
    f = imp.adapt_police(row)
    assert abs(f["lng"] - 121.5098) < 0.001 and abs(f["lat"] - 25.0439) < 0.001
