from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime, timedelta

from auth.role_enum import (
    ROLE_COMMANDER_ZH,
    ROLE_OBSERVER_ZH,
    ROLE_OPERATOR_ZH,
    ROLE_SYSADMIN,
    ROLE_SYSADMIN_ZH,
)
from core.database import (
    _MIGRATIONS,
    _m010_role_detail_down,
    _m011_audit_correlation_id,
    _m011_audit_correlation_id_down,
    _m012_audit_hash_prev,
    _m012_audit_hash_prev_down,
    _m013_cop_v1_schema,
    _m013_cop_v1_schema_down,
    _m014_cop_entities_audit_cols,
    _m014_cop_entities_audit_cols_down,
)
from repositories.account_repo import create_account


def _login(client, username: str, pin: str) -> str:
    r = client.post("/api/auth/login", json={"username": username, "pin": pin})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def _auth_header(token: str) -> dict[str, str]:
    return {"X-Session-Token": token}


def _audit_count() -> int:
    from core.database import get_conn

    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]


def _latest_audit_action() -> str | None:
    from core.database import get_conn

    with get_conn() as conn:
        row = conn.execute("SELECT action_type FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
    return row["action_type"] if row else None


def _set_session(token: str, **values: str) -> None:
    from core.database import get_conn

    assignments = ", ".join(f"{key}=?" for key in values)
    params = [*values.values(), token]
    with get_conn() as conn:
        conn.execute(f"UPDATE sessions SET {assignments} WHERE token=?", params)  # nosec B608
        conn.commit()


def test_migrations_reach_m014_and_down_helpers_exist(client):
    # bump 13 → 14 (issue #29 PR-A: cop_entities updated_by/updated_at — per-entity CAS audit)
    assert _MIGRATIONS[-1][0] == 14
    assert callable(_m010_role_detail_down)
    assert callable(_m011_audit_correlation_id)
    assert callable(_m011_audit_correlation_id_down)
    assert callable(_m012_audit_hash_prev)
    assert callable(_m012_audit_hash_prev_down)
    assert callable(_m013_cop_v1_schema)
    assert callable(_m013_cop_v1_schema_down)
    assert callable(_m014_cop_entities_audit_cols)
    assert callable(_m014_cop_entities_audit_cols_down)


def test_m011_correlation_id_up_is_idempotent_and_down_removes_column(tmp_path):
    db_file = tmp_path / "m011.db"
    conn = sqlite3.connect(db_file)
    try:
        conn.execute("""
            CREATE TABLE audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action_type TEXT NOT NULL,
                detail TEXT
            )
        """)

        _m011_audit_correlation_id(conn)
        _m011_audit_correlation_id(conn)
        cols = [row[1] for row in conn.execute("PRAGMA table_info(audit_log)")]
        assert cols.count("correlation_id") == 1

        _m011_audit_correlation_id_down(conn)
        cols = [row[1] for row in conn.execute("PRAGMA table_info(audit_log)")]
    finally:
        conn.close()

    assert "correlation_id" not in cols


def test_m012_hash_prev_up_is_idempotent_and_down_removes_column(tmp_path):
    """Codeberg Issue #1 GAP-AUDIT-04: m012 mirrors m011 idempotent + reversible pattern."""
    db_file = tmp_path / "m012.db"
    conn = sqlite3.connect(db_file)
    try:
        conn.execute("""
            CREATE TABLE audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action_type TEXT NOT NULL,
                detail TEXT
            )
        """)

        _m012_audit_hash_prev(conn)
        _m012_audit_hash_prev(conn)
        cols = [row[1] for row in conn.execute("PRAGMA table_info(audit_log)")]
        assert cols.count("hash_prev") == 1

        _m012_audit_hash_prev_down(conn)
        cols = [row[1] for row in conn.execute("PRAGMA table_info(audit_log)")]
    finally:
        conn.close()

    assert "hash_prev" not in cols


def test_default_admin_is_sysadmin_and_session_status(client):
    r = client.post("/api/auth/login", json={"username": "admin", "pin": "1234"})
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == ROLE_SYSADMIN_ZH
    assert body["role_detail"] == ROLE_SYSADMIN
    assert len(body["session_id"]) >= 32

    status = client.get("/api/session/status", headers={"X-Session-Token": body["session_id"]})
    assert status.status_code == 200
    assert status.json()["valid"] is True
    assert status.json()["warning_threshold_seconds"] == 120


def test_session_status_does_not_reset_idle_warning_window(client):
    token = _login(client, "admin", "1234")
    old = (datetime.now(UTC) - timedelta(seconds=780)).strftime("%Y-%m-%dT%H:%M:%SZ")

    from core.database import get_conn

    with get_conn() as conn:
        conn.execute("UPDATE sessions SET idle_at=?, last_active=? WHERE token=?", (old, old, token))
        conn.commit()

    status = client.get("/api/session/status", headers={"X-Session-Token": token})
    assert status.status_code == 200
    remaining = status.json()["idle_remaining_seconds"]
    assert 100 <= remaining <= 130

    with get_conn() as conn:
        row = conn.execute("SELECT idle_at, last_active FROM sessions WHERE token=?", (token,)).fetchone()
    assert row["idle_at"] == old
    assert row["last_active"] == old


def test_observer_denied_admin_endpoint_and_audit_carries_correlation_id(client):
    create_account("observer", "1234", ROLE_OBSERVER_ZH, "Observer", "observer")
    token = _login(client, "observer", "1234")
    cid = "11111111-1111-4111-8111-111111111111"

    r = client.get(
        "/api/admin/accounts",
        headers={"X-Session-Token": token, "X-Correlation-ID": cid},
    )
    assert r.status_code == 403

    from core.database import get_conn

    with get_conn() as conn:
        row = conn.execute(
            "SELECT action_type, correlation_id, detail FROM audit_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row["action_type"] == "ROLE_DENIED"
    assert row["correlation_id"] == cid
    assert "request rejected" in row["detail"]


def test_commander_can_manage_operator_and_observer_accounts(client):
    create_account("cmd_mgr", "1234", ROLE_COMMANDER_ZH, "Commander", "commander")
    token = _login(client, "cmd_mgr", "1234")
    headers = _auth_header(token)

    operator = client.post(
        "/api/admin/accounts",
        json={"username": "cmd_operator", "pin": "1234", "role": ROLE_OPERATOR_ZH},
        headers=headers,
    )
    observer = client.post(
        "/api/admin/accounts",
        json={"username": "cmd_observer", "pin": "1234", "role": ROLE_OBSERVER_ZH},
        headers=headers,
    )
    assert operator.status_code == 200, operator.text
    assert observer.status_code == 200, observer.text

    listing = client.get("/api/admin/accounts", headers=headers)
    assert listing.status_code == 200
    names = {acct["username"] for acct in listing.json()}
    assert {"cmd_operator", "cmd_observer"}.issubset(names)
    assert "admin" not in names
    assert "cmd_mgr" not in names

    status = client.put(
        "/api/admin/accounts/cmd_operator/status",
        json={"status": "suspended"},
        headers=headers,
    )
    assert status.status_code == 200, status.text
    status = client.put(
        "/api/admin/accounts/cmd_operator/status",
        json={"status": "active"},
        headers=headers,
    )
    assert status.status_code == 200, status.text

    pin = client.put(
        "/api/admin/accounts/cmd_operator/pin",
        json={"new_pin": "5678"},
        headers=headers,
    )
    assert pin.status_code == 200, pin.text
    assert _login(client, "cmd_operator", "5678")

    role = client.put(
        "/api/admin/accounts/cmd_operator/role",
        json={"role": ROLE_OBSERVER_ZH},
        headers=headers,
    )
    assert role.status_code == 200, role.text

    archive = client.delete("/api/admin/accounts/cmd_observer", headers=headers)
    assert archive.status_code == 200, archive.text
    rejected = client.post("/api/auth/login", json={"username": "cmd_observer", "pin": "1234"})
    assert rejected.status_code == 403


def test_commander_cannot_create_or_promote_privileged_accounts(client):
    create_account("cmd_limited", "1234", ROLE_COMMANDER_ZH, "Commander", "commander")
    create_account("cmd_target", "1234", ROLE_OPERATOR_ZH, "Operator", "operator")
    headers = _auth_header(_login(client, "cmd_limited", "1234"))

    for suffix, role in (("sysadmin", ROLE_SYSADMIN_ZH), ("commander", ROLE_COMMANDER_ZH)):
        r = client.post(
            "/api/admin/accounts",
            json={"username": f"blocked_{suffix}", "pin": "1234", "role": role},
            headers=headers,
        )
        assert r.status_code == 403

        r = client.put(
            "/api/admin/accounts/cmd_target/role",
            json={"role": role},
            headers=headers,
        )
        assert r.status_code == 403


def test_commander_cannot_modify_privileged_accounts(client):
    create_account("cmd_guard", "1234", ROLE_COMMANDER_ZH, "Commander", "commander")
    create_account("peer_commander", "1234", ROLE_COMMANDER_ZH, "Peer Commander", "commander")
    headers = _auth_header(_login(client, "cmd_guard", "1234"))

    protected_targets = ["admin", "peer_commander"]
    for username in protected_targets:
        assert (
            client.put(
                f"/api/admin/accounts/{username}/status",
                json={"status": "suspended"},
                headers=headers,
            ).status_code
            == 403
        )
        assert (
            client.put(
                f"/api/admin/accounts/{username}/pin",
                json={"new_pin": "5678"},
                headers=headers,
            ).status_code
            == 403
        )
        assert (
            client.put(
                f"/api/admin/accounts/{username}/role",
                json={"role": ROLE_OPERATOR_ZH},
                headers=headers,
            ).status_code
            == 403
        )
        assert client.delete(f"/api/admin/accounts/{username}", headers=headers).status_code == 403


def test_commander_cannot_access_system_admin_endpoints(client):
    create_account("cmd_system_blocked", "1234", ROLE_COMMANDER_ZH, "Commander", "commander")
    headers = _auth_header(_login(client, "cmd_system_blocked", "1234"))

    checks = [
        ("GET", "/api/admin/status", None),
        ("GET", "/api/admin/audit-log", None),
        ("GET", "/api/admin/pi-nodes", None),
        ("GET", "/api/admin/schema-migrations", None),
        ("PUT", "/api/admin/pin", {"new_pin": "5678"}),
        ("POST", "/api/admin/reset-db", None),
        ("POST", "/api/admin/reset-exercise", None),
        ("POST", "/api/admin/suspend-all", None),
    ]
    for method, path, body in checks:
        r = client.request(method, path, json=body, headers=headers)
        assert r.status_code == 403, f"{method} {path}: {r.status_code} {r.text}"


def test_operator_and_observer_cannot_manage_accounts(client):
    create_account("op_blocked", "1234", ROLE_OPERATOR_ZH, "Operator", "operator")
    create_account("obs_blocked", "1234", ROLE_OBSERVER_ZH, "Observer", "observer")

    for username in ("op_blocked", "obs_blocked"):
        headers = _auth_header(_login(client, username, "1234"))
        assert client.get("/api/admin/accounts", headers=headers).status_code == 403
        assert (
            client.post(
                "/api/admin/accounts",
                json={"username": f"{username}_child", "pin": "1234", "role": ROLE_OPERATOR_ZH},
                headers=headers,
            ).status_code
            == 403
        )


def test_observer_denial_without_header_persists_generated_correlation_id(client):
    create_account("observer2", "1234", ROLE_OBSERVER_ZH, "Observer 2", "observer")
    token = _login(client, "observer2", "1234")

    r = client.get("/api/admin/accounts", headers={"X-Session-Token": token})
    assert r.status_code == 403

    from core.database import get_conn

    with get_conn() as conn:
        row = conn.execute("SELECT action_type, correlation_id FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
    assert row["action_type"] == "ROLE_DENIED"
    assert uuid.UUID(row["correlation_id"]).version == 4


def test_observer_cannot_create_event(client):
    create_account("observer_event", "1234", ROLE_OBSERVER_ZH, "Observer Event", "observer")
    token = _login(client, "observer_event", "1234")

    r = client.post(
        "/api/events",
        json={
            "reported_by_unit": "observer",
            "event_type": "other",
            "severity": "info",
            "description": "observer should not be able to create events",
            "operator_name": "observer_event",
        },
        headers=_auth_header(token),
    )

    assert r.status_code == 403
    assert _latest_audit_action() == "ROLE_DENIED"


def test_account_delete_is_soft_archive_and_login_rejected(client, auth):
    create_account("operator1", "1234", ROLE_OPERATOR_ZH, "Operator", "operator")

    r = client.delete("/api/admin/accounts/operator1", headers=auth)
    assert r.status_code == 200

    r = client.post("/api/auth/login", json={"username": "operator1", "pin": "1234"})
    assert r.status_code == 403

    from core.database import get_conn

    with get_conn() as conn:
        row = conn.execute("SELECT status, deleted_at FROM accounts WHERE username='operator1'").fetchone()
    assert row["status"] == "archived"
    assert row["deleted_at"]


def test_idle_timeout_audit_event(client):
    token = _login(client, "admin", "1234")
    old = (datetime.now(UTC) - timedelta(seconds=901)).strftime("%Y-%m-%dT%H:%M:%SZ")

    from core.database import get_conn

    with get_conn() as conn:
        conn.execute("UPDATE sessions SET idle_at=?, last_active=? WHERE token=?", (old, old, token))
        conn.commit()

    r = client.get("/api/auth/me", headers={"X-Session-Token": token})
    assert r.status_code == 401
    with get_conn() as conn:
        row = conn.execute("SELECT action_type FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
    assert row["action_type"] == "IDLE_KICKED"


def test_logged_out_token_reuse_does_not_emit_second_termination_audit(client):
    token = _login(client, "admin", "1234")
    logout = client.post("/api/auth/logout", headers={"X-Session-Token": token})
    assert logout.status_code == 200

    from core.database import get_conn

    def counts() -> dict[str, int]:
        with get_conn() as conn:
            rows = conn.execute("""
                SELECT action_type, COUNT(*) AS count
                FROM audit_log
                WHERE action_type IN ('SESSION_LOGOUT', 'SESSION_EXPIRED')
                GROUP BY action_type
            """).fetchall()
        return {row["action_type"]: row["count"] for row in rows}

    before = counts()
    r = client.get("/api/auth/me", headers={"X-Session-Token": token})
    assert r.status_code == 401
    after = counts()

    assert before.get("SESSION_LOGOUT", 0) == 1
    assert after.get("SESSION_LOGOUT", 0) == 1
    assert after.get("SESSION_EXPIRED", 0) == before.get("SESSION_EXPIRED", 0)


def test_ac18_middleware_rejection_priority_order(client):
    before = _audit_count()
    missing = client.get("/api/auth/me")
    assert missing.status_code == 401
    assert _audit_count() == before

    expired_token = _login(client, "admin", "1234")
    expired_at = (datetime.now(UTC) - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _set_session(expired_token, expires_at=expired_at)
    expired = client.get("/api/auth/me", headers={"X-Session-Token": expired_token})
    assert expired.status_code == 401
    assert _latest_audit_action() == "SESSION_EXPIRED"

    idle_token = _login(client, "admin", "1234")
    idle_at = (datetime.now(UTC) - timedelta(seconds=901)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _set_session(idle_token, idle_at=idle_at, last_active=idle_at)
    idle = client.get("/api/auth/me", headers={"X-Session-Token": idle_token})
    assert idle.status_code == 401
    assert _latest_audit_action() == "IDLE_KICKED"

    ip_token = _login(client, "admin", "1234")
    _set_session(ip_token, ip="10.10.10.5")
    ip_mismatch = client.get("/api/auth/me", headers={"X-Session-Token": ip_token})
    assert ip_mismatch.status_code == 401
    assert _latest_audit_action() == "BINDING_MISMATCH_IP"

    ua_token = _login(client, "admin", "1234")
    _set_session(ua_token, user_agent="curl")
    ua_mismatch = client.get("/api/auth/me", headers={"X-Session-Token": ua_token})
    assert ua_mismatch.status_code == 401
    assert _latest_audit_action() == "BINDING_MISMATCH_UA"

    create_account("observer_ac18", "1234", ROLE_OBSERVER_ZH, "Observer AC18", "observer")
    observer_token = _login(client, "observer_ac18", "1234")
    role_denied = client.get("/api/admin/accounts", headers={"X-Session-Token": observer_token})
    assert role_denied.status_code == 403
    assert _latest_audit_action() == "ROLE_DENIED"

    pass_token = _login(client, "admin", "1234")
    old = (datetime.now(UTC) - timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _set_session(pass_token, idle_at=old, last_active=old)
    allowed = client.get("/api/auth/me", headers={"X-Session-Token": pass_token})
    assert allowed.status_code == 200

    from core.database import get_conn

    with get_conn() as conn:
        row = conn.execute("SELECT idle_at FROM sessions WHERE token=?", (pass_token,)).fetchone()
    assert row["idle_at"] != old


def test_ac18_idle_timeout_precedes_ip_binding_mismatch(client):
    token = _login(client, "admin", "1234")
    idle_at = (datetime.now(UTC) - timedelta(seconds=901)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _set_session(token, idle_at=idle_at, last_active=idle_at, ip="10.10.10.5")

    r = client.get("/api/auth/me", headers={"X-Session-Token": token})
    assert r.status_code == 401
    assert _latest_audit_action() == "IDLE_KICKED"


def test_ip_binding_mismatch_audit_event(client):
    token = _login(client, "admin", "1234")

    from core.database import get_conn

    with get_conn() as conn:
        conn.execute("UPDATE sessions SET ip='10.10.10.5' WHERE token=?", (token,))
        conn.commit()

    r = client.get("/api/auth/me", headers={"X-Session-Token": token})
    assert r.status_code == 401
    with get_conn() as conn:
        row = conn.execute("SELECT action_type FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
    assert row["action_type"] == "BINDING_MISMATCH_IP"


def test_legacy_null_correlation_id_audit_rows_remain_readable(client):
    from core.database import get_conn
    from repositories.audit_repo import get_audit_log

    with get_conn() as conn:
        conn.execute("""
            INSERT INTO audit_log
                (operator, device_id, action_type, target_table, target_id, detail)
            VALUES
                ('legacy', NULL, 'LEGACY_NULL_CID', 'legacy', '1', '{}')
        """)
        conn.commit()

    rows = get_audit_log(limit=20)
    legacy = next(row for row in rows if row["action_type"] == "LEGACY_NULL_CID")
    assert "correlation_id" in legacy
    assert legacy["correlation_id"] is None


def test_legacy_role_detail_admin_backfills_to_sysadmin(tmp_path):
    db_file = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_file)
    try:
        conn.execute("CREATE TABLE accounts (username TEXT, role TEXT, role_detail TEXT)")
        conn.execute("CREATE TABLE sessions (token TEXT, role_detail TEXT)")
        conn.execute("INSERT INTO accounts VALUES ('admin', 'admin', 'admin')")
        from core.database import _m010_role_detail_backfill

        _m010_role_detail_backfill(conn)
        row = conn.execute("SELECT role, role_detail FROM accounts WHERE username='admin'").fetchone()
    finally:
        conn.close()
    assert row == ("系統管理員", "sysadmin")
