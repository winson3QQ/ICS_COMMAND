"""
repositories/_helpers.py — 共用 DB 工具函式
"""

import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta

from core.database import get_conn

# ── P1-14：exercise filter 第三態 sentinel ──────────────────────────────────
# repo 的 exercise_id 過濾參數有三種語意，None 無法同時表達「IS NULL」與「不過濾」，
# 故引入模組級 sentinel 區分：
#   - `int`        → exact match：WHERE exercise_id = ?
#   - `NULL_SCOPE` → WHERE exercise_id IS NULL（實戰 / 未分場池；strict isolation 用）
#   - `None`       → 不加 exercise filter（內部 / 既有 caller 撈全部用，行為不變）
# 放在 _helpers 而非 service：避免 _helpers ← repos ← exercise_service ← _helpers 形成
# import 環（sentinel 是最底層常數，無依賴）。
NULL_SCOPE = object()


def now_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_utc(s: str | None) -> str | None:
    """各種時間格式正規化為 ISO 8601 UTC+Z，None 回 None"""
    if not s:
        return None
    s = s.strip()
    if s.endswith("Z"):
        return s
    if " " in s and "T" not in s:
        s = s.replace(" ", "T", 1)
    if "+" in s or (s.count("-") > 2):
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            pass
    return s + "Z"


def iso_to_dt(iso: str) -> datetime:
    """ISO 8601 字串 → aware UTC datetime。

    naive（無時區）一律視為 UTC（補 tzinfo），確保跨來源時間能安全相減比較——
    XML parse 路徑已正規化為 `...Z`（aware），但 REST push（`POST /api/tak/events`）
    的 time 未正規化（`schemas/tak.py` 只 min_length=1），可能是 naive 或帶 offset。
    統一補 UTC 後 aware−aware 相減，不會觸發 aware−naive 的 TypeError。
    格式非法 → raise ValueError（caller 決定容錯）。
    """
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def add_minutes(iso_str: str, minutes: int) -> str:
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    return (dt + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    if "extra" in d and d["extra"]:
        try:
            d["extra"] = json.loads(d["extra"])
        except Exception:
            pass
    return d


def audit(
    operator: str,
    device_id: str | None,
    action_type: str,
    target_table: str,
    target_id: str,
    detail: dict,
    exercise_id: int | None = None,
    correlation_id: str | None = None,
):
    from core.audit_chain import compute_next_hash_prev
    from core.logging import get_correlation_id

    cid = correlation_id if correlation_id is not None else get_correlation_id()
    # GAP-AUDIT-04: hash_prev = prev record canonical hash (NIST AU-9(3))
    sql = """
        INSERT INTO audit_log
            (operator, device_id, action_type, target_table, target_id,
             detail, correlation_id, exercise_id, created_at, hash_prev)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """
    with get_conn() as conn:
        # Model B（issue #93）：未明傳 exercise_id → 戳當前 active session（演習/實戰），使 AAR
        # 能回放該場完整時間軸（含登入/設定/COP 操作等大小事）；無 active → NULL（實戰未分場）。
        # **exercise_* 生命週期 audit 例外**：保持 NULL，否則會被該場 cascade 刪除
        # （audit_log ∈ _EXERCISE_SCOPED_TABLES）→ 刪除/狀態軌跡無法留存（review #93-1）。
        # **#207 加例外**：跨演習系統掃除 `RETENTION_*`——TTL 清的是**所有場**的過期軌跡，
        # 綁單一 active 場語意錯置、且 PII 刪除證明會隨該場 cascade 消失（個資合規須留痕）。
        # 查詢複用本連線（不另開連線、不 import exercise_repo）：避免循環 import 與額外連線 lock 面。
        _system_scope = action_type.startswith("exercise_") or action_type.startswith("RETENTION_")
        if exercise_id is None and not _system_scope:
            _row = conn.execute("SELECT id FROM exercises WHERE status='active' LIMIT 1").fetchone()
            exercise_id = _row[0] if _row else None
        hash_prev = compute_next_hash_prev(conn)
        conn.execute(
            sql,
            (
                operator,
                device_id,
                action_type,
                target_table,
                str(target_id),
                json.dumps(detail, ensure_ascii=False),
                cid or None,
                exercise_id,
                now_utc(),
                hash_prev,
            ),
        )


# ── PIN hashing（PBKDF2-SHA256, 100k iterations）──────────────────────────


def hash_pin(pin: str, salt_hex: str | None = None) -> tuple[str, str]:
    if salt_hex is None:
        salt = os.urandom(16)
        salt_hex = salt.hex()
    else:
        salt = bytes.fromhex(salt_hex)
    h = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, 100_000)
    return h.hex(), salt_hex


def verify_pin(pin: str, stored_hash: str, stored_salt: str) -> bool:
    h, _ = hash_pin(pin, stored_salt)
    return h == stored_hash
