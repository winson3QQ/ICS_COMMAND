import json

from core.database import get_conn

from ._helpers import audit, now_utc, row_to_dict


def upsert_snapshot(data: dict, exercise_id: int | None = None) -> dict:
    """寫入快照（idempotent：同 snapshot_id 忽略）"""
    now  = now_utc()
    extra = data.get("extra") or {}
    for key in ("supplies", "units", "srt", "pending_intake", "cmist_pending",
                "post_total", "post_anomaly", "qrf_available", "isolation_count"):
        if data.get(key) is not None:
            extra[key] = data[key]

    casualties = data.get("casualties") or {}
    sql = """
        INSERT OR IGNORE INTO snapshots
            (snapshot_id, snapshot_time, node_type, source,
             casualties_red, casualties_yellow, casualties_green, casualties_black,
             bed_used, bed_total, waiting_count, pending_evac,
             vehicle_available, staff_on_duty, extra, received_at, exercise_id)
        VALUES (?,?,?,?, ?,?,?,?, ?,?,?,?, ?,?,?, ?,?)
    """
    with get_conn() as conn:
        cur = conn.execute(sql, (
            data["snapshot_id"],
            data["t"],
            data["node_type"],
            data.get("source", "auto"),
            casualties.get("red")    or data.get("casualties_red"),
            casualties.get("yellow") or data.get("casualties_yellow"),
            casualties.get("green")  or data.get("casualties_green"),
            casualties.get("black")  or data.get("casualties_black"),
            data.get("bed_used"),
            data.get("bed_total"),
            data.get("waiting_count"),
            data.get("pending_evac"),
            data.get("vehicle_available"),
            data.get("staff_on_duty"),
            json.dumps(extra, ensure_ascii=False) if extra else None,
            now,
            exercise_id,
        ))
        inserted = cur.rowcount > 0

    audit("system", None, "snapshot_received", "snapshots", data["snapshot_id"],
          {"node_type": data["node_type"], "inserted": inserted}, exercise_id)
    return {"inserted": inserted, "snapshot_id": data["snapshot_id"]}


def get_snapshots(node_type: str, limit: int = 20,
                  exercise_id: int | None = None) -> list[dict]:
    with get_conn() as conn:
        if exercise_id is not None:
            rows = conn.execute(
                "SELECT * FROM snapshots WHERE node_type=? AND exercise_id=? "
                "ORDER BY snapshot_time DESC LIMIT ?",
                (node_type, exercise_id, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM snapshots WHERE node_type=? "
                "ORDER BY snapshot_time DESC LIMIT ?",
                (node_type, limit)
            ).fetchall()
    return [row_to_dict(r) for r in rows]


def get_latest_snapshot(node_type: str, exercise_id: int | None = None) -> dict | None:
    rows = get_snapshots(node_type, limit=1, exercise_id=exercise_id)
    return rows[0] if rows else None
