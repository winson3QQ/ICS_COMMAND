"""
backup_service.py — 指揮部 SQLite 自動備份服務

對應 ROADMAP C3-D；NIST 800-53 CP-9 / CP-10 / SC-28（Backup DR Drill #41）、
CIS Controls v8 §11、個資法 §27 安全維護義務。

設計原則：
- 用 SQLite online backup API（sqlite3.Connection.backup）— 相容 WAL，
  並發寫入時仍能取得 consistent snapshot
- gzip 壓縮 + sha256 校驗 + atomic write（先寫 .tmp 再 rename）
- 7 天 rolling retention（可設定，#41 Sync v2 從 30 縮減）
- ⭐ #41 Backup DR Drill: Fernet AES-128-CBC 加密層（E-1, NIST SC-28）
  - encrypt_file / decrypt_file: 對 .db.gz 加密成 .db.gz.enc（Sync v3 後綴疊加）
  - key 來源 BACKUP_ENCRYPTION_KEY env var（E-3, env-only, 無 rotation）
- License 無感（per Decision E：法規 / 安全功能不接受 license 控制；
  全 tier 必開）
"""

from __future__ import annotations

import gzip
import hashlib
import os
import shutil
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog

from core.database import online_snapshot

log = structlog.get_logger()

# #41 Sync v2: 7 天 default (從 30 校準, 對齊 production 演練週期 + storage 成本)
DEFAULT_RETAIN_DAYS = 7
BACKUP_FILENAME_PATTERN = "ics-%Y-%m-%dT%H-%M-%SZ.db.gz"
# #41 Sync v3: 加密 backup 檔名 = .db.gz + .enc 後綴疊加 (對齊既有 ISO 8601 + .enc)
BACKUP_ENCRYPTED_FILENAME_PATTERN = "ics-%Y-%m-%dT%H-%M-%SZ.db.gz.enc"
# #41 E-3: 加密 key 從 env var 讀取 (production 部署時 deploy SOP 設定)
# P1-12b（#228）：統一金鑰來源 = BACKUP_KEY（HKDF child[0]，unlock_key.py 提供）優先，
# fallback 舊 BACKUP_ENCRYPTION_KEY（過渡相容）。與 user_data_backup_service 同源。
BACKUP_KEY_ENV = "BACKUP_KEY"
BACKUP_ENCRYPTION_KEY_ENV = "BACKUP_ENCRYPTION_KEY"


@dataclass
class BackupResult:
    path: Path
    size_bytes: int
    duration_ms: int
    timestamp: datetime
    sha256: str


@dataclass
class BackupInfo:
    path: Path
    size_bytes: int
    timestamp: datetime  # parsed from filename


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _format_filename(ts: datetime) -> str:
    return ts.strftime(BACKUP_FILENAME_PATTERN)


def _format_encrypted_filename(ts: datetime) -> str:
    """#41 Sync v3: encrypted backup 檔名 (.db.gz.enc 後綴疊加)"""
    return ts.strftime(BACKUP_ENCRYPTED_FILENAME_PATTERN)


def _parse_timestamp(filename: str) -> datetime | None:
    """解析 backup 檔名為 timestamp (支援 .db.gz 與 .db.gz.enc, #41 Sync v3)"""
    for pattern in (BACKUP_FILENAME_PATTERN, BACKUP_ENCRYPTED_FILENAME_PATTERN):
        try:
            return datetime.strptime(filename, pattern).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ─── #41 Backup DR Drill: Fernet 加密層（E-1, E-3, NIST SC-28）──────────────


def _load_encryption_key() -> bytes:
    """從 BACKUP_ENCRYPTION_KEY env var 讀取 Fernet key（E-3, env-only）。

    Returns:
        Fernet key (32 url-safe base64 bytes)

    Raises:
        RuntimeError: env var 未設定 (production deploy SOP 必須設此 env var)
    """
    key = os.getenv(BACKUP_KEY_ENV) or os.getenv(BACKUP_ENCRYPTION_KEY_ENV)
    if not key:
        raise RuntimeError(
            f"{BACKUP_KEY_ENV}（或舊 {BACKUP_ENCRYPTION_KEY_ENV}）env var 未設定 — "
            f"加密 backup 必需此 key。P1-12a unlock_key.py 提供 BACKUP_KEY，或"
            f"產生: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
        )
    return key.encode() if isinstance(key, str) else key


