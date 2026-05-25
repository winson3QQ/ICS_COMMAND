"""
tests/unit/test_ingest_endpoint_allowlist.py — Ingest 端點 HMAC Depends 白名單（AC-14）

確保所有機對機 ingest 端點均掛載 Depends(verify_hmac)。
此測試為靜態斷言（不啟動 server），CI 自動執行。

若未來新增 ingest 端點，需同步更新 PROTECTED_INGEST 並掛上 verify_hmac。
"""

import sys
from pathlib import Path

# 讓 import 能找到 src 模組
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))


def test_all_ingest_endpoints_require_hmac():
    """AC-14：PROTECTED_INGEST 所有端點均掛 Depends(verify_hmac)。"""
    from fastapi.routing import APIRoute
    from middleware.trusted_ingest import verify_hmac
    from main import app

    # 已知需要 HMAC 保護的端點白名單（新增 ingest endpoint 時一起更新此清單）
    PROTECTED_INGEST = {
        ("POST", "/api/snapshots"),
        ("POST", "/api/sync/push"),
        ("POST", "/api/pi-push/{unit_id}"),   # Option-A：主推送路徑補 HMAC（TI-01 scope 擴充）
    }

    # 建立 route map：(method, path) → route
    route_map: dict[tuple[str, str], APIRoute] = {}
    for route in app.routes:
        if isinstance(route, APIRoute):
            for method in (route.methods or []):
                route_map[(method.upper(), route.path)] = route

    for method, path in PROTECTED_INGEST:
        route = route_map.get((method, path))
        assert route is not None, (
            f"Route {method} {path} 未在 app 中找到。"
            f"請確認 router prefix 正確，或從 PROTECTED_INGEST 移除已廢棄端點。"
        )
        # 收集 route.dependencies 的依賴函式集合
        dep_fns = {d.dependency for d in (route.dependencies or [])}
        assert verify_hmac in dep_fns, (
            f"{method} {path} 未掛 Depends(verify_hmac)。"
            f"請在 router decorator 加上 dependencies=[Depends(verify_hmac)]，"
            f"或將此端點從 PROTECTED_INGEST 移除並補充說明。"
        )
