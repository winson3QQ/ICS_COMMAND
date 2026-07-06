# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""services/tak_mission_package.py — #509：TAK mission-package（data package）zip 解析（純邏輯）。

reality-check（2026-07-06 dogfood）：現場分享/廣播照片 = ATAK 把 **mission-package zip** 上 Enterprise
Sync、經 fileshare `b-f-t-r` 通告。zip 結構：
    MANIFEST/manifest.xml
    <uid>/<uid>.cot           ← `b-i-x-i` 影像 marker CoT（自帶 <point> 座標 + link 到父 marker）
    <hash>/<photo>.jpg        ← 照片本體
故現場照片＝**有座標的 b-i-x-i 影像 marker**（非孤立物）。

**安全（縱深，來源是不可信現場裝置）**：
- **zip-slip**：拒絕 entry 名含 `..` / 絕對路徑 / 反斜線 → 不解、跳過（防寫出 zip 外）。
- **image magic-byte**：只收真圖（JPEG/PNG/GIF/WebP），擋偽副檔名。
- **size 上限**：單圖 + 解壓總量封頂（防 zip bomb / 灌爆）。
- 純解析、不落地、不觸網（fetch 在 tak_files；ingest 在 cop_service）。
"""

from __future__ import annotations

import hashlib
import io
import zipfile

# 單圖上限 25MB、解壓總量上限 60MB、entry 數上限（防 zip bomb）。
_MAX_IMAGE_BYTES = 25 * 1024 * 1024
_MAX_TOTAL_BYTES = 60 * 1024 * 1024
_MAX_ENTRIES = 64

# 真圖 magic（與 routers/tak.py `_looks_like_image` 一致；WebP 需 RIFF....WEBP）。
_IMAGE_MAGIC = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a")
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp")


class MissionPackageError(Exception):
    """mission-package 解析失敗（非 zip / 損毀 / 超限 / 全空）。"""


def _read_entry(zf: zipfile.ZipFile, name: str) -> bytes:
    """讀 zip entry bytes；損毀/截斷/CRC 不符 entry 的解壓例外一律轉 MissionPackageError（不可信來源、
    best-effort：ZipFile 建構只讀 central directory，實際解壓錯在 read 期才拋，須在此收）。"""
    try:
        return zf.read(name)
    except Exception as exc:  # noqa: BLE001 — BadZipFile/zlib.error/EOFError… 一律轉本模組錯
        raise MissionPackageError(f"entry 解壓失敗 {name}：{exc}") from exc


def _looks_like_image(data: bytes) -> bool:
    if data.startswith(_IMAGE_MAGIC):
        return True
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def _safe_entry(name: str) -> bool:
    """zip-slip 防線：拒絕可逃出解壓根的 entry 名。"""
    if not name or name.startswith("/") or name.startswith("\\"):
        return False
    if ".." in name.replace("\\", "/").split("/"):
        return False
    return "\\" not in name  # TAK zip 用正斜線；反斜線一律拒（Windows 逃逸）


def parse_mission_package(zip_bytes: bytes) -> dict:
    """解 mission-package zip → {cot_xml: str|None, images: [{name, data, sha256, mimetype}]}。

    只抽第一個 `.cot`（影像 marker CoT）+ 所有真圖；MANIFEST 與其餘忽略。zip-slip/magic/size 全擋。
    非 zip / 損毀 / 全無有效內容 → raise MissionPackageError。
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise MissionPackageError(f"非 zip 或損毀：{exc}") from exc

    names = zf.namelist()
    if len(names) > _MAX_ENTRIES:
        raise MissionPackageError(f"entry 數超限（{len(names)} > {_MAX_ENTRIES}，防 zip bomb）")

    cot_xml: str | None = None
    images: list[dict] = []
    total = 0
    for name in names:
        if not _safe_entry(name):
            continue  # zip-slip → 跳過
        lower = name.lower()
        if lower.endswith("/") or "manifest" in lower:
            continue
        info = zf.getinfo(name)
        # 解壓前先看宣告大小（防 zip bomb 於 read 前）：單檔超上限即跳過。
        if info.file_size > _MAX_IMAGE_BYTES:
            continue
        if lower.endswith(".cot") and cot_xml is None:
            data = _read_entry(zf, name)
            total += len(data)
            if total > _MAX_TOTAL_BYTES:
                raise MissionPackageError("解壓總量超限（防 zip bomb）")
            cot_xml = data.decode("utf-8", "replace")
            continue
        if lower.endswith(_IMAGE_EXTS):
            data = _read_entry(zf, name)
            total += len(data)
            if total > _MAX_TOTAL_BYTES:
                raise MissionPackageError("解壓總量超限（防 zip bomb）")
            if not _looks_like_image(data):
                continue  # 偽副檔名（magic 不符）→ 跳過
            images.append(
                {
                    "name": name.rsplit("/", 1)[-1],
                    "data": data,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "mimetype": _mimetype_for(lower),
                }
            )
    if cot_xml is None and not images:
        raise MissionPackageError("mission-package 無有效 CoT / 圖片")
    return {"cot_xml": cot_xml, "images": images}


def _mimetype_for(lower_name: str) -> str:
    if lower_name.endswith(".png"):
        return "image/png"
    if lower_name.endswith(".gif"):
        return "image/gif"
    if lower_name.endswith(".webp"):
        return "image/webp"
    return "image/jpeg"
