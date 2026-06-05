"""P2-03（#107）— main.py lifespan 的 TAK 訂閱背景 task 啟停（_start/_stop_tak_subscriber）。

驗證：TAK_ENABLED gate、config 無效不擋啟動（回 None）、啟動後 _stop 能 cancel 回收。
"""

from __future__ import annotations

import asyncio

from core import config

# 註：`main` 走 **函式內 lazy import** —— module 頂層 import main 會在 collection 期執行
# main.py、提前綁定 DB（conftest 也是 lazy import app 才避開），否則污染其他測試的 schema_version。


def test_start_disabled_returns_none(tmp_db, monkeypatch):
    import main

    monkeypatch.setattr(config, "TAK_ENABLED", False)
    assert asyncio.run(main._start_tak_subscriber()) is None


def test_start_enabled_invalid_config_returns_none(tmp_db, monkeypatch):
    import main

    # TAK_ENABLED 但無 cafile 又沒 allow_insecure → build_subscribe_config raise → 不擋啟動，回 None
    monkeypatch.setattr(config, "TAK_ENABLED", True)
    monkeypatch.setattr(config, "TAK_COT_URL", "tls://h:8089")
    monkeypatch.setattr(config, "TAK_CLIENT_CERT", "/c")
    monkeypatch.setattr(config, "TAK_CLIENT_KEY", "/k")
    monkeypatch.setattr(config, "TAK_CAFILE", None)
    monkeypatch.setattr(config, "TAK_ALLOW_INSECURE_TLS", False)
    assert asyncio.run(main._start_tak_subscriber()) is None


def test_start_launches_task_and_stop_cancels(tmp_db, monkeypatch):
    import main

    monkeypatch.setattr(config, "TAK_ENABLED", True)
    monkeypatch.setattr(config, "TAK_COT_URL", "tls://h:8089")
    monkeypatch.setattr(config, "TAK_CLIENT_CERT", "/c")
    monkeypatch.setattr(config, "TAK_CLIENT_KEY", "/k")
    monkeypatch.setattr(config, "TAK_CAFILE", None)
    monkeypatch.setattr(config, "TAK_ALLOW_INSECURE_TLS", True)  # 顯式 opt-in → config 可建

    started = {"ran": False}

    async def fake_subscribe(cfg, *, stop_event=None, **kw):
        started["ran"] = True
        await asyncio.sleep(10)  # 長駐，直到被 cancel

    from services import tak_service

    monkeypatch.setattr(tak_service, "subscribe", fake_subscribe)

    async def run():
        handle = await main._start_tak_subscriber()
        assert handle is not None
        task, stop_event = handle
        await asyncio.sleep(0.02)  # 讓 task 起跑
        assert started["ran"]
        assert not task.done()
        await main._stop_tak_subscriber(handle)  # 應 cancel + 回收
        assert task.done()

    asyncio.run(run())


def test_stop_none_is_noop(tmp_db):
    import main

    asyncio.run(main._stop_tak_subscriber(None))  # 不該 raise
