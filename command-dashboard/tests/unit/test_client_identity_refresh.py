# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""tests/unit/test_client_identity_refresh.py — #344 Slice 2：背景週期刷新 uid→CN 快取。

鎖住 _refresh_client_identity_once（週期任務的單次主體）：配置時從 subscriptions 寫入、未配置跳過、
TAK 錯 best-effort 回 0（不中斷迴圈）。
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


def test_refresh_writes_identity(monkeypatch):
    monkeypatch.setattr(tak_group_sync, "is_configured", lambda: True)

    async def _subs():
        return {"u1": "cn1", "u2": "cn2"}

    monkeypatch.setattr(tak_group_sync, "online_uid_to_username", _subs)
    n = asyncio.run(main._refresh_client_identity_once())
    assert n == 2
    assert client_identity_repo.get_username("u1") == "cn1"
    assert client_identity_repo.get_username("u2") == "cn2"


def test_refresh_skips_when_unconfigured(monkeypatch):
    monkeypatch.setattr(tak_group_sync, "is_configured", lambda: False)
    assert asyncio.run(main._refresh_client_identity_once()) == 0


def test_refresh_best_effort_on_error(monkeypatch):
    """TAK 拉取錯 → 吞錯回 0（迴圈下輪再試，不崩）。"""
    monkeypatch.setattr(tak_group_sync, "is_configured", lambda: True)

    async def _boom():
        raise RuntimeError("tak down")

    monkeypatch.setattr(tak_group_sync, "online_uid_to_username", _boom)
    assert asyncio.run(main._refresh_client_identity_once()) == 0


def test_refresh_empty_subs_noop(monkeypatch):
    monkeypatch.setattr(tak_group_sync, "is_configured", lambda: True)

    async def _empty():
        return {}

    monkeypatch.setattr(tak_group_sync, "online_uid_to_username", _empty)
    assert asyncio.run(main._refresh_client_identity_once()) == 0
