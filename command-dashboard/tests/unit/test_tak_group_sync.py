# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""tests/unit/test_tak_group_sync.py — #344 faction 分類 → TAK group 同步。

鎖住 sync_client_faction：
- 未配置 admin cert → skip（純 ICS 視圖層，不同步）
- faction 無對應群 → skip
- device 離線（subscriptions/all 查無 clientUid）→ skip、不寫
- 正常：subscriptions/all clientUid→username → PUT update-groups 帶單一 faction 群 + 補齊三欄
- best-effort：TAK 寫錯不 raise（回 synced=False）
"""

import asyncio

import pytest

from services import tak_group_sync
from services.tak_rest_client import TakRestError


class _FakeClient:
    def __init__(self, endpoints, put_raises=None):
        self._endpoints = endpoints
        self._put_raises = put_raises
        self.put_calls = []
        self.closed = False

    async def get_json(self, path, params=None):
        return {"data": self._endpoints}

    async def put_json(self, path, body):
        self.put_calls.append((path, body))
        if self._put_raises:
            raise self._put_raises
        return None

    async def close(self):
        self.closed = True


@pytest.fixture
def _admin_configured(monkeypatch):
    """假裝已配置 admin cert + 注入 fake client；回 fake client 供斷言。"""
    monkeypatch.setattr(tak_group_sync.config, "TAK_MARTI_URL", "https://takserver:8443")
    monkeypatch.setattr(tak_group_sync.config, "TAK_MARTI_ADMIN_CERT", "/x/admin.pem")
    monkeypatch.setattr(tak_group_sync.config, "TAK_MARTI_ADMIN_KEY", "/x/admin.key")

    def _make(endpoints, put_raises=None):
        fc = _FakeClient(endpoints, put_raises)
        monkeypatch.setattr(tak_group_sync, "_build_admin_client", lambda: fc)
        return fc

    return _make


def test_skip_when_not_configured(monkeypatch):
    monkeypatch.setattr(tak_group_sync.config, "TAK_MARTI_ADMIN_CERT", "")
    res = asyncio.run(tak_group_sync.sync_client_faction("UID-1", "blue"))
    assert res["synced"] is False and res["reason"] == "tak-admin-not-configured"


def test_skip_when_no_group_for_faction(_admin_configured):
    fc = _admin_configured([{"clientUid": "UID-1", "username": "dev-1"}])
    res = asyncio.run(tak_group_sync.sync_client_faction("UID-1", None))  # None faction 無群
    assert res["synced"] is False and not fc.put_calls


def test_skip_when_device_offline(_admin_configured):
    fc = _admin_configured([{"clientUid": "OTHER", "username": "x"}])  # subscriptions/all 無 UID-1
    res = asyncio.run(tak_group_sync.sync_client_faction("UID-1", "blue"))
    assert res["synced"] is False and res["reason"] == "device-offline-or-unknown"
    assert not fc.put_calls  # 離線 → 不寫


def test_sync_puts_single_faction_group_with_all_fields(_admin_configured):
    fc = _admin_configured([{"clientUid": "UID-1", "username": "dev-1", "callsign": "甲"}])
    res = asyncio.run(tak_group_sync.sync_client_faction("UID-1", "red"))
    assert res["synced"] is True and res["username"] == "dev-1" and res["group"] == "red"
    assert len(fc.put_calls) == 1
    path, body = fc.put_calls[0]
    assert path == "/user-management/api/update-groups"
    # 單一 faction 群（replace=脫離其他群）+ 三欄齊全（否則 server NPE→500）
    assert body == {"username": "dev-1", "groupList": ["red"], "groupListIN": [], "groupListOUT": []}
    assert fc.closed is True  # client 有關閉


def test_best_effort_swallows_tak_error(_admin_configured):
    fc = _admin_configured([{"clientUid": "UID-1", "username": "dev-1"}], put_raises=TakRestError("boom"))
    res = asyncio.run(tak_group_sync.sync_client_faction("UID-1", "blue"))
    assert res["synced"] is False and res["reason"].startswith("sync-error:")
    assert fc.closed is True  # 錯誤路徑也關閉


def test_best_effort_swallows_build_error(monkeypatch):
    """cert 檔缺/壞 → _build_admin_client 拋 OSError → 吞成 synced=False，不冒進 classify（500）。"""
    monkeypatch.setattr(tak_group_sync.config, "TAK_MARTI_URL", "https://takserver:8443")
    monkeypatch.setattr(tak_group_sync.config, "TAK_MARTI_ADMIN_CERT", "/missing/admin.pem")
    monkeypatch.setattr(tak_group_sync.config, "TAK_MARTI_ADMIN_KEY", "/missing/admin.key")

    def _boom():
        raise FileNotFoundError("/missing/admin.pem")  # ssl.load_cert_chain 對缺檔拋這個

    monkeypatch.setattr(tak_group_sync, "_build_admin_client", _boom)
    res = asyncio.run(tak_group_sync.sync_client_faction("UID-1", "blue"))
    assert res["synced"] is False and res["reason"].startswith("sync-error:")  # 不 raise


# ── #404：list_online_subscriptions（在線視圖，含匿名）──────────────────────────


def test_list_online_not_configured(monkeypatch):
    monkeypatch.setattr(tak_group_sync.config, "TAK_MARTI_ADMIN_CERT", "")
    assert asyncio.run(tak_group_sync.list_online_subscriptions()) == []


def test_list_online_parses_uid_user_groups(_admin_configured):
    fc = _admin_configured(
        [
            {"clientUid": "AC4B", "username": "3QQ-itak", "groups": [{"name": "__ANON__"}, {"name": "__ANON__"}]},
            {"clientUid": "", "username": "ics-cot", "groups": [{"name": "red"}, {"name": "blue"}]},
        ]
    )
    out = asyncio.run(tak_group_sync.list_online_subscriptions())
    assert out == [
        {"client_uid": "AC4B", "username": "3QQ-itak", "groups": ["__ANON__"]},  # 群去重 + 排序
        {"client_uid": "", "username": "ics-cot", "groups": ["blue", "red"]},
    ]
    assert fc.closed is True  # client 有關閉


def test_list_online_best_effort_on_error(_admin_configured):
    fc = _admin_configured([], put_raises=None)

    async def _boom(path, params=None):
        raise TakRestError("subscriptions 500")

    fc.get_json = _boom
    assert asyncio.run(tak_group_sync.list_online_subscriptions()) == []
    assert fc.closed is True  # 錯誤路徑也關閉
