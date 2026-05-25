"""
ttx.py — TTX inject 管理（C0：綁定 exercise_id，取代舊 session_id）

C5 前向相容：
- TTX_ORCHESTRATOR role 可呼叫此 router
- 每個 inject 有 signature 欄位（C0 加，C5 填入簽章值）
"""

import glob
import json
import os
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request

from auth.service import validate_session
from core.config import SRC_DIR
from repositories.decision_repo import create_decision
from repositories.event_repo import create_event
from repositories.snapshot_repo import upsert_snapshot
from repositories.ttx_repo import (
    bulk_create_ttx_injects,
    get_ttx_injects,
    mark_ttx_inject_done,
)
from schemas.ttx import TTXInjectBulkIn

router = APIRouter(prefix="/api/ttx", tags=["TTX"])

_SNAPSHOT_TYPE_MAP = {
    "snapshot_shelter": "shelter", "snapshot_medical": "medical",
    "snapshot_forward": "forward", "snapshot_security": "security",
    "shelter": "shelter", "medical": "medical",
    "forward": "forward", "security": "security",
}


@router.get("/exercises/{exercise_id}/injects")
def list_injects(exercise_id: int, request: Request):
    validate_session(request)
    return get_ttx_injects(exercise_id)


@router.post("/exercises/{exercise_id}/injects")
def bulk_injects(exercise_id: int, body: TTXInjectBulkIn, request: Request):
    validate_session(request)
    count = bulk_create_ttx_injects(exercise_id, body.injects)
    return {"ok": True, "imported": count}


@router.post("/exercises/{exercise_id}/injects/{inject_id}/push")
def push_inject(exercise_id: int, inject_id: str, request: Request, live: bool = False):
    """執行單一 inject。live=true 時用當前 UTC 取代固定時間戳。"""
    sess    = validate_session(request)
    injects = get_ttx_injects(exercise_id)
    inject  = next((i for i in injects if i["id"] == inject_id), None)
    if not inject:
        raise HTTPException(404, "Inject 不存在")
    if inject["status"] != "pending":
        raise HTTPException(409, f"Inject 已執行（status={inject['status']}）")

    payload = inject["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    inject_type = inject["inject_type"]

    if live:
        _now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        if "t" in payload:
            payload["t"] = _now
        if "snapshot_id" in payload:
            payload["snapshot_id"] = f"live-{inject_id[:8]}-{int(datetime.now(UTC).timestamp())}"

    results = []

    def _map_snap(p):
        if "node_type" not in p and "type" in p:
            p["node_type"] = _SNAPSHOT_TYPE_MAP.get(p["type"], p["type"])
        return p

    if inject_type == "snapshot":
        r = upsert_snapshot(_map_snap(payload), exercise_id)
        results.append({"type": "snapshot", "result": r})

    elif inject_type == "event":
        ev_data = {
            "reported_by_unit":         payload.get("reported_by_unit", inject["target_unit"]),
            "event_type":               payload.get("event_type", "ttx_inject"),
            "severity":                 payload.get("severity", "info"),
            "description":              payload.get("description", inject["title"]),
            "operator_name":            payload.get("operator_name", sess["username"]),
            "location_desc":            payload.get("location_desc"),
            "location_zone_id":         payload.get("location_zone_id"),
            "needs_commander_decision": payload.get("needs_commander_decision", 0),
        }
        r = create_event(ev_data, exercise_id)
        results.append({"type": "event", "result": r})

    elif inject_type == "decision":
        dec_data = {
            "decision_type":      payload.get("decision_type", "initial"),
            "severity":           payload.get("severity", "warning"),
            "decision_title":     payload.get("decision_title", inject["title"]),
            "impact_description": payload.get("impact_description", ""),
            "suggested_action_a": payload.get("suggested_action_a", ""),
            "suggested_action_b": payload.get("suggested_action_b"),
            "created_by":         payload.get("created_by", sess["username"]),
            "primary_event_id":   payload.get("primary_event_id"),
        }
        r = create_decision(dec_data, exercise_id)
        results.append({"type": "decision", "result": r})

    elif inject_type == "compound":
        items = payload if isinstance(payload, list) else payload.get("items", [])
        for item in items:
            sub_type    = item.get("type", "snapshot")
            sub_payload = item.get("payload", item)
            if sub_type == "snapshot":
                r = upsert_snapshot(_map_snap(sub_payload), exercise_id)
                results.append({"type": "snapshot", "result": r})
            elif sub_type == "event":
                sub_ev = {
                    "reported_by_unit": sub_payload.get("reported_by_unit", "system"),
                    "event_type":       sub_payload.get("event_type", "ttx_inject"),
                    "severity":         sub_payload.get("severity", "info"),
                    "description":      sub_payload.get("description", ""),
                    "operator_name":    sub_payload.get("operator_name", sess["username"]),
                }
                r = create_event(sub_ev, exercise_id)
                results.append({"type": "event", "result": r})

    mark_ttx_inject_done(inject_id, sess["username"])
    return {"ok": True, "inject_id": inject_id, "results": results}


# ── 情境檔案（scenarios/*.json）───────────────────────────────────────────────


@router.get("/scenarios", tags=["TTX"])
def list_scenarios(request: Request):
    validate_session(request)
    scenario_dir = str(SRC_DIR.parent / "scenarios")
    if not os.path.isdir(scenario_dir):
        return []
    result = []
    for f in sorted(glob.glob(os.path.join(scenario_dir, "*.json"))):
        try:
            data = json.loads(open(f, encoding="utf-8").read())
            result.append({
                "id":           data.get("id", os.path.basename(f).replace(".json", "")),
                "name":         data.get("name", ""),
                "description":  data.get("description", ""),
                "duration_min": data.get("duration_min"),
                "inject_count": len(data.get("injects", [])),
            })
        except Exception:
            pass
    return result


@router.post("/scenarios/{scenario_id}/load", tags=["TTX"])
def load_scenario(scenario_id: str, exercise_id: int, request: Request):
    """載入預建情境的 injects 到指定演練"""
    validate_session(request)
    scenario_dir = str(SRC_DIR.parent / "scenarios")
    fpath        = os.path.join(scenario_dir, f"{scenario_id}.json")
    if not os.path.isfile(fpath):
        raise HTTPException(404, f"情境 {scenario_id} 不存在")
    data  = json.loads(open(fpath, encoding="utf-8").read())
    count = bulk_create_ttx_injects(exercise_id, data.get("injects", []))
    return {"ok": True, "exercise_id": exercise_id, "injects_loaded": count}
