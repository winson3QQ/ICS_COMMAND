# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""tests/unit/test_client_identity_refresh.py — #344 Slice 2 + #477b：背景週期刷新。

#477b 把「uid→CN 快取刷新」與「TAK 群 reconcile」合併成**一次 subscriptions/all poll**
（_refresh_faction_state_once）。本檔鎖住其 identity 半（配置時從 subscriptions 寫入、未配置跳過、
TAK 錯 best-effort 回 0 不中斷迴圈、upsert 錯亦吞、空 subs noop）。群 reconcile 半見 test_faction_admin。
"""

import asyncio

import pytest

import main
from repositories import client_identity_repo
from services import tak_group_sync

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _db(tmp_db):
    yield


def _subs(rows):
    """把 [(uid, cn)] 包成 list_online_subscriptions 的回傳格式（含空 groups）。"""

    async def _f():
        return [{"client_uid": u, "username": n, "groups": []} for u, n in rows]

    return _f


def test_refresh_writes_identity(monkeypatch):
    monkeypatch.setattr(tak_group_sync, "is_configured", lambda: True)
    monkeypatch.setattr(tak_group_sync, "list_online_subscriptions", _subs([("u1", "cn1"), ("u2", "cn2")]))
    n = asyncio.run(main._refresh_faction_state_once())
    assert n == 2
    assert client_identity_repo.get_username("u1") == "cn1"
    assert client_identity_repo.get_username("u2") == "cn2"


def test_refresh_skips_when_unconfigured(monkeypatch):
    monkeypatch.setattr(tak_group_sync, "is_configured", lambda: False)
    assert asyncio.run(main._refresh_faction_state_once()) == 0


def test_refresh_best_effort_on_error(monkeypatch):
    """TAK 拉取錯 → 吞錯回 0（迴圈下輪再試，不崩）。"""
    monkeypatch.setattr(tak_group_sync, "is_configured", lambda: True)

    async def _boom():
        raise RuntimeError("tak down")

    monkeypatch.setattr(tak_group_sync, "list_online_subscriptions", _boom)
    assert asyncio.run(main._refresh_faction_state_once()) == 0


def test_refresh_swallows_upsert_error(monkeypatch):
    """review MED：upsert_many 拋（如 ingest 爭用的 DB lock）也須吞掉回 0，否則例外逃進週期迴圈把
    poller 永久殺掉。"""
    monkeypatch.setattr(tak_group_sync, "is_configured", lambda: True)
    monkeypatch.setattr(tak_group_sync, "list_online_subscriptions", _subs([("u1", "cn1")]))

    def _boom(_):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(client_identity_repo, "upsert_many", _boom)
    assert asyncio.run(main._refresh_faction_state_once()) == 0  # 不拋、回 0


def test_refresh_empty_subs_noop(monkeypatch):
    monkeypatch.setattr(tak_group_sync, "is_configured", lambda: True)
    monkeypatch.setattr(tak_group_sync, "list_online_subscriptions", _subs([]))
    assert asyncio.run(main._refresh_faction_state_once()) == 0
