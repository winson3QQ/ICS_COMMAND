# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""tests/unit/test_tak_resync.py — P2-14 (C) Marti 權威 resync（#194/#173）

鎖住的不變式：
- parse_cot_events：`<events>` 集合 → list[CoTEventIn]；空集合 → []；root 非 events → raise；
  單筆壞（缺 point）→ 跳過不拖垮整批；非 event 子元素容忍；超大小上限 → raise。
- build_window / _format_marti_time：`.000Z` 毫秒格式 + [now-lookback, now]。
- resync_once：拉快照 → 逐筆 ingest；ingest 回 None=skipped、raise=errors、dict=ingested；
  空 body → 0；**只呼叫 ingest（upsert-only），絕不呼叫任何刪除**。
- resync_enabled / run_resync：缺 URL/cert → disabled，不建 client。
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from services import tak_resync, tak_service

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cot"


def _run(coro):
    return asyncio.run(coro)


def _collection_xml() -> str:
    return (_FIXTURES / "sa_events_collection.xml").read_text(encoding="utf-8")


_T = "time='2026-06-11T04:00:00Z' start='2026-06-11T04:00:00Z' stale='2026-06-11T04:02:00Z'"


def _event(uid, *, point=True) -> str:
    """最小合法（或缺 point 的壞）<event>，含必填 time/start/stale。"""
    pt = "<point lat='1' lon='2' hae='0' ce='0' le='0'/>" if point else "<detail/>"
    return f"<event uid='{uid}' type='a-f-G' how='m-g' {_T}>{pt}</event>"


# ── parse_cot_events（集合解析）────────────────────────────────────────────────


def test_parse_collection_returns_all_events():
    events = tak_service.parse_cot_events(_collection_xml())
    assert [e.uid for e in events] == ["RESYNC-PLI-1", "RESYNC-MARK-2"]
    # 第二筆帶 <archive/> → archived=True（#161 持久標記沿用）
    assert events[1].archived is True
    assert events[0].archived is False


def test_parse_empty_events_collection():
    assert tak_service.parse_cot_events("<events></events>") == []


def test_parse_root_not_events_raises():
    # 單筆 <event> 餵進集合解析 → root 非 <events> → raise（與 parse_cot_xml 分工明確）
    with pytest.raises(tak_service.CoTParseError):
        tak_service.parse_cot_events(_event("x"))


def test_parse_skips_bad_event_keeps_good():
    # 第一筆缺 <point>（壞）→ 跳過；第二筆好 → 保留。一顆壞 event 不拖垮整批 resync。
    xml = f"<events>{_event('BAD', point=False)}{_event('GOOD')}</events>"
    events = tak_service.parse_cot_events(xml)
    assert [e.uid for e in events] == ["GOOD"]


def test_parse_tolerates_non_event_children():
    xml = f"<events><meta foo='bar'/>{_event('E1')}</events>"
    events = tak_service.parse_cot_events(xml)
    assert [e.uid for e in events] == ["E1"]


