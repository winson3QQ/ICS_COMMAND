"""#296 RBAC 路由分類矩陣（golden snapshot）。

防 #287 類「新 router 沒在 allowed_roles_for 登記 → 落寬鬆預設 → 靜默 broken access
control」：把所有 /api/ route 的 (method, path → 角色閘) 鎖成 golden。

新增 / 改動 route 或改 allowed_roles_for → 本測試失敗 → **必須人工**：
  1. 確認新 route 的角色閘在 `auth/role_enum.allowed_roles_for` 分類正確（勿讓它落
     預設 READ/WRITE）；
  2. 確認無誤後，把下方 GOLDEN 更新成新值。
失敗訊息會列出 新增 / 消失 / 角色變動 的 route。

注意：此測試鎖的是 `allowed_roles_for` 的輸出。部分 route（HMAC ingest、health/status/
version、csp-report、auth/*）在 middleware 更早被豁免，其 allowed_roles_for 值不被諮詢
（dead value）——但仍納入 golden 以鎖住「分類函式」本身的穩定。實際豁免另由
test_ingest_endpoint_allowlist（HMAC）與 middleware AUTH_EXEMPT 守。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

# golden：ROLE method /path（由 allowed_roles_for 對 app.routes 全列舉產生，2026-06-21 凍結）
# ROLE: READ / WRITE / COMMAND / SYSADMIN / ACCT_MGR / EXEMPT
_GOLDEN_RAW = """
COMMAND  GET    /api/admin/accounts
COMMAND  POST   /api/admin/accounts
COMMAND  DELETE /api/admin/accounts/{username}
COMMAND  GET    /api/admin/accounts/{username}/certs
COMMAND  POST   /api/admin/accounts/{username}/certs
COMMAND  POST   /api/admin/accounts/{username}/certs/issue
COMMAND  DELETE /api/admin/accounts/{username}/certs/{cert_id}
COMMAND  PUT    /api/admin/accounts/{username}/display-name
COMMAND  PUT    /api/admin/accounts/{username}/pin
COMMAND  PUT    /api/admin/accounts/{username}/role
COMMAND  PUT    /api/admin/accounts/{username}/status
SYSADMIN GET    /api/admin/audit-log
SYSADMIN GET    /api/admin/backups
SYSADMIN POST   /api/admin/backups
SYSADMIN GET    /api/admin/backups/{name}/preview
SYSADMIN GET    /api/admin/backups/{name}/restore-cmd
SYSADMIN POST   /api/admin/backups/{name}/verify
SYSADMIN GET    /api/admin/pi-nodes
SYSADMIN POST   /api/admin/pi-nodes
SYSADMIN DELETE /api/admin/pi-nodes/{unit_id}
SYSADMIN POST   /api/admin/pi-nodes/{unit_id}/rekey
SYSADMIN PUT    /api/admin/pin
SYSADMIN POST   /api/admin/reset-db
SYSADMIN POST   /api/admin/reset-exercise
SYSADMIN POST   /api/admin/restore
SYSADMIN GET    /api/admin/retention
SYSADMIN POST   /api/admin/retention
SYSADMIN GET    /api/admin/schema-migrations
SYSADMIN GET    /api/admin/status
SYSADMIN POST   /api/admin/suspend-all
SYSADMIN GET    /api/admin/user-data-backups
SYSADMIN POST   /api/admin/user-data-backups
SYSADMIN GET    /api/admin/user-data-backups/{name}/download
SYSADMIN GET    /api/admin/user-data-backups/{name}/manifest
SYSADMIN POST   /api/admin/user-data-backups/{name}/restore
COMMAND  GET    /api/ai/export/{exercise_id}
WRITE    POST   /api/ai/recommend
COMMAND  POST   /api/ai/recommendations/{rec_id}/outcome
COMMAND  GET    /api/ai/report/{exercise_id}
READ     GET    /api/audit_log
EXEMPT   POST   /api/auth/change-initial-pin
EXEMPT   GET    /api/auth/heartbeat
EXEMPT   POST   /api/auth/login
EXEMPT   POST   /api/auth/logout
EXEMPT   GET    /api/auth/me
READ     GET    /api/chat
READ     GET    /api/config/{key}
COMMAND  POST   /api/config/{key}
READ     GET    /api/cop/entities
WRITE    POST   /api/cop/entities
WRITE    DELETE /api/cop/entities/{uid}
READ     GET    /api/cop/entities/{uid}
WRITE    PUT    /api/cop/entities/{uid}
READ     GET    /api/cop/squads
READ     GET    /api/dashboard
READ     GET    /api/decisions
WRITE    POST   /api/decisions
WRITE    POST   /api/decisions/{decision_id}/decide
READ     GET    /api/event_taxonomy
SYSADMIN POST   /api/event_taxonomy
READ     GET    /api/events
WRITE    POST   /api/events
WRITE    PATCH  /api/events/{event_id}
READ     GET    /api/events/{event_id}/chain
WRITE    PATCH  /api/events/{event_id}/deadline
WRITE    POST   /api/events/{event_id}/notes
WRITE    PATCH  /api/events/{event_id}/status
READ     GET    /api/exercises
COMMAND  POST   /api/exercises
SYSADMIN DELETE /api/exercises/{exercise_id}
COMMAND  GET    /api/exercises/{exercise_id}
COMMAND  GET    /api/exercises/{exercise_id}/aar
COMMAND  POST   /api/exercises/{exercise_id}/aar
COMMAND  POST   /api/exercises/{exercise_id}/activate
COMMAND  POST   /api/exercises/{exercise_id}/archive
COMMAND  POST   /api/exercises/{exercise_id}/enroll
COMMAND  GET    /api/exercises/{exercise_id}/kpis
COMMAND  PUT    /api/exercises/{exercise_id}/status
COMMAND  GET    /api/exercises/{exercise_id}/timeline
COMMAND  GET    /api/exercises/{exercise_id}/tracks
READ     GET    /api/facilities
READ     GET    /api/health
WRITE    POST   /api/ingress/pi-node/{unit_id}
READ     GET    /api/manual_records
WRITE    POST   /api/manual_records
WRITE    PATCH  /api/manual_records/{record_id}/synced
COMMAND  POST   /api/map/upload-image
READ     GET    /api/map_config
WRITE    POST   /api/map_config
READ     GET    /api/pi-data/{unit_id}/list
WRITE    POST   /api/pi-push/{unit_id}
WRITE    POST   /api/security/csp-report
EXEMPT   GET    /api/session/status
WRITE    POST   /api/snapshots
READ     GET    /api/snapshots/{node_type}
READ     GET    /api/staff
READ     GET    /api/status
READ     GET    /api/sync/log
COMMAND  POST   /api/sync/push
READ     GET    /api/sync/{sync_id}
COMMAND  POST   /api/sync/{sync_id}/resolve
SYSADMIN POST   /api/tak/connection
COMMAND  POST   /api/tak/downlink
COMMAND  POST   /api/tak/events
COMMAND  POST   /api/tak/resync
WRITE    POST   /api/tak/share/{uid}
READ     GET    /api/tak/status
COMMAND  GET    /api/ttx/exercises/{exercise_id}/injects
COMMAND  POST   /api/ttx/exercises/{exercise_id}/injects
COMMAND  POST   /api/ttx/exercises/{exercise_id}/injects/{inject_id}/push
COMMAND  GET    /api/ttx/scenarios
COMMAND  POST   /api/ttx/scenarios/{scenario_id}/load
READ     GET    /api/version
"""


def _role_label(roleset) -> str:
    from auth.role_enum import (
        ACCOUNT_MANAGER_ROLES,
        COMMAND_ROLES,
        READ_ROLES,
        SYSADMIN_ONLY,
        WRITE_ROLES,
    )

    if roleset is None:
        return "EXEMPT"
    for label, val in (
        ("READ", READ_ROLES),
        ("WRITE", WRITE_ROLES),
        ("COMMAND", COMMAND_ROLES),
        ("SYSADMIN", SYSADMIN_ONLY),
        ("ACCT_MGR", ACCOUNT_MANAGER_ROLES),
    ):
        if roleset == val:
            return label
    return "CUSTOM:" + ",".join(sorted(roleset))


def _parse_golden() -> set[tuple[str, str, str]]:
    out = set()
    for line in _GOLDEN_RAW.strip().splitlines():
        role, method, path = line.split()
        out.add((method, path, role))
    return out


def _live_matrix() -> set[tuple[str, str, str]]:
    from fastapi.routing import APIRoute

    from auth.role_enum import allowed_roles_for
    from main import app

    out = set()
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path.startswith("/api/"):
            for method in route.methods or []:
                if method in ("HEAD", "OPTIONS"):
                    continue
                out.add((method, route.path, _role_label(allowed_roles_for(method, route.path))))
    return out


def test_rbac_route_matrix_matches_golden():
    live = _live_matrix()
    golden = _parse_golden()

    added = live - golden       # 新 route / 角色變動（新值側）
    removed = golden - live     # 消失 route / 角色變動（舊值側）

    msg_parts = []
    if added:
        msg_parts.append(
            "新增或角色變動（live 有、golden 無）——請確認 allowed_roles_for 分類正確"
            "（勿落寬鬆預設）後更新 GOLDEN：\n  "
            + "\n  ".join(f"{r} {m} {p}" for m, p, r in sorted(added, key=lambda x: (x[1], x[0])))
        )
    if removed:
        msg_parts.append(
            "消失或角色變動（golden 有、live 無）：\n  "
            + "\n  ".join(f"{r} {m} {p}" for m, p, r in sorted(removed, key=lambda x: (x[1], x[0])))
        )
    assert not (added or removed), "\n\n".join(msg_parts)
