"""
core/database.py — SQLite 連線管理與 schema 初始化
"""

import os
import sqlite3
import sys
from collections.abc import Generator

from .config import DB_PATH


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_db() -> Generator[sqlite3.Connection]:
    """FastAPI Depends 使用"""
    conn = get_conn()
    try:
        yield conn
    finally:
        conn.close()


def _ensure_db_permissions() -> None:
    """DB 檔案權限強制 0600（trusted_keys 含 HMAC secret 明文）。
    Windows 跳過（NTFS ACL 由 OS 管理）。
    若檔案不存在則跳過（get_conn 建立後 Phase 2 再設）。
    """
    if sys.platform != "win32" and DB_PATH.exists():
        os.chmod(DB_PATH, 0o600)


def init_db() -> None:
    """建立所有資料表（idempotent）"""
    _ensure_db_permissions()  # Phase 1：DB 已存在時先鎖權限
    conn = get_conn()
    try:
        _create_tables(conn)
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()
    _ensure_db_permissions()  # Phase 2：get_conn 建立新 DB 後再鎖


# ─────────────────────────────────────────────────────────────────────────────
# Schema 定義
# ─────────────────────────────────────────────────────────────────────────────


def _create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    -- ── 既有表（保留相容，migration 補欄位）────────────────────────────────

    CREATE TABLE IF NOT EXISTS snapshots (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_id         TEXT UNIQUE NOT NULL,
        snapshot_time       TEXT NOT NULL,
        node_type           TEXT NOT NULL,
        source              TEXT DEFAULT 'auto',
        casualties_red      INTEGER,
        casualties_yellow   INTEGER,
        casualties_green    INTEGER,
        casualties_black    INTEGER,
        bed_used            INTEGER,
        bed_total           INTEGER,
        waiting_count       INTEGER,
        pending_evac        INTEGER,
        vehicle_available   INTEGER,
        staff_on_duty       INTEGER,
        extra               TEXT,
        received_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
        exercise_id         INTEGER REFERENCES exercises(id)
    );

    CREATE TABLE IF NOT EXISTS events (
        id                       TEXT PRIMARY KEY,
        event_code               TEXT UNIQUE,
        reported_by_unit         TEXT NOT NULL,
        location_desc            TEXT,
        location_zone_id         TEXT,
        event_type               TEXT NOT NULL,
        severity                 TEXT DEFAULT 'info',
        status                   TEXT DEFAULT 'open',
        response_type            TEXT,
        response_deadline        TEXT,
        needs_commander_decision INTEGER DEFAULT 0,
        description              TEXT,
        related_person_name      TEXT,
        assigned_unit            TEXT,
        occurred_at              TEXT,
        operator_name            TEXT,
        notes                    TEXT,
        resolved_at              TEXT,
        created_at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
        exercise_id              INTEGER REFERENCES exercises(id),
        event_type_id            INTEGER REFERENCES event_types(id),
        acknowledged_at          TEXT,
        resolution_notes         TEXT
    );

    CREATE TABLE IF NOT EXISTS decisions (
        id                 TEXT PRIMARY KEY,
        primary_event_id   TEXT,
        decision_seq       INTEGER DEFAULT 1,
        parent_decision_id TEXT,
        superseded_by      TEXT,
        decision_type      TEXT NOT NULL,
        severity           TEXT NOT NULL,
        decision_title     TEXT NOT NULL,
        impact_description TEXT NOT NULL,
        suggested_action_a TEXT NOT NULL,
        suggested_action_b TEXT,
        status             TEXT DEFAULT 'pending',
        decided_by         TEXT,
        decided_at         TEXT,
        execution_note     TEXT,
        created_by         TEXT NOT NULL,
        created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
        exercise_id        INTEGER REFERENCES exercises(id),
        decision_type_v2   TEXT,
        rationale          TEXT,
        affected_units     TEXT,
        outcome_at         TEXT,
        outcome_notes_ext  TEXT
    );

    CREATE TABLE IF NOT EXISTS audit_log (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        operator     TEXT,
        device_id    TEXT,
        action_type  TEXT NOT NULL,
        target_table TEXT,
        target_id    TEXT,
        detail       TEXT,
        correlation_id TEXT,
        exercise_id  INTEGER REFERENCES exercises(id),
        created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
    );

    CREATE TABLE IF NOT EXISTS manual_records (
        id           TEXT PRIMARY KEY,
        form_id      TEXT NOT NULL,
        form_type    TEXT,
        target_table TEXT,
        operator     TEXT NOT NULL,
        summary      TEXT,
        payload      TEXT,
        sync_status  TEXT DEFAULT 'pending',
        submitted_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
        synced_at    TEXT,
        exercise_id  INTEGER REFERENCES exercises(id)
    );

    CREATE TABLE IF NOT EXISTS predictions (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        unit        TEXT NOT NULL,
        data        TEXT NOT NULL,
        created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
    );

    CREATE TABLE IF NOT EXISTS sync_log (
        id                 TEXT PRIMARY KEY,
        source_unit        TEXT NOT NULL,
        sync_started_at    TEXT NOT NULL,
        sync_completed_at  TEXT,
        data_gap_start     TEXT,
        data_gap_end       TEXT,
        pass1_merged       INTEGER DEFAULT 0,
        pass2_manual       INTEGER DEFAULT 0,
        pass3_added        INTEGER DEFAULT 0,
        conflicts_manual   INTEGER DEFAULT 0,
        status             TEXT NOT NULL DEFAULT 'pending',
        triggered_by       TEXT,
        operator           TEXT,
        detail             TEXT,
        created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
    );

    CREATE TABLE IF NOT EXISTS accounts (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        username     TEXT UNIQUE NOT NULL,
        role         TEXT NOT NULL DEFAULT '操作員',
        role_detail  TEXT,
        display_name TEXT,
        status       TEXT NOT NULL DEFAULT 'active',
        pin_hash     TEXT NOT NULL,
        pin_salt     TEXT NOT NULL,
        created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
        updated_at   TEXT,
        last_login   TEXT
    );

    CREATE TABLE IF NOT EXISTS config (
        key         TEXT PRIMARY KEY,
        value       TEXT,
        updated_by  TEXT,
        updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
    );

    CREATE TABLE IF NOT EXISTS pi_nodes (
        unit_id      TEXT PRIMARY KEY,
        label        TEXT NOT NULL,
        api_key      TEXT NOT NULL,
        last_seen_at TEXT,
        last_data_at TEXT,
        created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
        revoked_at   TEXT
    );

    CREATE TABLE IF NOT EXISTS pi_received_batches (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        unit_id      TEXT NOT NULL,
        pushed_at    TEXT NOT NULL,
        received_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
        records_json TEXT NOT NULL
    );

    -- ── ttx_injects（session_id 欄位舊版，migration 改名） ────────────────

    CREATE TABLE IF NOT EXISTS ttx_injects (
        id                  TEXT PRIMARY KEY,
        exercise_id         INTEGER REFERENCES exercises(id),
        inject_seq          INTEGER NOT NULL,
        target_unit         TEXT NOT NULL,
        inject_type         TEXT NOT NULL,
        title               TEXT NOT NULL,
        description         TEXT,
        payload             TEXT,
        scheduled_offset_min INTEGER DEFAULT 0,
        status              TEXT DEFAULT 'pending',
        injected_at         TEXT,
        signature           TEXT,
        created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
    );

    -- ── C0 新表 ───────────────────────────────────────────────────────────

    CREATE TABLE IF NOT EXISTS exercises (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        name               TEXT NOT NULL,
        date               TEXT,
        location           TEXT,
        type               TEXT NOT NULL DEFAULT 'ttx',  -- 'real' | 'ttx'
        scenario_summary   TEXT,
        weather            TEXT,
        participant_count  INTEGER,
        organizing_body    TEXT,
        status             TEXT NOT NULL DEFAULT 'setup', -- 'setup'|'active'|'archived'
        started_at         TEXT,
        ended_at           TEXT,
        -- TTX 專屬（type='real' 時為 NULL）
        facilitator        TEXT,
        scenario_id        TEXT,
        -- C5 前向相容
        mutex_locked       INTEGER NOT NULL DEFAULT 0,  -- 1 = 有 active exercise，不可並行
        created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
    );

    CREATE TABLE IF NOT EXISTS event_types (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        code             TEXT UNIQUE NOT NULL,
        name_zh          TEXT NOT NULL,
        category         TEXT NOT NULL,
        default_severity TEXT DEFAULT 'medium'
    );

    CREATE TABLE IF NOT EXISTS resource_snapshots (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        exercise_id    INTEGER REFERENCES exercises(id),
        unit_type      TEXT NOT NULL,
        snapshot_at    TEXT NOT NULL,
        total_beds     INTEGER,
        occupied_beds  INTEGER,
        light_count    INTEGER,
        medium_count   INTEGER,
        severe_count   INTEGER,
        deceased_count INTEGER,
        source         TEXT NOT NULL DEFAULT 'pi_push'
    );

    CREATE TABLE IF NOT EXISTS aar_entries (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        exercise_id INTEGER REFERENCES exercises(id),
        category    TEXT NOT NULL,  -- 'well'|'improve'|'recommend'
        content     TEXT NOT NULL,
        created_by  TEXT,
        created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
    );

    CREATE TABLE IF NOT EXISTS exercise_kpis (
        exercise_id INTEGER NOT NULL REFERENCES exercises(id),
        kpi_key     TEXT NOT NULL,
        kpi_value   REAL,
        computed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
        PRIMARY KEY (exercise_id, kpi_key)
    );

    CREATE TABLE IF NOT EXISTS ai_recommendations (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        exercise_id         INTEGER REFERENCES exercises(id),
        made_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
        recommendation_type TEXT NOT NULL,
        content             TEXT NOT NULL,
        confidence          REAL,
        accepted            INTEGER,  -- NULL=待決, 1=採納, 0=否決
        related_decision_id INTEGER REFERENCES decisions(id),
        outcome_notes       TEXT
    );

    -- ── RBAC roles（config key）───────────────────────────────────────────
    -- 用 accounts.role 欄位，合法值：
    --   'operator' | 'commander' | 'admin' | 'ttx_orchestrator'

    -- ── Sessions（持久化，server 重啟後仍有效）────────────────────────────
    CREATE TABLE IF NOT EXISTS sessions (
        token        TEXT PRIMARY KEY,
        username     TEXT NOT NULL,
        role         TEXT NOT NULL,
        role_detail  TEXT,
        display_name TEXT,
        last_active  TEXT NOT NULL,
        idle_at      TEXT,
        expires_at   TEXT,
        ip           TEXT,
        user_agent   TEXT,
        status       TEXT NOT NULL DEFAULT 'active',
        created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
    );

    -- ── TI-01：Trusted Ingest（HMAC 金鑰 + Nonce 快取）────────────────────

    CREATE TABLE IF NOT EXISTS trusted_keys (
        key_id              TEXT PRIMARY KEY,
        secret              TEXT NOT NULL,         -- hex 64 chars，明文存儲，需 DB chmod 0600
        status              TEXT NOT NULL DEFAULT 'active'
                            CHECK(status IN ('active', 'revoked', 'expired')),
        created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
        expires_at          TEXT,
        rotated_from_key_id TEXT
    );

    CREATE TABLE IF NOT EXISTS nonce_cache (
        nonce      TEXT PRIMARY KEY,
        created_at INTEGER NOT NULL               -- unix ms，用於 Lazy Expiry
    );

    -- ── CSP 違規記錄（C1-F RV2-01）────────────────────────────────────────
    -- browser 送 Content-Security-Policy violation report 至 /api/security/csp-report
    -- 用於 24h Report-Only 收集期間 + 切換 enforce 後的監測
    CREATE TABLE IF NOT EXISTS csp_violations (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        reported_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
        source_ip           TEXT,
        violated_directive  TEXT,
        blocked_uri         TEXT,
        document_uri        TEXT,
        raw_report          TEXT  -- JSON blob 全文，供後查
    );
    """)


# ─────────────────────────────────────────────────────────────────────────────
# Migration：版本化 schema 升級（C1-E）
# ─────────────────────────────────────────────────────────────────────────────


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    """建立 schema_migrations 追蹤表（若不存在）。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        )
    """)


def _applied_versions(conn: sqlite3.Connection) -> set[int]:
    return {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}


def _mark_applied(conn: sqlite3.Connection, version: int, name: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (?, ?)",
        (version, name),
    )


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")  # nosec B608


# ── 各版本 migration 函式 ──────────────────────────────────────────────────


def _m001_events_columns(conn: sqlite3.Connection) -> None:
    """events：補 exercise_id / event_type_id / assigned_unit / 時間欄位。"""
    _add_column_if_missing(conn, "events", "exercise_id", "INTEGER REFERENCES exercises(id)")
    _add_column_if_missing(conn, "events", "event_type_id", "INTEGER REFERENCES event_types(id)")
    _add_column_if_missing(conn, "events", "assigned_unit", "TEXT")
    _add_column_if_missing(conn, "events", "acknowledged_at", "TEXT")
    _add_column_if_missing(conn, "events", "resolved_at", "TEXT")
    _add_column_if_missing(conn, "events", "resolution_notes", "TEXT")


def _m002_decisions_columns(conn: sqlite3.Connection) -> None:
    """decisions：補 exercise_id / rationale / affected_units / outcome 欄位。"""
    _add_column_if_missing(conn, "decisions", "exercise_id", "INTEGER REFERENCES exercises(id)")
    _add_column_if_missing(conn, "decisions", "made_by", "TEXT")
    _add_column_if_missing(conn, "decisions", "decision_type", "TEXT")
    _add_column_if_missing(conn, "decisions", "rationale", "TEXT")
    _add_column_if_missing(conn, "decisions", "affected_units", "TEXT")
    _add_column_if_missing(conn, "decisions", "outcome_at", "TEXT")
    _add_column_if_missing(conn, "decisions", "outcome_notes", "TEXT")


def _m003_exercise_id_spread(conn: sqlite3.Connection) -> None:
    """snapshots / manual_records / audit_log：補 exercise_id。"""
    _add_column_if_missing(conn, "snapshots", "exercise_id", "INTEGER REFERENCES exercises(id)")
    _add_column_if_missing(conn, "manual_records", "exercise_id", "INTEGER REFERENCES exercises(id)")
    _add_column_if_missing(conn, "audit_log", "exercise_id", "INTEGER REFERENCES exercises(id)")


def _m004_c1a_accounts(conn: sqlite3.Connection) -> None:
    """C1-A：accounts 補登入鎖定 + 首次設定欄位。"""
    _add_column_if_missing(conn, "accounts", "failed_login_count", "INTEGER NOT NULL DEFAULT 0")
    _add_column_if_missing(conn, "accounts", "locked_until", "TEXT")
    # is_default_pin=1 表示尚未首次修改 PIN；舊帳號視為已設定，預設 0
    _add_column_if_missing(conn, "accounts", "is_default_pin", "INTEGER NOT NULL DEFAULT 0")


def _m006_csp_violations(conn: sqlite3.Connection) -> None:
    """C1-F RV2-01：補建 csp_violations 表（已存在的 DB 也跑一次）。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS csp_violations (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            reported_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            source_ip           TEXT,
            violated_directive  TEXT,
            blocked_uri         TEXT,
            document_uri        TEXT,
            raw_report          TEXT
        )
    """)


def _m005_ttx_injects_rebuild(conn: sqlite3.Connection) -> None:
    """ttx_injects：舊版有 session_id（FK → ttx_sessions），整表重建；清除 ttx_sessions。"""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "ttx_injects" in tables:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(ttx_injects)")}
        if "session_id" in cols:
            conn.execute("DROP TABLE ttx_injects")
    if "ttx_sessions" in tables:
        conn.execute("DROP TABLE IF EXISTS ttx_sessions")


# ── Migration 清單（version, name, fn）────────────────────────────────────


def _m007_sessions_idle_absolute(conn: sqlite3.Connection) -> None:
    _add_column_if_missing(conn, "sessions", "role_detail", "TEXT")
    _add_column_if_missing(conn, "sessions", "idle_at", "TEXT")
    _add_column_if_missing(conn, "sessions", "expires_at", "TEXT")
    _add_column_if_missing(conn, "sessions", "status", "TEXT NOT NULL DEFAULT 'active'")
    conn.execute("UPDATE sessions SET idle_at=COALESCE(idle_at, last_active)")
    conn.execute(
        "UPDATE sessions SET expires_at=COALESCE(expires_at, strftime('%Y-%m-%dT%H:%M:%SZ', created_at, '+14 hours'))"
    )


def _m008_sessions_binding(conn: sqlite3.Connection) -> None:
    _add_column_if_missing(conn, "sessions", "ip", "TEXT")
    _add_column_if_missing(conn, "sessions", "user_agent", "TEXT")


def _m009_accounts_soft_delete(conn: sqlite3.Connection) -> None:
    _add_column_if_missing(conn, "accounts", "deleted_at", "TEXT")
    _add_column_if_missing(conn, "accounts", "status", "TEXT NOT NULL DEFAULT 'active'")
    conn.execute("UPDATE accounts SET status='active' WHERE status IS NULL OR status=''")


def _m010_role_detail_backfill(conn: sqlite3.Connection) -> None:
    _add_column_if_missing(conn, "accounts", "role_detail", "TEXT")
    _add_column_if_missing(conn, "sessions", "role_detail", "TEXT")
    conn.execute("""
        UPDATE accounts
           SET role_detail = CASE
               WHEN role_detail = 'admin' THEN 'sysadmin'
               WHEN role_detail IN ('sysadmin','commander','operator','observer') THEN role_detail
               WHEN role = 'admin' THEN 'sysadmin'
               WHEN role = '系統管理員' THEN 'sysadmin'
               WHEN role = '指揮官' THEN 'commander'
               WHEN role = '操作員' THEN 'operator'
               WHEN role = '觀察員' THEN 'observer'
               ELSE COALESCE(role_detail, 'operator')
           END
    """)
    conn.execute("""
        UPDATE accounts
           SET role = CASE role_detail
               WHEN 'sysadmin' THEN '系統管理員'
               WHEN 'commander' THEN '指揮官'
               WHEN 'operator' THEN '操作員'
               WHEN 'observer' THEN '觀察員'
               ELSE role
           END
    """)
    conn.execute("UPDATE sessions SET role_detail='sysadmin' WHERE role_detail='admin'")


def _m010_role_detail_down(conn: sqlite3.Connection) -> None:
    _add_column_if_missing(conn, "accounts", "role_detail", "TEXT")
    conn.execute("UPDATE accounts SET role_detail=NULL")


def _m011_audit_correlation_id(conn: sqlite3.Connection) -> None:
    _add_column_if_missing(conn, "audit_log", "correlation_id", "TEXT")


def _m011_audit_correlation_id_down(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(audit_log)")}
    if "correlation_id" in cols:
        conn.execute("ALTER TABLE audit_log DROP COLUMN correlation_id")


def _m012_audit_hash_prev(conn: sqlite3.Connection) -> None:
    """Issue #1 (Codeberg) GAP-AUDIT-04 — audit_log hash chain (NIST AU-9(3))。

    新增 hash_prev TEXT column。既有 records (pre-task) hash_prev=NULL，
    視為 chain 起點之前；新 INSERT 從 m012 套用後第一筆開始 fill non-NULL hash。
    """
    _add_column_if_missing(conn, "audit_log", "hash_prev", "TEXT")


def _m012_audit_hash_prev_down(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(audit_log)")}
    if "hash_prev" in cols:
        conn.execute("ALTER TABLE audit_log DROP COLUMN hash_prev")


_MIGRATIONS: list[tuple[int, str, object]] = [
    (1, "events_columns", _m001_events_columns),
    (2, "decisions_columns", _m002_decisions_columns),
    (3, "exercise_id_spread", _m003_exercise_id_spread),
    (4, "c1a_accounts", _m004_c1a_accounts),
    (5, "ttx_injects_rebuild", _m005_ttx_injects_rebuild),
    (6, "csp_violations", _m006_csp_violations),
    (7, "sessions_idle_absolute", _m007_sessions_idle_absolute),
    (8, "sessions_binding", _m008_sessions_binding),
    (9, "accounts_soft_delete", _m009_accounts_soft_delete),
    (10, "role_detail_backfill", _m010_role_detail_backfill),
    (11, "audit_correlation_id", _m011_audit_correlation_id),
    (12, "audit_hash_prev", _m012_audit_hash_prev),
]


def _migrate(conn: sqlite3.Connection) -> None:
    """依序執行尚未套用的 migrations，已套用的跳過（idempotent）。"""
    _ensure_migrations_table(conn)
    applied = _applied_versions(conn)
    for version, name, fn in _MIGRATIONS:
        if version not in applied:
            fn(conn)
            _mark_applied(conn, version, name)


def get_schema_version(conn: sqlite3.Connection) -> int:
    """回傳目前已套用的最高 migration 版本號（0 表示全新 DB）。"""
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        return row[0] or 0
    except Exception:
        return 0


def get_health_schema_version(conn: sqlite3.Connection) -> int | None:
    """Health endpoint schema version; None means the schema table is unavailable."""
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        return row[0] if row and row[0] is not None else None
    except Exception:
        return None
