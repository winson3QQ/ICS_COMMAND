# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""P2-02 Wave 2（#106）— tak_service.subscribe() / _consume_cot 單元測試。

不碰真實網路：`subscribe` 的 pytak.protocol_factory 用 monkeypatch 換成 fake reader。
async 測試沿用本 repo 慣例（sync test + asyncio.run），不依賴 pytest-asyncio mode。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from services import tak_service
from services.tak_service import _consume_cot, build_subscribe_config

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cot"

# 真實 entity（友軍）
_VALID = (FIXTURES / "valid_minimal.xml").read_bytes()
# TakControl 協定協商事件（傳輸層產物，應被濾掉）
_CONTROL = (
    b'<event version="2.0" uid="takp-1" type="t-x-takp-v" '
    b'time="2026-06-05T04:00:00Z" start="2026-06-05T04:00:00Z" '
    b'stale="2026-06-05T04:01:00Z" how="m-g">'
    b'<point lat="0.0" lon="0.0"/>'
    b'<detail><TakControl><TakProtocolSupport version="1"/></TakControl></detail></event>'
)
_MALFORMED = b"<event>not valid cot</event>"


# ── _consume_cot（純處理，無網路）──────────────────────────────────────────


def test_consume_valid_cot_calls_ingest():
    got = []
    asyncio.run(_consume_cot(_VALID, got.append))
    assert len(got) == 1
    assert got[0].uid == "HAZ-001" and got[0].type == "b-d"


def test_consume_control_event_filtered_not_ingested():
    got = []
    asyncio.run(_consume_cot(_CONTROL, got.append))
    assert got == []  # t-x-takp-v 被濾，不進 ingest


def test_consume_malformed_does_not_raise_and_skips():
    got = []
    # 不丟例外（單筆壞不該斷串流），也不 ingest
    asyncio.run(_consume_cot(_MALFORMED, got.append))
    assert got == []


def test_consume_awaits_async_ingest():
    got = []

    async def aingest(event):
        await asyncio.sleep(0)
        got.append(event)

    asyncio.run(_consume_cot(_VALID, aingest))
    assert len(got) == 1


# ── TAK-E（#151）：ingest 速率限制（_TokenBucket + _consume_cot limiter）──────


def test_token_bucket_allows_within_capacity_and_refills():
    from services.tak_service import _TokenBucket

    b = _TokenBucket(rate_per_sec=2.0)  # capacity 預設 = max(1, rate) = 2
    assert b.take(0.0) is True  # token 2 → 1
    assert b.take(0.0) is True  # token 1 → 0
    assert b.take(0.0) is False  # 空桶 → 丟棄
    assert b.dropped == 1
    # 經 1 秒補 rate×1 = 2 token（capped at capacity）
    assert b.take(1.0) is True
    assert b.take(1.0) is True
    assert b.take(1.0) is False
    assert b.dropped == 2


def test_consume_rate_limited_drops_when_bucket_empty():
    """容量耗盡後同一時刻的後續 CoT 被丟棄（不進 ingest），但不丟例外、不中斷。"""
    from services.tak_service import _TokenBucket

    bucket = _TokenBucket(rate_per_sec=1.0)  # capacity 1
    got = []

    async def run():
        await _consume_cot(_VALID, got.append, limiter=bucket)  # 有 token → ingest
        await _consume_cot(_VALID, got.append, limiter=bucket)  # 同 loop tick 無 token → 丟棄

    asyncio.run(run())
    assert len(got) == 1  # 只有第一筆進 ingest
    assert bucket.dropped == 1


def test_consume_control_event_does_not_consume_token():
    """TakControl 事件在限速前已被濾掉 → 不佔 token（限速只算真實 entity 寫入率）。"""
    from services.tak_service import _TokenBucket

    bucket = _TokenBucket(rate_per_sec=1.0)  # capacity 1
    got = []

    async def run():
        await _consume_cot(_CONTROL, got.append, limiter=bucket)  # 控制事件，不佔 token
        await _consume_cot(_VALID, got.append, limiter=bucket)  # token 仍在 → ingest

    asyncio.run(run())
    assert len(got) == 1  # 真實 entity 進了（控制事件沒消耗 token）
    assert bucket.dropped == 0


def test_consume_no_limiter_keeps_existing_behavior():
    """limiter=None（預設）→ 不限速，沿用既有 ingest 行為（向後相容）。"""
    got = []
    for _ in range(5):
        asyncio.run(_consume_cot(_VALID, got.append))  # 無 limiter
    assert len(got) == 5


# ── build_subscribe_config ────────────────────────────────────────────────


def test_config_with_cafile_verifies_server():
    cfg = build_subscribe_config(
        cot_url="tls://tak.ics.local:8089", client_cert="/c.pem", client_key="/k.pem", cafile="/root.pem"
    )
    assert cfg.get("COT_URL") == "tls://tak.ics.local:8089"
    assert cfg.get("PYTAK_TLS_CLIENT_CAFILE") == "/root.pem"
    assert cfg.get("PYTAK_TLS_DONT_VERIFY") is None
    assert cfg.get("TAK_PROTO") == "0"  # v0 CoT XML


def test_config_without_cafile_raises_unless_opt_in():
    # fail-closed：沒 cafile 又沒顯式 allow_insecure_tls → raise（不靜默關閉 server 驗證）
    import pytest

    with pytest.raises(ValueError, match="cafile"):
        build_subscribe_config(cot_url="tls://h:8089", client_cert="/c", client_key="/k")


