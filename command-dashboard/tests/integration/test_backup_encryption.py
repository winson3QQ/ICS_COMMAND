# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
tests/integration/test_backup_encryption.py — Backup DR Drill (#41) encryption tests

對應 #41 Step A Approval frozen decisions:
  E-1  Fernet AES-128-CBC (cryptography library)
  E-3  BACKUP_ENCRYPTION_KEY env var (E-3, env-only)
  Sync v3  .db.gz.enc 後綴疊加
  Sync v2  DEFAULT_RETAIN_DAYS = 7

不影響既有 test_backup_service.py (additive only, 紅線 4-A 守線).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from cryptography.fernet import Fernet, InvalidToken

from services.backup_service import (
    BACKUP_ENCRYPTED_FILENAME_PATTERN,
    BACKUP_ENCRYPTION_KEY_ENV,
    DEFAULT_RETAIN_DAYS,
    create_backup,
    decrypt_file,
    encrypt_file,
    list_backups,
    restore_backup,
    verify_backup,
)

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    """產生 minimal SQLite DB with schema_migrations + sample row"""
    db_path = tmp_path / "src.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE schema_migrations (version TEXT)")
        conn.execute("INSERT INTO schema_migrations(version) VALUES('test_v1')")
        conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, data TEXT)")
        conn.execute("INSERT INTO events(data) VALUES('row1'), ('row2'), ('row3')")
        conn.commit()
    finally:
        conn.close()
    return db_path


@pytest.fixture
def fresh_key() -> bytes:
    """產生新 Fernet key (per-test)"""
    return Fernet.generate_key()


@pytest.fixture
def env_key(fresh_key: bytes, monkeypatch: pytest.MonkeyPatch) -> bytes:
    """設 BACKUP_ENCRYPTION_KEY env var (per-test)"""
    monkeypatch.setenv(BACKUP_ENCRYPTION_KEY_ENV, fresh_key.decode())
    return fresh_key


# ── Sync v2: DEFAULT_RETAIN_DAYS = 7 ────────────────────────────────────────


def test_default_retain_days_is_7():
    """#41 Sync v2 校準: 7 天 default (從 30 縮減)"""
    assert DEFAULT_RETAIN_DAYS == 7, f"Sync v2 校準: 7 天 default, got {DEFAULT_RETAIN_DAYS}"


# ── Sync v3: encrypted filename pattern ─────────────────────────────────────


def test_encrypted_filename_pattern_format():
    """#41 Sync v3: .db.gz.enc 後綴疊加 (對齊 ISO 8601)"""
    assert BACKUP_ENCRYPTED_FILENAME_PATTERN.endswith(".db.gz.enc")
    assert "%Y-%m-%d" in BACKUP_ENCRYPTED_FILENAME_PATTERN
    assert "T%H-%M-%SZ" in BACKUP_ENCRYPTED_FILENAME_PATTERN


# ── E-3: BACKUP_ENCRYPTION_KEY env var ──────────────────────────────────────


