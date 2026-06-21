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
from datetime import UTC, datetime, timedelta
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

# 滾動保留（rolling retention）：手動會無限累積 → 定期清。保護 archive（演習結束
# 里程碑）+ pre-restore（還原前防呆）不被自動刪；其餘（manual/shutdown/pre-reset）
# 留最新 KEEP_MIN 筆，再老於 RETAIN_DAYS 才刪。env 可覆寫。
RETAIN_DAYS = int(os.getenv("ICS_BACKUP_RETAIN_DAYS", "30"))
KEEP_MIN = int(os.getenv("ICS_BACKUP_KEEP_MIN", "10"))


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


_SQLITE_MAGIC = b"SQLite format 3\x00"
_SQLITE_SIDECARS = ("-wal", "-shm", "-journal")  # WAL/SHM/rollback：runtime 暫態，不入備份


def _is_sqlite(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            return f.read(16) == _SQLITE_MAGIC
    except OSError:
        return False


def _online_backup_db(src_db: Path, dst: Path) -> None:
    """以一致快照把 live DB 匯出為**明文** .db（即使 DB 正被寫入）。

    取代「raw 複製 ics.db + 各別 tar -wal/-shm」—— 後者在 live DB 下會 torn snapshot
    （db 與 wal 讀取時間差→還原後不一致甚至損毀）。

    P1-12c #229：收口至 core.database.online_snapshot —— 明文 live DB 走 SQLite online
    backup API（內部 checkpoint，產出單一自洽 .db、不再含 -wal/-shm）；加密 live DB 走
    sqlcipher_export 解成明文。產物恆明文（外層 gzip+Fernet 保護），restore 路徑一致。
    """
    from core.database import online_snapshot

    online_snapshot(src_db, dst)


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
    prune: bool = True,
) -> BackupResult:
    """打包 data/ 整包 → gzip tar（含 MANIFEST.json）→ Fernet 加密 → atomic 寫出。

    prune=True（預設）建立後跑滾動保留；測試 setup 可關閉以免清掉刻意造的老檔。
    """
    if not data_dir.exists():
        raise FileNotFoundError(f"data 目錄不存在：{data_dir}")

    ts = timestamp or _now_utc()
    started = time.perf_counter()
    all_files = _iter_data_files(data_dir)

    # SQLite DB 走 online backup（一致快照）；其 -wal/-shm/-journal 暫態檔排除。
    db_files = [p for p in all_files if p.suffix == ".db" and _is_sqlite(p)]
    exclude: set[Path] = set()
    for db in db_files:
        exclude.add(db)
        exclude.update(db.with_name(db.name + suf) for suf in _SQLITE_SIDECARS)
    raw_files = [p for p in all_files if p not in exclude]

    # manifest 列「實際進包」的檔：raw + 一致快照的 db（不含 -wal/-shm）
    manifest = _build_manifest(data_dir, raw_files + db_files, trigger=trigger, exercise=exercise)
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")

    raw = io.BytesIO()
    with tempfile.TemporaryDirectory(prefix="ics-dbsnap-") as td:
        snap_dir = Path(td)
        with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=6, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w") as tar:
                mi = tarfile.TarInfo(MANIFEST_NAME)
                mi.size = len(manifest_bytes)
                mi.mtime = 0
                tar.addfile(mi, io.BytesIO(manifest_bytes))
                for p in raw_files:
                    arc = "data/" + p.relative_to(data_dir).as_posix()
                    tar.add(p, arcname=arc, recursive=False)
                for db in db_files:
                    snap = snap_dir / db.name
                    _online_backup_db(db, snap)  # 一致快照
                    arc = "data/" + db.relative_to(data_dir).as_posix()
                    tar.add(snap, arcname=arc, recursive=False)

    ciphertext = _encrypt(raw.getvalue())
    final_path = backup_dir / ts.strftime(filename_pattern)
    _atomic_write(final_path, ciphertext)
    duration_ms = int((time.perf_counter() - started) * 1000)
    digest = _sha256_bytes(ciphertext)

    n_files = len(manifest["files"])
    log.info(
        "userdata_backup_created",
        msg=f"data/ 備份完成 {final_path.stat().st_size} bytes / {n_files} 檔 / {duration_ms}ms",
        detail={"path": str(final_path), "trigger": trigger, "files": n_files, "sha256": digest},
    )
    # 滾動保留：每次建立後在此單一點 prune（涵蓋全部觸發 —— 手動 / archive / shutdown /
    # pre-reset，否則自動觸發路徑會無限累積）。pre-restore 略過：還原進行中不額外加延遲/
    # 風險（且它受保護不會被刪，跳過無損）。best-effort，不擋備份本身。
    if prune and trigger != "pre-restore":
        try:
            cleanup_user_data_backups(backup_dir)
        except Exception:
            log.warning("userdata_backup_cleanup_failed", msg="滾動保留清理失敗（best-effort）", exc_info=True)
    return BackupResult(
        path=final_path,
        size_bytes=final_path.stat().st_size,
        sha256=digest,
        duration_ms=duration_ms,
        timestamp=ts,
        manifest=manifest,
    )


def _manifest_from_plaintext(plaintext: bytes) -> dict:
    """從已解密的 tar.gz bytes 讀 MANIFEST.json + 驗 schema。"""
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


