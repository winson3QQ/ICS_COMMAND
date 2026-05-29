"""map_config_store unit tests（P1-13 / issue #27）。

涵蓋：seed→runtime ensure、read fallback、atomic write、idempotent、migration script。
所有 test 用 tmp_path 隔離真實檔案系統（不污染 command-dashboard/data/）。
"""

from __future__ import annotations

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


def test_read_prefers_runtime_over_seed(tmp_path: Path):
    seed = tmp_path / "seed.json"
    runtime = tmp_path / "runtime.json"
    seed.write_text(json.dumps({"maps": {"outdoor": {"zones": [{"id": "seed_only"}]}}}))
    runtime.write_text(json.dumps(_SAMPLE_BODY))

    got = map_config_store.read(path=runtime, seed=seed)
    assert got == _SAMPLE_BODY


def test_read_falls_back_to_seed_when_runtime_missing(tmp_path: Path):
    """ensure() 沒跑過 / runtime 被誤刪 → read 仍能撐住。"""
    seed = tmp_path / "seed.json"
    runtime = tmp_path / "runtime.json"
    seed.write_text(json.dumps(_SAMPLE_BODY))

    got = map_config_store.read(path=runtime, seed=seed)
    assert got == _SAMPLE_BODY


def test_read_returns_empty_shell_when_both_missing(tmp_path: Path):
    seed = tmp_path / "seed.json"
    runtime = tmp_path / "runtime.json"

    got = map_config_store.read(path=runtime, seed=seed)
    assert got == {"maps": {"indoor": {"zones": []}, "outdoor": {"zones": []}}}


def test_read_falls_back_when_runtime_malformed(tmp_path: Path):
    """半寫 / 損壞 JSON → 不該整個爆，靠 seed 接住。"""
    seed = tmp_path / "seed.json"
    runtime = tmp_path / "runtime.json"
    seed.write_text(json.dumps(_SAMPLE_BODY))
    runtime.write_text("not-a-json{{{", encoding="utf-8")

    got = map_config_store.read(path=runtime, seed=seed)
    assert got == _SAMPLE_BODY


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
    monkeypatch.setattr(
        "command-dashboard.scripts.migrate_map_config".replace("-", "_").split(".")[-1]
        if False
        else "scripts.migrate_map_config",
        None,
        raising=False,
    )
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