def test_parse_oversize_collection_raises():
    big = "<events>" + ("<x/>" * (tak_service._MAX_COT_COLLECTION_BYTES // 3 + 10)) + "</events>"
    with pytest.raises(tak_service.CoTParseError):
        tak_service.parse_cot_events(big)


def test_parse_xxe_blocked_in_collection():
    # DTD/外部實體在集合路徑同樣被 defusedxml 擋（與單筆一致）
    xxe = (
        "<?xml version='1.0'?><!DOCTYPE events [<!ENTITY x SYSTEM 'file:///etc/passwd'>]>"
        "<events><event uid='&x;' type='a-f-G'><point lat='1' lon='2' hae='0' ce='0' le='0'/></event></events>"
    )
    with pytest.raises(tak_service.CoTParseError):
        tak_service.parse_cot_events(xxe)


# ── 時間窗 ────────────────────────────────────────────────────────────────────


def test_format_marti_time_millis_z():
    dt = datetime(2026, 6, 11, 4, 47, 35, 123456, tzinfo=UTC)
    assert tak_resync._format_marti_time(dt) == "2026-06-11T04:47:35.000Z"


def test_build_window_lookback():
    now = datetime(2026, 6, 11, 5, 0, 0, tzinfo=UTC)
    start, end = tak_resync.build_window(now, 7200)  # 2h
    assert start == "2026-06-11T03:00:00.000Z"
    assert end == "2026-06-11T05:00:00.000Z"


# ── resync_once（拉 → ingest 計數）─────────────────────────────────────────────


class _FakeClient:
    """注入式假 Marti client：get_text 回固定文字、記錄呼叫的 path/params。"""

    def __init__(self, text):
        self._text = text
        self.calls = []

    async def get_text(self, path, params=None):
        self.calls.append((path, params))
        return self._text

    async def close(self):
        pass


def test_resync_once_counts(monkeypatch):
    seen = []

    async def fake_ingest(event):
        seen.append(event.uid)
        return {"uid": event.uid}  # 視為成功 upsert

    monkeypatch.setattr(tak_resync.cop_service, "ingest_cot_event", fake_ingest)
    client = _FakeClient(_collection_xml())
    summary = _run(tak_resync.resync_once(client, lookback_s=7200))

    assert summary == {"fetched": 2, "ingested": 2, "skipped": 0, "errors": 0}
    assert seen == ["RESYNC-PLI-1", "RESYNC-MARK-2"]
    # 打的是 /cot/sa + start/end（無 bbox：實測帶不帶相同 → 省略）
    path, params = client.calls[0]
    assert path == "/Marti/api/cot/sa"
    assert set(params) == {"start", "end"}


def test_resync_once_skipped_and_errors(monkeypatch):
    async def fake_ingest(event):
        if event.uid == "RESYNC-PLI-1":
            return None  # 重送/亂序/GeoChat → skipped（非錯）
        raise RuntimeError("boom")  # 單筆例外 → errors，不中斷整批

    monkeypatch.setattr(tak_resync.cop_service, "ingest_cot_event", fake_ingest)
    summary = _run(tak_resync.resync_once(_FakeClient(_collection_xml()), lookback_s=7200))
    assert summary == {"fetched": 2, "ingested": 0, "skipped": 1, "errors": 1}


def test_resync_once_empty_body(monkeypatch):
    called = []
    monkeypatch.setattr(tak_resync.cop_service, "ingest_cot_event", lambda e: called.append(e) or None)
    summary = _run(tak_resync.resync_once(_FakeClient(None), lookback_s=7200))
    assert summary == {"fetched": 0, "ingested": 0, "skipped": 0, "errors": 0}
    assert called == []  # 空 body → 不解析、不 ingest


def test_resync_only_upserts_never_deletes(monkeypatch):
    # 核心安全不變式（reality check Gap 4）：resync 只走 ingest_cot_event，
    # 絕不依「不在快照」反推刪本地 entity（會誤殺 manual:/command 源）。
    # 守法：ingest 是唯一被呼叫的 cop_service 變更入口（其餘刪除函式零呼叫）。
    deletes = []
    monkeypatch.setattr(tak_resync.cop_service, "ingest_cot_event", lambda e: {"uid": e.uid})
    for name in ("delete_cop_entity", "soft_delete"):
        if hasattr(tak_resync.cop_service, name):
            monkeypatch.setattr(tak_resync.cop_service, name, lambda *a, **k: deletes.append(a))
    _run(tak_resync.resync_once(_FakeClient(_collection_xml()), lookback_s=7200))
    assert deletes == []


# ── config gating ─────────────────────────────────────────────────────────────


def test_resync_enabled_requires_url_and_cert(monkeypatch):
    monkeypatch.setattr(tak_resync.config, "TAK_MARTI_URL", "")
    assert tak_resync.resync_enabled() is False
    monkeypatch.setattr(tak_resync.config, "TAK_MARTI_URL", "https://tak:8443")
    monkeypatch.setattr(tak_resync.config, "TAK_MARTI_READ_CERT", "")
    assert tak_resync.resync_enabled() is False
    monkeypatch.setattr(tak_resync.config, "TAK_MARTI_READ_CERT", "/c.pem")
    monkeypatch.setattr(tak_resync.config, "TAK_MARTI_READ_KEY", "/k.pem")
    assert tak_resync.resync_enabled() is True


def test_run_resync_disabled_short_circuits(monkeypatch):
    monkeypatch.setattr(tak_resync.config, "TAK_MARTI_URL", "")
    # 不該建 client（缺配置）→ 若誤建會炸（cert 空）。回 disabled summary。
    summary = _run(tak_resync.run_resync())
    assert summary["enabled"] is False
    assert summary["fetched"] == 0


# ── #222：重連 outbound 對帳（reconcile_shared_outbound）──────────────────────────


def _set_downlink_configured(monkeypatch, *, configured=True):
    # #222 review fix：reconcile 現 gate `tak_runtime.effective_enabled() and is_configured()`
    # （對齊其餘出向路徑，停用時不繞過開關洩漏位置）→ 兩者一起 patch。
    monkeypatch.setattr("services.tak_runtime.effective_enabled", lambda: configured)
    monkeypatch.setattr("services.tak_runtime.is_configured", lambda: configured)


def test_reconcile_outbound_pushes_all_shared(monkeypatch):
    """每個 shared_tak entity 當前狀態都重推一次（斷線期間移動/編輯的補位）。"""
    sent = []
    ents = [
        {"uid": "U1", "type": "a-h-G", "lat": 25.0, "lon": 121.0, "attributes": {}},
        {"uid": "U2", "type": "a-f-G", "lat": 24.5, "lon": 120.9, "attributes": {}},
    ]
    _set_downlink_configured(monkeypatch)
    monkeypatch.setattr(tak_resync.cop_entity_repo, "list_shared_tak_entities", lambda **k: ents)
    monkeypatch.setattr("services.tak_downlink.entity_to_cot", lambda e: f"<cot uid={e['uid']}>")

    async def _fake_send(cot):
        sent.append(cot)

    monkeypatch.setattr("services.tak_downlink.send_cot", _fake_send)

    pushed = _run(tak_resync.reconcile_shared_outbound())
    assert pushed == 2
    assert sent == ["<cot uid=U1>", "<cot uid=U2>"]


def test_reconcile_outbound_best_effort_one_fails(monkeypatch):
    """單筆送出失敗（畸形/抖動）不中斷其餘——best-effort 逐筆。"""
    sent = []
    ents = [{"uid": "BAD"}, {"uid": "OK"}]
    _set_downlink_configured(monkeypatch)
    monkeypatch.setattr(tak_resync.cop_entity_repo, "list_shared_tak_entities", lambda **k: ents)
    monkeypatch.setattr("services.tak_downlink.entity_to_cot", lambda e: f"<cot {e['uid']}>")

    async def _fake_send(cot):
        if "BAD" in cot:
            raise RuntimeError("send fail")
        sent.append(cot)

    monkeypatch.setattr("services.tak_downlink.send_cot", _fake_send)

    pushed = _run(tak_resync.reconcile_shared_outbound())
    assert pushed == 1
    assert sent == ["<cot OK>"]


def test_reconcile_outbound_noop_when_downlink_unconfigured(monkeypatch):
    """TAK 出向未配置 → no-op：連 entity 都不查、不送，回 0（不每筆 raise 洗 log）。"""
    _set_downlink_configured(monkeypatch, configured=False)
    called = []
    monkeypatch.setattr(tak_resync.cop_entity_repo, "list_shared_tak_entities", lambda **k: called.append(1) or [])
    pushed = _run(tak_resync.reconcile_shared_outbound())
    assert pushed == 0
    assert called == []


def test_reconcile_outbound_noop_when_toggle_disabled_even_if_configured(monkeypatch):
    """#222 review fix（fail-open 守門）：cert 配齊但 admin 關掉開關（effective_enabled=False）
    → 不對帳、不送（否則重連會繞過開關、洩漏 shared 標記位置，與 #222 的 409 閘矛盾）。"""
    monkeypatch.setattr("services.tak_runtime.effective_enabled", lambda: False)
    monkeypatch.setattr("services.tak_runtime.is_configured", lambda: True)  # cert 仍在
    called = []
    monkeypatch.setattr(tak_resync.cop_entity_repo, "list_shared_tak_entities", lambda **k: called.append(1) or [])
    pushed = _run(tak_resync.reconcile_shared_outbound())
    assert pushed == 0
    assert called == []  # gate 早於查詢 → 開關關了連查都不查
