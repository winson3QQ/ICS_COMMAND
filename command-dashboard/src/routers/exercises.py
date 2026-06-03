"""
exercises.py — 演練場次管理（C0 新增）
合併原 TTX sessions + 新增 real 場次支援
"""


from fastapi import APIRouter, HTTPException, Request

from auth.service import validate_session
from repositories._helpers import audit
from repositories.aar_repo import create_aar_entry, get_aar_entries
from repositories.exercise_repo import delete_exercise, update_exercise_status
from schemas.exercise import AAREntryIn, ExerciseCreateIn, ExerciseStatusIn
from services.exercise_service import archive, create, get, list_all, set_active
from services.realtime_hub import cop_hub

router = APIRouter(prefix="/api/exercises", tags=["演練"])


@router.post("")
def create_exercise(body: ExerciseCreateIn, request: Request):
    validate_session(request)
    return create(body.model_dump())


@router.get("")
def list_exercises(type: str | None = None):
    return list_all(type)


@router.get("/{exercise_id}")
def get_exercise(exercise_id: int):
    ex = get(exercise_id)
    if not ex:
        raise HTTPException(404, "演練不存在")
    return ex


@router.post("/{exercise_id}/activate")
async def activate(exercise_id: int, request: Request):
    sess = validate_session(request)
    if not get(exercise_id):
        raise HTTPException(404, "演練不存在")
    try:
        result = set_active(exercise_id, sess["username"])
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    # P1-14：active 場改變 → 廣播給所有 session，各 client 重新依新 scope 對帳（map/面板/chip 即時反應）
    await cop_hub.broadcast_all({"op": "exercise_switched"})
    return result


@router.post("/{exercise_id}/archive")
async def do_archive(exercise_id: int, request: Request):
    sess = validate_session(request)
    if not get(exercise_id):
        raise HTTPException(404, "演練不存在")
    result = archive(exercise_id, sess["username"])
    await cop_hub.broadcast_all({"op": "exercise_switched"})  # 同 activate：通知所有 session 重新對帳
    return result


@router.delete("/{exercise_id}")
async def delete_ex(exercise_id: int, request: Request):
    # P1-14：硬刪一場 + 級聯其資料。SYSADMIN_ONLY（role_enum 守）；進行中不可刪（需先歸檔）。
    sess = validate_session(request)
    ex = get(exercise_id)
    if not ex:
        raise HTTPException(404, "演練不存在")
    if ex.get("status") == "active":
        raise HTTPException(409, "進行中的演習不可刪除，請先歸檔")
    cleared = delete_exercise(exercise_id)
    # exercise_id=None → audit 本身不被級聯清掉（留存刪除軌跡）
    audit(sess["username"], None, "exercise_deleted", "exercises", str(exercise_id), {"cleared": cleared})
    # 演習集合改變 → 廣播，其他 session 的演習清單 / chip 即時更新（即使非 active）
    await cop_hub.broadcast_all({"op": "exercise_switched"})
    return {"ok": True, "cleared": cleared}


@router.put("/{exercise_id}/status")
def update_status(exercise_id: int, body: ExerciseStatusIn, request: Request):
    sess = validate_session(request)
    try:
        update_exercise_status(exercise_id, body.status, sess["username"])
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return {"ok": True}


# ── AAR ─────────────────────────────────────────────────────────────────────

@router.post("/{exercise_id}/aar")
def add_aar(exercise_id: int, body: AAREntryIn, request: Request):
    sess = validate_session(request)
    try:
        return create_aar_entry(exercise_id, body.category, body.content,
                                body.created_by or sess["username"])
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/{exercise_id}/aar")
def get_aar(exercise_id: int):
    return get_aar_entries(exercise_id)
