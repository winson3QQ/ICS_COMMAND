# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""tests/unit/test_tak_attachments.py — #509：現場照片附件 handler + 本地存 + 服務。

驗：_store_attachment（寫檔+連結+dedup）、list/owner/read 服務 helpers、handle_fileshare 端到端
（mock 抓檔+ingest → 解 mission-package → ingest b-i-x-i marker → 存照片掛 marker）。
"""

import asyncio
import io
import zipfile

import pytest

from schemas.tak import CoTEventIn
from services import cop_service, tak_attachments, tak_files


@pytest.fixture(autouse=True)
def _db(tmp_db):
    """走真 DB（cop_entities / cop_entity_links / exercises），需 init 過的 tmp DB。"""
    yield


_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
_SHA_A = "a" * 64
_BIXI_COT = (
    "<event version='2.0' uid='IMG-MARKER-1' type='b-i-x-i' time='2026-06-05T04:00:00Z' "
    "start='2026-06-05T04:00:00Z' stale='2099-01-01T00:00:00Z' how='h-g-i-g-o'>"
    "<point lat='24.77' lon='121.01' hae='0' ce='9999999' le='9999999'/>"
    "<detail><contact callsign='FIELD-1'/></detail></event>"
)


def _mk_mission_package() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("MANIFEST/manifest.xml", "<MissionPackageManifest/>")
        z.writestr("IMG-MARKER-1/IMG-MARKER-1.cot", _BIXI_COT)
        z.writestr("hash/20260605.jpg", _JPEG)
    return buf.getvalue()


def _ingest_marker(uid="MK-1"):
    ev = CoTEventIn(
        uid=uid,
        type="a-f-G",
        time="2026-06-05T04:00:00Z",
        start="2026-06-05T04:00:00Z",
        stale="2099-01-01T00:00:00Z",
        how="m-g",
        lat=24.1,
        lon=120.6,
    )
    return asyncio.run(cop_service.ingest_cot_event(ev))


def test_store_dedup_list_owner_read(tmp_path, monkeypatch):
    from core import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    _ingest_marker("MK-1")  # marker（連結 src）
    img = {"name": "p.jpg", "data": _JPEG, "sha256": _SHA_A, "mimetype": "image/jpeg"}

    assert tak_attachments._store_attachment("MK-1", img) is True
    assert tak_attachments._store_attachment("MK-1", img) is False  # dedup 同 (marker,sha)
    assert (tmp_path / "tak_attachments" / _SHA_A).exists()  # 檔名=sha256

    assert tak_attachments.list_local_attachments("MK-1") == [
        {"hash": _SHA_A, "name": "p.jpg", "mimeType": "image/jpeg"}
    ]
    assert tak_attachments.local_attachment_owner(_SHA_A) == "MK-1"
    got = tak_attachments.read_local_attachment(_SHA_A)
    assert got == (_JPEG, "image/jpeg")


def test_owner_none_for_unknown_or_bad_hash():
    assert tak_attachments.local_attachment_owner("f" * 64) is None
    assert tak_attachments.local_attachment_owner("not-a-hash") is None
    assert tak_attachments.read_local_attachment("f" * 64) is None


def test_handle_fileshare_end_to_end(tmp_path, monkeypatch):
    """b-f-t-r → 抓 zip → 解 → ingest b-i-x-i marker → 存照片掛該 marker。"""
    from core import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(tak_files, "filestore_enabled", lambda: True)

    class _FakeClient:
        async def close(self):
            pass

    monkeypatch.setattr(tak_files, "_build_read_client", lambda: _FakeClient())

    async def _fake_dl(client, h, **kw):
        return _mk_mission_package()

    monkeypatch.setattr(tak_files, "download_content", _fake_dl)

    # 真 ingest（b-i-x-i 經 #508 分流器 → 存為 entity）
    event = CoTEventIn(
        uid="FS-EVENT-1",
        type="b-f-t-r",
        time="2026-06-05T04:00:00Z",
        start="2026-06-05T04:00:00Z",
        stale="2099-01-01T00:00:00Z",
        how="h-e",
        lat=0.0,
        lon=0.0,
        detail={"fileshare": {"sha256": "b" * 64, "filename": "x.zip"}},
    )
    asyncio.run(tak_attachments.handle_fileshare(event))

    # b-i-x-i marker 上圖（ingest 進 cop_entities）
    from repositories.cop_entity_repo import get_cop_entity

    marker = get_cop_entity("IMG-MARKER-1")
    assert marker is not None and marker["type"] == "b-i-x-i"
    # 照片存本地並掛該 marker
    atts = tak_attachments.list_local_attachments("IMG-MARKER-1")
    assert len(atts) == 1 and atts[0]["mimeType"] == "image/jpeg"


def test_handle_fileshare_no_sha_skips(tmp_path, monkeypatch):
    from core import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    event = CoTEventIn(
        uid="FS-2",
        type="b-f-t-r",
        time="2026-06-05T04:00:00Z",
        start="2026-06-05T04:00:00Z",
        stale="2099-01-01T00:00:00Z",
        how="h-e",
        lat=0.0,
        lon=0.0,
        detail={},  # 無 fileshare
    )
    asyncio.run(tak_attachments.handle_fileshare(event))  # 不拋、no-op
