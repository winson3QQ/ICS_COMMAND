from datetime import UTC, datetime, timedelta

import structlog
from fastapi import APIRouter, HTTPException, Request

from repositories.decision_repo import create_decision
from repositories.event_marker_repo import get_event_chain
from repositories.event_repo import add_event_note, create_event, get_events, patch_event, update_event_status
from schemas.event import DeadlinePatch, EventIn, EventNoteIn, EventPatch
from services.exercise_service import current_exercise_id, resolve_scope

router = APIRouter(prefix="/api/events", tags=["事件"])
log = structlog.get_logger()

VALID_SEVERITIES = {"info", "warning", "critical"}
VALID_UNITS = {"shelter", "medical", "forward", "security", "command"}


@router.post("")
def post_event(ev: EventIn):
    if ev.severity not in VALID_SEVERITIES:
        raise HTTPException(422, f"severity 必須是 {VALID_SEVERITIES}")
    if ev.reported_by_unit not in VALID_UNITS:
        raise HTTPException(422, f"reported_by_unit 必須是 {VALID_UNITS}")
    # P1-14：exercise_id 由 server 端 active 場決定，不信任 client 帶的 ev.exercise_id
    # （對齊 cop create 強制 source='manual' 的 doctrine）。無 active → NULL＝實戰池。
    active_ex = current_exercise_id()
    result = create_event(ev.model_dump(), active_ex)
    log.info(
        "event_created",
        msg="事件建立",
        detail={
            "event_id": result.get("id"),
            "severity": ev.severity,
            "event_type": ev.event_type,
            "unit": ev.reported_by_unit,
        },
    )
    if ev.needs_commander_decision:
        create_decision(
            {
                "primary_event_id": result["id"],
                "decision_type": "initial",
                "severity": ev.severity if ev.severity != "info" else "warning",
                "decision_title": ev.description[:60],
                "impact_description": f"來源：{ev.reported_by_unit}　{ev.event_type}",
                "suggested_action_a": "（計劃情報組補充建議動作）",
                "created_by": ev.operator_name,
            },
            active_ex,
        )
    return result


@router.get("")
def get_ev(request: Request, status: str | None = None, limit: int = 50, exercise_id: int | None = None):
    # P1-14：預設只回當前 active 場；commander 可顯式帶 exercise_id 看歷史（resolve_scope 守門）。
    return get_events(status, limit, resolve_scope(request.state.session, exercise_id))


@router.get("/{event_id}/chain")
def get_chain(event_id: str, request: Request, exercise_id: int | None = None):
    """P2-27 導航鏈：一處看「事 → 標記（via event_markers junction）→ 決策」完整脈絡。

    取代「event↔cop 靠 attributes JSON glue、四表割裂無導航」的現狀。
    報(chats)/行(下行) 兩段尚無 FK，留 P2-28 / P2-13 B（見 event_marker_repo.get_event_chain）。

    **P1-14 PII 守門**：本端點回傳 event（含 related_person_name 等 PII）+ 決策，故與 `get_ev`
    同走 `resolve_scope`——預設限當前 active 場 / 實戰池；指揮層可顯式帶 `exercise_id` 看歷史。
    event 不在範圍（含不存在）→ 404，不洩漏跨場 event 的存在性。多場資料於同一 DB 並存
    （exercises archived 保留 + AAR 回放需要），故此閘為實質邊界、非裝飾。
    """
    chain = get_event_chain(event_id, resolve_scope(request.state.session, exercise_id))
    if chain is None:
        raise HTTPException(404, "event not found")
    return chain


@router.patch("/{event_id}")
def patch_ev(event_id: str, body: EventPatch):
    updates = {}
    if body.assigned_unit is not None:
        updates["assigned_unit"] = body.assigned_unit or None
    if body.location_desc is not None:
        updates["location_desc"] = body.location_desc
    if updates:
        patch_event(event_id, updates)
        log.info("event_updated", msg="事件更新", detail={"event_id": event_id, "fields": list(updates.keys())})
    return {"ok": True}


@router.patch("/{event_id}/deadline")
def patch_deadline(event_id: str, body: DeadlinePatch):
    events = get_events()
    ev = next((e for e in events if e["id"] == event_id), None)
    if not ev:
        raise HTTPException(404, "event not found")
    current = ev.get("response_deadline")
    base = datetime.fromisoformat(current.replace("Z", "+00:00")) if current else datetime.now(UTC)
    new_dl = (base + timedelta(minutes=body.delta_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
    patch_event(event_id, {"response_deadline": new_dl})
    return {"ok": True, "new_deadline": new_dl}


@router.patch("/{event_id}/status")
def patch_status(event_id: str, status: str, operator: str):
    try:
        update_event_status(event_id, status, operator)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}


@router.post("/{event_id}/notes")
def add_note(event_id: str, body: EventNoteIn):
    try:
        return add_event_note(event_id, body.text, body.operator)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
