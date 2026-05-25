"""
exercise_service.py — 演練場次業務邏輯
C5 前向相容：set_active() 含 mutex 防護
"""

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
