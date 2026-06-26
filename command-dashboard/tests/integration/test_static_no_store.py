# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
tests/integration/test_static_no_store.py

鎖住：static JS 一律 Cache-Control: no-store（_NoCacheJsStaticFiles）。
根除「動態 import 的 ES module（cop_stream.js）被瀏覽器快取卡住、新舊 API 不匹配」
（issue #29 dogfood 撞到，連硬重整都繞不掉）。非 .js 靜態（css/字型）不受影響。
"""

from __future__ import annotations


def test_static_js_is_no_store(client):
    r = client.get("/static/js/main.js")
    assert r.status_code == 200, r.text
    assert r.headers.get("cache-control") == "no-store"


def test_dynamic_import_module_is_no_store(client):
    # cop_stream.js = 登入後動態 import 的模組（最容易被快取卡住的那個）
    r = client.get("/static/js/map/cop_stream.js")
    assert r.status_code == 200, r.text
    assert r.headers.get("cache-control") == "no-store"


def test_non_js_static_not_forced_no_store(client):
    # css 不應被強制 no-store（沿用預設快取，避免每次重抓字型/樣式）
    r = client.get("/static/css/ds-tokens.css")
    assert r.status_code == 200, r.text
    assert r.headers.get("cache-control") != "no-store"
