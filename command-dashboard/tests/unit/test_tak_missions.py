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


# ── sync_mission_once（M1a：mission CoT → COP，複用 resync 縫）──────────────


def _ev(uid):
    return type("E", (), {"uid": uid})()


def _patch_sync(monkeypatch, events, ingest_results):
    """mock parse_cot_events → 回 events；ingest_cot_event → 依序回 ingest_results（dict/None/raise）。"""
    monkeypatch.setattr(tak_missions.tak_service, "parse_cot_events", lambda raw: events)
    it = iter(ingest_results)

    async def _ingest(event):
        r = next(it)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(tak_missions.cop_service, "ingest_cot_event", _ingest)


def test_sync_mission_counts_ingested_skipped(monkeypatch):
    # 3 筆：ingested / skipped(None) / ingested
    _patch_sync(monkeypatch, [_ev("U1"), _ev("U2"), _ev("U3")], [{"uid": "U1"}, None, {"uid": "U3"}])
    client = _FakeClient(text_ret="<events><event uid='U1'/></events>")
    out = _run(tak_missions.sync_mission_once(client, "ICS"))
    assert out == {"fetched": 3, "ingested": 2, "skipped": 1, "errors": 0, "removed": 0}
    assert client.calls[0] == ("get_text", "/Marti/api/missions/ICS/cot")


def test_sync_mission_best_effort_single_failure(monkeypatch):
    # 中間一筆 ingest 拋 → errors 記數、不中斷後續
    _patch_sync(monkeypatch, [_ev("U1"), _ev("U2"), _ev("U3")], [{"uid": "U1"}, RuntimeError("boom"), {"uid": "U3"}])
    out = _run(tak_missions.sync_mission_once(_FakeClient(text_ret="<events/>"), "ICS"))
    assert out == {"fetched": 3, "ingested": 2, "skipped": 0, "errors": 1, "removed": 0}


def test_sync_mission_empty_cot_zero(monkeypatch):
    # 空 mission → get_text None → 不呼叫 parse/ingest、回 0
    called = {"parse": False}
    monkeypatch.setattr(tak_missions.tak_service, "parse_cot_events", lambda raw: called.update(parse=True) or [])
    out = _run(tak_missions.sync_mission_once(_FakeClient(text_ret=None), "ICS"))
    assert out == {"fetched": 0, "ingested": 0, "skipped": 0, "errors": 0, "removed": 0}
    assert called["parse"] is False  # 空 body 直接短路，不進 parse


def test_sync_mission_rejects_bad_name_before_client():
    with pytest.raises(TakMissionError):
        _run(tak_missions.sync_mission_once(_FakeClient(text_ret="<events/>"), "../x"))


# ── M4 可靠刪除：/cot-diff（上輪有這輪沒 → 軟刪；只碰 mission /cot 交付物）───────────


@pytest.fixture(autouse=True)
def _reset_mission_state():
    """清 M4 的 in-memory /cot-diff baseline，避免跨測試污染。"""
    tak_missions._mission_last_uids.clear()
    yield
    tak_missions._mission_last_uids.clear()


def _patch_removes(monkeypatch, existing_tak_uids):
    """mock cop_service.soft_delete_tak_entity → 對 existing_tak_uids 回 truthy（模擬成功軟刪
    tak entity）、其餘 None（不存在/非 tak）。回記錄的被呼叫 uid 清單。"""
    called = []

    async def _del(uid):
        called.append(uid)
        return {"uid": uid} if uid in existing_tak_uids else None

    monkeypatch.setattr(tak_missions.cop_service, "soft_delete_tak_entity", _del)
    return called


def _patch_cot_seq(monkeypatch, event_sets):
    """parse_cot_events 依序回 event_sets 每組（模擬連續 poll 的不同 /cot）；ingest 一律成功。"""
    seq = iter(event_sets)
    monkeypatch.setattr(tak_missions.tak_service, "parse_cot_events", lambda raw: next(seq))

    async def _ingest(event):
        return {"uid": getattr(event, "uid", None)}

    monkeypatch.setattr(tak_missions.cop_service, "ingest_cot_event", _ingest)


def test_reconcile_first_poll_no_baseline_no_delete(monkeypatch):
    # 首輪無 baseline → 不刪（即使 /cot 有 uid），只建 baseline
    _patch_cot_seq(monkeypatch, [[_ev("U1"), _ev("U2")]])
    deleted = _patch_removes(monkeypatch, {"U1", "U2"})
    out = _run(tak_missions.sync_mission_once(_FakeClient(text_ret="<events/>"), "ICS"))
    assert out["removed"] == 0
    assert deleted == []
    assert tak_missions._mission_last_uids["ICS"] == {"U1", "U2"}  # baseline 建立


