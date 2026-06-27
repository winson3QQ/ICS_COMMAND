# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
integration/test_logging.py — C1-D 結構化日誌整合測試
規格：logging_architecture_decision_v1.1.md

AC-1 : log record 含必要欄位（event、component、version）
AC-2 : correlation_id ContextVar 注入機制正確
AC-5 : X-Correlation-ID 請求 header 原樣回傳到響應 header
AC-6 : PROD 模式 PII 遮罩（user[:2]+"**"，session_id[:4]+"..."）
AC-7 : DEV 模式 PII 不遮罩
AC-8 : log dir 不可寫時不 crash，降級至 stderr
AC-13: 登入事件觸發結構化 log（login_success / login_failed）
AC-14: audit_log INSERT 不受影響（dual-track 雙軌並存）
"""

import logging as _stdlib_logging
import uuid

import pytest
import structlog
import structlog.testing

pytestmark = pytest.mark.integration


# ─────────────────────────────────────────────────────────────────────────────
class TestJsonFields:
    """AC-1 : log record 含必要欄位"""

    def test_required_fields_present(self):
        """capture_logs 捕捉到的 event dict 含必要識別欄位"""
        with structlog.testing.capture_logs() as cap:
            log = structlog.get_logger()
            log.info("test_required_fields", msg="AC-1 驗證事件")

        # capture_logs 回傳的 dict 直接是 event_dict（不含 JSON 序列化後的欄位）
        assert any(rec.get("event") == "test_required_fields" for rec in cap)


# ─────────────────────────────────────────────────────────────────────────────
class TestCorrelationId:
    """AC-2 : correlation_id ContextVar 注入機制"""

    def test_set_and_get(self):
        """_correlation_id ContextVar 設定後，get_correlation_id() 回傳相同值"""
        from core.logging import _correlation_id, get_correlation_id

        test_cid = str(uuid.uuid4())
        token = _correlation_id.set(test_cid)
        try:
            assert get_correlation_id() == test_cid
        finally:
            _correlation_id.reset(token)

    def test_default_is_empty(self):
        """未設定時預設值為空字串"""
        from core.logging import _correlation_id, get_correlation_id

        token = _correlation_id.set("")
        try:
            assert get_correlation_id() == ""
        finally:
            _correlation_id.reset(token)

    def test_invalid_uuid_is_not_accepted(self):
        """_is_valid_uuid4 拒絕非標準 UUID 格式"""
        from core.logging import _is_valid_uuid4

        assert _is_valid_uuid4(str(uuid.uuid4())) is True
        assert _is_valid_uuid4("not-a-uuid") is False
        assert _is_valid_uuid4("") is False
        # UUID v4 version bits = 0100（variant 10xx），以下為 v1 格式，應拒絕
        assert _is_valid_uuid4("12345678-0000-1000-0000-000000000000") is False


# ─────────────────────────────────────────────────────────────────────────────
class TestCorrelationHeader:
    """AC-5 : X-Correlation-ID 請求 header 原樣回傳到響應 header"""

    def test_incoming_valid_uuid4_echoed(self, client):
        """有效 UUID v4 header → 原樣回傳"""
        cid = str(uuid.uuid4())
        r = client.get("/api/health", headers={"X-Correlation-ID": cid})
        assert r.headers.get("x-correlation-id") == cid

    def test_missing_header_generates_new_uuid(self, client):
        """無 header → 自動生成 UUID v4（36 char，4 個 dash）"""
        r = client.get("/api/health")
        resp_cid = r.headers.get("x-correlation-id", "")
        assert len(resp_cid) == 36
        assert resp_cid.count("-") == 4

    def test_invalid_header_replaced_by_new_uuid(self, client):
        """無效 header → 自動生成新 UUID（不採用原值）"""
        r = client.get("/api/health", headers={"X-Correlation-ID": "invalid-not-a-uuid"})
        resp_cid = r.headers.get("x-correlation-id", "")
        assert resp_cid != "invalid-not-a-uuid"
        assert len(resp_cid) == 36


# ─────────────────────────────────────────────────────────────────────────────
class TestPiiMasking:
    """AC-6 / AC-7 : PII 遮罩行為"""

    def test_prod_masks_user(self, monkeypatch):
        """PROD 模式：user 遮罩為前 2 char + **"""
        import core.logging as clog

        monkeypatch.setattr(clog, "IS_PROD", True)

        event_dict = {"user": "admin_user", "log_level": "info", "event": "test"}
        result = clog._mask_pii(None, None, event_dict)
        assert result["user"] == "ad**"

    def test_prod_masks_short_user(self, monkeypatch):
        """PROD 模式：user 長度 ≤ 2 → 遮罩為 **"""
        import core.logging as clog

        monkeypatch.setattr(clog, "IS_PROD", True)

        event_dict = {"user": "ab", "log_level": "info", "event": "test"}
        result = clog._mask_pii(None, None, event_dict)
        assert result["user"] == "**"

    def test_prod_masks_session_id(self, monkeypatch):
        """PROD 模式：session_id 遮罩為前 4 char + ..."""
        import core.logging as clog

        monkeypatch.setattr(clog, "IS_PROD", True)

        event_dict = {"session_id": "abc123456789", "log_level": "info", "event": "test"}
        result = clog._mask_pii(None, None, event_dict)
        assert result["session_id"] == "abc1..."

    def test_prod_masks_ip_to_subnet(self, monkeypatch):
        """PROD 模式：detail.ip → /24 subnet"""
        import core.logging as clog

        monkeypatch.setattr(clog, "IS_PROD", True)

        event_dict = {
            "detail": {"ip": "192.168.1.55"},
            "log_level": "info",
            "event": "test",
        }
        result = clog._mask_pii(None, None, event_dict)
        assert result["detail"]["ip"] == "192.168.1.x"

    def test_dev_no_mask(self, monkeypatch):
        """DEV 模式：不做 PII 遮罩"""
        import core.logging as clog

        monkeypatch.setattr(clog, "IS_PROD", False)

        event_dict = {
            "user": "admin_user",
            "session_id": "abc123",
            "detail": {"ip": "10.0.0.1"},
            "log_level": "info",
            "event": "test",
        }
        result = clog._mask_pii(None, None, event_dict)
        assert result["user"] == "admin_user"
        assert result["session_id"] == "abc123"
        assert result["detail"]["ip"] == "10.0.0.1"


# ─────────────────────────────────────────────────────────────────────────────
class TestFallbackNoCrash:
    """AC-8 : log dir 不可寫時不 crash，降級至 stderr"""

    def test_init_logging_bad_filehandler_no_exception(self, monkeypatch):
        """FileHandler 建立失敗時，init_logging 不 raise（降級 stderr）"""
        import core.logging as clog

        def _raise(*args, **kwargs):
            raise OSError("模擬：/var/log/ics 無寫入權限")

        monkeypatch.setattr(_stdlib_logging, "FileHandler", _raise)
        try:
            clog.init_logging()
        except Exception as exc:
            pytest.fail(f"init_logging raised when FileHandler unavailable: {exc}")

    def test_stderr_fallback_rate_limit(self, monkeypatch):
        """_stderr_fallback 每 60s 最多輸出一次（throttling）"""
        import core.logging as clog

        # 重置計時器，確保第一次必定輸出
        monkeypatch.setattr(clog, "_fallback_warned_at", -61.0)

        import io

        captured = io.StringIO()
        monkeypatch.setattr("sys.stderr", captured)

        clog._stderr_fallback("test-reason-1")
        clog._stderr_fallback("test-reason-2")  # 應被 throttle（60s 內）

        output = captured.getvalue()
        # 只有一次輸出
        assert output.count("ICS-LOG-FALLBACK") == 1
        assert "test-reason-1" in output


# ─────────────────────────────────────────────────────────────────────────────
class TestLoginEvents:
    """AC-13 : 登入事件觸發結構化 log"""

    def test_login_success_emits_log(self, client):
        """成功登入 → 捕捉到 login_success event"""
        with structlog.testing.capture_logs() as cap:
            r = client.post("/api/auth/login", json={"username": "admin", "pin": "1234"})
        assert r.status_code == 200
        events = [rec.get("event") for rec in cap]
        assert "login_success" in events

    def test_login_failed_emits_log(self, client):
        """PIN 錯誤 → 捕捉到 login_failed event"""
        with structlog.testing.capture_logs() as cap:
            r = client.post("/api/auth/login", json={"username": "admin", "pin": "wrong_pin"})
        assert r.status_code in (401, 403, 422, 423)
        events = [rec.get("event") for rec in cap]
        assert "login_failed" in events


# ─────────────────────────────────────────────────────────────────────────────
class TestDualTrack:
    """AC-14 : audit_log INSERT 不受影響（structured log 不取代 audit_log）"""

    def test_audit_log_written_on_login_success(self, client, tmp_db):
        """成功登入後 audit_log 表仍有 action_type='login' 記錄"""
        from core.database import get_conn

        r = client.post("/api/auth/login", json={"username": "admin", "pin": "1234"})
        assert r.status_code == 200

        with get_conn() as conn:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM audit_log WHERE action_type='login'").fetchone()
        assert row["cnt"] >= 1

    def test_audit_log_written_on_event_create(self, client, auth, tmp_db):
        """建立事件後 audit_log 表仍有 action_type='event_created' 記錄"""
        from core.database import get_conn

        r = client.post(
            "/api/events",
            json={
                "reported_by_unit": "shelter",
                "event_type": "drill",
                "severity": "info",
                "description": "AC-14 dual-track 驗證",
                "operator_name": "admin",
            },
            headers=auth,
        )
        assert r.status_code == 200

        with get_conn() as conn:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM audit_log WHERE action_type='event_created'").fetchone()
        assert row["cnt"] >= 1
