"""
dashboard_service.py — 組裝儀表板回應資料
前端每 10 秒呼叫 /api/dashboard，此 service 負責聚合所有資料。
"""

import json

import calc_engine
from repositories import (
    decision_repo,
    event_repo,
    pi_batch_repo,
    pi_node_repo,
    snapshot_repo,
)


def _pi_batch_to_snapshot(unit_id: str, pushed_at: str, records: list) -> dict | None:
    """從 pi_received_batches.records 衍生出 calc_engine 期望的 snapshot dict"""
    if not records:
        return None

    by_table = {}
    for r in records:
        tbl = r.get("table_name", "")
        rec = r.get("record", {})
        if tbl and rec:
            by_table.setdefault(tbl, []).append(rec)

    snap = {
        "snapshot_id":   f"pi_derived_{unit_id}_{pushed_at}",
        "snapshot_time": pushed_at,
        "node_type":     unit_id,
        "source":        "pi_push",
    }

    if unit_id == "medical":
        patients = by_table.get("patients", [])
        active   = [p for p in patients if p.get("current_zone") != "已離區"]
        colors   = {"red": 0, "yellow": 0, "green": 0, "black": 0}
        for p in active:
            c = p.get("triage_color", "")
            if c in colors:
                colors[c] += 1
        snap.update({
            "casualties_red":    colors["red"],
            "casualties_yellow": colors["yellow"],
            "casualties_green":  colors["green"],
            "casualties_black":  colors["black"],
            "bed_used":          len(active),
            "bed_total":         max(len(active) + 5, 20),
            "waiting_count":     len([p for p in active
                                      if p.get("care_status") == "triaged"
                                      and p.get("disposition", "在場") == "在場"]),
            "pending_evac":      len([p for p in active if p.get("disposition") == "後送"]),
            "staff_on_duty":     None,
        })
        all_pts = by_table.get("patients", [])
        snap["extra"] = {
            "src_a": len([p for p in all_pts if p.get("source_type") == "A"]),
            "src_b": len([p for p in all_pts if p.get("source_type") == "B"]),
            "src_c": len([p for p in all_pts if p.get("source_type") == "C"]),
        }

    elif unit_id == "shelter":
        persons   = by_table.get("persons", [])
        beds      = by_table.get("beds", [])
        beds_meta = by_table.get("beds_meta", [])
        placed    = [p for p in persons if p.get("status") == "已安置"]
        waiting   = [p for p in persons if p.get("status") == "等候中"]

        capacity_max = next(
            (int(m["capacity_max"]) for m in beds_meta
             if m.get("_id") == "capacity" and m.get("capacity_max")), None)
        active_beds = [b for b in beds if b.get("status") != "suspended"]
        total_beds  = (capacity_max if capacity_max is not None
                       else (len(active_beds) if active_beds else max(len(placed) + 5, 12)))

        srt = {"red": 0, "yellow": 0, "green": 0}
        for p in placed:
            c = p.get("srt_color", "")
            if c in srt:
                srt[c] += 1
        snap.update({
            "bed_used":       len(placed),
            "bed_total":      total_beds,
            "pending_intake": len(waiting),
            "staff_on_duty":  None,
            "extra":          {"srt": srt},
        })

    # 共用：物資
    resources = by_table.get("resources", [])
    if resources:
        supplies = {}
        supplies_max = {}
        for r in resources:
            if r.get("disabled"):
                continue
            key = r.get("name", "").lower().replace(" ", "_")[:20]
            supplies[key]     = r.get("qty_current", 0)
            supplies_max[key] = r.get("qty_initial", 0) or r.get("qty_current", 0)
        extra = snap.get("extra", {}) if isinstance(snap.get("extra"), dict) else {}
        extra["supplies"]     = supplies
        extra["supplies_max"] = supplies_max
        snap["extra"]         = extra

    # 共用：事件壓力
    incidents = by_table.get("incidents", [])
    if incidents:
        open_incs = [i for i in incidents if i.get("status") not in ("已結案", "closed")]
        sev       = {"高": 0, "中": 0, "低": 0}
        for i in open_incs:
            sev[i.get("severity", "中")] = sev.get(i.get("severity", "中"), 0) + 1
        extra = snap.get("extra", {}) if isinstance(snap.get("extra"), dict) else {}
        extra["incident_pressure"] = {
            "high": sev["高"], "medium": sev["中"], "low": sev["低"],
            "open_total": len(open_incs),
        }
        snap["extra"] = extra

    return snap


def _enrich_from_pi_batches(unit: str, base_snaps: list, limit: int = 40) -> list:
    """將 Pi push 衍生快照插入快照列表前端"""
    batches   = pi_batch_repo.get_recent_pi_batches(unit, limit)
    pi_snaps  = []
    for batch in batches:
        records = (json.loads(batch["records_json"])
                   if isinstance(batch["records_json"], str) else batch["records_json"])
        snap = _pi_batch_to_snapshot(unit, batch["pushed_at"], records)
        if snap:
            pi_snaps.append(snap)
    return pi_snaps + base_snaps if pi_snaps else base_snaps


def build_dashboard(exercise_id: int | None = None) -> dict:
    """組裝儀表板完整回應"""
    # 各組最近快照
    med_snaps  = _enrich_from_pi_batches("medical",  snapshot_repo.get_snapshots("medical",  40, exercise_id))
    sh_snaps   = _enrich_from_pi_batches("shelter",  snapshot_repo.get_snapshots("shelter",  40, exercise_id))
    fwd_snaps  = snapshot_repo.get_snapshots("forward",  40, exercise_id)
    sec_snaps  = snapshot_repo.get_snapshots("security", 40, exercise_id)

    # 事件
    open_ev   = event_repo.get_events("open",        50, exercise_id)
    prog_ev   = event_repo.get_events("in_progress", 50, exercise_id)
    res_ev    = event_repo.get_events("resolved",    50, exercise_id)

    calc = calc_engine.dashboard_calc(
        med_snaps, sh_snaps, fwd_snaps, sec_snaps,
        open_event_count=len(open_ev) + len(prog_ev),
        event_trend_up=len(open_ev) > 5,
    )

    events = sorted(open_ev + prog_ev + res_ev,
                    key=lambda e: e["occurred_at"], reverse=True)

    pending   = decision_repo.get_decisions("pending",  exercise_id)
    approved  = decision_repo.get_decisions("approved", exercise_id)
    completed = decision_repo.get_decisions("completed", exercise_id)

    sh_hist  = _enrich_from_pi_batches("shelter", snapshot_repo.get_snapshots("shelter", 100, exercise_id), 100)
    med_hist = _enrich_from_pi_batches("medical", snapshot_repo.get_snapshots("medical", 100, exercise_id), 100)

    all_dec = decision_repo.get_decisions(exercise_id=exercise_id)
    chains  = {}
    for dec in all_dec:
        key = dec.get("primary_event_id") or dec["id"]
        chains.setdefault(key, []).append(dec)

    return {
        "calc":   calc,
        "events": events,
        "open_events_on_map": [e for e in events if e.get("location_zone_id")],
        "decisions": {
            "pending": pending,
            "decided": sorted(approved + completed,
                               key=lambda d: d.get("decided_at", ""), reverse=True)[:20],
            "chains":  chains,
        },
        "shelter_history": sh_hist,
        "medical_history": med_hist,
        "pi_nodes": pi_node_repo.list_pi_nodes(),
    }
