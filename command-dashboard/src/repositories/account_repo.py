from __future__ import annotations

from datetime import UTC, datetime, timedelta

from auth.role_enum import (
    ROLE_SYSADMIN,
    ROLE_SYSADMIN_ZH,
    normalize_role_pair,
    role_zh_to_en,
)
from core.database import get_conn

from ._helpers import audit, hash_pin, now_utc, verify_pin

LOCKOUT_THRESHOLD = 5
LOCKOUT_DURATION_MIN = 15


def _iso_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_to_dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _is_locked(row: dict) -> bool:
    locked_until = row.get("locked_until")
    if not locked_until:
        return False
    return datetime.now(UTC) < _iso_to_dt(locked_until)


def _public_account(row: dict) -> dict:
    row.pop("pin_hash", None)
    row.pop("pin_salt", None)
    row["role"], row["role_detail"] = normalize_role_pair(row.get("role"), row.get("role_detail"))
    return row


def create_account(
    username: str,
    pin: str,
    role: str = "\u64cd\u4f5c\u54e1",
    display_name: str | None = None,
    role_detail: str | None = None,
    operator: str = "admin",
) -> dict:
    role, role_detail = normalize_role_pair(role, role_detail)
    pin_hash, pin_salt = hash_pin(pin)
    now = now_utc()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO accounts
               (username, pin_hash, pin_salt, role, role_detail, display_name, status, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (username, pin_hash, pin_salt, role, role_detail, display_name, "active", now),
        )
    audit(operator, None, "account_created", "accounts", username, {"role": role, "role_detail": role_detail})
    return {
        "username": username,
        "role": role,
        "role_detail": role_detail,
        "display_name": display_name,
        "status": "active",
        "created_at": now,
    }


def get_all_accounts() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT username, role, role_detail, display_name, status, created_at, updated_at
                 FROM accounts
                WHERE COALESCE(status, 'active') != 'archived'
                ORDER BY created_at"""
        ).fetchall()
    return [_public_account(dict(r)) for r in rows]


def get_account(username: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT username, role, role_detail, display_name, status, created_at, updated_at
                 FROM accounts
                WHERE username=?
                  AND COALESCE(status, 'active') != 'archived'""",
            (username,),
        ).fetchone()
    return _public_account(dict(row)) if row else None


def update_account_status(username: str, status: str, operator: str) -> bool:
    now = now_utc()
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE accounts SET status=?, updated_at=? WHERE username=? AND COALESCE(status, 'active') != 'archived'",
            (status, now, username),
        )
    if cur.rowcount:
        audit(operator, None, "account_status_updated", "accounts", username, {"status": status})
    return cur.rowcount > 0


def update_account_pin(username: str, new_pin: str, operator: str) -> bool:
    pin_hash, pin_salt = hash_pin(new_pin)
    now = now_utc()
    with get_conn() as conn:
        cur = conn.execute(
            """UPDATE accounts
                  SET pin_hash=?, pin_salt=?, updated_at=?
                WHERE username=? AND COALESCE(status, 'active') != 'archived'""",
            (pin_hash, pin_salt, now, username),
        )
    if cur.rowcount:
        audit(operator, None, "account_pin_reset", "accounts", username, {})
    return cur.rowcount > 0


def update_account_role(
    username: str,
    role: str,
    operator: str,
    role_detail: str | None = None,
) -> bool:
    role, role_detail = normalize_role_pair(role, role_detail)
    now = now_utc()
    with get_conn() as conn:
        cur = conn.execute(
            """UPDATE accounts
                  SET role=?, role_detail=?, updated_at=?
                WHERE username=? AND COALESCE(status, 'active') != 'archived'""",
            (role, role_detail, now, username),
        )
    if cur.rowcount:
        audit(operator, None, "account_role_updated", "accounts", username, {"role": role, "role_detail": role_detail})
    return cur.rowcount > 0


def update_account_display_name(username: str, display_name: str, operator: str) -> bool:
    now = now_utc()
    with get_conn() as conn:
        cur = conn.execute(
            """UPDATE accounts
                  SET display_name=?, updated_at=?
                WHERE username=? AND COALESCE(status, 'active') != 'archived'""",
            (display_name, now, username),
        )
    if cur.rowcount:
        audit(operator, None, "account_display_name_updated", "accounts", username, {"display_name": display_name})
    return cur.rowcount > 0


