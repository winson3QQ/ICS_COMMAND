# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
test_user_data_backup.py — P1-12b user_data_backup_service 測試（#228）

不需 FIDO2：BACKUP_KEY 以 Fernet.generate_key() 注入 env（monkeypatch）。
驗證：整包 round-trip / 排除 backups/ / manifest / 壞檔不動 current /
pre-restore 自動產生 / legacy key 過渡解密 / path-traversal 拒絕。
"""

import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from services import user_data_backup_service as uds  # noqa: E402


@pytest.fixture
def key(monkeypatch):
    k = Fernet.generate_key().decode()
    monkeypatch.setenv("BACKUP_KEY", k)
    monkeypatch.delenv("BACKUP_ENCRYPTION_KEY", raising=False)
    return k


def _seed_data(data_dir: Path):
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "ics.db").write_bytes(b"SQLITE_FAKE_DB")
    (data_dir / "map_config.json").write_text('{"zones": []}', encoding="utf-8")
    sub = data_dir / "uploads"
    sub.mkdir()
    (sub / "img.png").write_bytes(b"PNGDATA")
    # backups/ 內容必須被排除（防遞迴）
    bk = data_dir / "backups"
    bk.mkdir()
    (bk / "old.tar.gz.enc").write_bytes(b"SHOULD_NOT_BE_INCLUDED")


def test_roundtrip_excludes_backups(key, tmp_path):
    data = tmp_path / "data"
    _seed_data(data)
    backup_dir = data / "backups"

    res = uds.create_backup(data, backup_dir, trigger="manual")
    assert res.path.exists() and res.path.name.endswith(".tar.gz.enc")
    assert res.sha256 and res.size_bytes > 0

    manifest = uds.read_manifest(res.path)
    assert manifest["schema"] == uds.MANIFEST_SCHEMA
    assert manifest["trigger"] == "manual"
    files = set(manifest["files"])
    assert "ics.db" in files
    assert "map_config.json" in files
    assert "uploads/img.png" in files
    # backups/ 下的東西不得入包
    assert not any(f.startswith("backups/") for f in files)


def test_restore_replaces_data(key, tmp_path):
    data = tmp_path / "data"
    _seed_data(data)
    backup_dir = data / "backups"
    res = uds.create_backup(data, backup_dir)

    # 還原前竄改 current
    (data / "ics.db").write_bytes(b"CORRUPTED")
    (data / "map_config.json").unlink()

    out = uds.restore_backup(res.path, data, backup_dir)
    assert out["restart_required"] is True
    assert out["pre_restore"] is not None
    assert (data / "ics.db").read_bytes() == b"SQLITE_FAKE_DB"
    assert (data / "map_config.json").exists()
    # pre-restore 自動備份存在
    assert (backup_dir / out["pre_restore"]).exists()
    # backups/ 在還原後保留（含 pre-restore）
    assert (backup_dir / res.path.name).exists()


def test_bad_key_does_not_touch_current(key, tmp_path, monkeypatch):
    data = tmp_path / "data"
    _seed_data(data)
    backup_dir = data / "backups"
    res = uds.create_backup(data, backup_dir)

    # 換成不同 key → 解密必失敗
    monkeypatch.setenv("BACKUP_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("BACKUP_ENCRYPTION_KEY", raising=False)
    before = (data / "ics.db").read_bytes()
    with pytest.raises(ValueError, match="解密失敗"):
        uds.restore_backup(res.path, data, backup_dir)
    # current 未被動到
    assert (data / "ics.db").read_bytes() == before


def test_legacy_key_decrypts_during_transition(tmp_path, monkeypatch):
    """舊 backup 用 BACKUP_ENCRYPTION_KEY 加密，過渡期兩 env 並存仍可解。"""
    legacy = Fernet.generate_key().decode()
    monkeypatch.delenv("BACKUP_KEY", raising=False)
    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", legacy)
    data = tmp_path / "data"
    _seed_data(data)
    backup_dir = data / "backups"
    res = uds.create_backup(data, backup_dir)  # 用 legacy 加密

    # 過渡：新 BACKUP_KEY 上線、legacy 仍保留 → 舊 backup 仍可讀
    monkeypatch.setenv("BACKUP_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", legacy)
    manifest = uds.read_manifest(res.path)
    assert manifest["schema"] == uds.MANIFEST_SCHEMA


def test_missing_key_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("BACKUP_KEY", raising=False)
    monkeypatch.delenv("BACKUP_ENCRYPTION_KEY", raising=False)
    data = tmp_path / "data"
    _seed_data(data)
    with pytest.raises(RuntimeError, match="BACKUP_KEY"):
        uds.create_backup(data, data / "backups")


def test_manifest_carries_exercise_metadata(key, tmp_path):
    data = tmp_path / "data"
    _seed_data(data)
    ex = {"id": 7, "name": "颱風演習", "type": "ttx", "status": "archived"}
    res = uds.create_backup(data, data / "backups", trigger="archive", exercise=ex)
    manifest = uds.read_manifest(res.path)
    assert manifest["exercise"] == ex
    assert manifest["trigger"] == "archive"


def test_real_sqlite_db_consistent_snapshot_excludes_wal(key, tmp_path):
    """真 SQLite DB（WAL 模式）→ online backup 一致快照；-wal/-shm 不入備份，
    還原出的 DB 含已寫入資料、可正常開啟（非 torn snapshot）。"""
    import sqlite3

    data = tmp_path / "data"
    data.mkdir(parents=True)
    db = data / "ics.db"
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.execute("INSERT INTO t (v) VALUES ('committed')")
    conn.commit()
    assert (data / "ics.db-wal").exists()  # live：-wal 仍在

    res = uds.create_backup(data, data / "backups")
    files = set(uds.read_manifest(res.path)["files"])
    assert "ics.db" in files
    assert "ics.db-wal" not in files and "ics.db-shm" not in files  # 暫態檔排除
    conn.close()

    dest = tmp_path / "restored"
    dest.mkdir()
    uds.restore_backup(res.path, dest, dest / "backups", pre_restore=False)
    rconn = sqlite3.connect(str(dest / "ics.db"))
    assert rconn.execute("SELECT v FROM t").fetchone()[0] == "committed"
    rconn.close()


def test_manifest_preserves_crafted_exercise_name(key, tmp_path):
    """manifest 的 exercise.name 原樣保存（後端不改），前端負責跳脫 — 確認資料不被吞。"""
    data = tmp_path / "data"
    _seed_data(data)
    ex = {"id": 1, "name": "<script>x</script>颱風", "type": "ttx", "status": "archived"}
    res = uds.create_backup(data, data / "backups", trigger="archive", exercise=ex)
    assert uds.read_manifest(res.path)["exercise"]["name"] == "<script>x</script>颱風"


def test_read_manifest_decrypts_once(key, tmp_path, monkeypatch):
    """restore_backup 解密一次（read_manifest 與解壓共用 plaintext）。"""
    data = tmp_path / "data"
    _seed_data(data)
    backup_dir = data / "backups"
    res = uds.create_backup(data, backup_dir)
    calls = {"n": 0}
    orig = uds._decrypt

    def counting(ct):
        calls["n"] += 1
        return orig(ct)

    monkeypatch.setattr(uds, "_decrypt", counting)
    uds.restore_backup(res.path, data, backup_dir, pre_restore=False)
    assert calls["n"] == 1, f"預期解密 1 次，實得 {calls['n']}"


def test_retention_protects_archive_and_pre_restore(key, tmp_path):
    """滾動保留：老的 manual 被刪；archive（演習結束）+ pre-restore 受保護不刪；
    最新 keep_min 筆一律留。"""
    from datetime import UTC, datetime, timedelta

    data = tmp_path / "data"
    _seed_data(data)
    bd = data / "backups"
    base = datetime(2026, 1, 1, tzinfo=UTC)

    def mk(trigger, day, pattern=uds.FILENAME_PATTERN):
        ts = base + timedelta(days=day)
        # prune=False：setup 不自動清，否則刻意造的老檔會在建立時就被清掉
        return uds.create_backup(
            data,
            bd,
            trigger=trigger,
            timestamp=ts,
            filename_pattern=pattern,
            prune=False,
            exercise={"id": 1, "name": "E", "type": "ttx", "status": "archived"} if trigger == "archive" else None,
        ).path

    old_manual = mk("manual", 0)  # 最老 manual → 應被刪
    old_archive = mk("archive", 1)  # 老 archive → 保護
    old_pre = mk("manual", 2, uds.PRE_RESTORE_PATTERN)  # pre-restore 檔名 → 保護
    recent = [mk("manual", 10 + i) for i in range(12)]  # 最新一批（超過 keep_min=10）

    # now 設在很久以後，使 day0~2 都超出 retain_days
    deleted = uds.cleanup_user_data_backups(bd, retain_days=30, keep_min=10, now=base + timedelta(days=400))
    names = {p.name for p in deleted}
    assert old_manual.name in names  # 老 manual 被刪
    assert old_archive.name not in names  # archive 保護
    assert old_pre.name not in names  # pre-restore 保護
    assert all(r.name not in names for r in recent[-10:])  # 最新 keep_min 保留


def test_path_traversal_rejected(key, tmp_path, monkeypatch):
    """偽造含 ../ 逃逸路徑的 archive 還原時被拒（current 不動）。"""
    import gzip
    import io
    import tarfile

    data = tmp_path / "data"
    _seed_data(data)
    backup_dir = data / "backups"

    # 手工造一個惡意 archive（成員名含 ../）
    raw = io.BytesIO()
    with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w") as tar:
            payload = b"PWNED"
            mi = tarfile.TarInfo("../escape.txt")
            mi.size = len(payload)
            tar.addfile(mi, io.BytesIO(payload))
            # 補一個 MANIFEST 讓 read_manifest 過
            import json

            mb = json.dumps({"schema": uds.MANIFEST_SCHEMA}).encode()
            m2 = tarfile.TarInfo(uds.MANIFEST_NAME)
            m2.size = len(mb)
            tar.addfile(m2, io.BytesIO(mb))
    evil = backup_dir / "evil.tar.gz.enc"
    evil.parent.mkdir(parents=True, exist_ok=True)
    evil.write_bytes(uds._encrypt(raw.getvalue()))

    with pytest.raises(ValueError, match="逃逸|data/"):
        uds.restore_backup(evil, data, backup_dir, pre_restore=False)
    assert not (tmp_path / "escape.txt").exists()