def read_manifest(backup_path: Path) -> dict:
    """解密讀 MANIFEST.json（不解整包，restore 前預覽用）。

    驗 schema 欄位，異常 → ValueError（前端顯示「非本系統 backup」）。
    """
    return _manifest_from_plaintext(_decrypt(backup_path.read_bytes()))


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
    # 解密一次，manifest 與解壓共用同份 plaintext（避免重複解密）
    plaintext = _decrypt(backup_path.read_bytes())
    manifest = _manifest_from_plaintext(plaintext)  # schema 驗證；失敗即止，不動 current

    pre_restore_path: Path | None = None
    if pre_restore and data_dir.exists() and _iter_data_files(data_dir):
        pre = create_backup(
            data_dir, backup_dir, trigger="pre-restore", filename_pattern=PRE_RESTORE_PATTERN
        )
        pre_restore_path = pre.path

    with tempfile.TemporaryDirectory(prefix="ics-restore-") as td:
        tmp = Path(td)
        with gzip.GzipFile(fileobj=io.BytesIO(plaintext), mode="rb") as gz:
            with tarfile.open(fileobj=gz, mode="r") as tar:
                _safe_extract(tar, tmp)
        extracted = tmp / "data"
        if not extracted.exists():
            raise ValueError("backup 內無 data/ — 結構不符，已中止（current 未動）")

        # 替換 data/ 下非 backups/ 的內容（backups/ 保留：含剛產生的 pre-restore）。
        # 解壓已完成、temp 內容已驗證在手 → wipe 才開始（縮短風險窗）。wipe 後若 copy
        # 失敗，data/ 會不完整 → 拋帶 pre-restore 名的錯，明示自動備份可救回（非靜默半毀）。
        data_dir.mkdir(parents=True, exist_ok=True)
        try:
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
        except OSError as e:
            hint = f"（請用 {pre_restore_path.name} 還原）" if pre_restore_path else "（無 pre-restore 可救）"
            log.error("userdata_restore_interrupted", msg=f"還原中斷，data/ 可能不完整{hint}", exc_info=True)
            raise RuntimeError(f"還原寫入中斷，data/ 可能不完整 — 請以 pre-restore 備份救回{hint}：{e}") from e

    # P1-12c #229：backup 內的 ics.db 恆為明文（產物設計）；加密部署下還原到 data/ 後須
    # re-encrypt，否則下次 get_conn 以 PRAGMA key 開明文檔會失敗。
    from core.config import DB_ENCRYPTED, DB_PATH

    if DB_ENCRYPTED:
        from core.database import reencrypt_in_place

        reencrypt_in_place(data_dir / DB_PATH.name)

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


def _parse_ts(name: str) -> datetime | None:
    """從備份檔名解時間戳（userdata-/pre-restore- 兩 pattern）。"""
    for pat in (FILENAME_PATTERN, PRE_RESTORE_PATTERN):
        try:
            return datetime.strptime(name, pat).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def _is_archive_backup(path: Path) -> bool:
    """解密讀 manifest 判斷是否 archive（演習結束）。解不開 → 保守視為「是」（保護）。"""
    try:
        return read_manifest(path).get("trigger") == "archive"
    except Exception:
        return True  # 無法判定 → 不刪（保守）


def cleanup_user_data_backups(
    backup_dir: Path,
    *,
    retain_days: int = RETAIN_DAYS,
    keep_min: int = KEEP_MIN,
    now: datetime | None = None,
) -> list[Path]:
    """滾動保留：刪老的非保護備份，回傳被刪路徑。

    保護（永不自動刪）：archive（演習結束里程碑）+ pre-restore（還原前防呆，檔名前綴）。
    其餘（manual/shutdown/pre-reset）：留最新 keep_min 筆；更舊且超過 retain_days 才刪。
    archive 判定需解密 manifest → 只對「已是刪除候選（夠舊、超出 keep_min、非 pre-restore）」
    的檔做，省解密。
    """
    if not backup_dir.exists():
        return []
    cutoff = (now or _now_utc()) - timedelta(days=retain_days)
    entries: list[tuple[Path, datetime | None, bool]] = []
    for p in backup_dir.iterdir():
        if p.is_file() and p.name.endswith(".tar.gz.enc"):
            entries.append((p, _parse_ts(p.name), p.name.startswith("pre-restore-")))
    # 最新在前（無法解析時間者排最後、視為最舊）
    entries.sort(key=lambda e: e[1] or datetime.min.replace(tzinfo=UTC), reverse=True)

    deleted: list[Path] = []
    for idx, (p, ts, is_pre) in enumerate(entries):
        if idx < keep_min:
            continue  # 留最新 keep_min 筆（不論種類）
        if ts is not None and ts > cutoff:
            continue  # 仍在保留窗內
        if is_pre:
            continue  # 保護 pre-restore
        if _is_archive_backup(p):
            continue  # 保護 archive（演習結束）
        try:
            p.unlink()
            deleted.append(p)
            log.info("userdata_backup_pruned", msg=f"滾動保留刪除 {p.name}", detail={"path": str(p)})
        except OSError:
            log.warning("userdata_backup_prune_failed", msg=f"刪除失敗 {p.name}", exc_info=True)
    return deleted
