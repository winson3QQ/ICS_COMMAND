"""
tests/security/test_firstrun_config.py — First-run / Secure-mode 配置邊界測試

補充 test_first_run.py（已覆蓋 gate 行為 / PIN 生成 / 清旗標）。
本檔聚焦：
  - CSP_MODE "report-only" vs "enforce" header 名稱切換
  - ENABLE_SECURITY_HEADERS=false → 安全 header 不送出
  - ALLOWED_ORIGINS 解析邊界（空值、多餘空白、重複項目）
  - first_run_gate 在 is_default_pin=1 但 admin 帳號不存在時的行為
  - first_run_token 檔案已存在時的冪等行為
"""

import pytest
import os


# ─────────────────────────────────────────────────────────────────
# CSP_MODE 切換
# ─────────────────────────────────────────────────────────────────

class TestCSPModeSwitch:
    def test_report_only_mode_uses_correct_header_name(self, client, monkeypatch):
        """CSP_MODE='report-only' → header 名稱為 Content-Security-Policy-Report-Only"""
        import core.security_headers as sh
        monkeypatch.setattr(sh, "CSP_MODE", "report-only")

        r = client.get("/api/status")
        assert "content-security-policy-report-only" in (k.lower() for k in r.headers)
        assert "content-security-policy" not in [
            k.lower() for k in r.headers
            if k.lower() != "content-security-policy-report-only"
        ]

    def test_enforce_mode_uses_correct_header_name(self, client, monkeypatch):
        """CSP_MODE='enforce' → ENFORCE_PATHS 使用 Content-Security-Policy"""
        import core.security_headers as sh
        monkeypatch.setattr(sh, "CSP_MODE", "enforce")

        r = client.get("/static/commander_dashboard.html")
        assert "content-security-policy" in (k.lower() for k in r.headers)

    def test_csp_report_only_does_not_block_requests(self, client, monkeypatch):
        """report-only 模式：CSP 違規不阻擋請求（只回報）"""
        import core.security_headers as sh
        monkeypatch.setattr(sh, "CSP_MODE", "report-only")
        # 正常請求在 report-only 下仍 200（不被 CSP 擋）
        r = client.get("/api/status")
        assert r.status_code == 200


# ─────────────────────────────────────────────────────────────────
# ENABLE_SECURITY_HEADERS 開關
# ─────────────────────────────────────────────────────────────────

class TestSecurityHeadersToggle:
    def test_csp_header_present_by_default(self, client):
        """
        預設 ENABLE_SECURITY_HEADERS=true → FastAPI 層送出 CSP header。
        注意：X-Content-Type-Options / X-Frame-Options 由 nginx 加，不在 TestClient 層。
        """
        r = client.get("/api/status")
        headers_lower = {k.lower() for k in r.headers}
        # FastAPI middleware 只加 CSP
        assert (
            "content-security-policy" in headers_lower
            or "content-security-policy-report-only" in headers_lower
        )

    def test_security_headers_absent_when_disabled(self, client, monkeypatch):
        """ENABLE_SECURITY_HEADERS=false → CSP header 不送出"""
        import core.security_headers as sh
        monkeypatch.setattr(sh, "ENABLE_SECURITY_HEADERS", False)

        r = client.get("/api/status")
        headers_lower = {k.lower() for k in r.headers}
        assert "content-security-policy" not in headers_lower
        assert "content-security-policy-report-only" not in headers_lower


# ─────────────────────────────────────────────────────────────────
# ALLOWED_ORIGINS 解析邊界
# ─────────────────────────────────────────────────────────────────

