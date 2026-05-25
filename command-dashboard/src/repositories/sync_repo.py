"""
sync_repo.py — 三 Pass 對齊（網路恢復後同步）
"""

import json
import uuid

from core.database import get_conn

from ._helpers import audit, now_utc, row_to_dict


def _unit_to_node(unit: str) -> str:
    return {
        "shelter":  "shelter",
        "medical":  "medical",
        "forward":  "forward",
        "security": "security",
    }.get(unit, unit)


def execute_three_pass(source_unit: str, sync_data: dict,
                       operator: str = "auto") -> dict:
    """三 Pass 對齊邏輯（斷網恢復同步）"""
    sync_id   = str(uuid.uuid4())
    now       = now_utc()
    start_ts  = sync_data.get("sync_start_ts", now)
    device_id = sync_data.get("device_id", "")

    p1_merged  = 0
    p1_added   = 0
    p2_pending = []
    p3_added   = 0

    with get_conn() as conn:

        # ── Pass 1：SNAPSHOT 去重與補齊 ──────────────────────────────────
        for snap in (sync_data.get("snapshots") or []):
            snap_id = snap.get("snapshot_id") or snap.get("snapshot_uuid")
            if not snap_id:
                continue

            existing  = conn.execute(
                "SELECT id, source FROM snapshots WHERE snapshot_id=?", (snap_id,)).fetchone()
            casualties = snap.get("casualties") or {}
            extra_json = json.dumps(snap.get("extra") or {}, ensure_ascii=False)

            if existing:
                new_source = "merged" if existing["source"] == "qr" else existing["source"]
                conn.execute("""
                    UPDATE snapshots
                    SET source=?, extra=?,
                        casualties_red=?, casualties_yellow=?,
                        casualties_green=?, casualties_black=?,
                        bed_used=?, bed_total=?,
                        waiting_count=?, pending_evac=?,
                        vehicle_available=?, staff_on_duty=?
                    WHERE snapshot_id=?
                """, (
                    new_source, extra_json,
                    casualties.get("red")    or snap.get("casualties_red"),
                    casualties.get("yellow") or snap.get("casualties_yellow"),
                    casualties.get("green")  or snap.get("casualties_green"),
                    casualties.get("black")  or snap.get("casualties_black"),
                    snap.get("bed_used"), snap.get("bed_total"),
                    snap.get("waiting_count"), snap.get("pending_evac"),
                    snap.get("vehicle_available"), snap.get("staff_on_duty"),
                    snap_id))
                p1_merged += 1
            else:
                node_type = snap.get("node_type") or _unit_to_node(source_unit)
                conn.execute("""
                    INSERT OR IGNORE INTO snapshots
                        (snapshot_id, node_type, source, snapshot_time,
                         casualties_red, casualties_yellow, casualties_green, casualties_black,
                         bed_used, bed_total, waiting_count, pending_evac,
                         vehicle_available, staff_on_duty, extra, received_at)
                    VALUES (?,?,?,?, ?,?,?,?, ?,?,?,?, ?,?,?,?)
                """, (
                    snap_id, node_type, "sync_recovery",
                    snap.get("t") or snap.get("snapshot_time") or now,
                    casualties.get("red")    or snap.get("casualties_red"),
                    casualties.get("yellow") or snap.get("casualties_yellow"),
                    casualties.get("green")  or snap.get("casualties_green"),
                    casualties.get("black")  or snap.get("casualties_black"),
                    snap.get("bed_used"), snap.get("bed_total"),
                    snap.get("waiting_count"), snap.get("pending_evac"),
                    snap.get("vehicle_available"), snap.get("staff_on_duty"),
                    extra_json, now))
                p1_added += 1

        # ── Pass 2：手動記錄模糊比對 ──────────────────────────────────────
        for rec in (sync_data.get("manual_records") or []):
            rec_time    = rec.get("submitted_at") or rec.get("timestamp") or now
            rec_type    = rec.get("form_id") or rec.get("type", "")
            rec_summary = rec.get("summary", "")

            conflict = conn.execute("""
                SELECT id, summary, submitted_at FROM manual_records
                WHERE form_id=?
                  AND ABS((julianday(submitted_at) - julianday(?)) * 1440) < 30
                ORDER BY ABS(julianday(submitted_at) - julianday(?))
                LIMIT 1
            """, (rec_type, rec_time, rec_time)).fetchone()

            if conflict:
                p2_pending.append({
                    "incoming": {"summary": rec_summary, "time": rec_time, "data": rec},
                    "existing": row_to_dict(conflict),
                    "action":   None,
                })
            else:
                conn.execute("""
                    INSERT INTO manual_records
                        (id, form_id, form_type, target_table, operator,
                         summary, payload, sync_status, submitted_at)
                    VALUES (?,?,?,?,?,?,?,?,?)
                """, (
                    str(uuid.uuid4()), rec_type,
                    rec.get("form_type", ""), rec.get("target_table", ""),
                    operator, rec_summary,
                    json.dumps(rec.get("payload") or rec, ensure_ascii=False),
                    "synced", rec_time))
                p3_added += 1

        # ── Pass 3：事件補傳 ──────────────────────────────────────────────
        for ev in (sync_data.get("events") or []):
            ev_id = ev.get("id")
            if ev_id and conn.execute("SELECT id FROM events WHERE id=?", (ev_id,)).fetchone():
                continue
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO events
                        (id, unit, type, description, severity, status,
                         occurred_at, operator, source)
                    VALUES (?,?,?,?,?,?,?,?,?)
                """, (
                    ev_id or str(uuid.uuid4()),
                    ev.get("unit", source_unit),
                    ev.get("type", "sync_recovery"),
                    ev.get("description", ""),
                    ev.get("severity", "info"),
                    ev.get("status", "open"),
                    ev.get("occurred_at") or now,
                    ev.get("operator", operator),
                    "sync_recovery"))
                p3_added += 1
            except Exception:
                pass

        # ── sync_log ──────────────────────────────────────────────────────
        status = "completed" if not p2_pending else "partial"
        detail = json.dumps({
            "pass1_merged": p1_merged, "pass1_added": p1_added,
            "pass2_conflicts": p2_pending, "pass3_added": p3_added,
            "device_id": device_id,
        }, ensure_ascii=False)

        conn.execute("""
            INSERT INTO sync_log
                (id, source_unit, sync_started_at, sync_completed_at,
                 data_gap_start, data_gap_end,
                 pass1_merged, pass2_manual, pass3_added, conflicts_manual,
                 status, triggered_by, operator, detail)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            sync_id, source_unit, now, now,
            start_ts, now,
            p1_merged, len(p2_pending), p3_added, len(p2_pending),
            status, "api", operator, detail))

    audit(operator, device_id, "three_pass_sync", "sync_log", sync_id, {
        "source_unit": source_unit, "p1_merged": p1_merged,
        "p1_added": p1_added, "p2_pending": len(p2_pending), "p3_added": p3_added})

    return {
        "sync_id": sync_id, "status": status,
        "pass1_merged": p1_merged, "pass1_added": p1_added,
        "pass2_conflicts": len(p2_pending), "pass3_added": p3_added,
        "conflicts": p2_pending,
    }


