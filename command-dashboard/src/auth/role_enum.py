from __future__ import annotations

from collections.abc import Iterable

ROLE_SYSADMIN = "sysadmin"
ROLE_COMMANDER = "commander"
ROLE_OPERATOR = "operator"
ROLE_OBSERVER = "observer"

ROLE_SYSADMIN_ZH = "\u7cfb\u7d71\u7ba1\u7406\u54e1"
ROLE_COMMANDER_ZH = "\u6307\u63ee\u5b98"
ROLE_OPERATOR_ZH = "\u64cd\u4f5c\u54e1"
ROLE_OBSERVER_ZH = "\u89c0\u5bdf\u54e1"

ROLE_ZH_TO_EN = {
    ROLE_SYSADMIN_ZH: ROLE_SYSADMIN,
    ROLE_COMMANDER_ZH: ROLE_COMMANDER,
    ROLE_OPERATOR_ZH: ROLE_OPERATOR,
    ROLE_OBSERVER_ZH: ROLE_OBSERVER,
    "admin": ROLE_SYSADMIN,
}
ROLE_EN_TO_ZH = {
    ROLE_SYSADMIN: ROLE_SYSADMIN_ZH,
    ROLE_COMMANDER: ROLE_COMMANDER_ZH,
    ROLE_OPERATOR: ROLE_OPERATOR_ZH,
    ROLE_OBSERVER: ROLE_OBSERVER_ZH,
}

ALL_ROLES = frozenset(ROLE_EN_TO_ZH)
READ_ROLES = frozenset({ROLE_SYSADMIN, ROLE_COMMANDER, ROLE_OPERATOR, ROLE_OBSERVER})
WRITE_ROLES = frozenset({ROLE_SYSADMIN, ROLE_COMMANDER, ROLE_OPERATOR})
COMMAND_ROLES = frozenset({ROLE_SYSADMIN, ROLE_COMMANDER})
ACCOUNT_MANAGER_ROLES = frozenset({ROLE_SYSADMIN, ROLE_COMMANDER})
SYSADMIN_ONLY = frozenset({ROLE_SYSADMIN})


def role_zh_to_en(role: str | None, role_detail: str | None = None) -> str | None:
    if role_detail in ALL_ROLES:
        return role_detail
    if role_detail in ROLE_ZH_TO_EN:
        return ROLE_ZH_TO_EN[role_detail]
    if role in ALL_ROLES:
        return role
    return ROLE_ZH_TO_EN.get(role or "")


def role_en_to_zh(role_detail: str | None, role: str | None = None) -> str:
    if role_detail in ROLE_EN_TO_ZH:
        return ROLE_EN_TO_ZH[role_detail]
    if role in ROLE_ZH_TO_EN:
        return role or ROLE_OPERATOR_ZH
    if role in ROLE_EN_TO_ZH:
        return ROLE_EN_TO_ZH[role or ROLE_OPERATOR]
    return ROLE_OPERATOR_ZH


def normalize_role_pair(role: str | None, role_detail: str | None = None) -> tuple[str, str]:
    role_en = role_zh_to_en(role, role_detail) or ROLE_OPERATOR
    return role_en_to_zh(role_en, role), role_en


def require_role(*allowed_roles: str):
    allowed = frozenset(allowed_roles)

    def check(session: dict) -> dict:
        role = role_zh_to_en(session.get("role"), session.get("role_detail"))
        if role not in allowed:
            from fastapi import HTTPException

            raise HTTPException(403, "role denied")
        return session

    check.allowed_roles = allowed  # type: ignore[attr-defined]
    return check


def is_role_allowed(session: dict, allowed_roles: Iterable[str]) -> bool:
    role = role_zh_to_en(session.get("role"), session.get("role_detail"))
    return role in set(allowed_roles)


def allowed_roles_for(method: str, path: str) -> frozenset[str] | None:
    method = method.upper()
    if path.startswith("/api/auth/") or path == "/api/session/status":
        return None
    if path == "/api/admin/accounts" or path.startswith("/api/admin/accounts/"):
        return ACCOUNT_MANAGER_ROLES
    if path.startswith("/api/admin/"):
        return SYSADMIN_ONLY
    if path.startswith("/api/config/"):
        return READ_ROLES if method == "GET" else COMMAND_ROLES
    # 拆 case：map_config 寫入（POST）是 operator 日常操作（畫 zone / route / 拖事件位置），
    # 開放給 WRITE_ROLES；但 GET（看地圖）必須含 observer，否則觀察員登入後 _loadMapConfig
    # 吃 403、地圖整片載不出。故分 method：GET → READ_ROLES、寫入 → WRITE_ROLES。
    # upload-image 屬系統設定（更換底圖），維持 COMMAND_ROLES。
    # 源於 issue #24 dogfood（operator POST 被 403 silent fail）+ observer 看不到地圖（後續發現）。
    if path == "/api/map_config":
        return READ_ROLES if method == "GET" else WRITE_ROLES
    # 事件分類 taxonomy（P1-10d 地基，#60/#66）：GET 給 READ_ROLES（前端渲染事件需要）；
    # 編輯（POST）限 sysadmin（admin 編輯器 #66 決策）。
    if path == "/api/event_taxonomy":
        return READ_ROLES if method == "GET" else SYSADMIN_ONLY
    if path == "/api/map/upload-image":
        return COMMAND_ROLES
    if path.startswith("/api/ai/recommendations/"):
        return COMMAND_ROLES
    if path.startswith("/api/ai/"):
        return WRITE_ROLES if method == "POST" else READ_ROLES
    if path.startswith("/api/sync/") and method != "GET":
        return COMMAND_ROLES
    if path.startswith("/api/tak/"):
        return WRITE_ROLES if method == "POST" else READ_ROLES
    if method in {"GET", "HEAD", "OPTIONS"}:
        return READ_ROLES
    return WRITE_ROLES
