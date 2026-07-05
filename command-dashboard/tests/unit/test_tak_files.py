# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
tests/unit/test_tak_files.py — #503：TAK file store client（ICS↔TAK 圖片交換底層）

鎖住的不變式：
- hash 驗證：非 SHA-256 hex（含 path 遍歷 `../`）一律 raise，且**在打 client 前**擋掉（安全）
- search_files：None/空 filter 自動略去；回應結構跨版本容錯（{results}/裸 list/垃圾→[]）
- download_file / get_file_metadata：合法 hash 才打；hash 進 path 前已驗
- extract_hash/name/mimetype：跨版本大小寫欄位容錯
- upload_file：留樁——明確拋（待真機定 legacy servlet 格式，不猜）
- filestore_enabled：Marti URL + 讀 cert 缺任一則停用
"""

import asyncio

import pytest

from services import tak_files
from services.tak_files import TakFilestoreError


def _run(coro):
    return asyncio.run(coro)


class _FakeClient:
    """記錄呼叫的 fake Marti client：get_json / get_bytes 回預設值並記 path/params。"""

    def __init__(self, *, json_ret=None, bytes_ret=None):
        self._json_ret = json_ret
        self._bytes_ret = bytes_ret
        self.calls = []

    async def get_json(self, path, params=None):
        self.calls.append(("get_json", path, params))
        return self._json_ret

    async def get_bytes(self, path, params=None, *, max_bytes=None):
        self.calls.append(("get_bytes", path, params))
        return self._bytes_ret


_VALID = "a" * 64  # 合法 SHA-256 hex


# ── hash 驗證（安全核心）──────────────────────────────────────────────────


def test_is_valid_hash_accepts_sha256():
    assert tak_files.is_valid_hash(_VALID)
    assert tak_files.is_valid_hash("0123456789abcdef" * 4)
    assert tak_files.is_valid_hash("ABCDEF" + "0" * 58)  # 大寫 hex 也收


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "xyz",
        "a" * 63,  # 太短
        "a" * 65,  # 太長
        "g" * 64,  # 非 hex
        "../../../etc/passwd",  # path 遍歷
        "a" * 32 + "/../secret",  # 夾帶遍歷
        "deadbeef/metadata",  # 夾帶 path 段
    ],
)
def test_is_valid_hash_rejects_bad(bad):
    assert not tak_files.is_valid_hash(bad)


def test_download_rejects_traversal_hash_before_calling_client():
    """path 遍歷 hash → 在打 client **前**就拋（client 完全沒被呼叫）＝安全不變式。"""
    client = _FakeClient(bytes_ret=b"x")
    with pytest.raises(TakFilestoreError, match="非法檔案 hash"):
        _run(tak_files.download_file(client, "../../../etc/passwd"))
    assert client.calls == []  # 完全沒打出去


def test_metadata_rejects_bad_hash_before_calling_client():
    client = _FakeClient(json_ret={"x": 1})
    with pytest.raises(TakFilestoreError, match="非法檔案 hash"):
        _run(tak_files.get_file_metadata(client, "not-a-hash"))
    assert client.calls == []


# ── download / metadata 正常路徑 ─────────────────────────────────────────


def test_download_valid_hash_hits_correct_path():
    client = _FakeClient(bytes_ret=b"\x89PNG")
    out = _run(tak_files.download_file(client, _VALID))
    assert out == b"\x89PNG"
    assert client.calls == [("get_bytes", f"/Marti/api/files/{_VALID}", None)]


def test_metadata_valid_hash_returns_dict():
    client = _FakeClient(json_ret={"Name": "photo.jpg", "Hash": _VALID})
    out = _run(tak_files.get_file_metadata(client, _VALID))
    assert out["Name"] == "photo.jpg"
    assert client.calls[0][1] == f"/Marti/api/files/{_VALID}/metadata"


def test_metadata_non_dict_returns_none():
    client = _FakeClient(json_ret=["unexpected"])
    assert _run(tak_files.get_file_metadata(client, _VALID)) is None


# ── search filter + 回應解析 ─────────────────────────────────────────────


def test_search_drops_none_and_empty_filters():
    client = _FakeClient(json_ret={"results": []})
    _run(tak_files.search_files(client, uid="U1", keyword=None, mimetype="", mission="M"))
    _, path, params = client.calls[0]
    assert path == "/Marti/api/sync/search"
    assert params == {"uid": "U1", "mission": "M"}  # None/"" 略去


def test_search_no_filters_passes_none():
    client = _FakeClient(json_ret={"results": []})
    _run(tak_files.search_files(client))
    assert client.calls[0][2] is None  # 無 filter → params=None


def test_search_parses_data_wrapper_5_7():
    # TAK 5.7 契約：ApiResponseNavigableSetResource → 結果在 data（非 results）
    client = _FakeClient(
        json_ret={
            "version": "3",
            "type": "com.bbn.marti.sync.model.Resource",
            "data": [
                {"hash": "h1", "filename": "a.jpg", "mimeType": "image/jpeg", "creatorUid": "M-1"},
                {"hash": "h2", "filename": "b.jpg", "mimeType": "image/jpeg"},
            ],
            "nodeId": "n1",
        }
    )
    out = _run(tak_files.search_files(client, uid="M-1"))
    assert [m["hash"] for m in out] == ["h1", "h2"]
    assert tak_files.extract_hash(out[0]) == "h1"
    assert tak_files.extract_mimetype(out[0]) == "image/jpeg"


def test_search_parses_legacy_results_wrapper():
    # 舊版 / 相容：results 鍵
    client = _FakeClient(json_ret={"resultCount": 2, "results": [{"Hash": "h1"}, {"Hash": "h2"}]})
    out = _run(tak_files.search_files(client, uid="U1"))
    assert [m["Hash"] for m in out] == ["h1", "h2"]


def test_search_parses_bare_list():
    client = _FakeClient(json_ret=[{"Hash": "h1"}])
    assert _run(tak_files.search_files(client))[0]["Hash"] == "h1"


def test_search_garbage_returns_empty():
    for junk in (None, 123, "str", {"no_results_key": 1}, {"results": "notlist"}):
        client = _FakeClient(json_ret=junk)
        assert _run(tak_files.search_files(client)) == []


# ── 跨版本欄位容錯 ────────────────────────────────────────────────────────


def test_extract_helpers_cross_version_keys():
    assert tak_files.extract_hash({"hash": "h"}) == "h"
    assert tak_files.extract_hash({"Hash": "H"}) == "H"
    assert tak_files.extract_name({"Filename": "a.jpg"}) == "a.jpg"
    assert tak_files.extract_mimetype({"MIMEType": "image/jpeg"}) == "image/jpeg"
    assert tak_files.extract_hash({"nope": 1}) is None


# ── upload 留樁 ───────────────────────────────────────────────────────────


def test_upload_is_stub_pending_device():
    """上傳留樁：明確拋（待真機定 legacy servlet 格式，不猜格式）。"""
    client = _FakeClient()
    with pytest.raises(TakFilestoreError, match="待真機定格式"):
        _run(tak_files.upload_file(client, content=b"x", filename="a.jpg", mimetype="image/jpeg"))


# ── filestore_enabled 設定閘 ─────────────────────────────────────────────


def test_filestore_enabled_requires_url_and_read_cert(monkeypatch):
    monkeypatch.setattr(tak_files.config, "TAK_MARTI_URL", "https://tak:8443")
    monkeypatch.setattr(tak_files.config, "TAK_MARTI_READ_CERT", "/c.pem")
    monkeypatch.setattr(tak_files.config, "TAK_MARTI_READ_KEY", "/k.pem")
    assert tak_files.filestore_enabled()
    monkeypatch.setattr(tak_files.config, "TAK_MARTI_URL", "")
    assert not tak_files.filestore_enabled()
