# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
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

# #343 紅藍隔離：faction 可見性（與上面 RBAC 權限軸**正交**的新軸）。
# sysadmin（白隊/導調）全見 → None sentinel；commander/operator/observer（藍軍）只見 blue+neutral。
# 注意：commander 雖屬高權 COMMAND_ROLES，faction 上仍只見藍方（演習身分 ≠ 系統權限）。
# 僅 source='tak' 受此過濾（#146 所有權：manual/command 自建恆對藍方可見）；enforcement 在
# routers/cop.py（REST）+ services/realtime_hub.py（WS）。
BLUE_VISIBLE_FACTIONS = frozenset({"blue", "neutral"})


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


def visible_factions_for_session(session: dict) -> frozenset[str] | None:
    """#343：此 session 可見的 faction 集合；None = 全見（sysadmin/白隊）。

    供 routers/cop.py（REST 過濾）與 realtime_hub（WS 過濾）共用同一映射，避免兩處漂移。
    """
    role = role_zh_to_en(session.get("role"), session.get("role_detail"))
    if role == ROLE_SYSADMIN:
        return None
    return BLUE_VISIBLE_FACTIONS


# #370：歷史上靠「寬鬆預設」(GET→READ / else→WRITE) 才通的現役路由 → 改 default-deny 前於此
# 明確登記，照其凍結分類（golden, test_rbac_route_matrix），行為零變更。個別端點是否該更嚴屬
# 另一條 hardening 線（#370 follow-up）。註：health/status/version/ingress/pi-push/csp-report/
# snapshots-POST 於 middleware 更早豁免，分類為 dead value，但 golden 仍鎖之 → 一併登記保持穩定。
# /api/sync 非-GET 已由 allowed_roles_for 上方 COMMAND case 先攔，故 sync 僅 GET→READ 生效。
_LEGACY_DEFAULT_PREFIXES = (
    "/api/cop",
    "/api/events",
    "/api/decisions",
    "/api/manual_records",
    "/api/snapshots",
    "/api/security",
    "/api/sync",
    "/api/ingress",
    "/api/pi-push",
    "/api/pi-data",
)
_LEGACY_DEFAULT_EXACT = frozenset(
    {
        "/api/dashboard",
        "/api/staff",
        "/api/audit_log",
        "/api/facilities",
        "/api/health",
        "/api/status",
        "/api/version",
    }
)