def get_sync_log(source_unit: str | None = None, limit: int = 20) -> list[dict]:
    with get_conn() as conn:
        if source_unit:
            rows = conn.execute(
                "SELECT * FROM sync_log WHERE source_unit=? "
                "ORDER BY sync_started_at DESC LIMIT ?",
                (source_unit, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM sync_log ORDER BY sync_started_at DESC LIMIT ?",
                (limit,)).fetchall()
    result = []
    for r in rows:
        d = row_to_dict(r)
        if d.get("detail") and isinstance(d["detail"], str):
            try:
                d["detail"] = json.loads(d["detail"])
            except Exception:
                pass
        result.append(d)
    return result


def get_sync_conflicts(sync_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM sync_log WHERE id=?", (sync_id,)).fetchone()
    if not row:
        return None
    d = row_to_dict(row)
    if isinstance(d.get("detail"), str):
        try:
            d["detail"] = json.loads(d["detail"])
        except Exception:
            pass
    return d


def resolve_conflict(sync_id: str, conflict_idx: int,
                     action: str, operator: str) -> dict:
    entry = get_sync_conflicts(sync_id)
    if not entry:
        raise ValueError(f"sync_id {sync_id} not found")

    conflicts = (entry.get("detail") or {}).get("pass2_conflicts") or []
    if conflict_idx >= len(conflicts):
        raise ValueError(f"conflict_idx {conflict_idx} out of range")

    conflict              = conflicts[conflict_idx]
    conflict["action"]    = action
    conflict["resolved_by"] = operator
    conflict["resolved_at"] = now_utc()

    if action == "keep_incoming":
        rec = conflict.get("incoming", {}).get("data", {})
        now = now_utc()
        with get_conn() as conn:
            conn.execute("""
                INSERT INTO manual_records
                    (id, form_id, form_type, target_table, operator,
                     summary, payload, sync_status, submitted_at)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (
                str(uuid.uuid4()),
                rec.get("form_id", rec.get("type", "")),
                rec.get("form_type", ""), rec.get("target_table", ""),
                operator, conflict["incoming"].get("summary", ""),
                json.dumps(rec, ensure_ascii=False),
                "synced", conflict["incoming"].get("time", now)))

    detail     = entry.get("detail") or {}
    detail["pass2_conflicts"] = conflicts
    all_done   = all(c.get("action") for c in conflicts)
    new_status = "completed" if all_done else "partial"

    with get_conn() as conn:
        conn.execute("UPDATE sync_log SET detail=?, status=? WHERE id=?",
                     (json.dumps(detail, ensure_ascii=False), new_status, sync_id))

    audit(operator, None, "conflict_resolved", "sync_log", sync_id,
          {"conflict_idx": conflict_idx, "action": action})
    return {"sync_id": sync_id, "conflict_idx": conflict_idx,
            "action": action, "status": new_status}
