# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""tests/unit/test_tak_mission_package.py — #509：mission-package zip 解析（純邏輯 + 安全）。

驗：真結構（MANIFEST + b-i-x-i .cot + .jpg）抽出 cot_xml + 圖；zip-slip 跳過；偽副檔名（magic 不符）
跳過；非 zip / 全空 raise；oversized 跳過。
"""

import io
import zipfile

import pytest

from services import tak_mission_package as mp

_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64  # 真 JPEG magic
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
_COT = (
    "<event version='2.0' uid='7283d28d' type='b-i-x-i' how='h-g-i-g-o'>"
    "<point lat='24.77' lon='121.01'/><detail><contact callsign='X'/></detail></event>"
)
_MANIFEST = '<?xml version="1.0"?><MissionPackageManifest version="2"><Contents/></MissionPackageManifest>'


def _mk_zip(entries: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_parses_real_structure():
    """MANIFEST + <uid>/<uid>.cot（b-i-x-i）+ <hash>/photo.jpg → 抽 cot_xml + 圖。"""
    z = _mk_zip(
        {
            "MANIFEST/manifest.xml": _MANIFEST,
            "7283d28d/7283d28d.cot": _COT,
            "abc123/20260706.jpg": _JPEG,
        }
    )
    out = mp.parse_mission_package(z)
    assert out["cot_xml"] is not None and "b-i-x-i" in out["cot_xml"]
    assert len(out["images"]) == 1
    img = out["images"][0]
    assert img["name"] == "20260706.jpg" and img["mimetype"] == "image/jpeg"
    assert img["data"] == _JPEG and len(img["sha256"]) == 64


def test_png_mimetype():
    out = mp.parse_mission_package(_mk_zip({"a/x.png": _PNG}))
    assert out["images"][0]["mimetype"] == "image/png"


def test_safe_entry_rejects_traversal():
    """zip-slip 縱深防線 _safe_entry：.. / 絕對路徑 / 反斜線 一律 False（實際 zip.read 走記憶體、
    entry 名不落磁碟已使 zip-slip moot，此為額外防禦）。"""
    assert mp._safe_entry("ok/good.jpg") is True
    assert mp._safe_entry("a/b/c.jpg") is True
    assert mp._safe_entry("../evil.jpg") is False
    assert mp._safe_entry("x/../y.jpg") is False
    assert mp._safe_entry("/abs/evil.jpg") is False
    assert mp._safe_entry("a\\b\\evil.jpg") is False
    assert mp._safe_entry("") is False


def test_forced_traversal_entry_skipped():
    """以 ZipInfo 強塞含 .. 的 entry（繞過 writestr 正規化）→ parse 跳過、只留安全圖。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(zipfile.ZipInfo("../../evil.jpg"), _JPEG)
        z.writestr("ok/good.jpg", _JPEG)
    out = mp.parse_mission_package(buf.getvalue())
    assert [i["name"] for i in out["images"]] == ["good.jpg"]


def test_fake_image_magic_mismatch_skipped():
    """副檔名 .jpg 但內容非圖（magic 不符）→ 跳過（擋偽裝）。"""
    z = _mk_zip({"a/fake.jpg": b"<html>not an image</html>", "a/real.jpg": _JPEG})
    out = mp.parse_mission_package(z)
    assert [i["name"] for i in out["images"]] == ["real.jpg"]


def test_oversized_image_skipped():
    big = _mk_zip({"a/huge.jpg": _JPEG + b"\x00" * (mp._MAX_IMAGE_BYTES + 1)})
    # 宣告大小超 _MAX_IMAGE_BYTES → 跳過；無其他內容 → 全空 raise
    with pytest.raises(mp.MissionPackageError):
        mp.parse_mission_package(big)


def test_non_zip_raises():
    with pytest.raises(mp.MissionPackageError):
        mp.parse_mission_package(b"not a zip at all")


def test_empty_package_raises():
    with pytest.raises(mp.MissionPackageError):
        mp.parse_mission_package(_mk_zip({"MANIFEST/manifest.xml": _MANIFEST}))


def test_too_many_entries_raises():
    with pytest.raises(mp.MissionPackageError):
        mp.parse_mission_package(_mk_zip({f"a/{i}.txt": b"x" for i in range(mp._MAX_ENTRIES + 1)}))