def encrypt_file(plaintext_path: Path, output_path: Path, *, key: bytes | None = None) -> Path:
    """將 plaintext .db.gz 加密成 .db.gz.enc (Fernet AES-128-CBC, E-1)。

    Args:
        plaintext_path: 來源檔案 (e.g. .db.gz from create_backup)
        output_path:    輸出加密檔案 (e.g. .db.gz.enc)
        key:            Fernet key (None → 從 env var 讀)

    Returns:
        output_path

    Note:
        Fernet 是 stream-safe 但會 load 整個檔案進 memory.
        SQLite backup 一般 < 100MB, 不擔心 OOM. 若需 chunked encryption 留 future.
    """
    from cryptography.fernet import Fernet  # lazy import (E-4 dep)

    fernet_key = key or _load_encryption_key()
    f = Fernet(fernet_key)
    with plaintext_path.open("rb") as fin:
        plaintext = fin.read()
    ciphertext = f.encrypt(plaintext)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = output_path.with_suffix(output_path.suffix + ".tmp")
    with tmp_out.open("wb") as fout:
        fout.write(ciphertext)
    tmp_out.replace(output_path)  # atomic
    log.info(
        "backup_encrypted",
        msg=f"備份已加密 (Fernet AES-128-CBC), 大小 {output_path.stat().st_size} bytes",
        detail={
            "src": str(plaintext_path),
            "dst": str(output_path),
            "src_size": plaintext_path.stat().st_size,
            "dst_size": output_path.stat().st_size,
        },
    )
    return output_path


def decrypt_file(ciphertext_path: Path, output_path: Path, *, key: bytes | None = None) -> Path:
    """將 .db.gz.enc 解密成 .db.gz (Fernet, restore drill 用)。

    Args:
        ciphertext_path: 加密來源 (e.g. .db.gz.enc)
        output_path:     解密輸出 (e.g. .db.gz)
        key:             Fernet key (None → env var)

    Returns:
        output_path

    Raises:
        cryptography.fernet.InvalidToken: key 不對 / 檔案損毀
    """
    from cryptography.fernet import Fernet  # lazy import

    fernet_key = key or _load_encryption_key()
    f = Fernet(fernet_key)
    with ciphertext_path.open("rb") as fin:
        ciphertext = fin.read()
    plaintext = f.decrypt(ciphertext)  # raises InvalidToken if tampered/wrong key
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = output_path.with_suffix(output_path.suffix + ".tmp")
    with tmp_out.open("wb") as fout:
        fout.write(plaintext)
    tmp_out.replace(output_path)  # atomic
    log.info(
        "backup_decrypted",
        msg="備份已解密",
        detail={"src": str(ciphertext_path), "dst": str(output_path)},
    )
    return output_path


def create_backup(
    db_path: Path,
    backup_dir: Path,
    *,
    timestamp: datetime | None = None,
) -> BackupResult:
    """產生 SQLite gzipped backup。

    使用 online backup API 取得 consistent snapshot（即使 source DB 正在寫入）。
    寫入採 atomic：先寫 .tmp，校驗後 rename。
    """
    if not db_path.exists():
        raise FileNotFoundError(f"來源 DB 不存在：{db_path}")

    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = timestamp or _now_utc()
    final_path = backup_dir / _format_filename(ts)
    tmp_gz = final_path.with_suffix(final_path.suffix + ".tmp")

    started = time.perf_counter()

    with tempfile.NamedTemporaryFile(prefix="ics-backup-", suffix=".db", dir=backup_dir, delete=False) as raw_tmp:
        raw_tmp_path = Path(raw_tmp.name)

    try:
        # P1-12c #229：online_snapshot 收口——明文 live DB 走 sqlite3 backup API（原行為），
        # 加密 live DB 走 sqlcipher_export 解成明文。產物恆為明文 .db（外層 gzip+Fernet 保護）。
        online_snapshot(db_path, raw_tmp_path)

        with raw_tmp_path.open("rb") as fin, gzip.open(tmp_gz, "wb", compresslevel=6) as fout:
            shutil.copyfileobj(fin, fout)

        digest = _sha256_file(tmp_gz)
        size = tmp_gz.stat().st_size
        tmp_gz.replace(final_path)
        duration_ms = int((time.perf_counter() - started) * 1000)

        log.info(
            "backup_created",
            msg=f"備份建立完成，大小 {size} bytes，耗時 {duration_ms}ms",
            detail={"path": str(final_path), "size": size, "sha256": digest, "duration_ms": duration_ms},
        )
        return BackupResult(
            path=final_path,
            size_bytes=size,
            duration_ms=duration_ms,
            timestamp=ts,
            sha256=digest,
        )
    except Exception:
        if tmp_gz.exists():
            tmp_gz.unlink(missing_ok=True)
        raise
    finally:
        raw_tmp_path.unlink(missing_ok=True)