def test_encrypt_file_missing_key_env_raises(tmp_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """#41 E-3: env var 未設 → RuntimeError (fail-fast 避免靜默 plain backup)"""
    monkeypatch.delenv(BACKUP_ENCRYPTION_KEY_ENV, raising=False)
    src = tmp_db
    dst = tmp_path / "out.enc"
    with pytest.raises(RuntimeError, match=BACKUP_ENCRYPTION_KEY_ENV):
        encrypt_file(src, dst)


def test_encrypt_decrypt_roundtrip_with_env_key(env_key: bytes, tmp_db: Path, tmp_path: Path):
    """#41 E-1: encrypt + decrypt 對齊 (Fernet AES-128-CBC)"""
    src = tmp_db
    enc_path = tmp_path / "out.db.gz.enc"
    dec_path = tmp_path / "out_decrypted.db.gz"

    encrypt_file(src, enc_path)
    assert enc_path.exists()
    assert enc_path.stat().st_size > 0
    # Encrypted bytes should NOT match plaintext
    assert enc_path.read_bytes() != src.read_bytes()

    decrypt_file(enc_path, dec_path)
    assert dec_path.exists()
    assert dec_path.read_bytes() == src.read_bytes(), "decrypt 應 byte-equal 原檔"


def test_encrypt_with_explicit_key_param(
    fresh_key: bytes, tmp_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """encrypt_file 接受顯式 key= 參數 (override env var)"""
    monkeypatch.delenv(BACKUP_ENCRYPTION_KEY_ENV, raising=False)
    src = tmp_db
    enc = tmp_path / "out.enc"
    encrypt_file(src, enc, key=fresh_key)
    assert enc.exists()
    # decrypt 用同 key 還原
    dec = tmp_path / "dec.db"
    decrypt_file(enc, dec, key=fresh_key)
    assert dec.read_bytes() == src.read_bytes()


def test_decrypt_wrong_key_raises_invalid_token(fresh_key: bytes, tmp_db: Path, tmp_path: Path):
    """#41 E-1: 錯 key (Fernet InvalidToken) — 對應 篡改 / wrong key 偵測"""
    src = tmp_db
    enc = tmp_path / "out.enc"
    encrypt_file(src, enc, key=fresh_key)

    wrong_key = Fernet.generate_key()
    dec = tmp_path / "dec.db"
    with pytest.raises(InvalidToken):
        decrypt_file(enc, dec, key=wrong_key)
    # 錯 key 不應產生 output (atomic: tmp 失敗 cleanup)
    assert not dec.exists() or dec.read_bytes() == b""


def test_decrypt_tampered_ciphertext_raises(env_key: bytes, tmp_db: Path, tmp_path: Path):
    """#41 E-1: 篡改 ciphertext → InvalidToken (Fernet 含 HMAC integrity)"""
    src = tmp_db
    enc = tmp_path / "out.enc"
    encrypt_file(src, enc)

    # 篡改 ciphertext bytes
    raw = bytearray(enc.read_bytes())
    if len(raw) > 50:
        raw[50] ^= 0xFF  # flip a byte mid-payload
    enc.write_bytes(bytes(raw))

    dec = tmp_path / "dec.db"
    with pytest.raises(InvalidToken):
        decrypt_file(enc, dec)


# ── End-to-end: create_backup + encrypt → list_backups 含 encrypted=True ──


def test_create_backup_then_encrypt_listed(env_key: bytes, tmp_db: Path, tmp_path: Path):
    """#41 流程: create_backup → encrypt_file → list_backups 應含 .db.gz.enc"""
    backup_dir = tmp_path / "backups"
    result = create_backup(tmp_db, backup_dir)
    assert result.path.name.endswith(".db.gz")

    enc_path = result.path.with_suffix(result.path.suffix + ".enc")
    encrypt_file(result.path, enc_path)
    result.path.unlink()  # 模擬 backup_db.py --encrypt: 刪 plaintext

    backups = list_backups(backup_dir)
    assert len(backups) == 1
    assert backups[0].path.name.endswith(".db.gz.enc")


# ── Restore drill end-to-end (D-1 對齊) ─────────────────────────────────────


def test_restore_drill_encrypted_backup(env_key: bytes, tmp_db: Path, tmp_path: Path):
    """#41 D-1: encrypted backup → decrypt → restore → row count match"""
    backup_dir = tmp_path / "backups"

    # 1. create + encrypt backup
    result = create_backup(tmp_db, backup_dir)
    enc_path = result.path.with_suffix(result.path.suffix + ".enc")
    encrypt_file(result.path, enc_path)
    result.path.unlink()

    # 2. count_before
    conn = sqlite3.connect(str(tmp_db))
    count_before = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    conn.close()
    assert count_before == 3

    # 3. drill: decrypt to tmp + verify + restore to new target
    decrypted_tmp = tmp_path / "decrypted.db.gz"
    decrypt_file(enc_path, decrypted_tmp)
    assert verify_backup(decrypted_tmp), "decrypted backup 應 verify pass"

    target = tmp_path / "restored.db"
    restore_backup(decrypted_tmp, target)

    # 4. count_after
    conn = sqlite3.connect(str(target))
    count_after = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    conn.close()

    assert count_before == count_after, f"#41 D-1 drill: count_before={count_before} != count_after={count_after}"


# ── Sync v3: list_backups 同時含 .db.gz 與 .db.gz.enc ───────────────────────


def test_list_backups_includes_both_plain_and_encrypted(env_key: bytes, tmp_db: Path, tmp_path: Path):
    """#41 Sync v3: list_backups 應同時包含 .db.gz 與 .db.gz.enc"""
    backup_dir = tmp_path / "backups"

    # 建一個 plain .db.gz
    create_backup(tmp_db, backup_dir)

    # 再建一個並加密成 .db.gz.enc (用不同 timestamp)
    import time

    time.sleep(1.1)  # 確保 timestamp 至少差 1 秒
    result2 = create_backup(tmp_db, backup_dir)
    enc_path = result2.path.with_suffix(result2.path.suffix + ".enc")
    encrypt_file(result2.path, enc_path)
    result2.path.unlink()

    backups = list_backups(backup_dir)
    names = [b.path.name for b in backups]
    assert any(n.endswith(".db.gz") and not n.endswith(".enc") for n in names), f"應含至少一個 .db.gz: {names}"
    assert any(n.endswith(".db.gz.enc") for n in names), f"應含至少一個 .db.gz.enc: {names}"
    assert len(backups) == 2
