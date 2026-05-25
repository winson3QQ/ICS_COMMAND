"""
tests/integration/test_backup_service.py — 自動備份服務測試

涵蓋：
  - create_backup：online backup API consistent snapshot
  - list_backups / cleanup_old_backups：30 天 rolling retention
  - verify_backup：壞掉的 backup 偵測
  - restore_backup：還原 + 安全檢查（不允許覆寫已存在）

對應 ROADMAP C3-D / NIST CP-9 / CP-10 / CIS §11。
"""

from __future__ import annotations

import gzip
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from services.backup_service import (
    BackupResult,
    cleanup_old_backups,
    create_backup,
    list_backups,
    restore_backup,
    verify_backup,
)


def _make_db(path: Path) -> None:
    """建立帶 schema_migrations 的測試 DB。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        );
        INSERT INTO schema_migrations (version, name) VALUES (1, 'init');
        CREATE TABLE events (id INTEGER PRIMARY KEY, name TEXT);
        INSERT INTO events (id, name) VALUES (1, '測試事件');
        """
    )
    conn.commit()
    conn.close()


class TestCreateBackup:
    def test_creates_gzipped_file(self, tmp_path: Path):
        db = tmp_path / "ics.db"
        _make_db(db)
        result = create_backup(db, tmp_path / "backups")

        assert isinstance(result, BackupResult)
        assert result.path.exists()
        assert result.path.name.endswith(".db.gz")
        assert result.size_bytes > 0
        assert len(result.sha256) == 64  # hex sha256
        assert result.duration_ms >= 0

    def test_backup_is_valid_sqlite(self, tmp_path: Path):
        db = tmp_path / "ics.db"
        _make_db(db)
        result = create_backup(db, tmp_path / "backups")

        # 解壓縮 → 開來看資料還在
        decompressed = tmp_path / "decompressed.db"
        with gzip.open(result.path, "rb") as fin, decompressed.open("wb") as fout:
            fout.write(fin.read())

        conn = sqlite3.connect(str(decompressed))
        try:
            row = conn.execute("SELECT name FROM events WHERE id=1").fetchone()
            assert row == ("測試事件",)
        finally:
            conn.close()

    def test_raises_when_source_missing(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            create_backup(tmp_path / "nope.db", tmp_path / "backups")

    def test_filename_uses_utc_timestamp(self, tmp_path: Path):
        db = tmp_path / "ics.db"
        _make_db(db)
        ts = datetime(2026, 6, 15, 3, 0, 0, tzinfo=UTC)
        result = create_backup(db, tmp_path / "backups", timestamp=ts)
        assert result.path.name == "ics-2026-06-15T03-00-00Z.db.gz"


class TestListBackups:
    def test_empty_dir(self, tmp_path: Path):
        assert list_backups(tmp_path / "backups") == []

    def test_sorted_oldest_first(self, tmp_path: Path):
        db = tmp_path / "ics.db"
        _make_db(db)
        backup_dir = tmp_path / "backups"
        for day in [10, 5, 15]:
            create_backup(
                db,
                backup_dir,
                timestamp=datetime(2026, 6, day, 3, 0, 0, tzinfo=UTC),
            )
        backups = list_backups(backup_dir)
        assert [b.timestamp.day for b in backups] == [5, 10, 15]

    def test_ignores_non_backup_files(self, tmp_path: Path):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        (backup_dir / "garbage.txt").write_text("hi")
        (backup_dir / "ics-not-a-date.db.gz").write_bytes(b"\x1f\x8b")
        assert list_backups(backup_dir) == []


class TestCleanupOldBackups:
    def test_keeps_recent_deletes_old(self, tmp_path: Path):
        db = tmp_path / "ics.db"
        _make_db(db)
        backup_dir = tmp_path / "backups"
        now = datetime(2026, 6, 30, 3, 0, 0, tzinfo=UTC)

        for days_ago in [1, 15, 29, 30, 31, 60]:
            ts = now - timedelta(days=days_ago)
            create_backup(db, backup_dir, timestamp=ts)

        # 30 天 retention：cutoff = now - 30 days
        # days_ago < 30 keep；days_ago >= 30 delete
        deleted = cleanup_old_backups(backup_dir, retain_days=30, now=now)
        assert len(deleted) == 3  # 30, 31, 60 days_ago

        remaining = list_backups(backup_dir)
        ages = [(now - b.timestamp).days for b in remaining]
        assert all(a < 30 for a in ages)

    def test_no_op_when_all_recent(self, tmp_path: Path):
        db = tmp_path / "ics.db"
        _make_db(db)
        backup_dir = tmp_path / "backups"
        now = datetime(2026, 6, 30, 3, 0, 0, tzinfo=UTC)

        for days_ago in [1, 5, 10]:
            create_backup(db, backup_dir, timestamp=now - timedelta(days=days_ago))

        deleted = cleanup_old_backups(backup_dir, retain_days=30, now=now)
        assert deleted == []
        assert len(list_backups(backup_dir)) == 3


class TestVerifyBackup:
    def test_valid_backup_passes(self, tmp_path: Path):
        db = tmp_path / "ics.db"
        _make_db(db)
        result = create_backup(db, tmp_path / "backups")
        assert verify_backup(result.path) is True

    def test_corrupt_gzip_fails(self, tmp_path: Path):
        bad = tmp_path / "ics-2026-06-15T03-00-00Z.db.gz"
        bad.write_bytes(b"not gzipped")
        assert verify_backup(bad) is False

    def test_missing_file_fails(self, tmp_path: Path):
        assert verify_backup(tmp_path / "nope.db.gz") is False

    def test_backup_without_schema_migrations_fails(self, tmp_path: Path):
        db = tmp_path / "no_schema.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE foo (id INTEGER)")
        conn.commit()
        conn.close()
        result = create_backup(db, tmp_path / "backups")
        assert verify_backup(result.path) is False


