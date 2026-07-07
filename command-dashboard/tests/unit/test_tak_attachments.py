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

    # #518：list 帶 direction/server_purgeable/local（此處未帶 pkg_hash/direction → None/False）。
    assert tak_attachments.list_local_attachments("MK-1") == [
        {
            "hash": _SHA_A,
            "name": "p.jpg",
            "mimeType": "image/jpeg",
            "direction": None,
            "server_purgeable": False,
            "local": True,
        }
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


def test_handle_fileshare_skips_ics_own_downlink(tmp_path, monkeypatch):
    """#509-P3 echo 防護：ICS 自己下行推的 b-f-t-r（senderUid=ICS-CMD）廣播回自己 → 不 re-ingest
    （不下載、不橋、不以重建 CoT 覆寫 marker）。"""
    from core import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(tak_files, "filestore_enabled", lambda: True)
    called = []

    async def _spy_dl(client, h, **kw):
        called.append(h)
        return None

    monkeypatch.setattr(tak_files, "download_content", _spy_dl)
    event = CoTEventIn(
        uid="FS-ICS-OWN",
        type="b-f-t-r",
        time="2026-06-05T04:00:00Z",
        start="2026-06-05T04:00:00Z",
        stale="2099-01-01T00:00:00Z",
        how="h-e",
        lat=0.0,
        lon=0.0,
        detail={"fileshare": {"sha256": "b" * 64, "senderUid": "ICS-CMD", "filename": "x.zip"}},
    )
    asyncio.run(tak_attachments.handle_fileshare(event))
    assert called == []  # 沒下載＝確定沒 re-ingest


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


# ── #509-P2：Enterprise Sync 主動輪詢橋 ────────────────────────────────────────────


def _fake_client():
    class _C:
        async def close(self):
            pass

    return _C()


def test_poll_bridges_missionpackage_skips_others_and_seen(tmp_path, monkeypatch):
    """輪詢：只橋帶 `missionpackage` keyword 者（跳過 ICS 自傳 #503 檔）；已見過的 hash 不重橋。"""
    from core import config
    from repositories.cop_entity_repo import get_cop_entity

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(tak_files, "_build_read_client", _fake_client)
    tak_attachments._seen_hashes.clear()

    seen_kw = {}

    async def _fake_search(client, **kw):
        seen_kw.update(kw)
        return [
            {"Hash": "c" * 64, "Keywords": ["missionpackage"], "Name": "field.zip", "Tool": "private"},
            {"Hash": "d" * 64, "Keywords": ["MK-99"], "Name": "ics-upload.jpg"},  # #503 自傳 → 跳過
        ]

    async def _fake_dl(client, h, **kw):
        return _mk_mission_package() if h == "c" * 64 else None

    monkeypatch.setattr(tak_files, "search_files", _fake_search)
    monkeypatch.setattr(tak_files, "download_content", _fake_dl)

    n = asyncio.run(tak_attachments.poll_filestore_once())
    assert n == 1  # 只橋 missionpackage 那筆（tool=private 也橋，見下）
    # 回歸：不得把搜尋限死 tool=public（真機 ATAK 分享落 tool=private，濾 public 會漏）。
    assert seen_kw.get("tool") != "public"
    assert get_cop_entity("IMG-MARKER-1") is not None
    assert len(tak_attachments.list_local_attachments("IMG-MARKER-1")) == 1

    # 第二輪：兩個 hash 都已 seen → 不重橋、不重複附件
    n2 = asyncio.run(tak_attachments.poll_filestore_once())
    assert n2 == 0
    assert len(tak_attachments.list_local_attachments("IMG-MARKER-1")) == 1


def test_poll_skips_ics_own_uploads(tmp_path, monkeypatch):
    """#509-P3：ICS 自己下行推的 zip（creatorUid=ICS-CMD）→ 輪詢跳過（不自我 re-ingest 覆寫 marker）。"""
    from core import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(tak_files, "_build_read_client", _fake_client)
    tak_attachments._seen_hashes.clear()

    dl_calls = []

    async def _fake_search(client, **kw):
        return [{"Hash": "c" * 64, "Keywords": ["missionpackage"], "Name": "ics.zip", "CreatorUid": "ICS-CMD"}]

    async def _spy_bridge(client, h):
        dl_calls.append(h)
        return "M"

    monkeypatch.setattr(tak_files, "search_files", _fake_search)
    monkeypatch.setattr(tak_attachments, "_bridge_package_by_hash", _spy_bridge)

    assert asyncio.run(tak_attachments.poll_filestore_once()) == 0  # ICS 自傳 → 不橋
    assert dl_calls == []  # 根本沒進 _bridge
    assert ("c" * 64) in tak_attachments._seen_hashes  # 標 seen 不重掃


def test_bridge_attaches_even_when_package_cot_older(tmp_path, monkeypatch):
    """時序差異：live marker 較新 → ingest 回 None，但 marker 仍在 → 照片照樣掛（poll 關鍵韌性）。"""
    from core import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    # 先 ingest 一個較「新」的同 uid marker（package 內 CoT time=2026-06-05，故此為更新）
    newer = CoTEventIn(
        uid="IMG-MARKER-1",
        type="a-u-G",
        time="2027-01-01T00:00:00Z",
        start="2027-01-01T00:00:00Z",
        stale="2099-01-01T00:00:00Z",
        how="m-g",
        lat=24.7,
        lon=121.0,
    )
    asyncio.run(cop_service.ingest_cot_event(newer))

    async def _fake_dl(client, h, **kw):
        return _mk_mission_package()

    monkeypatch.setattr(tak_files, "download_content", _fake_dl)
    res = asyncio.run(tak_attachments._bridge_package_by_hash(_fake_client(), "e" * 64))
    assert res == "IMG-MARKER-1"  # ingest no-op（舊）但 marker 存在 → 仍回 marker
    assert len(tak_attachments.list_local_attachments("IMG-MARKER-1")) == 1


def test_poll_transient_download_error_not_marked_seen(tmp_path, monkeypatch):
    """下載層錯（網路/HTTP）→ 不標 seen，下一輪會重試（現場照片不因暫斷永久漏）。"""
    from core import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(tak_files, "_build_read_client", _fake_client)
    tak_attachments._seen_hashes.clear()

    async def _fake_search(client, **kw):
        return [{"Hash": "c" * 64, "Keywords": ["missionpackage"], "Name": "field.zip"}]

    calls = {"n": 0}

    async def _flaky_dl(client, h, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient network")
        return _mk_mission_package()

    monkeypatch.setattr(tak_files, "search_files", _fake_search)
    monkeypatch.setattr(tak_files, "download_content", _flaky_dl)

    assert asyncio.run(tak_attachments.poll_filestore_once()) == 0  # 首輪下載炸 → 未橋、未標 seen
    assert ("c" * 64) not in tak_attachments._seen_hashes
    assert asyncio.run(tak_attachments.poll_filestore_once()) == 1  # 次輪重試成功


def test_handle_fileshare_transient_download_not_marked_seen(tmp_path, monkeypatch):
    """announce 路徑下載暫斷 → 不標 seen，讓主動輪詢仍能後備（review S1 回歸）。"""
    from core import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(tak_files, "filestore_enabled", lambda: True)
    monkeypatch.setattr(tak_files, "_build_read_client", _fake_client)
    tak_attachments._seen_hashes.clear()

    async def _boom_dl(client, h, **kw):
        raise RuntimeError("transient")

    monkeypatch.setattr(tak_files, "download_content", _boom_dl)
    event = CoTEventIn(
        uid="FS-3",
        type="b-f-t-r",
        time="2026-06-05T04:00:00Z",
        start="2026-06-05T04:00:00Z",
        stale="2099-01-01T00:00:00Z",
        how="h-e",
        lat=0.0,
        lon=0.0,
        detail={"fileshare": {"sha256": "c" * 64, "filename": "x.zip"}},
    )
    asyncio.run(tak_attachments.handle_fileshare(event))  # 不拋
    assert ("c" * 64) not in tak_attachments._seen_hashes  # 暫斷未標 seen → 輪詢仍會撿


# ── #518：方向感知刪除（L1 本地 + L2 server）+ 墓碑擋輪詢復活 ─────────────────────────

_SHA_PKG = "c" * 64  # 假 mission-package zip hash（L2 刪除目標 + 墓碑鍵）


def _store_uplink(tmp_path, monkeypatch, marker="MK-1", sha=_SHA_A, pkg=_SHA_PKG):
    """存一張上行附件（帶 pkg_hash + direction）供刪除測試。回附件 sha。"""
    from core import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    _ingest_marker(marker)
    img = {"name": "p.jpg", "data": _JPEG, "sha256": sha, "mimetype": "image/jpeg"}
    tak_attachments._store_attachment(marker, img, pkg_hash=pkg, direction="uplink")
    return sha


def test_delete_attachment_l1_only(tmp_path, monkeypatch):
    """L1：刪連結 + 本地檔 + 立墓碑；未要求 purge → 不碰 server（server_purged=None）。"""
    sha = _store_uplink(tmp_path, monkeypatch)
    assert (tmp_path / "tak_attachments" / sha).exists()

    res = asyncio.run(tak_attachments.delete_attachment(sha, actor="cmd", purge_server=False))
    assert res["local_deleted"] is True
    assert res["direction"] == "uplink"
    assert res["server_purged"] is None  # 未嘗試
    assert not (tmp_path / "tak_attachments" / sha).exists()  # 本地檔已刪
    assert tak_attachments.list_local_attachments("MK-1") == []  # 連結已移除
    from repositories import cop_entity_repo

    assert cop_entity_repo.is_pkg_tombstoned(_SHA_PKG)  # 墓碑已立


def test_delete_attachment_l2_purges_server(tmp_path, monkeypatch):
    """L2：purge_server=True → 呼叫 tak_files.delete_file(pkg_hash)，server_purged=True。"""
    sha = _store_uplink(tmp_path, monkeypatch)
    monkeypatch.setattr(tak_files, "filestore_write_enabled", lambda: True)
    monkeypatch.setattr(tak_files, "_build_write_client", _fake_client)
    deleted = []

    async def _fake_delete(client, h):
        deleted.append(h)
        return True

    monkeypatch.setattr(tak_files, "delete_file", _fake_delete)

    res = asyncio.run(tak_attachments.delete_attachment(sha, actor="cmd", purge_server=True))
    assert res["server_purged"] is True
    assert deleted == [_SHA_PKG]  # 刪的是所屬 zip hash（非影像 sha）


def test_delete_attachment_l2_best_effort_on_error(tmp_path, monkeypatch):
    """L2 best-effort：server 刪失敗不推翻 L1——本地仍刪、墓碑仍立、狀態分開回報。"""
    sha = _store_uplink(tmp_path, monkeypatch)
    monkeypatch.setattr(tak_files, "filestore_write_enabled", lambda: True)
    monkeypatch.setattr(tak_files, "_build_write_client", _fake_client)

    async def _boom(client, h):
        raise tak_files.TakFilestoreError("server 拒絕")

    monkeypatch.setattr(tak_files, "delete_file", _boom)

    res = asyncio.run(tak_attachments.delete_attachment(sha, actor="cmd", purge_server=True))
    assert res["local_deleted"] is True  # L1 成立
    assert res["server_purged"] is False and res["server_error"]  # L2 失敗、狀態回報
    assert not (tmp_path / "tak_attachments" / sha).exists()
    from repositories import cop_entity_repo

    assert cop_entity_repo.is_pkg_tombstoned(_SHA_PKG)  # 墓碑仍立（輪詢不復活）


def test_delete_then_poll_does_not_resurrect(tmp_path, monkeypatch):
    """核心：刪除後即使 ICS 重啟（_seen_hashes 清空），輪詢也不把該包重橋回來（墓碑擋復活）。"""
    from core import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(tak_files, "_build_read_client", _fake_client)
    tak_attachments._seen_hashes.clear()

    async def _fake_search(client, **kw):
        return [{"Hash": _SHA_PKG, "Keywords": ["missionpackage"], "Name": "field.zip"}]

    async def _fake_dl(client, h, **kw):
        return _mk_mission_package() if h == _SHA_PKG else None

    monkeypatch.setattr(tak_files, "search_files", _fake_search)
    monkeypatch.setattr(tak_files, "download_content", _fake_dl)

    # 第一輪：橋進來，附件掛上 IMG-MARKER-1
    assert asyncio.run(tak_attachments.poll_filestore_once()) == 1
    atts = tak_attachments.list_local_attachments("IMG-MARKER-1")
    assert len(atts) == 1 and atts[0]["direction"] == "uplink" and atts[0]["server_purgeable"] is True

    # 刪除（L1，立墓碑）
    res = asyncio.run(tak_attachments.delete_attachment(atts[0]["hash"], actor="cmd", purge_server=False))
    assert res["local_deleted"] is True
    assert tak_attachments.list_local_attachments("IMG-MARKER-1") == []

    # 模擬重啟：清 in-memory seen → 再輪詢。墓碑擋 → 不重橋、附件不復活。
    tak_attachments._seen_hashes.clear()
    assert asyncio.run(tak_attachments.poll_filestore_once()) == 0
    assert tak_attachments.list_local_attachments("IMG-MARKER-1") == []


def test_delete_attachment_unknown_hash(tmp_path, monkeypatch):
    """查無本地附件 → local_deleted=False（router 據此回 404），不拋。"""
    from core import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    res = asyncio.run(tak_attachments.delete_attachment("f" * 64, actor="cmd", purge_server=True))
    assert res["local_deleted"] is False
    assert res["server_purged"] is None  # 沒附件 → 連 L2 都不嘗試


def test_delete_marker_scoped_leaves_other_marker(tmp_path, monkeypatch):
    """faction 邊界：同一張圖掛在 MK-A、MK-B 兩 marker 上，帶 marker_uid 刪 MK-A → 只移 MK-A 連結，
    MK-B 連結與本地檔皆保留（不波及另一〔可能不可見〕marker）。"""
    from core import config
    from repositories import cop_entity_repo

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    _ingest_marker("MK-A")
    _ingest_marker("MK-B")
    img = {"name": "p.jpg", "data": _JPEG, "sha256": _SHA_A, "mimetype": "image/jpeg"}
    tak_attachments._store_attachment("MK-A", img, pkg_hash=_SHA_PKG, direction="uplink")
    tak_attachments._store_attachment("MK-B", img, pkg_hash=_SHA_PKG, direction="uplink")

    res = asyncio.run(tak_attachments.delete_attachment(_SHA_A, marker_uid="MK-A", actor="cmd", purge_server=False))
    assert res["local_deleted"] is True and res["marker_uid"] == "MK-A"
    assert tak_attachments.list_local_attachments("MK-A") == []  # MK-A 連結移除
    assert len(tak_attachments.list_local_attachments("MK-B")) == 1  # MK-B 連結保留
    assert (tmp_path / "tak_attachments" / _SHA_A).exists()  # 仍有 marker 連 → 本地檔保留
    assert cop_entity_repo.is_pkg_tombstoned(_SHA_PKG)  # 墓碑仍立（該 zip 已被指揮層刪意圖）


def test_delete_then_passive_fileshare_no_resurrect(tmp_path, monkeypatch):
    """被動路徑亦不復活：刪除立墓碑後，iTAK 重廣播 b-f-t-r → handle_fileshare 不重橋（墓碑守門在共用橋接）。"""
    from core import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(tak_files, "_build_read_client", _fake_client)
    monkeypatch.setattr(tak_files, "filestore_enabled", lambda: True)

    dl_calls = []

    async def _fake_dl(client, h, **kw):
        dl_calls.append(h)
        return _mk_mission_package()

    monkeypatch.setattr(tak_files, "download_content", _fake_dl)

    # 先橋一次（被動）：現場照片進來
    from schemas.tak import CoTEventIn

    def _fileshare_event():
        return CoTEventIn(
            uid="FS-1",
            type="b-f-t-r",
            time="2026-06-05T04:00:00Z",
            start="2026-06-05T04:00:00Z",
            stale="2099-01-01T00:00:00Z",
            how="h-e",
            lat=0.0,
            lon=0.0,
            detail={"fileshare": {"sha256": _SHA_PKG, "filename": "field.zip"}},
        )

    asyncio.run(tak_attachments.handle_fileshare(_fileshare_event()))
    atts = tak_attachments.list_local_attachments("IMG-MARKER-1")
    assert len(atts) == 1
    assert dl_calls == [_SHA_PKG]  # 首次有下載

    # 刪除（立墓碑）
    asyncio.run(tak_attachments.delete_attachment(atts[0]["hash"], marker_uid="IMG-MARKER-1", actor="cmd"))
    assert tak_attachments.list_local_attachments("IMG-MARKER-1") == []

    # 被動重廣播同一 zip：墓碑守門在 _bridge → 根本不下載、不重橋
    dl_calls.clear()
    tak_attachments._seen_hashes.clear()  # 模擬重啟：in-memory seen 清空
    asyncio.run(tak_attachments.handle_fileshare(_fileshare_event()))
    assert dl_calls == []  # 墓碑擋在下載前
    assert tak_attachments.list_local_attachments("IMG-MARKER-1") == []  # 不復活
