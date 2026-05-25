"""
exercises.py — 演練場次管理（C0 新增）
合併原 TTX sessions + 新增 real 場次支援
"""


from fastapi import APIRouter, HTTPException, Request

from auth.service import validate_session
from repositories.aar_repo import create_aar_entry, get_aar_entries
from repositories.exercise_repo import update_exercise_status
from schemas.exercise import AAREntryIn, ExerciseCreateIn, ExerciseStatusIn
from services.exercise_service import archive, create, get, list_all, set_active

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
def activate(exercise_id: int, request: Request):
    sess = validate_session(request)
    if not get(exercise_id):
        raise HTTPException(404, "演練不存在")
    try:
        return set_active(exercise_id, sess["username"])
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


@router.post("/{exercise_id}/archive")
def do_archive(exercise_id: int, request: Request):
    sess = validate_session(request)
    if not get(exercise_id):
        raise HTTPException(404, "演練不存在")
    return archive(exercise_id, sess["username"])


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
