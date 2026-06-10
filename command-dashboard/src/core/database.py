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


def _m013_cop_v1_schema(conn: sqlite3.Connection) -> None:
    """P1-03：COP（Common Operational Picture）正規化層 schema 凍結 v1。

    對齊 TAK CoT 規格（不自創）+ mini-taiwan map architecture 借鏡。
    支援 4 source: manual / pi-node / tak / waveink，以及 11 個 end-state scenarios
    （詳見 issue #15）。

    新增 3 張表：
    - cop_entities：地理性 entity 主表（CoT 主力，4 source 共用）
    - cop_entity_tracks：entity 軌跡時間序列（mini-taiwan 插值 + Wave 6 回放）
    - cop_entity_links：entity 關係（對齊 CoT_link.xsd）

    既有 events 表 ALTER：加 lat/lon 兩欄位（scenario 5：指揮部事件可地圖追蹤）。
    """
    # ── cop_entities ──────────────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cop_entities (
            uid            TEXT PRIMARY KEY,
            type           TEXT NOT NULL,
            time           TEXT NOT NULL,
            start          TEXT NOT NULL,
            stale          TEXT NOT NULL,
            how            TEXT NOT NULL,
            version        TEXT NOT NULL DEFAULT '2.0',
            lat            REAL NOT NULL,
            lon            REAL NOT NULL,
            hae            REAL NOT NULL DEFAULT 0,
            ce             REAL NOT NULL DEFAULT 9999999,
            le             REAL NOT NULL DEFAULT 9999999,
            heading_deg    REAL,
            speed_mps      REAL,
            source         TEXT NOT NULL
                CHECK(source IN ('manual','pi-node','tak','waveink')),
            received_at    TEXT NOT NULL
                DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            exercise_id    INTEGER REFERENCES exercises(id),
            access         TEXT,
            visible_to     TEXT NOT NULL DEFAULT '["all"]',
            origin_node_id TEXT,
            last_synced_at TEXT,
            version_clock  INTEGER NOT NULL DEFAULT 1,
            callsign       TEXT,
            remarks        TEXT,
            severity       TEXT NOT NULL DEFAULT 'info'
                CHECK(severity IN ('info','warning','critical')),
            attributes     TEXT NOT NULL DEFAULT '{}'
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cop_entities_stale    ON cop_entities(stale)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cop_entities_source   ON cop_entities(source)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cop_entities_type     ON cop_entities(type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cop_entities_exercise ON cop_entities(exercise_id)")

    # ── cop_entity_tracks ─────────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cop_entity_tracks (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            uid          TEXT NOT NULL REFERENCES cop_entities(uid) ON DELETE CASCADE,
            t            TEXT NOT NULL,
            lat          REAL NOT NULL,
            lon          REAL NOT NULL,
            hae          REAL NOT NULL DEFAULT 0,
            heading_deg  REAL,
            speed_mps    REAL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cop_tracks_uid_t ON cop_entity_tracks(uid, t)")

    # ── cop_entity_links ──────────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cop_entity_links (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            src_uid      TEXT NOT NULL REFERENCES cop_entities(uid) ON DELETE CASCADE,
            relation     TEXT NOT NULL,
            target_uid   TEXT NOT NULL,
            target_type  TEXT NOT NULL,
            url          TEXT,
            remarks      TEXT,
            mime         TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cop_links_src ON cop_entity_links(src_uid)")

    # ── events 表 ALTER：加 lat/lon（scenario 5 地圖追蹤）─────────────────
    _add_column_if_missing(conn, "events", "lat", "REAL")
    _add_column_if_missing(conn, "events", "lon", "REAL")


def _m013_cop_v1_schema_down(conn: sqlite3.Connection) -> None:
    """rollback：刪 3 表（events lat/lon 保留，反正 nullable）。"""
    conn.execute("DROP TABLE IF EXISTS cop_entity_links")
    conn.execute("DROP TABLE IF EXISTS cop_entity_tracks")
    conn.execute("DROP TABLE IF EXISTS cop_entities")


def _m014_cop_entities_audit_cols(conn: sqlite3.Connection) -> None:
    """Issue #29 PR-A：cop_entities 補 updated_by / updated_at。

    per-entity 協作編輯的 audit 落點：誰（updated_by）在何時（updated_at）改了
    這顆 entity。version_clock（樂觀鎖計數）已於 m013 預埋，本 migration 只補
    「最後一次變更的署名與時間」兩欄，供：
    - WS broadcast payload 附帶 updated_by（前線看得到是誰動的）
    - 409 conflict 回溯（兩人同改同一 uid 時，server_body 帶現值與 updater）

    兩欄皆 nullable：m013 之前既有的 entity（pi-node / tak ingest）updated_by=NULL
    表示「從未經 manual 編輯」，語意正確，不需 backfill。
    """
    _add_column_if_missing(conn, "cop_entities", "updated_by", "TEXT")
    _add_column_if_missing(conn, "cop_entities", "updated_at", "TEXT")


def _m014_cop_entities_audit_cols_down(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(cop_entities)")}
    for col in ("updated_at", "updated_by"):
        if col in cols:
            conn.execute(f"ALTER TABLE cop_entities DROP COLUMN {col}")  # nosec B608


def _m015_cop_entities_squad_cols(conn: sqlite3.Connection) -> None:
    """P2-06c（#126）：cop_entities 補小隊欄位 team_color / role / battery。

    從 CoT `<__group name=.. role=..>` 與 `<status battery=..>` 提取的一等欄位
    （對齊 callsign/remarks 先例，非 attributes JSON 鍵）。team_color 加 index 供
    P2-06d GROUP BY 聚合。三欄 nullable：非 TAK / 無 group 的 entity = NULL，語意正確。

    backfill（review #126-3）：既有 TAK entity（migration 前已 ingest）的 attributes JSON
    已含 __group/status，從中一次性回填，免等下次 update。title 標準化 SQL 難做 → 存 ATAK
    原始色名（通常已標準）；若大小寫不一，下次帶 __group 的 update 會覆寫成正規化值。
    """
    _add_column_if_missing(conn, "cop_entities", "team_color", "TEXT")
    _add_column_if_missing(conn, "cop_entities", "role", "TEXT")
    _add_column_if_missing(conn, "cop_entities", "battery", "INTEGER")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cop_entities_team_color ON cop_entities(team_color)")
    conn.execute("""
        UPDATE cop_entities
        SET team_color = json_extract(attributes, '$."__group".name'),
            role        = json_extract(attributes, '$."__group".role'),
            battery     = json_extract(attributes, '$."status".battery')
        WHERE source = 'tak' AND team_color IS NULL AND json_valid(attributes)
    """)


def _m015_cop_entities_squad_cols_down(conn: sqlite3.Connection) -> None:
    conn.execute("DROP INDEX IF EXISTS idx_cop_entities_team_color")
    cols = {row[1] for row in conn.execute("PRAGMA table_info(cop_entities)")}
    for col in ("battery", "role", "team_color"):
        if col in cols:
            conn.execute(f"ALTER TABLE cop_entities DROP COLUMN {col}")  # nosec B608


def _m016_chats_table(conn: sqlite3.Connection) -> None:
    """P2-07（#129）：GeoChat（CoT b-t-f）通聯記錄表，對齊 ICS-214 Unit Log。

    b-t-f 不進 cop_entities（作戰圖主表），由 cop_service.ingest_cot_event 分流至此。
    message 寫入前已由 chat_service `html.escape`（XSS 後端防線）。exercise_id 綁 active 場。
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chats (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_uid   TEXT NOT NULL,
            callsign     TEXT,
            message      TEXT NOT NULL DEFAULT '',
            "group"      TEXT,
            lat          REAL,
            lon          REAL,
            time         TEXT,
            exercise_id  INTEGER REFERENCES exercises(id),
            received_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chats_exercise ON chats(exercise_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chats_time ON chats(time)")


def _m016_chats_table_down(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS chats")


def _m017_cop_entities_planned_simulated(conn: sqlite3.Connection) -> None:
    """P2-11b（#140）：cop_entities 加 planned / simulated 旗標。

    - planned：MIL-STD-2525 空心框（計畫中）vs 實心框（實際）—— P2-13 下行指令用。
    - simulated：O/C 合成注入實體（`how="h-g-i-g-o"` CoT）—— P2-19 用，archive 時整批清除。

    兩欄 INTEGER 0/1（SQLite 無 bool type）NOT NULL DEFAULT 0：既有 entity 自動 = 0
    （實際 / 非合成），語意正確，不需 backfill。repo `_row_to_entity_dict` 轉回 bool。

    註：ROADMAP P2-11b 原列的 source enum 加 `'command'` 因 SQLite **CHECK 不可 ALTER**
    + table rebuild 撞 **FK cascade**（DROP parent 觸發 tracks/links `ON DELETE CASCADE`、
    `foreign_keys=OFF` 在 transaction 內無效）拆出，延 P2-13 動工前單獨謹慎 rebuild（#140 決策）。
    """
    _add_column_if_missing(conn, "cop_entities", "planned", "INTEGER NOT NULL DEFAULT 0")
    _add_column_if_missing(conn, "cop_entities", "simulated", "INTEGER NOT NULL DEFAULT 0")


def _m017_cop_entities_planned_simulated_down(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(cop_entities)")}
    for col in ("simulated", "planned"):
        if col in cols:
            conn.execute(f"ALTER TABLE cop_entities DROP COLUMN {col}")  # nosec B608


_COP_SOURCES_V1 = ("manual", "pi-node", "tak", "waveink")  # P1-03 凍結
_COP_SOURCES_V2 = (*_COP_SOURCES_V1, "command")  # #141：加 command（P2-13 下行指令來源）


def _rebuild_cop_entities(conn: sqlite3.Connection, source_values: tuple[str, ...]) -> None:
    """rebuild cop_entities，source CHECK 用給定 enum 值（#141 up/down 共用）。

    背景：SQLite **CHECK 不可 ALTER**，改 source 合法值集合只能整表 rebuild。cop_entities
    被 cop_entity_tracks / cop_entity_links 以 `ON DELETE CASCADE` reference，且 `get_conn`
    設 `PRAGMA foreign_keys=ON`。naive `DROP TABLE` parent（FK on）會觸發隱式 `DELETE FROM`
    → child CASCADE → **tracks/links 全刪**。

    解法：SQLite 官方 12-step 的 `foreign_keys=OFF` 版（實測 legacy_alter_table 在 3.50
    無法阻止 rename 改寫 child FK，不可用）——
      1. `PRAGMA foreign_keys=OFF`（DROP parent 不 cascade、不檢查 dangling）
      2. CREATE cop_entities_new（完整 33 欄，source CHECK = source_values）
      3. INSERT 用**動態舊欄位清單**（漏欄即 SQL 報錯，不靜默丟資料）
      4. DROP cop_entities（不 rename，避免 child FK 被改寫指向 _old）
      5. rename _new → cop_entities（child FK reference 'cop_entities' 對上 new）
      6. 動態重建所有原 index（讀 sqlite_master，含 m015 team_color，免手列漏）、`PRAGMA foreign_keys=ON`

    ⚠️ `foreign_keys` PRAGMA 只能在**無 transaction**時設，而 migration 跑在 init_db
    大 transaction 內 → 本函式先 `conn.commit()` 結束當前 transaction、切 autocommit、自開
    `BEGIN`/`COMMIT` 包 rebuild 保原子性（失敗 ROLLBACK），結束再切回。source_csv 由 code
    常數 tuple 組（非外部輸入）。
    """
    source_csv = ", ".join(f"'{s}'" for s in source_values)

    conn.commit()  # 結束 init_db 大 transaction（前面 migration 落地），讓 PRAGMA foreign_keys 可設
    prev_isolation = conn.isolation_level
    conn.isolation_level = None  # autocommit：PRAGMA foreign_keys 才生效
    began = False
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN")
        began = True
        old_cols = [r[1] for r in conn.execute("PRAGMA table_info(cop_entities)")]
        col_csv = ", ".join(old_cols)
        # 存所有 user index 的 CREATE SQL（DROP cop_entities 連帶刪 → rebuild 後動態重建，
        # 含 _m015 的 idx_cop_entities_team_color；手列 index 會漏建，review #141-1）
        idx_sqls = [
            r[0]
            for r in conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='cop_entities' AND sql IS NOT NULL"
            )
        ]
        conn.execute(f"""
            CREATE TABLE cop_entities_new (
                uid            TEXT PRIMARY KEY,
                type           TEXT NOT NULL,
                time           TEXT NOT NULL,
                start          TEXT NOT NULL,
                stale          TEXT NOT NULL,
                how            TEXT NOT NULL,
                version        TEXT NOT NULL DEFAULT '2.0',
                lat            REAL NOT NULL,
                lon            REAL NOT NULL,
                hae            REAL NOT NULL DEFAULT 0,
                ce             REAL NOT NULL DEFAULT 9999999,
                le             REAL NOT NULL DEFAULT 9999999,
                heading_deg    REAL,
                speed_mps      REAL,
                source         TEXT NOT NULL CHECK(source IN ({source_csv})),
                received_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                exercise_id    INTEGER REFERENCES exercises(id),
                access         TEXT,
                visible_to     TEXT NOT NULL DEFAULT '["all"]',
                origin_node_id TEXT,
                last_synced_at TEXT,
                version_clock  INTEGER NOT NULL DEFAULT 1,
                callsign       TEXT,
                remarks        TEXT,
                severity       TEXT NOT NULL DEFAULT 'info'
                    CHECK(severity IN ('info','warning','critical')),
                attributes     TEXT NOT NULL DEFAULT '{{}}',
                updated_by     TEXT,
                updated_at     TEXT,
                team_color     TEXT,
                role           TEXT,
                battery        INTEGER,
                planned        INTEGER NOT NULL DEFAULT 0,
                simulated      INTEGER NOT NULL DEFAULT 0,
                deleted        INTEGER NOT NULL DEFAULT 0,
                archived       INTEGER NOT NULL DEFAULT 0
            )
        """)  # nosec B608 — source_csv 為 code 常數 tuple，非外部輸入
        conn.execute(f"INSERT INTO cop_entities_new ({col_csv}) SELECT {col_csv} FROM cop_entities")  # nosec B608
        conn.execute("DROP TABLE cop_entities")  # foreign_keys=OFF → 不 cascade child（tracks/links 保留）
        conn.execute("ALTER TABLE cop_entities_new RENAME TO cop_entities")  # child FK 'cop_entities' 對上 new
        for idx_sql in idx_sqls:  # 動態重建所有原 index（含 m015 team_color，免手列漏建）
            conn.execute(idx_sql)
        conn.execute("COMMIT")
    except Exception:
        if began:  # BEGIN 前出錯時無 active transaction，ROLLBACK 會反拋蓋掉原因（review #141-4）
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.isolation_level = prev_isolation


def _m018_cop_entities_source_command(conn: sqlite3.Connection) -> None:
    """#141：cop_entities.source CHECK 加 'command'（P1-03 解凍，P2-13 下行指令來源）。

    table rebuild（CHECK 不可 ALTER）；idempotent：schema 已含 'command' 則 skip。
    詳見 _rebuild_cop_entities（foreign_keys=OFF 避 FK cascade）。
    """
    existing = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='cop_entities'").fetchone()
    if existing and "'command'" in existing[0]:
        return  # 已含 command（重跑）
    _rebuild_cop_entities(conn, _COP_SOURCES_V2)


def _m018_cop_entities_source_command_down(conn: sqlite3.Connection) -> None:
    """rollback：source CHECK 回 P1-03 的 4 值。

    ⚠️ 前提：rollback 前無 source='command' 的資料（否則 4-值 CHECK 在 INSERT 階段擋下、
    rebuild 失敗）。down 為 dev rollback 用途，正式環境不走。
    """
    existing = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='cop_entities'").fetchone()
    if existing and "'command'" not in existing[0]:
        return  # 已不含 command
    _rebuild_cop_entities(conn, _COP_SOURCES_V1)


def _m019_cop_entities_deleted(conn: sqlite3.Connection) -> None:
    """明確刪除墓碑（tombstone），與 stale（新鮮度）分離。

    背景：how=h-*（人工放置標記/繪圖）改持久化、豁免 stale 後，原本「stale=now 當刪除」對
    它們無效（持久 → 忽略 stale）。故需獨立的明確刪除旗標：deleted=1 在 list 預設**一律排除**，
    不論 how/stale。TAK `t-x-d-d` 刪除命令、操作員 DELETE 都改設此旗標。INTEGER 0/1
    NOT NULL DEFAULT 0，既有 row 自動=0（未刪），不需 backfill。
    """
    _add_column_if_missing(conn, "cop_entities", "deleted", "INTEGER NOT NULL DEFAULT 0")


def _m019_cop_entities_deleted_down(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(cop_entities)")}
    if "deleted" in cols:
        conn.execute("ALTER TABLE cop_entities DROP COLUMN deleted")  # nosec B608


def _m020_cop_entities_archived(conn: sqlite3.Connection) -> None:
    """#161：cop_entities 加 archived 旗標（CoT <archive/> 持久標記）。

    對齊 TAK 原生 streaming subscriber：archived=1 的外部 TAK entity（放置標記 a-*）在
    list 預設**豁免 stale**（過 stale 也保留，只有 deleted 墓碑才移除）＝對齊 TAK server
    repository + 其他 TAK client；無 archive（如繪圖 u-d-*）仍依 stale 過期。INTEGER 0/1
    NOT NULL DEFAULT 0，既有 row 自動=0（非持久，依 stale），語意正確不需 backfill。
    取代 WIP 0ba8fde 的 last-heard time 窗口。見 memory tak-streaming-archive-stale-vs-mission。
    """
    _add_column_if_missing(conn, "cop_entities", "archived", "INTEGER NOT NULL DEFAULT 0")


def _m020_cop_entities_archived_down(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(cop_entities)")}
    if "archived" in cols:
        conn.execute("ALTER TABLE cop_entities DROP COLUMN archived")  # nosec B608


def _m021_event_markers(conn: sqlite3.Connection) -> None:
    """P2-27：event↔marker 關聯 junction 表，取代 `attributes.kind='event'` JSON glue。

    分層解耦（見 docs/design/cop-event-layering.md）：感知標記（cop_entity）是一等公民
    （感知層、可共享）；事件（event）reference 一或多個標記（事故層、留 ICS）。**關係載
    junction 表、不污染 cop_entities schema** —— P2-30 share adapter 只唯讀標記欄、不讀本表，
    故標記外流時不帶事故層耦合（不外流邊界）。

    建模＝N:1（一事件聚多標記）且 **N:M-ready**（同一標記未來可關聯多事件，如一棟樓同屬
    火災+搜救）：PK(event_id, cop_entity_uid) 允許多 event 共享同 uid。雙向 ON DELETE
    CASCADE：event 硬刪→關聯消；marker 硬刪→關聯消（標記本體生命週期獨立、soft-delete
    墓碑不觸發）。role 預留（primary/related）。

    backfill：既有 `attributes.kind='event'` 的 cop_entity 從 `attributes.event_id` 一次性
    回填，**僅當該 event 仍存在**（EXISTS 守門滿足 events FK，避免孤兒關聯炸 FK）。
    cop_entity_uid 取自 cop_entities 自身、必滿足該側 FK。INSERT OR IGNORE 容重跑。
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS event_markers (
            event_id        TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            cop_entity_uid  TEXT NOT NULL REFERENCES cop_entities(uid) ON DELETE CASCADE,
            role            TEXT,
            created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            PRIMARY KEY (event_id, cop_entity_uid)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_event_markers_event ON event_markers(event_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_event_markers_uid   ON event_markers(cop_entity_uid)")
    conn.execute("""
        INSERT OR IGNORE INTO event_markers (event_id, cop_entity_uid, role)
        SELECT json_extract(c.attributes, '$.event_id'), c.uid, 'primary'
          FROM cop_entities c
         WHERE json_valid(c.attributes)
           AND json_extract(c.attributes, '$.kind') = 'event'
           AND json_extract(c.attributes, '$.event_id') IS NOT NULL
           AND EXISTS (SELECT 1 FROM events e WHERE e.id = json_extract(c.attributes, '$.event_id'))
    """)


def _m021_event_markers_down(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS event_markers")


def _m022_events_drop_dead_lat_lon(conn: sqlite3.Connection) -> None:
    """P2-27 位置 SoT 清理：刪 events.lat/lon 死欄。

    m013（line 636-637）為 scenario 5 預埋 events.lat/lon，但 create/patch **從未寫、查詢
    從未讀**（實證 event_repo）—— PR-G1b cutover 後事件位置由 `cop_entities.lat/lon`（事件
    圖釘）承載，**那才是唯一位置 SoT**。保留死欄會造成「events 也有位置」的假象 → 刪除。
    SQLite 3.35+ DROP COLUMN（本檔 down-migration 已用，見 _m014_down）。idempotent。
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
    for col in ("lat", "lon"):
        if col in cols:
            conn.execute(f"ALTER TABLE events DROP COLUMN {col}")  # nosec B608 — 欄名為常數


def _m022_events_drop_dead_lat_lon_down(conn: sqlite3.Connection) -> None:
    """rollback：把 lat/lon 加回（nullable，對齊 m013 的形狀）。"""
    _add_column_if_missing(conn, "events", "lat", "REAL")
    _add_column_if_missing(conn, "events", "lon", "REAL")


def _rebuild_with_fk(
    conn: sqlite3.Connection,
    table: str,
    new_table_ddl: str,
    scrub_sql: tuple[str, ...] = (),
) -> None:
    """通用 12-step FK rebuild（`foreign_keys=OFF`），泛化自 `_rebuild_cop_entities`。

    SQLite **FK / CHECK 不可 ALTER**，要對既有表加/改 FK 只能整表重建。`get_conn` 設
    `PRAGMA foreign_keys=ON`，naive `DROP TABLE` 會觸發 cascade / dangling 檢查，故走官方
    12-step 的 `foreign_keys=OFF` 版（與 `_rebuild_cop_entities` 同套，後者因獨有 source CHECK
    + child cascade 註記留為專用函式、不動）。

    參數：
      - `new_table_ddl`：建 `{table}_new` 的完整 CREATE（含**新 FK**，self-ref 與 child→parent
        一律寫**最終表名**，DROP 舊表後 RENAME `_new`→最終名即對上，對齊 `_rebuild_cop_entities`）。
      - `scrub_sql`：INSERT 後、DROP 舊表前在 `{table}_new` 上跑的 dangling 清洗（把指向已不存在
        parent 的 FK 欄位 null 化，使 `foreign_keys=ON` 後不留 orphan；P2-31 DoD）。

    複製用**動態舊欄位清單**（漏欄即 SQL 報錯、不靜默丟資料）、動態重建所有 user index。
    `foreign_keys` PRAGMA 只能在無 transaction 時設 → 先 commit 結束 init_db 大 transaction、
    切 autocommit、自包 BEGIN/COMMIT 保原子性（失敗 ROLLBACK），結束切回原 isolation。
    """
    conn.commit()  # 結束 init_db 大 transaction，讓 PRAGMA foreign_keys 可設
    prev_isolation = conn.isolation_level
    conn.isolation_level = None  # autocommit：PRAGMA foreign_keys 才生效
    began = False
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN")
        began = True
        old_cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]  # nosec B608 — table 為常數
        col_csv = ", ".join(old_cols)
        idx_sqls = [
            r[0]
            for r in conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
                (table,),
            )
        ]
        conn.execute(new_table_ddl)  # CREATE {table}_new（含新 FK）
        conn.execute(f"INSERT INTO {table}_new ({col_csv}) SELECT {col_csv} FROM {table}")  # nosec B608
        for sql in scrub_sql:  # dangling FK 欄位 null 化（FK on 後不留 orphan）
            conn.execute(sql)
        conn.execute(f"DROP TABLE {table}")  # nosec B608 — foreign_keys=OFF → 不 cascade
        conn.execute(f"ALTER TABLE {table}_new RENAME TO {table}")  # nosec B608 — FK 對上最終名
        for idx_sql in idx_sqls:  # 動態重建所有原 index，免手列漏建
            conn.execute(idx_sql)
        conn.execute("COMMIT")
    except Exception:
        if began:  # BEGIN 前出錯時無 active transaction，ROLLBACK 會反拋蓋掉原因
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.isolation_level = prev_isolation


# decisions 活表欄位 superset = base CREATE（_create_tables）+ _m002 補的 made_by / outcome_notes。
# rebuild 動態複製舊欄位，_new 須為 superset；漏列任一欄則複製時報錯（不靜默丟）。
_DECISIONS_NEW_DDL = """
    CREATE TABLE decisions_new (
        id                 TEXT PRIMARY KEY,
        primary_event_id   TEXT REFERENCES events(id) ON DELETE SET NULL,
        decision_seq       INTEGER DEFAULT 1,
        parent_decision_id TEXT REFERENCES decisions(id) ON DELETE SET NULL,
        superseded_by      TEXT REFERENCES decisions(id) ON DELETE SET NULL,
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
        outcome_notes_ext  TEXT,
        made_by            TEXT,
        outcome_notes      TEXT
    )
"""


def _m023_decisions_fk(conn: sqlite3.Connection) -> None:
    """P2-31（[#184](https://github.com/winson3QQ/ICS_COMMAND/issues/184)）：decisions 三欄補 FK。

    `primary_event_id → events(id)`、自我參照 `parent_decision_id` / `superseded_by →
    decisions(id)`，**全 `ON DELETE SET NULL`**：問責鏈不隨 event / 上游決策硬刪而消失；且
    `delete_exercise` 白名單刪除順序為 events→…→decisions（[exercise_repo.py]），events 先於
    decisions 刪 → 非 SET NULL 會炸 FK，故 SET NULL 是此順序下的**硬需求**而非偏好。

    rebuild 前 scrub dangling（指向已不存在 event / decision，含空字串）→ 先 null 化，
    `foreign_keys=ON` 後不留 orphan。idempotent：表已含 events FK 則 skip（含 fresh DB 重建後）。
    """
    existing = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='decisions'"
    ).fetchone()
    if existing and "REFERENCES events" in existing[0]:
        return  # 已加過 FK（重跑 / fresh DB rebuild 後）
    _rebuild_with_fk(
        conn,
        "decisions",
        _DECISIONS_NEW_DDL,
        scrub_sql=(
            "UPDATE decisions_new SET primary_event_id=NULL "
            "WHERE primary_event_id IS NOT NULL "
            "AND primary_event_id NOT IN (SELECT id FROM events)",
            "UPDATE decisions_new SET parent_decision_id=NULL "
            "WHERE parent_decision_id IS NOT NULL "
            "AND parent_decision_id NOT IN (SELECT id FROM decisions_new)",
            "UPDATE decisions_new SET superseded_by=NULL "
            "WHERE superseded_by IS NOT NULL "
            "AND superseded_by NOT IN (SELECT id FROM decisions_new)",
        ),
    )


def _m023_decisions_fk_down(conn: sqlite3.Connection) -> None:
    """rollback：rebuild 回無 FK 的 decisions（保留全欄與資料）。"""
    ddl = _DECISIONS_NEW_DDL.replace(" REFERENCES events(id) ON DELETE SET NULL", "")
    ddl = ddl.replace(" REFERENCES decisions(id) ON DELETE SET NULL", "")
    _rebuild_with_fk(conn, "decisions", ddl)


_AI_REC_NEW_DDL = """
    CREATE TABLE ai_recommendations_new (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        exercise_id         INTEGER REFERENCES exercises(id),
        made_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
        recommendation_type TEXT NOT NULL,
        content             TEXT NOT NULL,
        confidence          REAL,
        accepted            INTEGER,
        related_decision_id TEXT REFERENCES decisions(id) ON DELETE SET NULL,
        outcome_notes       TEXT
    )
"""


def _m024_ai_rec_decision_fk(conn: sqlite3.Connection) -> None:
    """P2-31 landmine（[#184](https://github.com/winson3QQ/ICS_COMMAND/issues/184)）：修
    `ai_recommendations.related_decision_id` 型別不符 FK。

    原 schema 為 **INTEGER REFERENCES decisions(id)**，但 `decisions.id` 是 TEXT(uuid) → 整數
    永遠配不到 → 該連結現狀**不可用**（FK on 時非 NULL 寫入會被擋）。改 **TEXT + ON DELETE
    SET NULL**：`delete_exercise` 刪除順序 decisions 先於 ai_recommendations，SET NULL 才不炸
    FK（與 m023 同理）。實測該欄前端無 UI、現存全 NULL → 無資料遷移；scrub 仍防禦性 null 化
    任何指向不存在 decision 的值。idempotent：欄型已 TEXT 則 skip（須在 m023 後跑，FK 對上重建後的 decisions）。
    """
    col_type = next(
        (r[2] for r in conn.execute("PRAGMA table_info(ai_recommendations)") if r[1] == "related_decision_id"),
        None,
    )
    if col_type is None or col_type.upper() == "TEXT":
        return  # 欄不存在（容錯）或已 TEXT（重跑 / fresh DB rebuild 後）
    _rebuild_with_fk(
        conn,
        "ai_recommendations",
        _AI_REC_NEW_DDL,
        scrub_sql=(
            "UPDATE ai_recommendations_new SET related_decision_id=NULL "
            "WHERE related_decision_id IS NOT NULL "
            "AND related_decision_id NOT IN (SELECT id FROM decisions)",
        ),
    )


def _m024_ai_rec_decision_fk_down(conn: sqlite3.Connection) -> None:
    """rollback：related_decision_id 回 INTEGER（去 FK）。"""
    ddl = _AI_REC_NEW_DDL.replace(
        "related_decision_id TEXT REFERENCES decisions(id) ON DELETE SET NULL",
        "related_decision_id INTEGER",
    )
    _rebuild_with_fk(conn, "ai_recommendations", ddl)


def _m025_events_drop_location_zone_id(conn: sqlite3.Connection) -> None:
    """P2-32（[#186](https://github.com/winson3QQ/ICS_COMMAND/issues/186)）：刪 events.location_zone_id 死欄。

    實測存的是 client 端 `'evt_'+Date.now()` 字串、無 `location_zone` 表、非位置來源——事件位置
    唯一 SoT 為 `cop_entities.lat/lon`（事件圖釘），event↔marker 權威連結為 `attributes.event_id`
    + P2-27 `event_markers` junction。誤命名欄退役（位置 SoT 清理另一半，承 m022 砍 lat/lon）。
    無 index / 無 FK → 直接 DROP COLUMN（SQLite 3.35+，比照 _m022）。idempotent。
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
    if "location_zone_id" in cols:
        conn.execute("ALTER TABLE events DROP COLUMN location_zone_id")  # nosec B608 — 欄名為常數


def _m025_events_drop_location_zone_id_down(conn: sqlite3.Connection) -> None:
    """rollback：把 location_zone_id 加回（nullable，對齊 base CREATE 的形狀）。"""
    _add_column_if_missing(conn, "events", "location_zone_id", "TEXT")


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
    (13, "cop_v1_schema", _m013_cop_v1_schema),
    (14, "cop_entities_audit_cols", _m014_cop_entities_audit_cols),
    (15, "cop_entities_squad_cols", _m015_cop_entities_squad_cols),
    (16, "chats_table", _m016_chats_table),
    (17, "cop_entities_planned_simulated", _m017_cop_entities_planned_simulated),
    (18, "cop_entities_source_command", _m018_cop_entities_source_command),
    (19, "cop_entities_deleted", _m019_cop_entities_deleted),
    (20, "cop_entities_archived", _m020_cop_entities_archived),
    (21, "event_markers", _m021_event_markers),
    (22, "events_drop_dead_lat_lon", _m022_events_drop_dead_lat_lon),
    (23, "decisions_fk", _m023_decisions_fk),
    (24, "ai_rec_decision_fk", _m024_ai_rec_decision_fk),
    (25, "events_drop_location_zone_id", _m025_events_drop_location_zone_id),
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