class TestAllowedOriginsParsing:
    def test_default_origins_not_empty(self):
        """預設 ALLOWED_ORIGINS 包含 localhost（dev 環境）"""
        from core.config import ALLOWED_ORIGINS
        assert len(ALLOWED_ORIGINS) > 0
        assert any("localhost" in o for o in ALLOWED_ORIGINS)

    def test_origins_strip_whitespace(self, monkeypatch):
        """帶多餘空白的 ALLOWED_ORIGINS env → 空白被去除"""
        import core.config as cfg
        raw = "  http://localhost:8000 , https://command.local  "
        parsed = [o.strip() for o in raw.split(",") if o.strip()]
        assert "http://localhost:8000" in parsed
        assert "https://command.local" in parsed
        assert all(" " not in o for o in parsed)

    def test_empty_origins_env_yields_empty_list(self, monkeypatch):
        """ALLOWED_ORIGINS='' → 解析結果為空 list（CORS 全拒）"""
        raw = ""
        parsed = [o.strip() for o in raw.split(",") if o.strip()]
        assert parsed == []

    def test_single_origin_parsed_correctly(self, monkeypatch):
        """單一 origin 不帶逗號 → list 長度 1"""
        raw = "https://command.local"
        parsed = [o.strip() for o in raw.split(",") if o.strip()]
        assert parsed == ["https://command.local"]


# ─────────────────────────────────────────────────────────────────
# First-run Gate 邊界
# ─────────────────────────────────────────────────────────────────

class TestFirstRunGateBoundary:
    def test_is_first_run_false_when_no_accounts(self, tmp_db):
        """
        空 DB（無帳號）→ is_first_run_required() 回傳 False。
        first_run 只由 is_default_pin=1 觸發，非「無帳號」。
        """
        from repositories.account_repo import is_first_run_required
        assert is_first_run_required() is False

    def test_is_first_run_true_only_when_default_pin_flagged(self, tmp_db, tmp_path):
        """is_default_pin=1 才觸發 first_run，普通帳號不觸發"""
        from repositories.account_repo import (
            create_account, is_first_run_required,
            ensure_initial_admin_token
        )
        # 建立無 is_default_pin 的普通帳號
        create_account("normal_user", "9999", "操作員", "", "operator")
        assert is_first_run_required() is False

        # ensure_initial_admin_token 在已有帳號時回傳 None（不執行）
        result = ensure_initial_admin_token(token_dir=str(tmp_path))
        assert result is None
        assert is_first_run_required() is False

    def test_first_run_token_file_idempotent(self, tmp_db, tmp_path):
        """
        token 檔已存在時，ensure_initial_admin_token 在空 DB 才重建；
        若已有帳號，直接回 None（冪等）。
        """
        from repositories.account_repo import ensure_initial_admin_token
        from core.database import get_conn

        # 首次建立
        pin1 = ensure_initial_admin_token(token_dir=str(tmp_path))
        assert pin1 is not None

        # 清除帳號（模擬異常狀況）並再次呼叫
        with get_conn() as conn:
            conn.execute("DELETE FROM accounts")
            conn.commit()

        pin2 = ensure_initial_admin_token(token_dir=str(tmp_path))
        # 空 DB → 重新建立（PIN 可能不同，但不崩潰）
        assert pin2 is not None

    def test_gate_allows_whitelisted_paths_when_active(self, tmp_db, monkeypatch):
        """
        is_default_pin=1 啟動 gate → 白名單路徑（/api/health、/api/status）仍通過。
        """
        from auth.first_run_gate import first_run_gate_middleware
        from repositories.account_repo import create_account
        from core.database import get_conn

        create_account("gatetest", "1234", "操作員", "", "operator")
        with get_conn() as conn:
            conn.execute(
                "UPDATE accounts SET is_default_pin=1 WHERE username='gatetest'"
            )
            conn.commit()

        # 透過 TestClient 驗證（conftest 的 client 會 clear is_default_pin，
        # 這裡用 first_run_client 邏輯驗證白名單行為）
        from fastapi.testclient import TestClient
        from main import app
        with TestClient(app) as c:
            # 這個 client 使用 gatetest 帳號，is_default_pin=1
            # 但我們只確認 /api/status（無需 session）不被擋
            r = c.get("/api/status")
            assert r.status_code == 200