def allowed_roles_for(method: str, path: str) -> frozenset[str] | None:
    """回傳某 (method, path) 允許的角色閘。契約（#370 起 default-deny）：
      - None          → public / 豁免（middleware 跳過角色檢查）
      - 非空 frozenset → 僅該些角色可過
      - 空 frozenset   → default-deny（未登記路徑，fail closed，403）

    **新增端點必須在下方明確分類**；漏配會落到空集合 deny，並被
    test_no_route_falls_through_to_deny_fallback 在 CI 擋下。
    """
    method = method.upper()
    if path.startswith("/api/auth/") or path == "/api/session/status":
        return None
    if path == "/api/admin/accounts" or path.startswith("/api/admin/accounts/"):
        return ACCOUNT_MANAGER_ROLES
    if path.startswith("/api/admin/"):
        return SYSADMIN_ONLY
    # #393：通用 config 端點 GET/POST /api/config/{key} 收成 **SYSADMIN_ONLY**（縱深）。原 GET→READ_ROLES
    # （observer 可讀任意 key）/ POST→COMMAND_ROLES（commander 可寫任意 key、無 allow-list）= 越權面：未來
    # 任何寫進 config 的機敏值自動對 observer 可見、commander 可改任意 runtime config。前端不直用此端點
    # （具體設定如 tak toggle / retention / map_config 各有專屬端點），故收 sysadmin-only 不破壞既有流程。
    if path.startswith("/api/config/"):
        return SYSADMIN_ONLY
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
    # #419 SBOM 下載：READ_ROLES（observer 以上，需登入）。列確切相依版本＝偵察面，
    # 故不入 config.py 未認證 allowlist（不同於 /api/version）；經 middleware 閘控。
    if path == "/api/sbom":
        return READ_ROLES
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
    # #287 H2：TTX inject 編排（建 inject / push 事件·決策·snapshot 進場 / 載情境）＝演習指揮層
    # 活動；inject 為「待推送的演習腳本」，參演的 operator/observer 不應預 see（會破壞演習）。
    # 原本無此 case → 落預設 GET=READ/POST=WRITE，使 observer 讀任意場 inject、operator 注入
    # 任意場（含已歸檔）→ broken access control + 跨場越權。比照 /api/exercises/ 鎖 COMMAND_ROLES：
    # COMMAND 本就可跨場編排（doctrine），故 exercise_id 吃 path 參數無需另做 resolve_scope。
    if path.startswith("/api/ttx/"):
        return COMMAND_ROLES
    if path.startswith("/api/sync/") and method != "GET":
        return COMMAND_ROLES
    if path.startswith("/api/tak/share/"):
        # P2-30 part 3（#180）：分享既有 COP 標記到 TAK 放寬到 WRITE_ROLES —— 一線回報敵情者
        # （operator）放置的感知標記可直接推上 TAK（符合 operator = 前線感知職責）。與 #146 收緊的
        # POST /api/tak/events 不同：share 只推「已存在的 cop_entity」（get→entity_to_cot），不接受
        # 任意 client CoT、不繞過 cop 來源守門；且 share endpoint audit-first（每次強制稽核分享意圖）。
        return WRITE_ROLES if method == "POST" else READ_ROLES
    if path == "/api/tak/chat":
        # #463 公測回報：出向 GeoChat 放寬到 WRITE_ROLES —— operator 是一線操作訊息者，
        # 通聯雙向屬其職責（推翻 #216「對外發話＝指揮層動作」的原始 rationale）。比照 #180
        # share 窄洞模式：端點 audit-first（TAK_CHAT_SEND）+ 發話者身分 server 端決定
        # （不信 client 宣告）→ 問責不減；observer 仍唯讀（GET /api/chat 看、不能發）。
        # operator 比照 commander：出向 DM 不在 ICS 層做 faction 檢查（權威邊界＝TAK #344
        # group 隔離；callsign 非可靠 faction 鍵——見 routers/tak.py send_geochat 註解）。
        return WRITE_ROLES if method == "POST" else READ_ROLES
    # ⚠ /api/tak/* POST 放寬清單（WRITE_ROLES 窄洞）：share（#180）、chat（#463）——僅上列兩
    # path；其餘 POST（events 注入、downlink、resync、admin cert/role）維持 COMMAND_ROLES。
    # 再加洞時更新此清單，勿在各洞內寫「僅此 path 放寬」的排他宣稱（會互相打臉）。
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
        # 鎖當前 active 場，歷史場 ?exercise_id 限 COMMAND_ROLES）。POST 未實作（出向 compose
        # 實作在 /api/tak/chat；#463 後為 WRITE_ROLES——若日後在此實作應對齊之，勿再引 #216
        # 已推翻的「指揮對外發話」rationale）；此處非-GET 維持 COMMAND 僅為 fail-closed 預留。
        return READ_ROLES if method == "GET" else COMMAND_ROLES
    # #370：歷史靠寬鬆預設才通的現役路由（模組層 _LEGACY_DEFAULT_*）照凍結分類回 READ/WRITE
    # ——行為零變更。新端點必須在上方明確分類，不再有寬鬆兜底（落下方 default-deny）。
    if path in _LEGACY_DEFAULT_EXACT or any(path == p or path.startswith(p + "/") for p in _LEGACY_DEFAULT_PREFIXES):
        return READ_ROLES if method in {"GET", "HEAD", "OPTIONS"} else WRITE_ROLES
    # #370 default-DENY：未登記路徑 fail closed。空 frozenset() 即正確 deny——middleware
    # (auth/middleware.py:63-64) 把 `allowed is not None` 當「有規則」、is_role_allowed(role,
    # frozenset()) 恆 False → 403。None 不重用（None=public/豁免）。空集合亦為回歸測試
    # (test_no_route_falls_through_to_deny_fallback) 偵測「落兜底」的訊號。
    return frozenset()
