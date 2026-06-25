from core.database import get_conn

from ._helpers import audit, now_utc


def get_config(key: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_config(key: str, value: str, operator: str | None = None):
    now = now_utc()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO config (key, value, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, value, now),
        )
    if operator:
        audit(operator, None, "config_updated", "config", key, {"value": value})
