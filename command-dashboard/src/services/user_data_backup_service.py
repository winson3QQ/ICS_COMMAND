"""
user_data_backup_service.py — P1-12b 整個 data/ 邊界的加密備份 / 還原（#228）

P1-13 確立 `data/` = user-data 邊界（CLAUDE.md 紅線）後，backup 應涵蓋整包
`data/`（`ics.db` + `map_config.json` + event_taxonomy + 未來 uploads），而非
DB-only。本 service 與既有 `backup_service.py`（DB-only gzip，systemd timer +
legacy 相容入口）並存，是 admin GUI 三層觸發 + restore 的主入口。

設計：
- tar 整個 data/（排除 data/backups/ 自己防遞迴）+ gzip + Fernet 加密
- MANIFEST.json 嵌在 archive 根（含 exercise metadata / trigger / app_version）
  → 機敏 metadata（演習名）隨 archive 加密，不另落明文
- 金鑰：BACKUP_KEY（P1-12a HKDF child[0]）優先，fallback 舊 BACKUP_ENCRYPTION_KEY
  → 既有 backup（舊 key 加密）在過渡期兩 env 並存時仍可解
- restore：解密 → 驗 manifest → 先自動備份當前（pre-restore-{ts}）→ 替換 data/

安全邊界（threat_model §3.4）：Fernet 只防 at-rest；runtime 已解密。
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import shutil
import tarfile
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import structlog

log = structlog.get_logger()

MANIFEST_NAME = "MANIFEST.json"
MANIFEST_SCHEMA = "ics-userdata-backup/v1"
FILENAME_PATTERN = "userdata-%Y-%m-%dT%H-%M-%SZ.tar.gz.enc"
PRE_RESTORE_PATTERN = "pre-restore-%Y-%m-%dT%H-%M-%SZ.tar.gz.enc"
BACKUP_SUBDIR = "backups"  # data/backups/ — 備份自身，打包時排除防遞迴

# 金鑰來源（threat_model §8.4：BACKUP_KEY 由 P1-12a unlock 提供 = HKDF child[0]）
BACKUP_KEY_ENV = "BACKUP_KEY"
LEGACY_KEY_ENV = "BACKUP_ENCRYPTION_KEY"  # #41 舊路徑，過渡相容


@dataclass
class BackupResult:
    path: Path
    size_bytes: int
    sha256: str
    duration_ms: int
    timestamp: datetime
    manifest: dict


def _now_utc() -> datetime:
    return datetime.now(UTC)


def key_available() -> bool:
    """是否有可用加密金鑰（L2/L3 觸發前判斷：無金鑰的 dev 部署直接略過備份）。"""
    return bool(os.getenv(BACKUP_KEY_ENV) or os.getenv(LEGACY_KEY_ENV))


def _encryption_key() -> bytes:
    """加密用金鑰：BACKUP_KEY 優先，fallback legacy。"""
    key = os.getenv(BACKUP_KEY_ENV) or os.getenv(LEGACY_KEY_ENV)
    if not key:
        raise RuntimeError(
            f"{BACKUP_KEY_ENV} 未設定（P1-12a unlock_key.py 提供，或過渡期設 {LEGACY_KEY_ENV}）"
        )
    return key.encode() if isinstance(key, str) else key


def _decryption_keys() -> list[bytes]:
    """解密候選金鑰：BACKUP_KEY 與 legacy 都試（過渡期舊 backup 仍可解）。"""
    keys = [os.getenv(BACKUP_KEY_ENV), os.getenv(LEGACY_KEY_ENV)]
    out = [k.encode() if isinstance(k, str) else k for k in keys if k]
    if not out:
        raise RuntimeError(f"{BACKUP_KEY_ENV}/{LEGACY_KEY_ENV} 皆未設定，無法解密")
    return out


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _iter_data_files(data_dir: Path) -> list[Path]:
    """data/ 下所有檔案，排除 backups/ 子目錄（防遞迴）。"""
    out: list[Path] = []
    backups_dir = data_dir / BACKUP_SUBDIR
    for p in data_dir.rglob("*"):
        if not p.is_file():
            continue
        if backups_dir in p.parents or p == backups_dir:
            continue
        out.append(p)
    return out


def _build_manifest(data_dir: Path, files: list[Path], *, trigger: str, exercise: dict | None) -> dict:
    from core.config import APP_VERSION

    return {
        "schema": MANIFEST_SCHEMA,
        "created_at": _now_utc().isoformat(),
        "app_version": APP_VERSION,
        "trigger": trigger,
        "exercise": exercise,  # None 或 {id,name,type,status}
        "files": sorted(str(p.relative_to(data_dir).as_posix()) for p in files),
        "total_bytes": sum(p.stat().st_size for p in files),
    }


def _encrypt(plaintext: bytes) -> bytes:
    from cryptography.fernet import Fernet  # lazy import

    return Fernet(_encryption_key()).encrypt(plaintext)


def _decrypt(ciphertext: bytes) -> bytes:
    from cryptography.fernet import Fernet
    from cryptography.fernet import InvalidToken

    last: Exception | None = None
    for key in _decryption_keys():
        try:
            return Fernet(key).decrypt(ciphertext)
        except InvalidToken as e:  # 換下一把候選 key
            last = e
    raise ValueError("解密失敗 — 金鑰不符或檔案損毀/竄改") from last


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def create_backup(
    data_dir: Path,
    backup_dir: Path,
    *,
    trigger: str = "manual",
    exercise: dict | None = None,
    timestamp: datetime | None = None,
    filename_pattern: str = FILENAME_PATTERN,
) -> BackupResult:
    """打包 data/ 整包 → gzip tar（含 MANIFEST.json）→ Fernet 加密 → atomic 寫出。"""
    if not data_dir.exists():
        raise FileNotFoundError(f"data 目錄不存在：{data_dir}")

    ts = timestamp or _now_utc()
    started = time.perf_counter()
    files = _iter_data_files(data_dir)
    manifest = _build_manifest(data_dir, files, trigger=trigger, exercise=exercise)
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")

    raw = io.BytesIO()
    with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=6, mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w") as tar:
            mi = tarfile.TarInfo(MANIFEST_NAME)
            mi.size = len(manifest_bytes)
            mi.mtime = 0
            tar.addfile(mi, io.BytesIO(manifest_bytes))
            for p in files:
                arc = "data/" + p.relative_to(data_dir).as_posix()
                tar.add(p, arcname=arc, recursive=False)

    ciphertext = _encrypt(raw.getvalue())
    final_path = backup_dir / ts.strftime(filename_pattern)
    _atomic_write(final_path, ciphertext)
    duration_ms = int((time.perf_counter() - started) * 1000)
    digest = _sha256_bytes(ciphertext)

    log.info(
        "userdata_backup_created",
        msg=f"data/ 備份完成 {final_path.stat().st_size} bytes / {len(files)} 檔 / {duration_ms}ms",
        detail={"path": str(final_path), "trigger": trigger, "files": len(files), "sha256": digest},
    )
    return BackupResult(
        path=final_path,
        size_bytes=final_path.stat().st_size,
        sha256=digest,
        duration_ms=duration_ms,
        timestamp=ts,
        manifest=manifest,
    )


def read_manifest(backup_path: Path) -> dict:
    """解密讀 MANIFEST.json（不解整包，restore 前預覽用）。

    驗 schema 欄位，異常 → ValueError（前端顯示「非本系統 backup」）。
    """
    ciphertext = backup_path.read_bytes()
    plaintext = _decrypt(ciphertext)
    with gzip.GzipFile(fileobj=io.BytesIO(plaintext), mode="rb") as gz:
        with tarfile.open(fileobj=gz, mode="r") as tar:
            try:
                member = tar.getmember(MANIFEST_NAME)
            except KeyError:
                raise ValueError("backup 缺 MANIFEST.json — 非本系統格式") from None
            fobj = tar.extractfile(member)
            if fobj is None:
                raise ValueError("MANIFEST.json 無法讀取")
            manifest = json.loads(fobj.read().decode("utf-8"))
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise ValueError(f"manifest schema 不符：{manifest.get('schema')}")
    return manifest


def _safe_extract(tar: tarfile.TarFile, dest: Path) -> None:
    """path-traversal-safe 解壓（拒絕 archive 內絕對路徑 / .. 逃逸）。"""
    dest_resolved = dest.resolve()
    for member in tar.getmembers():
        target = (dest / member.name).resolve()
        if dest_resolved != target and dest_resolved not in target.parents:
            raise ValueError(f"backup 含逃逸路徑，拒絕還原：{member.name}")
    # data="data" filter（Python 3.12+）：另擋符號連結逃逸 / 特殊檔，與上方
    # 路徑檢查雙保險。Python 3.14 起為預設，提前顯式指定避免 DeprecationWarning。
    tar.extractall(dest, filter="data")  # nosec B202 — 已逐 member 驗證在 dest 內 + filter


def restore_backup(
    backup_path: Path,
    data_dir: Path,
    backup_dir: Path,
    *,
    pre_restore: bool = True,
) -> dict:
    """還原 backup 到 data/。失敗不動 current（先解到 temp 驗證才替換）。

    流程：解密驗 manifest → （pre_restore）先備份當前為 pre-restore-{ts}
    → 解到 temp → 替換 data/ 下非 backups/ 的內容 → 回 manifest + restart 提示。
    """
    manifest = read_manifest(backup_path)  # 解密 + schema 驗證（失敗即止，不動 current）

    pre_restore_path: Path | None = None
    if pre_restore and data_dir.exists() and _iter_data_files(data_dir):
        pre = create_backup(
            data_dir, backup_dir, trigger="pre-restore", filename_pattern=PRE_RESTORE_PATTERN
        )
        pre_restore_path = pre.path

    plaintext = _decrypt(backup_path.read_bytes())
    with tempfile.TemporaryDirectory(prefix="ics-restore-") as td:
        tmp = Path(td)
        with gzip.GzipFile(fileobj=io.BytesIO(plaintext), mode="rb") as gz:
            with tarfile.open(fileobj=gz, mode="r") as tar:
                _safe_extract(tar, tmp)
        extracted = tmp / "data"
        if not extracted.exists():
            raise ValueError("backup 內無 data/ — 結構不符，已中止（current 未動）")

        # 替換 data/ 下非 backups/ 的內容（backups/ 保留：含剛產生的 pre-restore）
        data_dir.mkdir(parents=True, exist_ok=True)
        for child in data_dir.iterdir():
            if child.name == BACKUP_SUBDIR:
                continue
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        for child in extracted.iterdir():
            dest = data_dir / child.name
            if child.is_dir():
                shutil.copytree(child, dest)
            else:
                shutil.copy2(child, dest)

    log.info(
        "userdata_restored",
        msg=f"data/ 已從 {backup_path.name} 還原",
        detail={"from": str(backup_path), "pre_restore": str(pre_restore_path) if pre_restore_path else None},
    )
    return {
        "manifest": manifest,
        "pre_restore": pre_restore_path.name if pre_restore_path else None,
        "restart_required": True,  # 替換熱 DB 檔，需重啟服務讓新連線生效
    }
