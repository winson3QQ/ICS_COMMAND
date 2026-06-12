"""
exercises.py — 演練場次管理（C0 新增）
合併原 TTX sessions + 新增 real 場次支援
"""

from fastapi import APIRouter, HTTPException, Query, Request

from auth.service import validate_session
from repositories._helpers import NULL_SCOPE, audit, iso_utc
from repositories.aar_repo import create_aar_entry, get_aar_entries
from repositories.cop_entity_repo import get_cop_entity, list_tracks_by_exercise, update_cop_entity_cas
from repositories.exercise_repo import delete_exercise, update_exercise_status
from schemas.exercise import AAREntryIn, EnrollIn, ExerciseCreateIn, ExerciseStatusIn
from services.exercise_service import archive, create, current_exercise_id, get, list_all, set_active
from services.kpi_service import build_kpis
from services.realtime_hub import cop_hub
from services.timeline_service import build_timeline

router = APIRouter(prefix="/api/exercises", tags=["演練"])


async def _rescope_and_announce() -> None:
    """active 場切換後：先就地 rescope 所有跟隨 active 的 WS 連線到新 scope（#265，
    根除 WS scope 凍結 → 新場 entity 不 render），再廣播讓各 client 對帳 + 更新 chip/面板。
    順序重要：rescope 在 broadcast 之前，client 收到 exercise_switched 而觸發 resync GET 時，
    server 端 active 與連線 scope 都已是新值。"""
    new_scope = current_exercise_id() or NULL_SCOPE
    await cop_hub.rescope_active(new_scope)
    await cop_hub.broadcast_all({"op": "exercise_switched"})


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
    # P1-14：active 場改變 → 各 session 重新依新 scope 對帳（map/面板/chip 即時反應）。
    # #265：先就地 rescope 跟隨 active 的 WS 連線，再廣播（不靠 client 重連）。
    await _rescope_and_announce()
    return result


@router.post("/{exercise_id}/archive")
async def do_archive(exercise_id: int, request: Request):
    sess = validate_session(request)
    ex = get(exercise_id)
    if not ex:
        raise HTTPException(404, "演練不存在")
    result = archive(exercise_id, sess["username"])
    # P1-12b（#228）L2：演習歸檔 = 完整狀態 ceremony → 自動整包備份，manifest 帶
    # 演習 metadata（這個 backup = 演習 X 收尾完整狀態）。best-effort，不擋歸檔。
    backup_name = await _l2_archive_backup(ex, sess["username"])
    if backup_name:
        result = {**result, "backup": backup_name} if isinstance(result, dict) else result
    # 同 activate：歸檔 active 場 → active 變 None（NULL_SCOPE 實戰池），就地 rescope + 廣播（#265）
    await _rescope_and_announce()
    return result


async def _l2_archive_backup(exercise: dict, operator: str) -> str | None:
    """L2 整包備份（best-effort）。無金鑰（dev）→ None；失敗記 audit 不擋歸檔。"""
    import asyncio

    from core.config import DATA_DIR
    from services import user_data_backup_service as uds

    if not uds.key_available():
        return None
    ex_meta = {k: exercise.get(k) for k in ("id", "name", "type", "status")}
    try:
        res = await asyncio.to_thread(
            uds.create_backup, DATA_DIR, DATA_DIR / "backups", trigger="archive", exercise=ex_meta
        )
        audit(operator, None, "user_data_backup_created", "system", res.path.name,
              {"trigger": "archive", "exercise_id": exercise.get("id")})
        return res.path.name
    except Exception:
        audit(operator, None, "user_data_backup_failed", "system", "data",
              {"trigger": "archive", "exercise_id": exercise.get("id")})
        return None


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


