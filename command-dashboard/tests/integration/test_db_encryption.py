"""
test_db_encryption.py — P1-12c SQLCipher at-rest 加密整合測試（#229）

驗證加密路徑（driver 抽象 / online_snapshot 解密 / encrypt_db / reencrypt_in_place /
open_readonly_live / 金鑰驗證）。**需 sqlcipher3** —— Windows 無 wheel（已實測）會整檔
`importorskip` 跳過；於 CI（ubuntu，requirements.txt 裝 sqlcipher3-binary）實跑。

明文路徑的回歸由既有 test_backup_service / test_user_data_backup / test_health_enhanced 覆蓋。
"""

import sqlite3
from pathlib import Path

import pytest

# Windows 無 sqlcipher3 wheel → 整檔跳過；CI ubuntu 有 → 實跑。
pytest.importorskip("sqlcipher3")

import core.database as database  # noqa: E402

# 64 hex = 256-bit SQLCipher raw key（測試固定值）
_KEY = "0123456789abcdef" * 4
_SQLITE_MAGIC = b"SQLite format 3\x00"


def _is_plaintext_sqlite(path: Path) -> bool:
    with path.open("rb") as f:
        return f.read(16) == _SQLITE_MAGIC


@pytest.fixture
def enc(tmp_path, monkeypatch):
    """加密模式環境：DB_PATH 指 temp、DB_ENCRYPTED=True、DB_KEY 注入。"""
    db = tmp_path / "ics.db"
    monkeypatch.setattr(database, "DB_PATH", db)
    monkeypatch.setattr(database, "DB_ENCRYPTED", True)
    monkeypatch.setenv(database.DB_KEY_ENV, _KEY)
    return db


# ── driver 抽象：加密 round-trip ──────────────────────────────────────────────


def test_get_conn_encrypted_roundtrip(enc):
    conn = database.get_conn()
    try:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        conn.execute("INSERT INTO t (v) VALUES ('secret')")
        conn.commit()
    finally:
        conn.close()

    # 檔案不是明文 SQLite（連檔頭都加密）
    assert not _is_plaintext_sqlite(enc)

    # 重開（重新走 PRAGMA key）資料還在
    conn2 = database.get_conn()
    try:
        assert conn2.execute("SELECT v FROM t").fetchone()[0] == "secret"
    finally:
        conn2.close()


def test_encrypted_db_not_readable_as_plaintext(enc):
    conn = database.get_conn()
    try:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()
    # 原生 sqlite3 無金鑰 → 開不了（加密檔）
    with pytest.raises(sqlite3.DatabaseError):
        c = sqlite3.connect(str(enc))
        try:
            c.execute("SELECT name FROM sqlite_master")
        finally:
            c.close()


# ── 金鑰驗證：fail-closed ─────────────────────────────────────────────────────


def test_missing_key_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "ics.db")
    monkeypatch.setattr(database, "DB_ENCRYPTED", True)
    monkeypatch.delenv(database.DB_KEY_ENV, raising=False)
    with pytest.raises(RuntimeError, match=database.DB_KEY_ENV):
        database.get_conn()


def test_bad_key_format_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "ics.db")
    monkeypatch.setattr(database, "DB_ENCRYPTED", True)
    monkeypatch.setenv(database.DB_KEY_ENV, "not-hex")  # 非 64 hex
    with pytest.raises(RuntimeError, match="64 hex"):
        database.get_conn()


def test_short_key_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "ics.db")
    monkeypatch.setattr(database, "DB_ENCRYPTED", True)
    monkeypatch.setenv(database.DB_KEY_ENV, "abcd")  # hex 但長度不足
    with pytest.raises(RuntimeError, match="64 hex"):
        database.get_conn()


# ── online_snapshot：加密 → 明文（備份產物恆明文）────────────────────────────


def test_online_snapshot_decrypts_to_plaintext(enc, tmp_path):
    conn = database.get_conn()
    try:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        conn.execute("INSERT INTO t (v) VALUES ('payload')")
        conn.commit()
    finally:
        conn.close()

    snap = tmp_path / "snapshot.db"
    database.online_snapshot(enc, snap)

    # 快照是明文 SQLite，原生 sqlite3 可直接讀、資料對得上
    assert _is_plaintext_sqlite(snap)
    c = sqlite3.connect(str(snap))
    try:
        assert c.execute("SELECT v FROM t").fetchone()[0] == "payload"
    finally:
        c.close()


# ── encrypt_db / reencrypt_in_place：明文 → 加密（migration / restore）────────


def test_encrypt_db_roundtrip(enc, tmp_path, monkeypatch):
    plain = tmp_path / "plain.db"
    c = sqlite3.connect(str(plain))
    try:
        c.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        c.execute("INSERT INTO t (v) VALUES ('x')")
        c.commit()
    finally:
        c.close()

    out = tmp_path / "enc.db"
    database.encrypt_db(plain, out)

    assert not _is_plaintext_sqlite(out)  # 已加密
    # 以 DB_PATH=out 走 get_conn（加密）讀回
    monkeypatch.setattr(database, "DB_PATH", out)
    conn = database.get_conn()
    try:
        assert conn.execute("SELECT v FROM t").fetchone()[0] == "x"
    finally:
        conn.close()


def test_reencrypt_in_place(enc, tmp_path, monkeypatch):
    target = tmp_path / "live.db"
    c = sqlite3.connect(str(target))
    try:
        c.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        c.execute("INSERT INTO t (v) VALUES ('inplace')")
        c.commit()
    finally:
        c.close()
    assert _is_plaintext_sqlite(target)

    database.reencrypt_in_place(target)

    assert target.exists()
    assert not _is_plaintext_sqlite(target)  # 就地轉加密
    assert not target.with_name(target.name + ".plain-tmp").exists()  # 暫存已清

    monkeypatch.setattr(database, "DB_PATH", target)
    conn = database.get_conn()
    try:
        assert conn.execute("SELECT v FROM t").fetchone()[0] == "inplace"
    finally:
        conn.close()


# ── open_readonly_live：加密唯讀探針 ─────────────────────────────────────────


def test_open_readonly_live_encrypted(enc):
    conn = database.get_conn()
    try:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()

    ro = database.open_readonly_live(enc)
    try:
        assert ro.execute("SELECT 1").fetchone()[0] == 1
        # query_only：寫入應被拒
        with pytest.raises(Exception):
            ro.execute("INSERT INTO t (id) VALUES (1)")
            ro.commit()
    finally:
        ro.close()
