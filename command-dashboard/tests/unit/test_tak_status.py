"""
tests/unit/test_tak_status.py — P2-23（#163）：TAK 連線健康狀態追蹤 + /api/tak/status 連線健康欄位。

鎖住的不變式：
- 模組級狀態預設斷線、無 CoT
- _set_tak_connected：值翻轉才記 last_change_at（同值再 set 不動）
- _mark_tak_cot：戳 last_cot_at
- _consume_cot：收真實 CoT 才標 last_cot_at；控制事件（t-x-takp*）不標
- GET /api/tak/status 端點含 connected / last_cot_at / server 算的 last_cot_age_s
"""

import asyncio

from routers import tak as tak_router
from services import tak_service


def _cot(uid: str, type_: str) -> bytes:
    return (
        f'<event version="2.0" uid="{uid}" type="{type_}" how="m-g" '
        'time="2026-06-05T04:00:00Z" start="2026-06-05T04:00:00Z" stale="2099-01-01T00:00:00Z">'
        '<point lat="24.0" lon="120.0" hae="0" ce="0" le="0"/></event>'
    ).encode()


def test_status_default_disconnected():
    tak_service.reset_tak_status()
    assert tak_service.get_tak_status() == {"connected": False, "last_cot_at": None, "last_change_at": None}


def test_set_connected_toggles_and_stamps_change_only_on_flip():
    tak_service.reset_tak_status()
    tak_service._set_tak_connected(True)
    s = tak_service.get_tak_status()
    assert s["connected"] is True and s["last_change_at"] is not None and s["last_cot_at"] is None
    first = s["last_change_at"]
    tak_service._set_tak_connected(True)  # 同值再 set → 不更新 last_change_at
    assert tak_service.get_tak_status()["last_change_at"] == first


def test_mark_cot_stamps_last_cot():
    tak_service.reset_tak_status()
    assert tak_service.get_tak_status()["last_cot_at"] is None
    tak_service._mark_tak_cot()
    assert tak_service.get_tak_status()["last_cot_at"] is not None


def test_get_status_returns_copy_not_internal_ref():
    tak_service.reset_tak_status()
    snap = tak_service.get_tak_status()
    snap["connected"] = True  # 改拷貝不得污染內部
    assert tak_service.get_tak_status()["connected"] is False


def test_consume_cot_marks_last_cot_real_event_only():
    tak_service.reset_tak_status()
    # 控制事件（t-x-takp-v）→ 不標 last_cot_at（連線協商，非態勢資料）
    asyncio.run(tak_service._consume_cot(_cot("c1", "t-x-takp-v"), lambda e: None))
    assert tak_service.get_tak_status()["last_cot_at"] is None
    # 真實 CoT → 標
    asyncio.run(tak_service._consume_cot(_cot("r1", "a-f-G"), lambda e: None))
    assert tak_service.get_tak_status()["last_cot_at"] is not None


def test_status_endpoint_includes_connection_health():
    tak_service.reset_tak_status()
    tak_service._set_tak_connected(True)
    tak_service._mark_tak_cot()
    body = tak_router.tak_status()
    assert body["connected"] is True
    assert body["last_cot_at"] is not None
    assert isinstance(body["last_cot_age_s"], int) and body["last_cot_age_s"] >= 0
    assert "enabled" in body and "protocol" in body


def test_status_endpoint_disconnected_no_cot_age_none():
    tak_service.reset_tak_status()
    body = tak_router.tak_status()
    assert body["connected"] is False
    assert body["last_cot_at"] is None
    assert body["last_cot_age_s"] is None


# ── P2-24（#164）前端尾：running / configured 唯讀診斷欄位 ──


def test_is_configured_true_when_url_and_certs_present(monkeypatch):
    from core import config
    from services import tak_runtime

    monkeypatch.setattr(config, "TAK_COT_URL", "tls://tak:8089")
    monkeypatch.setattr(config, "TAK_CLIENT_CERT", "/p/cert.pem")
    monkeypatch.setattr(config, "TAK_CLIENT_KEY", "/p/key.pem")
    assert tak_runtime.is_configured() is True


def test_is_configured_false_when_any_param_missing(monkeypatch):
    from core import config
    from services import tak_runtime

    monkeypatch.setattr(config, "TAK_COT_URL", "tls://tak:8089")
    monkeypatch.setattr(config, "TAK_CLIENT_CERT", "")  # 缺憑證
    monkeypatch.setattr(config, "TAK_CLIENT_KEY", "/p/key.pem")
    assert tak_runtime.is_configured() is False


def test_status_endpoint_includes_running_and_configured(monkeypatch):
    """燈號要靠這兩欄區分『沒 task 在跑』vs『部署層未備妥』vs『真的在重連』。"""
    tak_service.reset_tak_status()
    from core import config

    monkeypatch.setattr(config, "TAK_COT_URL", "")  # 未備妥連線參數
    body = tak_router.tak_status()
    assert body["configured"] is False
    assert isinstance(body["running"], bool)