def test_config_insecure_opt_in_sets_dont_verify():
    cfg = build_subscribe_config(cot_url="tls://h:8089", client_cert="/c", client_key="/k", allow_insecure_tls=True)
    assert cfg.get("PYTAK_TLS_DONT_VERIFY") == "1"
    assert cfg.get("PYTAK_TLS_DONT_CHECK_HOSTNAME") == "1"


# ── subscribe（mock pytak 傳輸層）──────────────────────────────────────────


class _FakeReader:
    """模擬 pytak TLS reader：readuntil 逐筆吐 frame，吐完 raise IncompleteReadError（=EOF）。

    共用傳入的 list（不複製）→ 跨重連耗盡一次，避免每次重連重讀。
    """

    def __init__(self, frames):
        self._frames = frames  # 共用 reference，跨重連耗盡

    async def readuntil(self, sep):
        if self._frames:
            return self._frames.pop(0)
        raise asyncio.IncompleteReadError(b"", None)


class _FakeWriter:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_subscribe_ingests_entities_filters_control_and_stops(monkeypatch):
    frames = [_VALID, _CONTROL, _VALID]  # 2 真實 + 1 控制
    writer = _FakeWriter()

    async def fake_pf(config):
        return _FakeReader(frames), writer

    monkeypatch.setattr("pytak.protocol_factory", fake_pf)

    got = []

    async def run():
        stop = asyncio.Event()
        cfg = build_subscribe_config(cot_url="tls://h:8089", client_cert="/c", client_key="/k", allow_insecure_tls=True)
        task = asyncio.create_task(
            tak_service.subscribe(cfg, ingest=got.append, stop_event=stop, backoff_initial=0.01, backoff_max=0.01)
        )
        await asyncio.sleep(0.15)  # 連線 → 處理 frames → EOF 斷線 → 進重連退避
        stop.set()
        await asyncio.sleep(0.05)
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(run())
    # 只有 2 筆真實 entity 進 ingest（控制事件被濾）
    assert len(got) == 2
    assert all(e.type == "b-d" for e in got)
    assert writer.closed  # 斷線後 writer 被關


def test_subscribe_ingest_error_does_not_break_stream(monkeypatch):
    # 單筆 ingest 炸掉（如 transient DB error）不該斷整條串流：後續事件照常處理
    frames = [_VALID, _VALID]
    writer = _FakeWriter()

    async def fake_pf(config):
        return _FakeReader(frames), writer

    monkeypatch.setattr("pytak.protocol_factory", fake_pf)

    attempts = []

    def flaky_ingest(event):
        attempts.append(event)
        if len(attempts) == 1:
            raise RuntimeError("transient DB error")  # 第一筆炸

    async def run():
        stop = asyncio.Event()
        cfg = build_subscribe_config(cot_url="tls://h:8089", client_cert="/c", client_key="/k", allow_insecure_tls=True)
        task = asyncio.create_task(
            tak_service.subscribe(cfg, ingest=flaky_ingest, stop_event=stop, backoff_initial=0.01, backoff_max=0.01)
        )
        await asyncio.sleep(0.15)
        stop.set()
        await asyncio.sleep(0.05)
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(run())
    # 兩筆都被嘗試 ingest（第一筆 raise 被攔，串流沒斷，第二筆照常）
    assert len(attempts) == 2


def test_subscribe_reconnects_on_connect_failure_then_stops(monkeypatch):
    attempts = {"n": 0}

    async def flaky_pf(config):
        attempts["n"] += 1
        raise ConnectionRefusedError("server down")

    monkeypatch.setattr("pytak.protocol_factory", flaky_pf)

    async def run():
        stop = asyncio.Event()
        cfg = build_subscribe_config(cot_url="tls://h:8089", client_cert="/c", client_key="/k", allow_insecure_tls=True)
        task = asyncio.create_task(
            tak_service.subscribe(cfg, ingest=lambda e: None, stop_event=stop, backoff_initial=0.01, backoff_max=0.01)
        )
        await asyncio.sleep(0.1)  # 多次連線失敗 + 退避重試
        stop.set()
        await asyncio.sleep(0.05)
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(run())
    assert attempts["n"] >= 2  # 連線失敗有重試


def test_subscribe_calls_on_connect_each_connect(monkeypatch):
    """#222：on_connect 在每次 socket (重)連上都被呼叫——含 subscribe loop 內部自動重連
    （TAK server 不穩定關開時 resync/對帳的觸發點，不靠 tak_runtime.start）。fake_pf 每次連上
    即 EOF（空 reader）→ 反覆重連 → on_connect 應被呼叫多次（證明非只首次連上才觸發）。"""
    connects = []

    async def fake_pf(config):
        return _FakeReader([]), _FakeWriter()  # 連上即 EOF → 立刻斷線重連

    monkeypatch.setattr("pytak.protocol_factory", fake_pf)

    async def on_connect():
        connects.append(1)

    async def run():
        stop = asyncio.Event()
        cfg = build_subscribe_config(cot_url="tls://h:8089", client_cert="/c", client_key="/k", allow_insecure_tls=True)
        task = asyncio.create_task(
            tak_service.subscribe(
                cfg,
                ingest=lambda e: None,
                stop_event=stop,
                backoff_initial=0.01,
                backoff_max=0.01,
                on_connect=on_connect,
            )
        )
        await asyncio.sleep(0.12)  # 多輪 連上→EOF→重連
        stop.set()
        await asyncio.sleep(0.05)
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(run())
    assert len(connects) >= 2  # 每次 (重)連上都觸發，非只首次
