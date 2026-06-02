"""event_taxonomy_store unit tests（P1-10d 地基，issue #60/#66）。

涵蓋：seed→runtime ensure、read fallback、atomic write、idempotent，
以及 factory seed 本身的完整性（22 事件 / 6 群組 / 必要欄位 / severity 合法）。
所有 test 用 tmp_path 隔離，不污染 command-dashboard/data/。
"""

from __future__ import annotations

import json
from pathlib import Path

from core.config import EVENT_TAXONOMY_SEED
from services import event_taxonomy_store

_SAMPLE = {
    "version": 1,
    "groups": [{"key": "security", "label": "安全威脅", "order": 1}],
    "events": [
        {"key": "explosive", "label": "疑似爆裂物", "group": "security",
         "icon": "explosive", "abbr": "爆", "severity": "critical",
         "defaultAssigned": "forward", "cot_type": "a-h-G"},
    ],
}


# ── ensure() ────────────────────────────────────────────────

def test_ensure_copies_seed_when_runtime_missing(tmp_path: Path):
    seed = tmp_path / "seed.json"
    runtime = tmp_path / "data" / "event_taxonomy.json"
    seed.write_text(json.dumps(_SAMPLE), encoding="utf-8")
    event_taxonomy_store.ensure(path=runtime, seed=seed)
    assert runtime.exists()
    assert json.loads(runtime.read_text(encoding="utf-8")) == _SAMPLE


def test_ensure_is_idempotent_preserves_user_data(tmp_path: Path):
    seed = tmp_path / "seed.json"
    runtime = tmp_path / "data" / "event_taxonomy.json"
    runtime.parent.mkdir(parents=True)
    seed.write_text(json.dumps({"version": 1, "groups": [], "events": [{"key": "seed_only"}]}))
    runtime.write_text(json.dumps(_SAMPLE))
    event_taxonomy_store.ensure(path=runtime, seed=seed)
    event_taxonomy_store.ensure(path=runtime, seed=seed)
    assert json.loads(runtime.read_text(encoding="utf-8")) == _SAMPLE


def test_ensure_writes_empty_shell_when_seed_missing(tmp_path: Path):
    seed = tmp_path / "nope.json"
    runtime = tmp_path / "data" / "event_taxonomy.json"
    event_taxonomy_store.ensure(path=runtime, seed=seed)
    body = json.loads(runtime.read_text(encoding="utf-8"))
    assert body["events"] == [] and body["groups"] == []


# ── read() ──────────────────────────────────────────────────

def test_read_prefers_runtime_over_seed(tmp_path: Path):
    seed = tmp_path / "seed.json"
    runtime = tmp_path / "runtime.json"
    seed.write_text(json.dumps({"version": 1, "groups": [], "events": [{"key": "seed_only"}]}))
    runtime.write_text(json.dumps(_SAMPLE))
    assert event_taxonomy_store.read(path=runtime, seed=seed) == _SAMPLE


def test_read_falls_back_to_seed_then_shell(tmp_path: Path):
    seed = tmp_path / "seed.json"
    runtime = tmp_path / "runtime.json"
    seed.write_text(json.dumps(_SAMPLE))
    assert event_taxonomy_store.read(path=runtime, seed=seed) == _SAMPLE
    # 兩者皆無 → 空殼
    assert event_taxonomy_store.read(path=tmp_path / "x.json", seed=tmp_path / "y.json") == {
        "version": 1, "groups": [], "events": []
    }


def test_read_falls_back_when_runtime_not_dict(tmp_path: Path):
    """valid JSON 但非 dict（手改成 []）→ 不回，續 fallback 到 seed（review #68 MED）。"""
    seed = tmp_path / "seed.json"
    runtime = tmp_path / "runtime.json"
    seed.write_text(json.dumps(_SAMPLE))
    runtime.write_text("[]", encoding="utf-8")
    assert event_taxonomy_store.read(path=runtime, seed=seed) == _SAMPLE


def test_read_falls_back_when_runtime_malformed(tmp_path: Path):
    seed = tmp_path / "seed.json"
    runtime = tmp_path / "runtime.json"
    seed.write_text(json.dumps(_SAMPLE))
    runtime.write_text("not-json{{{", encoding="utf-8")
    assert event_taxonomy_store.read(path=runtime, seed=seed) == _SAMPLE


def test_read_backfills_source_from_seed(tmp_path: Path):
    """舊 runtime 缺 source → 依 key 從 seed 回填（source 為 read-only 事實，#66）。"""
    seed = tmp_path / "seed.json"
    runtime = tmp_path / "runtime.json"
    seed.write_text(json.dumps({
        "version": 1, "groups": [{"key": "security", "label": "安全"}],
        "events": [{"key": "explosive", "label": "爆", "group": "security",
                    "severity": "critical", "cot_type": "a-h-G", "source": "napsg"}],
    }))
    runtime.write_text(json.dumps({  # runtime 無 source（舊版建立）
        "version": 1, "groups": [{"key": "security", "label": "安全"}],
        "events": [{"key": "explosive", "label": "爆改", "group": "security",
                    "severity": "critical", "cot_type": "a-h-G"}],
    }))
    got = event_taxonomy_store.read(path=runtime, seed=seed)
    assert got["events"][0]["source"] == "napsg"   # 回填
    assert got["events"][0]["label"] == "爆改"      # runtime 其他值不被覆蓋


def test_read_backfill_does_not_override_existing_source(tmp_path: Path):
    seed = tmp_path / "seed.json"
    runtime = tmp_path / "runtime.json"
    seed.write_text(json.dumps({"version": 1, "groups": [], "events": [{"key": "x", "source": "napsg"}]}))
    runtime.write_text(json.dumps({"version": 1, "groups": [], "events": [{"key": "x", "source": "ics"}]}))
    assert event_taxonomy_store.read(path=runtime, seed=seed)["events"][0]["source"] == "ics"


# ── write_atomic() ──────────────────────────────────────────

def test_write_atomic_creates_and_replaces_no_tmp(tmp_path: Path):
    runtime = tmp_path / "data" / "event_taxonomy.json"
    event_taxonomy_store.write_atomic(_SAMPLE, path=runtime)
    assert json.loads(runtime.read_text(encoding="utf-8")) == _SAMPLE
    assert list((tmp_path / "data").glob("*.tmp")) == []


# ── factory seed 完整性 ─────────────────────────────────────

def test_factory_seed_is_valid_and_complete():
    """釘住 factory seed：22 事件 / 6 群組 / 必要欄位齊 / severity 合法 / group 參照存在。"""
    body = json.loads(EVENT_TAXONOMY_SEED.read_text(encoding="utf-8"))
    groups = {g["key"] for g in body["groups"]}
    assert len(body["groups"]) == 6
    assert len(body["events"]) == 22
    valid_sev = {"critical", "warning", "info"}
    keys = set()
    for ev in body["events"]:
        for field in ("key", "label", "group", "icon", "abbr", "severity", "cot_type"):
            assert field in ev, f"{ev.get('key')} 缺欄位 {field}"
        assert ev["severity"] in valid_sev, f"{ev['key']} severity 非法：{ev['severity']}"
        assert ev["group"] in groups, f"{ev['key']} 指向不存在群組 {ev['group']}"
        assert ev["key"] not in keys, f"重複 key：{ev['key']}"
        keys.add(ev["key"])
        assert ev.get("source") in {"napsg", "ics"}, f"{ev['key']} source 非法：{ev.get('source')}"