@router.post("/{exercise_id}/enroll")
async def enroll(exercise_id: int, body: EnrollIn, request: Request):
    """#267 納編/退編：把一個 cop entity 移進當前 active 場（enroll）/ 退回 NULL 常駐（unenroll）。

    COMMAND_ROLES（中央 gate `/api/exercises/*` 非 GET）。**不套 `_require_editable_source`**——
    納編只改歸屬、非編輯現場物件內容，故訓練(ttx)/實戰(real) 皆可（決策見 #267）。
    `{exercise_id}` 必為當前 active 場（server-authoritative，不信 client 任選歷史場）。

    歸屬改法＝直接改 `cop_entities.exercise_id`（CAS；含 PLI 撞 vc 的一次內部重試）。後續 PLI 不改
    exercise_id（不在 `_TAK_UPDATE_FIELDS`）→ 一次納編永久生效。雙廣播（舊 scope delete / 新 scope
    create）讓各 scope 連線正確增刪；疊看連線兩者皆收、version_clock LWW 就地過渡不消失。
    註：tracks 靠 uid JOIN cop_entities 取場（Design B）→ 納編**追溯**把該單位全部軌跡歸入本場 AAR。"""
    sess = validate_session(request)
    if not get(exercise_id):
        raise HTTPException(404, "演練不存在")
    if current_exercise_id() != exercise_id:
        raise HTTPException(409, "只能對當前 active 演習納編/退編")
    target = exercise_id if body.action == "enroll" else None

    entity = get_cop_entity(body.uid)
    if entity is None:
        raise HTTPException(404, f"entity 不存在：{body.uid}")
    old_scope = entity.get("exercise_id")
    if old_scope == target:
        return entity  # 已在該 scope，no-op

    expected_vc = entity["version_clock"]
    res = None
    for _ in range(2):  # PLI 可能在讀取後撞 vc → 重讀一次再 CAS
        res = update_cop_entity_cas(body.uid, expected_vc, {"exercise_id": target}, actor=sess["username"])
        if res["status"] == "ok":
            break
        if res["status"] == "notfound":
            raise HTTPException(404, f"entity 不存在：{body.uid}")
        expected_vc = res["entity"]["version_clock"]
        old_scope = res["entity"].get("exercise_id")
    if res is None or res["status"] != "ok":
        raise HTTPException(409, "version 衝突，請重試")
    new_entity = res["entity"]

    action = "cop_entity_enrolled" if target is not None else "cop_entity_unenrolled"
    audit(sess["username"], None, action, "cop_entities", body.uid, {"from": old_scope, "to": target})

    # 雙廣播：舊 scope 連線掉、新 scope 連線加。delete 帶 CAS 後 vc（保證 ≥ 任何連線快取值，含 PLI
    # 撞 vc 重試後的值 → 不會被 _applyDelete 當 stale 丟掉而殘留鬼影）。
    await cop_hub.broadcast(
        {"op": "delete", "uid": body.uid, "version_clock": new_entity["version_clock"]}, exercise_id=old_scope
    )
    await cop_hub.broadcast(
        {"op": "create", "uid": body.uid, "version_clock": new_entity["version_clock"], "entity": new_entity},
        exercise_id=new_entity.get("exercise_id"),
    )
    return new_entity


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
        return create_aar_entry(
            exercise_id, body.category, body.content, body.created_by or sess["username"], ref_t=body.ref_t
        )
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


# ── 演習指標（P2-21 子集 / issue #204）─────────────────────────────────────


@router.get("/{exercise_id}/kpis")
def get_kpis(exercise_id: int):
    """演習 KPI 快照（事件處置時長 / 通聯量 by 組 / 決策裁示時長 / 軌跡量 / AAR 條目數）。

    RBAC：COMMAND_ROLES（中央 gate `/api/exercises/*` 非 DELETE；統計含演習表現資訊，
    row 規格明定不暴露 public API）。量不出的指標回 null+reason（#204 誠實邊界）。
    """
    if not get(exercise_id):
        raise HTTPException(404, "演練不存在")
    return build_kpis(exercise_id)
