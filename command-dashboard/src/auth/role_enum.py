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
    # P1-14 HIGH-3：演練後分析 / ML 匯出（吃 path 參數 exercise_id）限指揮層，
    # 否則 observer 帶任意場號即可拉任意歷史場的後分析 / 訓練資料（跨場洩漏）。
    if path.startswith("/api/ai/report/") or path.startswith("/api/ai/export/"):
        return COMMAND_ROLES
    if path.startswith("/api/ai/"):
        return WRITE_ROLES if method == "POST" else READ_ROLES
    # P1-14 HIGH-4：exercises 個別場 detail / AAR / activate / archive / status 限指揮層
    # （擋 observer 帶任意 exercise_id 撈歷史場 AAR / metadata）。list（GET ""）保留 READ_ROLES
    # 供前端 header chip / 選擇器顯示場次；create（POST ""）限指揮層。
    if path == "/api/exercises":
        return READ_ROLES if method == "GET" else COMMAND_ROLES
    if path.startswith("/api/exercises/"):
        # 刪除（級聯清資料）破壞性最高 → 限 sysadmin；其餘（detail/aar/activate/archive/status）指揮層。
        return SYSADMIN_ONLY if method == "DELETE" else COMMAND_ROLES
    if path.startswith("/api/sync/") and method != "GET":
        return COMMAND_ROLES
    if path.startswith("/api/tak/share/"):
        # P2-30 part 3（#180）：分享既有 COP 標記到 TAK 放寬到 WRITE_ROLES —— 一線回報敵情者
        # （operator）放置的感知標記可直接推上 TAK（符合 operator = 前線感知職責）。與 #146 收緊的
        # POST /api/tak/events 不同：share 只推「已存在的 cop_entity」（get→entity_to_cot），不接受
        # 任意 client CoT、不繞過 cop 來源守門；且 share endpoint audit-first（每次強制稽核分享意圖）。
        # **窄洞**：僅此 path 放寬，其餘 /api/tak/* POST（events 注入、admin cert/role）維持 COMMAND_ROLES。
        return WRITE_ROLES if method == "POST" else READ_ROLES
    if path == "/api/tak/connection":
        # P2-24（#164）：runtime 開關 TAK 連線＝關掉整 COP 態勢全斷、blast radius 最大 →
        # 比照 /api/exercises/ DELETE 鎖 SYSADMIN_ONLY（commander 不可，避免誤觸把全 COP 弄瞎）。
        return SYSADMIN_ONLY
    if path.startswith("/api/tak/"):
        # #146：REST ingest（POST /api/tak/events）收緊到 COMMAND_ROLES —— 防 operator 經此
        # 端點注入/竄改 tak 物件、繞過 cop PUT/DELETE 的來源守門。真實 TAK 資料走 :8089 串流
        # （背景 task 直呼 ingest，不經 HTTP RBAC），故收緊無生產影響。機器對機器 auth = TAK-A。
        return COMMAND_ROLES if method == "POST" else READ_ROLES
    if path == "/api/chat":
        # #213 b1：通聯唯讀（GET）→ READ_ROLES——observer 做 audit/AAR 觀察需看當前場通聯，
        # 屬與 events/COP 同層級的情境資料；跨場 PII 由 resolve_scope 守門（observer/operator
        # 鎖當前 active 場，歷史場 ?exercise_id 限 COMMAND_ROLES）。出向 compose（POST，#216）
        # ＝指揮對外發話、audit-first → 預留 COMMAND_ROLES（未實作；明示避免落非-GET 的 WRITE_ROLES 預設）。
        return READ_ROLES if method == "GET" else COMMAND_ROLES
    if method in {"GET", "HEAD", "OPTIONS"}:
        return READ_ROLES
    return WRITE_ROLES