def test_reconcile_deletes_gone_uid(monkeypatch):
    # 上輪 /cot={U1,U2}、這輪={U1} → U2 從 mission 消失 → 軟刪 U2
    _patch_cot_seq(monkeypatch, [[_ev("U1"), _ev("U2")], [_ev("U1")]])
    deleted = _patch_removes(monkeypatch, {"U2"})
    client = _FakeClient(text_ret="<events/>")
    _run(tak_missions.sync_mission_once(client, "ICS"))  # baseline {U1,U2}
    out = _run(tak_missions.sync_mission_once(client, "ICS"))  # {U1} → U2 gone
    assert out["removed"] == 1
    assert deleted == ["U2"]


def test_reconcile_no_delete_if_still_present(monkeypatch):
    # 兩輪都有 U1 → 不刪
    _patch_cot_seq(monkeypatch, [[_ev("U1")], [_ev("U1")]])
    deleted = _patch_removes(monkeypatch, {"U1"})
    client = _FakeClient(text_ret="<events/>")
    _run(tak_missions.sync_mission_once(client, "ICS"))
    out = _run(tak_missions.sync_mission_once(client, "ICS"))
    assert out["removed"] == 0
    assert deleted == []


def test_reconcile_never_touches_non_mission_uid(monkeypatch):
    # 修正核心：從沒進 mission /cot 的 uid（live 串流 entity）永不被此 mission 刪
    _patch_cot_seq(monkeypatch, [[_ev("U1")], [_ev("U1")]])
    deleted = _patch_removes(monkeypatch, {"STREAM-LIVE"})  # 假設它是別處的 live entity
    client = _FakeClient(text_ret="<events/>")
    _run(tak_missions.sync_mission_once(client, "ICS"))
    _run(tak_missions.sync_mission_once(client, "ICS"))
    assert deleted == []  # STREAM-LIVE 從沒在 baseline → soft_delete 完全沒被呼叫


def test_reconcile_noop_not_counted(monkeypatch):
    # 上輪有 U1、這輪沒；但 soft_delete 回 None（entity 已不存在/非 tak）→ 嘗試但不計數
    _patch_cot_seq(monkeypatch, [[_ev("U1")], []])
    deleted = _patch_removes(monkeypatch, set())  # 一律回 None
    client = _FakeClient(text_ret="<events/>")
    _run(tak_missions.sync_mission_once(client, "ICS"))
    out = _run(tak_missions.sync_mission_once(client, "ICS"))
    assert out["removed"] == 0
    assert deleted == ["U1"]  # 嘗試過、None 不計數


# ── M1 wiring：config 清單 + run_mission_sync ────────────────────────────


def test_configured_mission_names_parses_dedups_validates(monkeypatch):
    monkeypatch.setattr(tak_missions.config, "TAK_MISSION_FEEDS", " ICS , OpX ,ICS, a/b , ,Good_1")
    # 去空白 + 保序去重 + 略去非法（a/b 斜線）與空項
    assert tak_missions.configured_mission_names() == ["ICS", "OpX", "Good_1"]


def test_configured_mission_names_empty():
    # 直接呼叫（無 monkeypatch）預設 env 空 → []
    import os

    if not os.getenv("TAK_MISSION_FEEDS"):
        assert tak_missions.configured_mission_names() == []


def test_mission_sync_enabled_needs_feeds_and_certs(monkeypatch):
    monkeypatch.setattr(tak_missions.config, "TAK_MARTI_URL", "https://tak:8443")
    monkeypatch.setattr(tak_missions.config, "TAK_MARTI_READ_CERT", "/c.pem")
    monkeypatch.setattr(tak_missions.config, "TAK_MARTI_READ_KEY", "/k.pem")
    monkeypatch.setattr(tak_missions.config, "TAK_MISSION_FEEDS", "ICS")
    assert tak_missions.mission_sync_enabled()
    monkeypatch.setattr(tak_missions.config, "TAK_MISSION_FEEDS", "")  # 無 feed → 停用
    assert not tak_missions.mission_sync_enabled()


