"""
exercise_service.py — 演練場次業務邏輯
C5 前向相容：set_active() 含 mutex 防護
"""

from auth.role_enum import COMMAND_ROLES, is_role_allowed
from repositories.exercise_repo import (
    create_exercise,
    get_active_exercise,
    get_exercise,
    list_exercises,
    update_exercise_status,
)


def create(data: dict) -> dict:
    return create_exercise(data)


def get(exercise_id: int) -> dict | None:
    return get_exercise(exercise_id)


def list_all(type_filter: str | None = None) -> list[dict]:
    return list_exercises(type_filter)


def set_active(exercise_id: int, operator: str) -> dict:
    """
    啟動演練。
    mutex：同一時間只能有一個 active，防止真實事故與演練資料混用。
    C5 Orchestrator 將用此 endpoint 觸發演練開始。
    """
    update_exercise_status(exercise_id, "active", operator)
    return get_exercise(exercise_id)


def archive(exercise_id: int, operator: str) -> dict:
    """結束演練，釋放 mutex"""
    update_exercise_status(exercise_id, "archived", operator)
    return get_exercise(exercise_id)


def current_exercise_id() -> int | None:
    """供其他 service/router 取得目前 active exercise_id"""
    ex = get_active_exercise()
    return ex["id"] if ex else None


def resolve_scope(session: dict | None, requested_exercise_id: int | None) -> int | None:
    """P1-14：GET / WS 的 exercise 範圍解析（安全閘）。

    - 未指定（None）→ 當前 active exercise（所有角色看當前場；無 active 則回 None＝實戰/未分場池）。
    - 顯式指定歷史場（int）→ **限 COMMAND_ROLES**（指揮層看歷史）；角色不足則忽略該請求、
      強制回當前 active（防 operator/observer 越權窺看其他場次資料）。

    對齊 cop create 強制 source='manual' 的「不信任 client 宣告」doctrine：
    範圍由 server 端 active 狀態 + 角色決定，不讓低權限 client 自行宣告要看哪場。
    """
    if requested_exercise_id is None:
        return current_exercise_id()
    if session and is_role_allowed(session, COMMAND_ROLES):
        return requested_exercise_id
    return current_exercise_id()
