from fastapi import APIRouter, HTTPException, Request

from repositories.decision_repo import create_decision, decide, get_decisions
from schemas.decision import DecideIn, DecisionIn
from services.exercise_service import current_exercise_id, resolve_scope

router = APIRouter(prefix="/api/decisions", tags=["裁示"])


@router.post("")
def post_decision(dec: DecisionIn):
    # P1-14：exercise_id 由 server 端 active 場決定，不信任 client 帶的 dec.exercise_id
    # （對齊 events / cop create doctrine）。無 active → NULL＝實戰池。
    return create_decision(dec.model_dump(), current_exercise_id())


@router.get("")
def get_dec(request: Request, status: str | None = None, exercise_id: int | None = None):
    # P1-14：預設只回當前 active 場；commander 可顯式帶 exercise_id 看歷史（resolve_scope 守門）。
    return get_decisions(status, resolve_scope(request.state.session, exercise_id))


@router.post("/{decision_id}/decide")
def do_decide(decision_id: str, body: DecideIn, request: Request, exercise_id: int | None = None):
    # #288 H3：與 get_dec 對稱套 resolve_scope——operator/observer 鎖當前場、commander 可帶
    # 歷史場；跨演習的 decision 在 repo 層視同不存在 → 不可越權裁示他場待裁指令。
    scope = resolve_scope(request.state.session, exercise_id)
    try:
        return decide(decision_id, body.action, body.decided_by, body.execution_note, scope)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
