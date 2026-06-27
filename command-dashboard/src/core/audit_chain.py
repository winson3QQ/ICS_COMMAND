# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
core/audit_chain.py — Audit log hash chain (NIST AU-9(3))

Codeberg Issue #1 GAP-AUDIT-04 — 為 audit_log 加入 SHA-256 hash chain，
使任一 record 被 UPDATE / DELETE 後 verify_audit_chain() 可偵測斷點。

設計（GK Step A Approval A-3 + Amendment v1 Sync v2 + CA Stage 6 校準）:
  Hash = SHA-256(canonical_form(record))
  canonical 含 hash_prev field (chain 鏈)

Canonical form (Command, 對齊 audit_log 實際 schema, m012 後):
  "|".join([
    str(id), str(operator), str(device_id), str(action_type),
    str(target_table), str(target_id), str(detail),
    str(exercise_id), str(created_at), str(correlation_id),
    str(hash_prev),
  ])
  NULL → ""; INTEGER → str(int_value); UTF-8 encoded → SHA-256 hex.

⚠️ CA Stage 6 reality check 校準:
  - GK Amendment v1 Sync v2 「移除 correlation_id」基於 IA 看 stale ics.db 結論;
    實際 _m011 設計 correlation_id 屬 audit_log column → canonical 包含
    （對齊 design intent, 守 hash 涵蓋 column 完整性）
  - GK Amendment v1 Sync v1 「m007」基於 IA 看 schema_migrations applied versions;
    實際 _MIGRATIONS list code 已含 m007~m011 → 本 task 用 m012
    （見 _MIGRATIONS in core/database.py）

Pre-task records (hash_prev=NULL) 視為 chain 起點之前 (Step A Approval A-4);
verify 從第一筆 hash_prev != NULL 開始驗證, 對齊 prev_record canonical hash。
"""

import hashlib
import sqlite3

# Canonical column 順序 (frozen, 不可改順序)
_AUDIT_COLS: tuple[str, ...] = (
    "id",
    "operator",
    "device_id",
    "action_type",
    "target_table",
    "target_id",
    "detail",
    "exercise_id",
    "created_at",
    "correlation_id",
    "hash_prev",
)

_SELECT_AUDIT_SQL = (
    "SELECT id, operator, device_id, action_type, target_table, target_id, "
    "detail, exercise_id, created_at, correlation_id, hash_prev "
    "FROM audit_log "
)


def _canonical(record: dict) -> str:
    """Canonical form for hashing. NULL → ""; INTEGER → str."""
    parts = []
    for col in _AUDIT_COLS:
        val = record.get(col)
        if val is None:
            parts.append("")
        else:
            parts.append(str(val))
    return "|".join(parts)


def compute_hash(record: dict) -> str:
    """SHA-256 hex of canonical form。"""
    return hashlib.sha256(_canonical(record).encode("utf-8")).hexdigest()


def compute_next_hash_prev(conn: sqlite3.Connection) -> str | None:
    """為下一筆 INSERT 計算 hash_prev = prev record 完整 hash。

    若 audit_log 為空（第一筆 INSERT）→ 回 None（chain 起點 hash_prev=NULL）。
    INSERT hook 呼叫此函式取得 hash_prev field 值。
    """
    row = conn.execute(_SELECT_AUDIT_SQL + "ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return None
    record = dict(zip(_AUDIT_COLS, row, strict=True))
    return compute_hash(record)


def verify_audit_chain(conn: sqlite3.Connection) -> dict:
    """Verify hash chain integrity。

    回傳 {ok: bool, total: int, broken_at: int|None, reason: str|None}
      - total: 已驗證的 chain records 數量（不含 pre-task NULL records）
      - broken_at: 第一個斷點 record id（若 ok=True 為 None）
      - reason: 斷點描述（若 ok=True 為 None）

    Pre-task records (hash_prev=NULL) 視為 chain 起點之前，verify 不算它們，
    但記住 prev_record 用於對下一筆 chain record 比對。
    """
    rows = conn.execute(_SELECT_AUDIT_SQL + "ORDER BY id ASC").fetchall()

    prev_record: dict | None = None
    total_verified = 0

    for row in rows:
        record = dict(zip(_AUDIT_COLS, row, strict=True))
        if record["hash_prev"] is None:
            # Pre-task record (chain 起點之前) — 不 verify, 只記憶 prev
            prev_record = record
            continue

        # Chain record: 必須有 prev_record (才能比對)
        if prev_record is None:
            return {
                "ok": False,
                "total": total_verified,
                "broken_at": record["id"],
                "reason": (
                    f"chain record id={record['id']} has no prev record (audit_log 結構異常: 第一筆即非 NULL hash_prev)"
                ),
            }

        expected = compute_hash(prev_record)
        if record["hash_prev"] != expected:
            return {
                "ok": False,
                "total": total_verified,
                "broken_at": record["id"],
                "reason": (
                    f"hash_prev mismatch at id={record['id']}: "
                    f"expected {expected[:16]}..., got {str(record['hash_prev'])[:16]}..."
                ),
            }

        prev_record = record
        total_verified += 1

    return {"ok": True, "total": total_verified, "broken_at": None, "reason": None}


__all__ = ["compute_hash", "compute_next_hash_prev", "verify_audit_chain"]