def delete_account(username: str, operator: str) -> bool:
    now = now_utc()
    with get_conn() as conn:
        cur = conn.execute(
            """UPDATE accounts
                  SET status='archived', deleted_at=?, updated_at=?
                WHERE username=? AND COALESCE(status, 'active') != 'archived'""",
            (now, now, username),
        )
    if cur.rowcount:
        audit(operator, None, "ACCOUNT_ARCHIVED", "accounts", username, {"decision": "soft_delete"})
    return cur.rowcount > 0


def suspend_all_accounts(operator: str) -> int:
    now = now_utc()
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE accounts SET status='suspended', updated_at=? WHERE status='active'",
            (now,),
        )
    audit(operator, None, "all_accounts_suspended", "accounts", "*", {})
    return cur.rowcount


def verify_login(username: str, pin: str) -> tuple[dict | None, str]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM accounts WHERE username=?", (username,)).fetchone()
        if not row:
            return None, "no_user"
        d = dict(row)
        if d.get("status") == "archived":
            return None, "archived"
        if d.get("status") != "active":
            return None, "suspended"
        if _is_locked(d):
            return None, "locked"
        if not verify_pin(pin, d["pin_hash"], d["pin_salt"]):
            new_count = (d.get("failed_login_count") or 0) + 1
            locked_until = None
            if new_count >= LOCKOUT_THRESHOLD:
                locked_until = (datetime.now(UTC) + timedelta(minutes=LOCKOUT_DURATION_MIN)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
            conn.execute(
                "UPDATE accounts SET failed_login_count=?, locked_until=? WHERE username=?",
                (new_count, locked_until, username),
            )
            conn.commit()
            if locked_until:
                audit(username, None, "account_locked", "accounts", username, {"failed_count": new_count})
                return None, "locked"
            return None, "bad_pin"
        conn.execute(
            "UPDATE accounts SET failed_login_count=0, locked_until=NULL, last_login=? WHERE username=?",
            (_iso_now(), username),
        )
        conn.commit()
    return _public_account(d), "ok"


def unlock_account(username: str, operator: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE accounts SET failed_login_count=0, locked_until=NULL WHERE username=?",
            (username,),
        )
    if cur.rowcount:
        audit(operator, None, "account_unlocked", "accounts", username, {})
    return cur.rowcount > 0


def ensure_default_admin(default_pin: str = "1234") -> None:
    with get_conn() as conn:
        cnt = conn.execute("SELECT COUNT(*) as c FROM accounts").fetchone()["c"]
    if cnt == 0:
        create_account("admin", default_pin, ROLE_SYSADMIN_ZH, "\u7cfb\u7d71\u7ba1\u7406\u54e1", ROLE_SYSADMIN)
        with get_conn() as conn:
            conn.execute("UPDATE accounts SET is_default_pin=1 WHERE username='admin'")
            conn.commit()


def ensure_initial_admin_token(token_dir: str | None = None) -> str | None:
    import logging
    import os
    import secrets

    with get_conn() as conn:
        cnt = conn.execute("SELECT COUNT(*) as c FROM accounts").fetchone()["c"]
    if cnt > 0:
        return None

    initial_pin = f"{secrets.randbelow(1_000_000):06d}"
    create_account("admin", initial_pin, ROLE_SYSADMIN_ZH, "\u7cfb\u7d71\u7ba1\u7406\u54e1", ROLE_SYSADMIN)
    with get_conn() as conn:
        conn.execute("UPDATE accounts SET is_default_pin=1 WHERE username='admin'")
        conn.commit()

    target_dir = token_dir or os.path.expanduser("~/.ics")
    token_file = os.path.join(target_dir, "first_run_token")
    try:
        os.makedirs(target_dir, mode=0o700, exist_ok=True)
        with open(token_file, "w", encoding="utf-8") as f:
            f.write(initial_pin + "\n")
        os.chmod(token_file, 0o600)
    except OSError:
        logging.getLogger(__name__).warning("Unable to write first-run token", exc_info=True)

    import structlog as _structlog

    _structlog.get_logger().warning(
        "first_run_token_issued",
        msg="Initial admin PIN issued; operator must change it after login.",
        detail={"token_file": token_file},
    )
    return initial_pin


def clear_default_pin_flag(username: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute("UPDATE accounts SET is_default_pin=0 WHERE username=?", (username,))
        conn.commit()
    return cur.rowcount > 0


def is_first_run_required() -> bool:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT COUNT(*) as c
                 FROM accounts
                WHERE is_default_pin=1
                  AND (role_detail='sysadmin' OR role='系統管理員' OR role='admin')"""
        ).fetchone()
    return row["c"] > 0


def is_valid_account_role(role: str | None, role_detail: str | None = None) -> bool:
    return role_zh_to_en(role, role_detail) is not None
