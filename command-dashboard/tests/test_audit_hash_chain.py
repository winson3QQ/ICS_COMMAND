# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
tests/test_audit_hash_chain.py — Audit Hash Chain (Codeberg Issue #1, GAP-AUDIT-04)

對應 GK Step A Approval §3 Acceptance Criteria + Amendment v1 + CA Stage 6 校準:
  AC-1  Command migration m012 (audit_log 含 hash_prev TEXT)
        ⚠️ Sync v1 frozen wording 「m007」基於 stale ics.db; CA Stage 6 校準 m012
        (對齊 _MIGRATIONS list code reality, m007~m011 已被占用)
  AC-2  新 INSERT 自動填入 hash_prev (SHA-256 hex, 非 NULL 從第二筆起)
  AC-3  verify_audit_chain() chain intact 全通過
  AC-4  verify_audit_chain() 偵測竄改 (UPDATE → broken_at row id)
  AC-8  既有 NULL hash_prev records 不影響新 INSERT (向後相容)

Sync v2 + CA Stage 6 校準: canonical form 含 correlation_id (對齊 _m011 design intent)。

執行: pytest command-dashboard/tests/test_audit_hash_chain.py -v
"""

import sqlite3

import pytest

from core.audit_chain import compute_hash, compute_next_hash_prev, verify_audit_chain
from core.database import get_conn


def _direct_insert(conn: sqlite3.Connection, **kwargs) -> int:
    """Bypass normal hooks — used to simulate pre-task NULL records & tampering setup."""
    fields = [
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
    ]
    cols = ", ".join(fields)
    placeholders = ", ".join("?" for _ in fields)
    values = tuple(kwargs.get(f) for f in fields)
    cur = conn.execute(f"INSERT INTO audit_log({cols}) VALUES ({placeholders})", values)
    return cur.lastrowid


def _hooked_insert(conn: sqlite3.Connection, **kwargs) -> int:
    """Insert through compute_next_hash_prev — simulates production INSERT hook."""
    hash_prev = compute_next_hash_prev(conn)
    fields = [
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
    ]
    cols = ", ".join(fields)
    placeholders = ", ".join("?" for _ in fields)
    values = tuple(kwargs.get(f) for f in fields[:-1]) + (hash_prev,)
    cur = conn.execute(f"INSERT INTO audit_log({cols}) VALUES ({placeholders})", values)
    conn.commit()
    return cur.lastrowid


# ── AC-1 ────────────────────────────────────────────────────────────────────


def test_audit_schema_migration_m012(tmp_db):
    """init_db (含 m012) 後 audit_log 必含 hash_prev TEXT column。"""
    with get_conn() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(audit_log)").fetchall()]
        assert "hash_prev" in cols, f"hash_prev column missing: {cols}"
        # schema_migrations 應到 m012
        max_v = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        assert max_v >= 12, f"schema_migrations max version {max_v} < 12"
        # CA Stage 6 紀律: m011 / m012 都該 applied
        applied = {
            r[0] for r in conn.execute("SELECT version FROM schema_migrations WHERE version IN (11, 12)").fetchall()
        }
        assert applied == {11, 12}, f"m011 + m012 must both be applied; got {applied}"


# ── AC-2 ────────────────────────────────────────────────────────────────────


def test_audit_hash_prev_populated_on_insert(tmp_db):
    """經 compute_next_hash_prev 寫入: 第一筆 hash_prev=NULL, 第二筆起非 NULL。"""
    with get_conn() as conn:
        rid1 = _hooked_insert(
            conn,
            operator="alice",
            device_id="dev1",
            action_type="login",
            target_table="accounts",
            target_id="1",
            detail="{}",
            exercise_id=None,
            created_at="2026-05-07T10:00:00Z",
            correlation_id="cid-001",
        )
        rid2 = _hooked_insert(
            conn,
            operator="bob",
            device_id="dev2",
            action_type="logout",
            target_table="accounts",
            target_id="2",
            detail="{}",
            exercise_id=None,
            created_at="2026-05-07T10:01:00Z",
            correlation_id="cid-002",
        )

        r1 = conn.execute("SELECT hash_prev FROM audit_log WHERE id=?", (rid1,)).fetchone()
        r2 = conn.execute("SELECT hash_prev FROM audit_log WHERE id=?", (rid2,)).fetchone()

        assert r1[0] is None, f"first INSERT hash_prev should be NULL (chain start), got {r1[0]!r}"
        assert r2[0] is not None, "second INSERT hash_prev should be non-NULL"
        # SHA-256 hex = 64 chars
        assert len(r2[0]) == 64, f"hash_prev should be 64-char SHA-256 hex, got len={len(r2[0])}"
        assert all(c in "0123456789abcdef" for c in r2[0]), f"hex only, got {r2[0]!r}"


# ── AC-3 ────────────────────────────────────────────────────────────────────


def test_audit_chain_verify_pass(tmp_db):
    """連續寫 5 筆 (經 hook), verify_audit_chain 應 ok=True, total=4 (chain skip first)。"""
    with get_conn() as conn:
        for i in range(5):
            _hooked_insert(
                conn,
                operator=f"user{i}",
                device_id=f"dev{i}",
                action_type="event",
                target_table="events",
                target_id=str(i),
                detail=f'{{"i":{i}}}',
                exercise_id=None,
                created_at=f"2026-05-07T10:0{i}:00Z",
                correlation_id=f"cid-{i}",
            )
        result = verify_audit_chain(conn)
        assert result["ok"] is True, f"chain should be intact, got {result}"
        assert result["total"] == 4, f"4 chain records (skip first NULL), got {result['total']}"
        assert result["broken_at"] is None
        assert result["reason"] is None


# ── AC-4 ────────────────────────────────────────────────────────────────────


def test_audit_chain_detect_tampering(tmp_db):
    """寫 5 筆後竄改第 3 筆的 detail, verify 應回 broken_at = 第 4 筆 id (chain 比對下一筆)."""
    with get_conn() as conn:
        ids = []
        for i in range(5):
            rid = _hooked_insert(
                conn,
                operator=f"user{i}",
                device_id=f"dev{i}",
                action_type="event",
                target_table="events",
                target_id=str(i),
                detail=f'{{"i":{i}}}',
                exercise_id=None,
                created_at=f"2026-05-07T10:0{i}:00Z",
                correlation_id=f"cid-{i}",
            )
            ids.append(rid)

        # 確認 chain pristine
        assert verify_audit_chain(conn)["ok"] is True

        # 竄改第 3 筆 (index 2 的 ids[2]) 的 detail
        conn.execute("UPDATE audit_log SET detail=? WHERE id=?", ('{"tampered": true}', ids[2]))
        conn.commit()

        result = verify_audit_chain(conn)
        assert result["ok"] is False, "tampering should be detected"
        # broken_at = 下一筆 (ids[3]), 因為它的 hash_prev 應該對應 prev (ids[2]) 但 prev 已改
        assert result["broken_at"] == ids[3], (
            f"broken_at should be next record after tampered ({ids[3]}), got {result['broken_at']}"
        )
        assert "hash_prev mismatch" in result["reason"]


# ── AC-8 ────────────────────────────────────────────────────────────────────


def test_audit_backward_compat_null_hash_prev(tmp_db):
    """既有 pre-task records (hash_prev=NULL) + 新 records (有 hash_prev) 共存; verify 從 chain 起點驗。"""
    with get_conn() as conn:
        # 模擬 pre-task: 3 筆 hash_prev=NULL (m012 之前已存在)
        for i in range(3):
            _direct_insert(
                conn,
                operator=f"legacy{i}",
                device_id=f"dev{i}",
                action_type="legacy_event",
                target_table="events",
                target_id=str(i),
                detail=f'{{"legacy":{i}}}',
                exercise_id=None,
                created_at=f"2026-05-01T10:0{i}:00Z",
                correlation_id=None,
                hash_prev=None,
            )
        conn.commit()

        # 新寫入 3 筆 (經 hook, 第一筆 hash_prev 對齊 prev=legacy2)
        new_ids = []
        for i in range(3):
            rid = _hooked_insert(
                conn,
                operator=f"new{i}",
                device_id=f"dev{i}",
                action_type="new_event",
                target_table="events",
                target_id=str(100 + i),
                detail=f'{{"new":{i}}}',
                exercise_id=None,
                created_at=f"2026-05-07T10:0{i}:00Z",
                correlation_id=f"cid-new-{i}",
            )
            new_ids.append(rid)

        # 第一筆新 record 應該有 hash_prev (對齊 legacy 第 3 筆)
        r = conn.execute("SELECT hash_prev FROM audit_log WHERE id=?", (new_ids[0],)).fetchone()
        assert r[0] is not None, "first new record should have hash_prev (對齊 last pre-task)"

        # verify chain: legacy 3 筆 skip (NULL), new 3 筆全 verify
        result = verify_audit_chain(conn)
        assert result["ok"] is True, f"backward compat broken: {result}"
        assert result["total"] == 3, f"3 new chain records, got {result['total']}"


# ── compute_hash deterministic / canonical form 含 correlation_id ───────────


def test_canonical_form_includes_correlation_id():
    """CA Stage 6 校準: canonical form 含 correlation_id (對齊 _m011 design intent,
    撤銷 GK Amendment v1 Sync v2 「移除 correlation_id」 wording)."""
    rec_a = {
        "id": 1,
        "operator": "x",
        "device_id": "d",
        "action_type": "a",
        "target_table": "t",
        "target_id": "1",
        "detail": "{}",
        "exercise_id": None,
        "created_at": "2026-05-07T10:00:00Z",
        "correlation_id": "cid-A",
        "hash_prev": None,
    }
    rec_b = dict(rec_a)
    rec_b["correlation_id"] = "cid-B"

    h_a = compute_hash(rec_a)
    h_b = compute_hash(rec_b)
    assert h_a != h_b, "canonical form 必須含 correlation_id (兩筆只 correlation_id 不同 → hash 必不同)"


@pytest.fixture(autouse=True)
def _isolate_db(tmp_db):
    """所有 test 強制用 tmp_db (init_db 含 m012)."""
    yield
