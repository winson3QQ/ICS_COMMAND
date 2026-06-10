"""
exercises.py — 演練場次管理（C0 新增）
合併原 TTX sessions + 新增 real 場次支援
"""


from fastapi import APIRouter, HTTPException, Query, Request

from auth.service import validate_session
from repositories._helpers import audit, iso_utc
from repositories.aar_repo import create_aar_entry, get_aar_entries
from repositories.cop_entity_repo import list_tracks_by_exercise
from repositories.exercise_repo import delete_exercise, update_exercise_status
from schemas.exercise import AAREntryIn, ExerciseCreateIn, ExerciseStatusIn
from services.exercise_service import archive, create, get, list_all, set_active
from services.realtime_hub import cop_hub
from services.timeline_service import build_timeline

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


# ── 軌跡查詢（P2-06b / issue #123）─────────────────────────────────────────────


def _range_bound(value: str | None, *, end: bool) -> str | None:
    """from/to 時間界正規化為 ISO 8601 UTC Z（與軌跡 t 同格式才能字串比較）。
    只給日期（YYYY-MM-DD，無 T）→ 補當天起/訖；否則 'YYYY-MM-DDZ' 的字典序落在
    'YYYY-MM-DDThh:mm:ssZ' 之外（'Z'>'T'），單日查詢會把當天軌跡全漏掉。"""
    if not value:
        return None
    if "T" not in value and len(value) == 10:
        value += "T23:59:59" if end else "T00:00:00"
    return iso_utc(value)


@router.get("/{exercise_id}/tracks")
def get_tracks(
    exercise_id: int,
    uid: str | None = None,
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    limit: int = 1000,
    offset: int = 0,
):
    """某場（演習 ttx / 實戰 real）所有 entity 的軌跡時間序列（P2-20 AAR 回放資料源）。

    RBAC：COMMAND_ROLES（中央 gate `/api/exercises/*` 非 DELETE；軌跡含人員位置 PII）。
    回 [{uid, t, lat, lon, hae, heading_deg, speed_mps}]，t 升序，分頁。
    from/to 接完整 ISO 8601 或純日期（純日期補當天起訖，見 _range_bound）。
    """
    if not get(exercise_id):
        raise HTTPException(404, "演練不存在")
    return list_tracks_by_exercise(
        exercise_id,
        uid=uid,
        since=_range_bound(from_, end=False),
        until=_range_bound(to, end=True),
        # 服務端上限（防單次撈爆）。clamp 散落各 list endpoint（admin/cop 尚未套），統一化留 follow-up
        limit=min(max(limit, 1), 5000),
        offset=max(offset, 0),
    )


# ── AAR 統一時間軸（P2-20(A) / issue #199）────────────────────────────────────


@router.get("/{exercise_id}/timeline")
def get_timeline(
    exercise_id: int,
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    limit: int = 5000,
):
    """某場的統一時間軸：tracks + events + chats + 決策/指令 audit 合併、按 t 排序（AAR 回放資料源）。

    RBAC：COMMAND_ROLES（中央 gate `/api/exercises/*` 非 DELETE；含軌跡/通聯 PII，同 /tracks）。
    回 {meta: {count, t_start, t_end, truncated}, items: [{type, t, actor, payload}]}；
    truncated=true → 呼叫端縮 from/to 時間窗重查（不靜默截斷）。
    設計（事件流、不落盤快照）+ 業界調查見 #199。
    """
    if not get(exercise_id):
        raise HTTPException(404, "演練不存在")
    return build_timeline(
        exercise_id,
        since=_range_bound(from_, end=False),
        until=_range_bound(to, end=True),
        limit=min(max(limit, 1), 5000),
    )