def _enable_sync(monkeypatch, feeds):
    monkeypatch.setattr(tak_missions.config, "TAK_MARTI_URL", "https://tak:8443")
    monkeypatch.setattr(tak_missions.config, "TAK_MARTI_READ_CERT", "/c.pem")
    monkeypatch.setattr(tak_missions.config, "TAK_MARTI_READ_KEY", "/k.pem")
    monkeypatch.setattr(tak_missions.config, "TAK_MISSION_FEEDS", feeds)

    closed = {"n": 0}

    class _Client:
        async def close(self):
            closed["n"] += 1

    monkeypatch.setattr(tak_missions, "_build_read_client", lambda: _Client())
    return closed


def test_run_mission_sync_disabled_when_not_configured(monkeypatch):
    monkeypatch.setattr(tak_missions.config, "TAK_MISSION_FEEDS", "")
    out = _run(tak_missions.run_mission_sync())
    assert out["enabled"] is False and out["missions"] == {}


def test_run_mission_sync_aggregates_per_mission(monkeypatch):
    closed = _enable_sync(monkeypatch, "A,B")

    async def _fake_sync(client, name):
        return (
            {"fetched": 2, "ingested": 1, "skipped": 1, "errors": 0}
            if name == "A"
            else {"fetched": 3, "ingested": 3, "skipped": 0, "errors": 0}
        )

    monkeypatch.setattr(tak_missions, "sync_mission_once", _fake_sync)
    out = _run(tak_missions.run_mission_sync())
    assert out["enabled"] is True
    assert out["missions"]["A"]["ingested"] == 1 and out["missions"]["B"]["ingested"] == 3
    assert out["fetched"] == 5 and out["ingested"] == 4 and out["skipped"] == 1
    assert closed["n"] == 1  # client 有 close


def test_run_mission_sync_one_mission_failure_isolated(monkeypatch):
    _enable_sync(monkeypatch, "A,B")

    async def _fake_sync(client, name):
        if name == "A":
            raise TakMissionError("boom")  # A 失敗
        return {"fetched": 1, "ingested": 1, "skipped": 0, "errors": 0}

    monkeypatch.setattr(tak_missions, "sync_mission_once", _fake_sync)
    out = _run(tak_missions.run_mission_sync())
    # A 記 1 error、不中斷 B
    assert out["missions"]["A"]["errors"] == 1 and out["missions"]["B"]["ingested"] == 1
    assert out["errors"] == 1 and out["ingested"] == 1


# ── mission_poll_loop（M1 背景週期 poll）─────────────────────────────────


def test_poll_loop_disabled_when_interval_zero(monkeypatch):
    monkeypatch.setattr(tak_missions.config, "TAK_MISSION_POLL_INTERVAL_S", 0)
    calls = {"n": 0}

    async def _fake():
        calls["n"] += 1

    monkeypatch.setattr(tak_missions, "run_mission_sync", _fake)
    _run(tak_missions.mission_poll_loop(asyncio.Event()))  # 立即返回
    assert calls["n"] == 0  # interval≤0 → 不跑週期


def test_poll_loop_runs_then_stops_on_event(monkeypatch):
    monkeypatch.setattr(tak_missions.config, "TAK_MISSION_POLL_INTERVAL_S", 0.01)
    ev = asyncio.Event()
    calls = {"n": 0}

    async def _fake():
        calls["n"] += 1
        ev.set()  # 第一輪後 set → loop 下個 wait 立即醒、退出

    monkeypatch.setattr(tak_missions, "run_mission_sync", _fake)
    _run(tak_missions.mission_poll_loop(ev))
    assert calls["n"] >= 1


def test_poll_loop_best_effort_survives_failure(monkeypatch):
    monkeypatch.setattr(tak_missions.config, "TAK_MISSION_POLL_INTERVAL_S", 0.01)
    ev = asyncio.Event()
    calls = {"n": 0}

    async def _fake():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")  # 第一輪炸 → 不得中斷 loop
        ev.set()  # 第二輪停

    monkeypatch.setattr(tak_missions, "run_mission_sync", _fake)
    _run(tak_missions.mission_poll_loop(ev))  # 不拋
    assert calls["n"] >= 2  # 失敗後有續跑


# ── missions_enabled 設定閘 ──────────────────────────────────────────────


def test_missions_enabled_requires_url_and_read_cert(monkeypatch):
    monkeypatch.setattr(tak_missions.config, "TAK_MARTI_URL", "https://tak:8443")
    monkeypatch.setattr(tak_missions.config, "TAK_MARTI_READ_CERT", "/c.pem")
    monkeypatch.setattr(tak_missions.config, "TAK_MARTI_READ_KEY", "/k.pem")
    assert tak_missions.missions_enabled()
    monkeypatch.setattr(tak_missions.config, "TAK_MARTI_URL", "")
    assert not tak_missions.missions_enabled()
