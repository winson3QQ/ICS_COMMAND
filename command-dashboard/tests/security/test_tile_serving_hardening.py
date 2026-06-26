# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""tile 服務硬化（#64-1 dead exempt 移除 / #64-2 路徑穿越縱深）。

來源：P1-10c review（#62）的 pre-existing LOW。tile 路由在 /tiles/...（非 /api/），
auth_middleware 只 gate /api/* → tiles 本就不需 exempt；舊 /api/map/tiles/ 為 dead 條目。
serve_pmtiles 原靠 str path-converter 不含 '/' 擋穿越（慣例），加 resolve() 容器內檢查為縱深。
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from core.config import AUTH_EXEMPT_PREFIXES
from routers.map import serve_pmtiles


def test_dead_tile_exempt_prefix_removed():
    # #64-1：/api/map/tiles/ match 不到任何路由（實際為 /tiles/），dead/誤導 → 已移除。
    assert "/api/map/tiles/" not in AUTH_EXEMPT_PREFIXES
    # /static/ 仍需保留（serve 前端靜態資源）
    assert "/static/" in AUTH_EXEMPT_PREFIXES


@pytest.mark.parametrize(
    "bad",
    [
        "../../../etc/passwd.pmtiles",
        "../secret.pmtiles",
        "..%2f..%2fsecret.pmtiles".replace("%2f", "/"),  # 解碼後含 '/'，模擬繞過
    ],
)
def test_serve_pmtiles_rejects_path_traversal(bad):
    # #64-2：resolve() 容器檢查擋下逃出 MBTILES_DIR 的路徑 → 404（不洩漏外部檔）。
    with pytest.raises(HTTPException) as ei:
        serve_pmtiles(bad, request=None)  # 穿越在使用 request 前就 raise，故 request=None 可
    assert ei.value.status_code == 404


def test_serve_pmtiles_404_for_missing_in_container():
    # 容器內但不存在 / 非 .pmtiles → 404
    with pytest.raises(HTTPException) as ei:
        serve_pmtiles("nonexistent.pmtiles", request=None)
    assert ei.value.status_code == 404
    with pytest.raises(HTTPException) as ei2:
        serve_pmtiles("evil.txt", request=None)
    assert ei2.value.status_code == 404