def list_backups(backup_dir: Path) -> list[BackupInfo]:
    """列出所有 backup（timestamp 由舊到新排序）。

    #41 Sync v3: 同時包含 .db.gz (plaintext) 與 .db.gz.enc (encrypted)
    """
    if not backup_dir.exists():
        return []
    out: list[BackupInfo] = []
    for p in backup_dir.iterdir():
        if not p.is_file():
            continue
        # 接受 .db.gz 或 .db.gz.enc (對齊 #41 Sync v3 後綴疊加)
        if not (p.name.endswith(".db.gz") or p.name.endswith(".db.gz.enc")):
            continue
        ts = _parse_timestamp(p.name)
        if ts is None:
            continue
        out.append(BackupInfo(path=p, size_bytes=p.stat().st_size, timestamp=ts))
    out.sort(key=lambda b: b.timestamp)
    return out


def cleanup_old_backups(
    backup_dir: Path,
    retain_days: int = DEFAULT_RETAIN_DAYS,
    *,
    now: datetime | None = None,
) -> list[Path]:
    """刪除超過 retain_days 的 backup，回傳被刪的路徑清單。"""
    cutoff = (now or _now_utc()) - timedelta(days=retain_days)
    deleted: list[Path] = []
    for b in list_backups(backup_dir):
        # 邊界含在內：保留最近 retain_days 天 → 第 retain_days 天當天的備份視為過期
        if b.timestamp <= cutoff:
            b.path.unlink()
            deleted.append(b.path)
            log.info(
                "backup_deleted",
                msg="備份過期刪除",
                detail={"path": str(b.path), "age_days": (cutoff - b.timestamp).days},
            )
    return deleted


def verify_backup(backup_path: Path) -> bool:
    """驗證 backup 是有效 SQLite + schema_migrations 表存在。"""
    if not backup_path.exists() or not backup_path.name.endswith(".db.gz"):
        return False
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        with gzip.open(backup_path, "rb") as fin, tmp_path.open("wb") as fout:
            shutil.copyfileobj(fin, fout)
        conn = sqlite3.connect(str(tmp_path))
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            ).fetchone()
            return row is not None
        finally:
            conn.close()
    except (gzip.BadGzipFile, sqlite3.DatabaseError):
        return False
    finally:
        tmp_path.unlink(missing_ok=True)


def restore_backup(
    backup_path: Path,
    target_db_path: Path,
    *,
    overwrite: bool = False,
) -> Path:
    """從 backup 還原到 target。

    safety：預設 overwrite=False；如果 target 已存在會 raise。
    要強制還原請明確帶 overwrite=True（playbook 應記錄此操作）。
    """
    if not verify_backup(backup_path):
        raise ValueError(f"backup 無效或損毀：{backup_path}")
    if target_db_path.exists() and not overwrite:
        raise FileExistsError(f"目標已存在：{target_db_path}（要覆寫請明確帶 overwrite=True，並先備份當前 DB）")
    target_db_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(backup_path, "rb") as fin, target_db_path.open("wb") as fout:
        shutil.copyfileobj(fin, fout)
    log.info("backup_restored", msg="備份還原成功", detail={"from": str(backup_path), "to": str(target_db_path)})
    return target_db_path