class TestRestoreBackup:
    def test_restore_to_new_path(self, tmp_path: Path):
        db = tmp_path / "ics.db"
        _make_db(db)
        result = create_backup(db, tmp_path / "backups")

        target = tmp_path / "restored" / "ics.db"
        restored = restore_backup(result.path, target)

        assert restored == target
        assert target.exists()
        # 還原後仍可開啟 + 資料完整
        conn = sqlite3.connect(str(target))
        try:
            row = conn.execute("SELECT name FROM events WHERE id=1").fetchone()
            assert row == ("測試事件",)
        finally:
            conn.close()

    def test_refuses_overwrite_without_flag(self, tmp_path: Path):
        db = tmp_path / "ics.db"
        _make_db(db)
        result = create_backup(db, tmp_path / "backups")

        target = tmp_path / "existing.db"
        target.write_bytes(b"don't lose me")

        with pytest.raises(FileExistsError):
            restore_backup(result.path, target)

        # 原檔未動
        assert target.read_bytes() == b"don't lose me"

    def test_overwrite_with_flag(self, tmp_path: Path):
        db = tmp_path / "ics.db"
        _make_db(db)
        result = create_backup(db, tmp_path / "backups")

        target = tmp_path / "existing.db"
        target.write_bytes(b"old content")

        restore_backup(result.path, target, overwrite=True)
        # 還原成功 → 不再是 b"old content"
        conn = sqlite3.connect(str(target))
        try:
            row = conn.execute("SELECT COUNT(*) FROM events").fetchone()
            assert row[0] == 1
        finally:
            conn.close()

    def test_invalid_backup_refused(self, tmp_path: Path):
        bad = tmp_path / "ics-2026-06-15T03-00-00Z.db.gz"
        bad.write_bytes(b"not gzip")
        with pytest.raises(ValueError, match="backup 無效"):
            restore_backup(bad, tmp_path / "target.db")
