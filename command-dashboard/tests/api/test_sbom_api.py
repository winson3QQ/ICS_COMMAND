# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""api/test_sbom_api.py — GET /api/sbom（#419）

驗：
  - RBAC：observer（READ_ROLES 最低權）可達（非 403）；未登入 → 401。
  - 無 SBOM 檔 → 404（dev / 非 release build）；有檔 → 200 + CycloneDX content-type + 內容。

RBAC 分類本身另由 tests/unit/test_rbac_route_matrix.py（golden + #370 default-deny 守門）鎖。
"""

import json

import pytest

pytestmark = pytest.mark.api


def test_sbom_404_when_absent(client, observer_auth, monkeypatch, tmp_path):
    """非 release build：SBOM 檔不存在 → 404（graceful，非 500）。observer 可達（非 403）。"""
    from routers import dashboard

    monkeypatch.setattr(dashboard, "SBOM_PATH", tmp_path / "nope.cdx.json")
    r = client.get("/api/sbom", headers=observer_auth)
    assert r.status_code == 404


def test_sbom_200_for_observer_when_present(client, observer_auth, monkeypatch, tmp_path):
    """有 SBOM：observer（READ_ROLES）→ 200 + CycloneDX media type + 可解析內容。"""
    f = tmp_path / "current.cdx.json"
    f.write_text(
        json.dumps({"bomFormat": "CycloneDX", "specVersion": "1.6", "components": []}),
        encoding="utf-8",
    )
    from routers import dashboard

    monkeypatch.setattr(dashboard, "SBOM_PATH", f)
    r = client.get("/api/sbom", headers=observer_auth)
    assert r.status_code == 200
    assert "cyclonedx" in r.headers["content-type"]
    assert r.json()["bomFormat"] == "CycloneDX"


def test_sbom_requires_auth(client, monkeypatch, tmp_path):
    """未登入 → 401（/api/sbom 不在未認證 allowlist，不同於 /api/version）。"""
    from routers import dashboard

    monkeypatch.setattr(dashboard, "SBOM_PATH", tmp_path / "x.cdx.json")
    r = client.get("/api/sbom")
    assert r.status_code == 401
