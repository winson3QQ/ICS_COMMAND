# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""facilities_store — P1-17 永久設施唯讀讀取（issue #88）。

驗：有效 seed → 原樣回；缺檔 / 非 dict / 壞 JSON → 最小空殼（不 raise）；快取以 (path,mtime) 鍵。
"""

from __future__ import annotations

import json

import pytest

from services import facilities_store


@pytest.fixture(autouse=True)
def _reset_cache():
    facilities_store._cache = None
    yield
    facilities_store._cache = None


def _seed(tmp_path, data):
    p = tmp_path / "facilities.seed.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def test_read_valid(tmp_path):
    p = _seed(
        tmp_path,
        {
            "count": 1,
            "sources": [{"key": "fire", "count": 1}],
            "facilities": [{"type": "fire", "name": "七美分隊", "lat": 23.19, "lng": 119.41}],
        },
    )
    d = facilities_store.read(seed=p)
    assert d["count"] == 1
    assert d["facilities"][0]["name"] == "七美分隊"


def test_missing_seed_returns_empty_shell(tmp_path):
    d = facilities_store.read(seed=tmp_path / "nope.json")
    assert d["facilities"] == [] and d["count"] == 0


def test_non_dict_returns_empty_shell(tmp_path):
    p = tmp_path / "f.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    assert facilities_store.read(seed=p)["facilities"] == []


def test_broken_json_returns_empty_shell(tmp_path):
    p = tmp_path / "f.json"
    p.write_text("{not json", encoding="utf-8")
    assert facilities_store.read(seed=p)["facilities"] == []


def test_cache_hit_same_object(tmp_path):
    p = _seed(tmp_path, {"count": 0, "facilities": []})
    a = facilities_store.read(seed=p)
    b = facilities_store.read(seed=p)
    assert a is b  # 同檔同 mtime → 回快取物件
