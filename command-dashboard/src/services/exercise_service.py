# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
exercise_service.py — 演練場次業務邏輯
C5 前向相容：set_active() 含 mutex 防護
"""

from auth.role_enum import COMMAND_ROLES, is_role_allowed
from repositories._helpers import NULL_SCOPE
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


def require_no_active_exercise() -> None:
    """#343 AAR 互斥閘（設計 §6）：有任何 active 演習（TTX/實戰）時，AAR / 回放讀取對**所有角色
    （含 sysadmin/白隊）一致關閉** → 409。斬斷 commander 用 AAR/時間軸偷看 live 紅軍的後門
    （紅藍隔離 §6，使用者拍板：白隊也須演習結束才看 AAR）。無 active（場已歸檔）→ 放行、全見。

    套用於回放/PII 出口：/aar(GET)、/tracks、/timeline、/kpis、ai /report、/export。
    """
    from fastapi import HTTPException

    if current_exercise_id() is not None:
        raise HTTPException(409, "AAR / 回放在演習進行中不開放（演習結束歸檔後可閱）")


def resolve_scope(session: dict | None, requested_exercise_id: int | None):
    """P1-14：GET / WS 的 exercise 範圍解析（安全閘）。回傳給 repo 當 exercise filter。

    回傳值對映 repo 三態（見 repositories._helpers.NULL_SCOPE）：
    - 未指定（None）→ 當前 active exercise（int）；無 active 則回 `NULL_SCOPE`＝strict
      isolation 到實戰 / 未分場池（**不是 None＝全部**，避免無 active 時洩漏所有場次）。
    - 顯式指定歷史場（int）→ **限 COMMAND_ROLES**（指揮層看歷史）；角色不足則忽略該請求、
      強制回當前 active / 實戰池（防 operator/observer 越權窺看其他場次資料）。

    current_exercise_id() 回 int（autoincrement 從 1，恆 truthy）或 None，故 `or NULL_SCOPE`
    安全：有 active 回該 int，無 active 才落 NULL_SCOPE。

    對齊 cop create 強制 source='manual' 的「不信任 client 宣告」doctrine：
    範圍由 server 端 active 狀態 + 角色決定，不讓低權限 client 自行宣告要看哪場。
    """
    if requested_exercise_id is None:
        return current_exercise_id() or NULL_SCOPE
    if session and is_role_allowed(session, COMMAND_ROLES):
        return requested_exercise_id
    return current_exercise_id() or NULL_SCOPE
