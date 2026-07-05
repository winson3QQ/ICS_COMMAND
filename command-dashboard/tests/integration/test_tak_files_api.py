# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""#503 上行：routers/tak.py file store 照片下載端點（GET /api/tak/files/*）。

驗：
- faction 安全：for-entity 只回本 session 看得到的 marker 附件；紅方物件對 commander → 404（不洩存在）
- image 過濾：非 image/* 附件不列
- 下載：hash 驗 SHA-256、content-type/檔名來自 metadata、Content-Disposition 防注入、強制 audit
- 未配置 file store → 422；hash 非法 → 400；查無 → 404
- RBAC：未登入 → 擋（READ_ROLES 中央 gate）

tak_files 讀側走真 :8443 → 一律 monkeypatch 攔（不碰網路），驗端點配線 / faction / audit。
"""

from __future__ import annotations

import pytest

from auth.role_enum import ROLE_COMMANDER_ZH
from core.database import get_conn
from repositories import cop_entity_repo
from repositories.account_repo import create_account
from schemas.cop import CoPEntity
from services import tak_files

_VALID_HASH = "a" * 64


def _mk_entity(uid, faction):
    cop_entity_repo.insert_cop_entity(
        CoPEntity(
            uid=uid,
            type="a-f-G",
            time="2026-01-01T00:00:00Z",
            start="2026-01-01T00:00:00Z",
            stale="2099-01-01T00:00:00Z",
            how="m-g",
            lat=24.0,
            lon=120.0,
            source="tak",
            faction=faction,
        )
    )


def _commander_auth(client):
    create_account("cmdr", "1234", role=ROLE_COMMANDER_ZH)
    r = client.post("/api/auth/login", json={"username": "cmdr", "pin": "1234"})
    assert r.status_code == 200, r.text
    return {"X-Session-Token": r.json()["session_id"]}


class _DummyClient:
    async def close(self):
        return None


@pytest.fixture
def filestore(monkeypatch):
    """攔 tak_files 讀側（不碰網路）；回可變 state 讓各測試設 search/meta/data 回傳值。"""
    state = {"search": [], "meta": None, "data": None}

    monkeypatch.setattr(tak_files, "filestore_enabled", lambda: True)
    monkeypatch.setattr(tak_files, "_build_read_client", lambda: _DummyClient())

    async def _search(client, **filters):
        state["last_filters"] = filters
        return state["search"]

    async def _meta(client, h):
        return state["meta"]

    async def _dl(client, h):
        return state["data"]

    monkeypatch.setattr(tak_files, "search_files", _search)
    monkeypatch.setattr(tak_files, "get_file_metadata", _meta)
    monkeypatch.setattr(tak_files, "download_file", _dl)
    return state


# ── for-entity 列表：faction 安全 ─────────────────────────────────────────


def test_list_entity_files_blue_visible_to_commander(client, filestore):
    _mk_entity("U-BLUE", "blue")
    filestore["search"] = [
        {"hash": "h1", "filename": "a.jpg", "mimeType": "image/jpeg", "size": 100},
        {"hash": "h2", "filename": "b.png", "mimeType": "image/png", "size": 200},
    ]
    auth = _commander_auth(client)
    r = client.get("/api/tak/files/for-entity/U-BLUE", headers=auth)
    assert r.status_code == 200, r.text
    files = r.json()["files"]
    assert [f["hash"] for f in files] == ["h1", "h2"]
    assert filestore["last_filters"] == {"uid": "U-BLUE"}  # 以 marker uid 查 file store


def test_list_entity_files_red_hidden_from_commander_404(client, filestore):
    # 紅方物件對 commander 不可見 → 404（與「不存在」同碼，不洩存在）
    _mk_entity("U-RED", "red")
    filestore["search"] = [{"hash": "h1", "filename": "a.jpg", "mimeType": "image/jpeg"}]
    auth = _commander_auth(client)
    r = client.get("/api/tak/files/for-entity/U-RED", headers=auth)
    assert r.status_code == 404


def test_list_entity_files_red_visible_to_sysadmin(client, auth, filestore):
    # admin = sysadmin（全見）→ 紅方也看得到
    _mk_entity("U-RED", "red")
    filestore["search"] = [{"hash": "h1", "filename": "a.jpg", "mimeType": "image/jpeg"}]
    r = client.get("/api/tak/files/for-entity/U-RED", headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["files"][0]["hash"] == "h1"


def test_list_entity_files_null_faction_hidden_from_commander(client, filestore):
    # 未分類（faction=None）對 commander fail-closed 隱藏 → 404
    _mk_entity("U-NULL", None)
    auth = _commander_auth(client)
    r = client.get("/api/tak/files/for-entity/U-NULL", headers=auth)
    assert r.status_code == 404


def test_list_entity_files_unknown_uid_404(client, auth, filestore):
    r = client.get("/api/tak/files/for-entity/NOPE", headers=auth)
    assert r.status_code == 404


def test_list_entity_files_filters_non_images(client, auth, filestore):
    _mk_entity("U-BLUE", "blue")
    filestore["search"] = [
        {"hash": "h1", "filename": "a.jpg", "mimeType": "image/jpeg"},
        {"hash": "h2", "filename": "doc.pdf", "mimeType": "application/pdf"},  # 非圖 → 濾掉
        {"hash": "", "filename": "x", "mimeType": "image/png"},  # 無 hash → 濾掉
    ]
    r = client.get("/api/tak/files/for-entity/U-BLUE", headers=auth)
    assert [f["hash"] for f in r.json()["files"]] == ["h1"]


def test_list_entity_files_filestore_disabled_422(client, auth, monkeypatch):
    monkeypatch.setattr(tak_files, "filestore_enabled", lambda: False)
    _mk_entity("U-BLUE", "blue")
    r = client.get("/api/tak/files/for-entity/U-BLUE", headers=auth)
    assert r.status_code == 422


# ── 下載端點 ──────────────────────────────────────────────────────────────


def test_download_serves_bytes_with_content_type_and_audits(client, auth, filestore):
    filestore["meta"] = {"mimeType": "image/jpeg", "filename": "photo.jpg"}
    filestore["data"] = b"\xff\xd8\xff\xe0JFIF-body"
    r = client.get(f"/api/tak/files/{_VALID_HASH}", headers=auth)
    assert r.status_code == 200, r.text
    assert r.content == b"\xff\xd8\xff\xe0JFIF-body"
    assert r.headers["content-type"].startswith("image/jpeg")
    assert 'filename="photo.jpg"' in r.headers["content-disposition"]
    # 強制 audit
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT detail FROM audit_log WHERE action_type='TAK_FILE_DOWNLOAD' AND target_id=?",
            (_VALID_HASH,),
        ).fetchall()
    assert len(rows) == 1


def test_download_invalid_hash_400_no_network(client, auth, filestore):
    # 非 SHA-256 hash → 400，且下載 fake 不該被叫到（state.data 沒被讀）
    r = client.get("/api/tak/files/not-a-valid-hash", headers=auth)
    assert r.status_code == 400


def test_download_content_disposition_sanitized(client, auth, filestore):
    # 惡意檔名（引號 + CRLF）→ Content-Disposition 只留白名單字元，擋 header 注入
    filestore["meta"] = {"mimeType": "image/png", "filename": 'a"b\r\nInjected: x.png'}
    filestore["data"] = b"\x89PNG"
    r = client.get(f"/api/tak/files/{_VALID_HASH}", headers=auth)
    assert r.status_code == 200
    cd = r.headers["content-disposition"]
    assert '"' not in cd.split("filename=")[1][1:-1]  # 檔名內無裸引號
    assert "\r" not in cd and "\n" not in cd
    assert "Injected" not in r.headers  # 沒被拆成新 header


def test_download_not_found_404(client, auth, filestore):
    filestore["data"] = None  # 下載回 None = 查無
    r = client.get(f"/api/tak/files/{_VALID_HASH}", headers=auth)
    assert r.status_code == 404


def test_download_filestore_disabled_422(client, auth, monkeypatch):
    monkeypatch.setattr(tak_files, "filestore_enabled", lambda: False)
    r = client.get(f"/api/tak/files/{_VALID_HASH}", headers=auth)
    assert r.status_code == 422


def test_files_endpoints_require_auth(client, filestore):
    # 未登入 → 中央 gate 擋（非 200）
    _mk_entity("U-BLUE", "blue")
    assert client.get("/api/tak/files/for-entity/U-BLUE").status_code != 200
    assert client.get(f"/api/tak/files/{_VALID_HASH}").status_code != 200
