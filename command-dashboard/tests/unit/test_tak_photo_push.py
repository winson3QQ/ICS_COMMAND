# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""tests/unit/test_tak_photo_push.py — #509-P3 下行照片推送編排。

驗 push_photo_to_marker：取 marker → 重建 CoT → 打包 zip → 上傳（mock）→ 送 b-f-t-r（mock），
以及守門（marker 不存在 / 缺座標 / 未配置寫 cert）與點對點定址透傳。純編排、mock 觸網。
"""

from __future__ import annotations

import asyncio

import pytest

from services import tak_downlink, tak_files, tak_photo_push
from services.tak_mission_package import parse_mission_package

_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 40
_MARKER = {"uid": "MK-1", "type": "a-u-G", "lat": 24.77, "lon": 121.01, "callsign": "FIELD-1", "deleted": 0}


class _FakeClient:
    async def close(self):
        pass


def _wire(monkeypatch, *, entity=_MARKER, upload_hash="d" * 64):
    """把觸網點全 mock 掉；回 (sent_cots, uploaded, stored)。"""
    from core import config
    from repositories import cop_entity_repo
    from services import tak_attachments

    sent, uploaded, stored = [], [], []
    monkeypatch.setattr(config, "TAK_DEVICE_CONNECT_HOST", "10.13.13.1")
    monkeypatch.setattr(cop_entity_repo, "get_cop_entity", lambda uid: entity)
    monkeypatch.setattr(tak_files, "filestore_write_enabled", lambda: True)
    monkeypatch.setattr(tak_files, "_build_write_client", lambda: _FakeClient())

    async def _fake_upload(client, **kw):
        uploaded.append(kw)
        return {"Hash": upload_hash}

    async def _fake_send(cot):
        sent.append(cot)

    monkeypatch.setattr(tak_files, "upload_file", _fake_upload)
    monkeypatch.setattr(tak_downlink, "send_cot", _fake_send)
    monkeypatch.setattr(tak_attachments, "_store_attachment", lambda uid, img: stored.append((uid, img)) or True)
    return sent, uploaded, stored


def test_push_broadcast_happy_path(monkeypatch):
    sent, uploaded, stored = _wire(monkeypatch)
    res = asyncio.run(tak_photo_push.push_photo_to_marker(marker_uid="MK-1", photo_bytes=_JPEG, filename="scene.jpg"))
    assert res["zip_hash"] == "d" * 64 and res["dest"] == "broadcast"
    # 上傳的是 zip（keyword=missionpackage），且內含 marker CoT + 照片
    up = uploaded[0]
    assert up["keywords"] == "missionpackage" and up["filename"].endswith(".zip")
    pkg = parse_mission_package(up["content"])
    assert "MK-1" in pkg["cot_xml"] and pkg["images"][0]["data"] == _JPEG
    # 送出一則 b-f-t-r 指向上傳 hash、廣播（無 marti dest）
    assert len(sent) == 1 and "b-f-t-r" in sent[0]
    assert "d" * 64 in sent[0] and "<marti>" not in sent[0]
    # 照片也在 ICS COP 本地掛 marker（不靠 poll re-ingest）
    assert stored and stored[0][0] == "MK-1" and stored[0][1]["data"] == _JPEG


def test_push_point_to_point_dest(monkeypatch):
    sent, _, _ = _wire(monkeypatch)
    res = asyncio.run(
        tak_photo_push.push_photo_to_marker(
            marker_uid="MK-1", photo_bytes=_JPEG, filename="s.jpg", dest_callsigns=["3QQ-iTAK"]
        )
    )
    assert res["dest"] == ["3QQ-iTAK"]
    assert '<marti><dest callsign="3QQ-iTAK"/></marti>' in sent[0]


def test_push_marker_not_found_raises(monkeypatch):
    _wire(monkeypatch, entity=None)
    with pytest.raises(tak_photo_push.PhotoPushError):
        asyncio.run(tak_photo_push.push_photo_to_marker(marker_uid="X", photo_bytes=_JPEG, filename="p.jpg"))


def test_push_deleted_marker_raises(monkeypatch):
    _wire(monkeypatch, entity={**_MARKER, "deleted": 1})
    with pytest.raises(tak_photo_push.PhotoPushError):
        asyncio.run(tak_photo_push.push_photo_to_marker(marker_uid="MK-1", photo_bytes=_JPEG, filename="p.jpg"))


def test_push_marker_without_coords_raises(monkeypatch):
    _wire(monkeypatch, entity={"uid": "G1", "type": "u-d-f", "lat": None, "lon": None, "deleted": 0})
    with pytest.raises(tak_photo_push.PhotoPushError):
        asyncio.run(tak_photo_push.push_photo_to_marker(marker_uid="G1", photo_bytes=_JPEG, filename="p.jpg"))


def test_push_write_not_configured_raises(monkeypatch):
    _wire(monkeypatch)
    monkeypatch.setattr(tak_files, "filestore_write_enabled", lambda: False)
    with pytest.raises(tak_photo_push.PhotoPushError):
        asyncio.run(tak_photo_push.push_photo_to_marker(marker_uid="MK-1", photo_bytes=_JPEG, filename="p.jpg"))


def test_push_no_device_host_raises(monkeypatch):
    """review #2：缺 TAK_DEVICE_CONNECT_HOST → senderUrl 現場抓不到 → fail loud（非靜默 200）。"""
    from core import config

    _wire(monkeypatch)
    monkeypatch.setattr(config, "TAK_DEVICE_CONNECT_HOST", "")
    with pytest.raises(tak_photo_push.PhotoPushError):
        asyncio.run(tak_photo_push.push_photo_to_marker(marker_uid="MK-1", photo_bytes=_JPEG, filename="p.jpg"))
