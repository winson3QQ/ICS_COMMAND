# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""P2-03（#107）+ P2-24（#164）— TAK 訂閱背景 task 的 runtime 控制器（services/tak_runtime）。

驗證：effective_enabled gate（start_if_enabled）、config 無效不擋啟動（回 False、不 raise）、
啟動後 stop 能 cancel 回收、重複 start 不建雙 task、stop 未在跑為 no-op。

註：tak_runtime 走 **函式內 lazy import** —— 避免 collection 期提前綁 DB（沿襲既有 conftest 慣例）。
"""

from __future__ import annotations

import asyncio

from core import config


def _reset_handle():
    from services import tak_runtime

    tak_runtime._handle = None  # 清模組級狀態，避免跨測試污染


def test_start_if_enabled_disabled_is_noop(tmp_db, monkeypatch):
    from services import tak_runtime

    _reset_handle()
    monkeypatch.setattr(config, "TAK_ENABLED", False)  # 無持久值 → effective=env=False
    asyncio.run(tak_runtime.start_if_enabled())
    assert tak_runtime.is_running() is False


def test_start_invalid_config_returns_false(tmp_db, monkeypatch):
    from services import tak_runtime

    _reset_handle()
    # 無 cafile 又沒 allow_insecure → build_subscribe_config raise → start 不擋、回 False
    monkeypatch.setattr(config, "TAK_ENABLED", True)
    monkeypatch.setattr(config, "TAK_COT_URL", "tls://h:8089")
    monkeypatch.setattr(config, "TAK_CLIENT_CERT", "/c")
    monkeypatch.setattr(config, "TAK_CLIENT_KEY", "/k")
    monkeypatch.setattr(config, "TAK_CAFILE", None)
    monkeypatch.setattr(config, "TAK_ALLOW_INSECURE_TLS", False)
    assert asyncio.run(tak_runtime.start()) is False
    assert tak_runtime.is_running() is False


def test_start_launches_task_and_stop_cancels(tmp_db, monkeypatch):
    from services import tak_runtime, tak_service

    _reset_handle()
    monkeypatch.setattr(config, "TAK_ENABLED", True)
    monkeypatch.setattr(config, "TAK_COT_URL", "tls://h:8089")
    monkeypatch.setattr(config, "TAK_CLIENT_CERT", "/c")
    monkeypatch.setattr(config, "TAK_CLIENT_KEY", "/k")
    monkeypatch.setattr(config, "TAK_CAFILE", None)
    monkeypatch.setattr(config, "TAK_ALLOW_INSECURE_TLS", True)  # 顯式 opt-in → config 可建

    started = {"ran": False}

    async def fake_subscribe(cfg, *, stop_event=None, **kw):
        started["ran"] = True
        await asyncio.sleep(10)  # 長駐直到被 cancel

    monkeypatch.setattr(tak_service, "subscribe", fake_subscribe)

    async def run():
        assert await tak_runtime.start() is True
        await asyncio.sleep(0.02)  # 讓 task 起跑
        assert started["ran"]
        assert tak_runtime.is_running()
        assert await tak_runtime.start() is False  # 重複 start → 不建雙 task
        assert await tak_runtime.stop() is True  # cancel + 回收
        assert tak_runtime.is_running() is False

    asyncio.run(run())


def test_stop_when_not_running_is_noop(tmp_db):
    from services import tak_runtime

    _reset_handle()
    assert asyncio.run(tak_runtime.stop()) is False  # 不該 raise
