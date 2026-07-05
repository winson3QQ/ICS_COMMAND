# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
tests/unit/test_tak_missions.py — #506 M0：TAK Mission/Data Sync 讀取地基

鎖住的不變式：
- name 驗證：非白名單（含 path 遍歷 `../`、斜線）一律 raise，且**在打 client 前**擋掉（安全）
- list/get/changes：ApiResponse `data` 陣列正規化（5.7 契約）；裸 list/垃圾容錯
- get_mission_cot：走 get_text（raw XML，非 JSON）
- get_mission：查無回 None、取 data[0]
- missions_enabled：Marti URL + 讀 cert 缺任一則停用
"""

import asyncio

import pytest

from services import tak_missions
from services.tak_missions import TakMissionError


def _run(coro):
    return asyncio.run(coro)


class _FakeClient:
    """記錄呼叫的 fake Marti client：get_json / get_text 回預設值並記 path。"""

    def __init__(self, *, json_ret=None, text_ret=None):
        self._json_ret = json_ret
        self._text_ret = text_ret
        self.calls = []

    async def get_json(self, path, params=None):
        self.calls.append(("get_json", path))
        return self._json_ret

    async def get_text(self, path, params=None):
        self.calls.append(("get_text", path))
        return self._text_ret


# ── name 驗證（安全核心）──────────────────────────────────────────────────


def test_valid_mission_name_accepts():
    for ok in ("ICS", "op-nightfall", "Feed_1", "a.b.c", "M" * 255):
        assert tak_missions.is_valid_mission_name(ok)


@pytest.mark.parametrize(
    "bad",
    ["", "a b", "a/b", "../etc", "op/../../x", "name\n", "M" * 256, "a;b", "a%2fb"],
)
def test_invalid_mission_name_rejects(bad):
    assert not tak_missions.is_valid_mission_name(bad)


def test_get_mission_rejects_bad_name_before_client():
    """path 遍歷/斜線 name → 在打 client **前**就拋（client 完全沒被呼叫）＝安全不變式。"""
    client = _FakeClient(json_ret={"data": []})
    with pytest.raises(TakMissionError, match="非法 mission name"):
        _run(tak_missions.get_mission(client, "../secret"))
    assert client.calls == []


def test_cot_and_changes_reject_bad_name_before_client():
    client = _FakeClient(json_ret={"data": []}, text_ret="x")
    for coro in (
        tak_missions.get_mission_cot(client, "a/b"),
        tak_missions.get_mission_changes(client, "a/b"),
    ):
        with pytest.raises(TakMissionError):
            _run(coro)
    assert client.calls == []


# ── list_missions ─────────────────────────────────────────────────────────


def test_list_missions_parses_data_wrapper():
    client = _FakeClient(
        json_ret={
            "version": "3",
            "type": "Mission",
            "data": [{"name": "ICS", "uids": ["U1"], "contents": []}, {"name": "OpX"}],
            "nodeId": "n1",
        }
    )
    out = _run(tak_missions.list_missions(client))
    assert [tak_missions.extract_name(m) for m in out] == ["ICS", "OpX"]
    assert client.calls == [("get_json", "/Marti/api/missions")]


def test_list_missions_empty_and_garbage():
    assert _run(tak_missions.list_missions(_FakeClient(json_ret={"data": []}))) == []
    for junk in (None, 123, "str", {"no_data": 1}, {"data": "notlist"}):
        assert _run(tak_missions.list_missions(_FakeClient(json_ret=junk))) == []


# ── get_mission ───────────────────────────────────────────────────────────


def test_get_mission_returns_first():
    client = _FakeClient(json_ret={"data": [{"name": "ICS", "uids": ["U1", "U2"], "contents": [{"hashes": ["h1"]}]}]})
    m = _run(tak_missions.get_mission(client, "ICS"))
    assert m["name"] == "ICS" and m["uids"] == ["U1", "U2"]
    assert client.calls[0] == ("get_json", "/Marti/api/missions/ICS")


def test_get_mission_not_found_returns_none():
    assert _run(tak_missions.get_mission(_FakeClient(json_ret={"data": []}), "Nope")) is None


# ── get_mission_cot（raw XML via get_text）────────────────────────────────


def test_get_mission_cot_returns_raw_xml():
    xml = "<events><event uid='U1'/></events>"
    client = _FakeClient(text_ret=xml)
    out = _run(tak_missions.get_mission_cot(client, "ICS"))
    assert out == xml
    assert client.calls[0] == ("get_text", "/Marti/api/missions/ICS/cot")


def test_get_mission_cot_empty_none():
    assert _run(tak_missions.get_mission_cot(_FakeClient(text_ret=None), "ICS")) is None


# ── get_mission_changes ───────────────────────────────────────────────────


def test_get_mission_changes_parses_data():
    client = _FakeClient(
        json_ret={
            "type": "MissionChange",
            "data": [
                {
                    "type": "ADD_CONTENT",
                    "contentUid": "U1",
                    "contentResource": {"hash": "h1", "mimeType": "image/jpeg"},
                },
                {"type": "CREATE_MISSION", "creatorUid": "C1"},
            ],
        }
    )
    out = _run(tak_missions.get_mission_changes(client, "ICS"))
    assert [c["type"] for c in out] == ["ADD_CONTENT", "CREATE_MISSION"]
    assert client.calls[0] == ("get_json", "/Marti/api/missions/ICS/changes")


# ── missions_enabled 設定閘 ──────────────────────────────────────────────


def test_missions_enabled_requires_url_and_read_cert(monkeypatch):
    monkeypatch.setattr(tak_missions.config, "TAK_MARTI_URL", "https://tak:8443")
    monkeypatch.setattr(tak_missions.config, "TAK_MARTI_READ_CERT", "/c.pem")
    monkeypatch.setattr(tak_missions.config, "TAK_MARTI_READ_KEY", "/k.pem")
    assert tak_missions.missions_enabled()
    monkeypatch.setattr(tak_missions.config, "TAK_MARTI_URL", "")
    assert not tak_missions.missions_enabled()
