# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""#296 RBAC 端到端強制：低權角色打高權端點 → middleware 回 403。

與 test_rbac_route_matrix（鎖 allowed_roles_for 分類表）互補：本檔證明 middleware **實際
執行**該分類（不只表正確）。利用「角色檢查在 handler/body 驗證之前」（middleware.py），
故不需備合法 body —— 被擋的會在 Pydantic 之前就回 403。

fixtures：auth(=sysadmin) 見根 conftest；operator_auth/observer_auth/commander_auth 見
tests/api/conftest.py。
"""

import pytest


@pytest.fixture
def roles(auth, observer_auth, operator_auth, commander_auth):
    return {
        "sysadmin": auth,
        "observer": observer_auth,
        "operator": operator_auth,
        "commander": commander_auth,
    }


# (角色, method, path)：該角色權限不足 → 預期 403。涵蓋各 tier 的代表端點。
_DENY = [
    # WRITE-tier：observer（唯讀）打寫入端點
    ("observer", "POST", "/api/events"),
    ("observer", "POST", "/api/decisions/x/decide"),
    ("observer", "POST", "/api/cop/entities"),
    ("observer", "POST", "/api/map_config"),
    ("observer", "PATCH", "/api/events/x/status"),
    # COMMAND-tier：operator 打指揮層端點
    ("operator", "POST", "/api/exercises"),
    ("operator", "POST", "/api/config/k"),
    ("operator", "POST", "/api/tak/events"),
    ("operator", "GET", "/api/admin/accounts"),
    ("operator", "POST", "/api/ttx/scenarios/s/load"),
    ("operator", "POST", "/api/ai/recommendations/r/outcome"),
    # SYSADMIN-only：commander 打 sysadmin 專屬端點
    ("commander", "POST", "/api/admin/reset-db"),
    ("commander", "DELETE", "/api/exercises/1"),
    ("commander", "POST", "/api/tak/connection"),
    ("commander", "POST", "/api/event_taxonomy"),
    ("commander", "GET", "/api/admin/audit-log"),
    # #393：通用 config 端點收成 sysadmin-only（原 GET=observer 可讀 / POST=commander 可寫任意 key）
    ("observer", "GET", "/api/config/k"),
    ("commander", "GET", "/api/config/k"),
    ("commander", "POST", "/api/config/k"),
]


@pytest.mark.parametrize("role,method,path", _DENY)
def test_underprivileged_role_gets_403(client, roles, role, method, path):
    r = client.request(method, path, headers=roles[role])
    assert r.status_code == 403, f"{role} {method} {path} → {r.status_code}（預期 403 role denied）"


# (角色, method, path)：該角色有權 → 不該被 403 擋（200 或其他非授權錯誤皆可）。
_ALLOW = [
    ("observer", "GET", "/api/events"),
    ("observer", "GET", "/api/cop/entities"),
    ("observer", "GET", "/api/chat"),
    ("operator", "GET", "/api/map_config"),
    ("operator", "POST", "/api/cop/entities"),  # operator 可寫 COP（WRITE）
    ("sysadmin", "GET", "/api/config/k"),  # #393：sysadmin 仍可讀寫 config（端點留著、只收權限）
]


@pytest.mark.parametrize("role,method,path", _ALLOW)
def test_authorized_role_not_forbidden(client, roles, role, method, path):
    r = client.request(method, path, headers=roles[role])
    assert r.status_code != 403, f"{role} {method} {path} 被誤擋 403（應有權）"


def test_missing_token_401(client):
    assert client.get("/api/events").status_code == 401
