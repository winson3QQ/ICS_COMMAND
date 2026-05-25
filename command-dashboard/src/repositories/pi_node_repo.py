import secrets

from core.database import get_conn

from ._helpers import audit, iso_utc, now_utc


def create_pi_node(unit_id: str, label: str) -> dict:
    api_key = secrets.token_hex(32)
    now     = now_utc()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO pi_nodes (unit_id, label, api_key, created_at) VALUES (?,?,?,?)",
            (unit_id, label, api_key, now))
    audit("admin", None, "pi_node_created", "pi_nodes", unit_id, {"label": label})
    return {"unit_id": unit_id, "label": label, "api_key": api_key, "created_at": now}


def list_pi_nodes() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT unit_id, label, api_key, last_seen_at, last_data_at, created_at, revoked_at "
            "FROM pi_nodes").fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["api_key_suffix"] = d.pop("api_key")[-8:]
        for k in ("last_seen_at", "last_data_at", "created_at", "revoked_at"):
            d[k] = iso_utc(d.get(k))
        result.append(d)
    return result


def delete_pi_node(unit_id: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM pi_nodes WHERE unit_id=?", (unit_id,))
    if cur.rowcount:
        audit("admin", None, "pi_node_deleted", "pi_nodes", unit_id, {})
    return cur.rowcount > 0


def revoke_pi_node_key(unit_id: str) -> dict | None:
    new_key = secrets.token_hex(32)
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE pi_nodes SET api_key=?, revoked_at=NULL, last_seen_at=NULL WHERE unit_id=?",
            (new_key, unit_id))
    if cur.rowcount == 0:
        return None
    audit("admin", None, "pi_node_rekeyed", "pi_nodes", unit_id, {})
    return {"unit_id": unit_id, "api_key": new_key}


def validate_pi_push(unit_id: str, bearer_token: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM pi_nodes WHERE unit_id=? AND api_key=? AND revoked_at IS NULL",
            (unit_id, bearer_token)).fetchone()
    return row is not None


def touch_pi_node(unit_id: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE pi_nodes SET last_seen_at=? WHERE unit_id=?",
            (now_utc(), unit_id))


def touch_pi_node_data(unit_id: str):
    now = now_utc()
    with get_conn() as conn:
        conn.execute(
            "UPDATE pi_nodes SET last_seen_at=?, last_data_at=? WHERE unit_id=?",
            (now, now, unit_id))
