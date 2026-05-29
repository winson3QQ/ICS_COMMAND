"""map_config_store unit tests（P1-13 / issue #27）。

涵蓋：seed→runtime ensure、read fallback、atomic write、idempotent、migration script。
所有 test 用 tmp_path 隔離真實檔案系統（不污染 command-dashboard/data/）。
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

from services import map_config_store

_SAMPLE_BODY = {
    "maps": {
        "indoor": {
            "zones": [{"id": "zone_a", "label": "A"}],
        },
        "outdoor": {
            "zones": [
                {"id": "node_a", "label": "A 站", "lat": 24.82, "lng": 121.01},
            ],
            "polygons": [],
        },
    }
}


# ── ensure() ────────────────────────────────────────────────


def test_ensure_copies_seed_when_runtime_missing(tmp_path: Path):
    seed = tmp_path / "seed.json"
    runtime = tmp_path / "data" / "map_config.json"
    seed.write_text(json.dumps(_SAMPLE_BODY), encoding="utf-8")

    map_config_store.ensure(path=runtime, seed=seed)

    assert runtime.exists()
    assert json.loads(runtime.read_text(encoding="utf-8")) == _SAMPLE_BODY


def test_ensure_is_idempotent(tmp_path: Path):
    """已存在 → no-op，內容不變（user data 不能被 seed 覆寫）。"""
    seed = tmp_path / "seed.json"
    runtime = tmp_path / "data" / "map_config.json"
    runtime.parent.mkdir(parents=True)
    seed.write_text(json.dumps({"maps": {"outdoor": {"zones": [{"id": "seed_only"}]}}}))
    runtime.write_text(json.dumps(_SAMPLE_BODY))

    map_config_store.ensure(path=runtime, seed=seed)
    map_config_store.ensure(path=runtime, seed=seed)  # 第二次 no-op

    assert json.loads(runtime.read_text(encoding="utf-8")) == _SAMPLE_BODY


def test_ensure_writes_empty_shell_when_seed_missing(tmp_path: Path):
    """seed 也不存在 — fail-loud-but-functional：寫最小空殼 + log warning，不 raise。"""
    seed = tmp_path / "nonexistent_seed.json"
    runtime = tmp_path / "data" / "map_config.json"

    map_config_store.ensure(path=runtime, seed=seed)

    assert runtime.exists()
    body = json.loads(runtime.read_text(encoding="utf-8"))
    assert "maps" in body
    assert body["maps"]["indoor"]["zones"] == []
    assert body["maps"]["outdoor"]["zones"] == []


# ── read() ──────────────────────────────────────────────────


# β Phase 1：read() 對無 version 的 body 補 version=0（ETag 樂觀鎖起點）。
# 既有測試的 _SAMPLE_BODY 無 version，read 後應多一個 version:0。
_SAMPLE_WITH_V0 = {**_SAMPLE_BODY, "version": 0}


def test_read_prefers_runtime_over_seed(tmp_path: Path):
    seed = tmp_path / "seed.json"
    runtime = tmp_path / "runtime.json"
    seed.write_text(json.dumps({"maps": {"outdoor": {"zones": [{"id": "seed_only"}]}}}))
    runtime.write_text(json.dumps(_SAMPLE_BODY))

    got = map_config_store.read(path=runtime, seed=seed)
    assert got == _SAMPLE_WITH_V0


def test_read_falls_back_to_seed_when_runtime_missing(tmp_path: Path):
    """ensure() 沒跑過 / runtime 被誤刪 → read 仍能撐住。"""
    seed = tmp_path / "seed.json"
    runtime = tmp_path / "runtime.json"
    seed.write_text(json.dumps(_SAMPLE_BODY))

    got = map_config_store.read(path=runtime, seed=seed)
    assert got == _SAMPLE_WITH_V0


def test_read_returns_empty_shell_when_both_missing(tmp_path: Path):
    seed = tmp_path / "seed.json"
    runtime = tmp_path / "runtime.json"

    got = map_config_store.read(path=runtime, seed=seed)
    # _EMPTY_SHELL 自帶 version:0
    assert got == {"version": 0, "maps": {"indoor": {"zones": []}, "outdoor": {"zones": []}}}


def test_read_falls_back_when_runtime_malformed(tmp_path: Path):
    """半寫 / 損壞 JSON → 不該整個爆，靠 seed 接住。"""
    seed = tmp_path / "seed.json"
    runtime = tmp_path / "runtime.json"
    seed.write_text(json.dumps(_SAMPLE_BODY))
    runtime.write_text("not-a-json{{{", encoding="utf-8")

    got = map_config_store.read(path=runtime, seed=seed)
    assert got == _SAMPLE_WITH_V0


def test_read_preserves_existing_version(tmp_path: Path):
    """已有 version 的 body → read 不覆寫。"""
    runtime = tmp_path / "runtime.json"
    runtime.write_text(json.dumps({"version": 42, "maps": {"outdoor": {"zones": []}}}))
    got = map_config_store.read(path=runtime, seed=tmp_path / "noseed.json")
    assert got["version"] == 42


# ── write_atomic() ──────────────────────────────────────────


def test_write_atomic_creates_file(tmp_path: Path):
    runtime = tmp_path / "data" / "map_config.json"
    map_config_store.write_atomic(_SAMPLE_BODY, path=runtime)

    assert runtime.exists()
    assert json.loads(runtime.read_text(encoding="utf-8")) == _SAMPLE_BODY


def test_write_atomic_replaces_existing(tmp_path: Path):
    runtime = tmp_path / "map_config.json"
    runtime.write_text(json.dumps({"old": True}), encoding="utf-8")

    map_config_store.write_atomic(_SAMPLE_BODY, path=runtime)
    assert json.loads(runtime.read_text(encoding="utf-8")) == _SAMPLE_BODY


def test_write_atomic_no_tmp_left_behind(tmp_path: Path):
    """成功寫完不該留 .tmp（os.replace 原子完成後 .tmp 已 rename 走）。"""
    runtime = tmp_path / "map_config.json"
    map_config_store.write_atomic(_SAMPLE_BODY, path=runtime)

    leftover = list(tmp_path.glob("*.tmp"))
    assert leftover == [], f"unexpected tmp files: {leftover}"


# ── Migration script ──────────────────────────────────────


def _run_migrate(env_overrides: dict[str, str]) -> subprocess.CompletedProcess:
    """跑 migrate script，可 override env var 改變 MAP_CONFIG_PATH/SEED/STATIC。"""
    repo_root = Path(__file__).resolve().parent.parent.parent
    script = repo_root / "command-dashboard" / "scripts" / "migrate_map_config.py"
    import os

    env = {**os.environ, **env_overrides}
    return subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        env=env,
    )


def test_migration_idempotent_no_op_when_runtime_exists(tmp_path: Path, monkeypatch):
    """模擬：NEW 已存在 → script 第二次跑 no-op。"""
    fake_runtime = tmp_path / "data" / "map_config.json"
    fake_seed = tmp_path / "static" / "map_config.seed.json"
    fake_old = tmp_path / "static" / "map_config.json"
    fake_runtime.parent.mkdir(parents=True)
    fake_seed.parent.mkdir(parents=True)
    fake_runtime.write_text(json.dumps(_SAMPLE_BODY))
    fake_old.write_text(json.dumps({"should_not_overwrite": True}))

    # 直接呼 migrate() 函式（避免 subprocess env path 麻煩）
    # 用 inline import 配合 monkeypatch 蓋常數
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    try:
        import migrate_map_config

        monkeypatch.setattr(migrate_map_config, "MAP_CONFIG_PATH", fake_runtime)
        monkeypatch.setattr(migrate_map_config, "MAP_CONFIG_SEED", fake_seed)
        monkeypatch.setattr(migrate_map_config, "OLD_PATH", fake_old)

        rc = migrate_map_config.migrate(dry_run=False)
        assert rc == 0
        # runtime 內容不變 — user data 沒被 OLD 覆寫
        assert json.loads(fake_runtime.read_text(encoding="utf-8")) == _SAMPLE_BODY
    finally:
        sys.path.pop(0)


def test_migration_copies_old_to_new_preserving_user_data(tmp_path: Path, monkeypatch):
    """模擬：NEW 不存在、OLD 有 user data → 搬到 NEW。"""
    fake_runtime = tmp_path / "data" / "map_config.json"
    fake_seed = tmp_path / "static" / "map_config.seed.json"
    fake_old = tmp_path / "static" / "map_config.json"
    fake_seed.parent.mkdir(parents=True)
    fake_seed.write_text(json.dumps({"factory_default": True}))
    fake_old.write_text(json.dumps(_SAMPLE_BODY))

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    try:
        import migrate_map_config

        monkeypatch.setattr(migrate_map_config, "MAP_CONFIG_PATH", fake_runtime)
        monkeypatch.setattr(migrate_map_config, "MAP_CONFIG_SEED", fake_seed)
        monkeypatch.setattr(migrate_map_config, "OLD_PATH", fake_old)

        rc = migrate_map_config.migrate(dry_run=False)
        assert rc == 0
        # NEW 拿到 OLD 的 user data（不是 seed 的 factory default）
        assert json.loads(fake_runtime.read_text(encoding="utf-8")) == _SAMPLE_BODY
    finally:
        sys.path.pop(0)


# ── compare_and_swap（β issue #29 Phase 1 version 樂觀鎖）──────────


def _cas(expected, body, actor="tester", path=None):
    """同步跑 async compare_and_swap（monkeypatch 已把 module 常數導到 tmp）。"""
    return asyncio.run(map_config_store.compare_and_swap(expected, body, actor))


def _setup_runtime(tmp_path, monkeypatch, version=0):
    runtime = tmp_path / "data" / "map_config.json"
    runtime.parent.mkdir(parents=True, exist_ok=True)
    body = {"version": version, "maps": {"outdoor": {"zones": []}}}
    runtime.write_text(json.dumps(body), encoding="utf-8")
    monkeypatch.setattr(map_config_store, "MAP_CONFIG_PATH", runtime)
    monkeypatch.setattr(map_config_store, "MAP_CONFIG_SEED", tmp_path / "noseed.json")
    return runtime


def test_cas_matching_version_writes_and_bumps(tmp_path, monkeypatch):
    runtime = _setup_runtime(tmp_path, monkeypatch, version=5)
    ok, result = _cas(5, {"maps": {"outdoor": {"zones": [{"id": "z1"}]}}}, actor="alice")
    assert ok is True
    assert result["version"] == 6
    assert result["updated_by"] == "alice"
    assert "updated_at" in result
    # disk 落地確認
    on_disk = json.loads(runtime.read_text(encoding="utf-8"))
    assert on_disk["version"] == 6
    assert on_disk["maps"]["outdoor"]["zones"][0]["id"] == "z1"


def test_cas_stale_version_rejects_no_write(tmp_path, monkeypatch):
    runtime = _setup_runtime(tmp_path, monkeypatch, version=7)
    ok, result = _cas(5, {"maps": {"outdoor": {"zones": [{"id": "loser"}]}}})
    assert ok is False
    # result 應為當前 server body（version 7，未被覆寫）
    assert result["version"] == 7
    # disk 沒被動
    on_disk = json.loads(runtime.read_text(encoding="utf-8"))
    assert on_disk["version"] == 7
    assert on_disk["maps"]["outdoor"]["zones"] == []


def test_cas_concurrent_only_one_wins(tmp_path, monkeypatch):
    """兩個並發 CAS 都用 expected=0：lock 序列化後，第一個寫 v1，第二個讀到 v1≠0 → reject。"""
    _setup_runtime(tmp_path, monkeypatch, version=0)

    async def _run_both():
        # 兩個 coroutine 都帶 expected=0
        return await asyncio.gather(
            map_config_store.compare_and_swap(0, {"maps": {"outdoor": {"zones": [{"id": "A"}]}}}, "a"),
            map_config_store.compare_and_swap(0, {"maps": {"outdoor": {"zones": [{"id": "B"}]}}}, "b"),
        )

    results = asyncio.run(_run_both())
    oks = [ok for ok, _ in results]
    # 正好一個成功一個失敗（lock 確保 read→write atomic）
    assert sorted(oks) == [False, True], f"預期一勝一敗，得到 {oks}"


def test_cas_legacy_no_version_treated_as_zero(tmp_path, monkeypatch):
    """legacy map_config（無 version 欄位）→ read() 補 0 → expected=0 可寫。"""
    runtime = tmp_path / "data" / "map_config.json"
    runtime.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_text(json.dumps({"maps": {"outdoor": {"zones": []}}}), encoding="utf-8")  # 無 version
    monkeypatch.setattr(map_config_store, "MAP_CONFIG_PATH", runtime)
    monkeypatch.setattr(map_config_store, "MAP_CONFIG_SEED", tmp_path / "noseed.json")

    ok, result = _cas(0, {"maps": {"outdoor": {"zones": []}}})
    assert ok is True
    assert result["version"] == 1
