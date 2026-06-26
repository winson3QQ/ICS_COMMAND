# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
tests/api/test_backups_api.py — Admin Backup API 測試

涵蓋：
  - 5 個 endpoint 都需 sysadmin session（無 session → 401）
  - GET /api/admin/backups list 行為
  - POST /api/admin/backups 觸發備份
  - POST /api/admin/backups/{name}/verify
  - GET /api/admin/backups/{name}/preview（read-only inspect）
  - GET /api/admin/backups/{name}/restore-cmd（產生 CLI 指令）
  - audit_log 整合（每個操作寫一筆 audit）

對應 ROADMAP C3-D 第二階段 / NIST CP-9/CP-10 / 個資法 §27。
"""

from __future__ import annotations

from pathlib import Path

import pytest


def admin_headers(auth):
    # #384：X-Admin-PIN 已移除；admin 端點僅靠 session（sysadmin 角色）把關。
    return dict(auth)


@pytest.fixture
def with_admin_pin(client):
    # 名稱沿用（避免大量改 usage）；#384 後不再設 Admin PIN，純 client passthrough。
    return client


@pytest.fixture
def backup_dir_isolated(tmp_db, monkeypatch):
    """把 routers/backups 的 BACKUP_DIR 指到 tmp_db 同位置避免汙染。"""
    import routers.backups as br

    backup_dir = tmp_db.parent / "backups"
    monkeypatch.setattr(br, "DB_PATH_PATH", tmp_db)
    monkeypatch.setattr(br, "BACKUP_DIR", backup_dir)
    return backup_dir


class TestAdminPinGate:
    def test_list_requires_session(self, with_admin_pin):
        r = with_admin_pin.get("/api/admin/backups")
        assert r.status_code == 401

    def test_trigger_requires_session(self, with_admin_pin):
        r = with_admin_pin.post("/api/admin/backups")
        assert r.status_code == 401

    def test_verify_requires_session(self, with_admin_pin):
        r = with_admin_pin.post("/api/admin/backups/x/verify")
        assert r.status_code == 401

    def test_preview_requires_session(self, with_admin_pin):
        r = with_admin_pin.get("/api/admin/backups/x/preview")
        assert r.status_code == 401

    def test_restore_cmd_requires_session(self, with_admin_pin):
        r = with_admin_pin.get("/api/admin/backups/x/restore-cmd")
        assert r.status_code == 401


class TestListBackups:
    def test_empty_list(self, with_admin_pin, backup_dir_isolated, auth):
        r = with_admin_pin.get("/api/admin/backups", headers=admin_headers(auth))
        assert r.status_code == 200
        body = r.json()
        assert body["backups"] == []
        assert body["total"] == 0
        # #41 Sync v2: DEFAULT_RETAIN_DAYS 30 → 7
        assert body["retain_days"] == 7

    def test_list_after_backup(self, with_admin_pin, backup_dir_isolated, auth):
        with_admin_pin.post("/api/admin/backups", headers=admin_headers(auth))
        r = with_admin_pin.get("/api/admin/backups", headers=admin_headers(auth))
        body = r.json()
        assert body["total"] == 1
        item = body["backups"][0]
        assert item["filename"].endswith(".db.gz")
        assert item["size_bytes"] > 0
        assert "ics-" not in item["name"]  # name 是 timestamp 部分


class TestTriggerBackup:
    def test_creates_backup_file(self, with_admin_pin, backup_dir_isolated, auth):
        r = with_admin_pin.post("/api/admin/backups", headers=admin_headers(auth))
        assert r.status_code == 200
        body = r.json()
        assert body["filename"].startswith("ics-")
        assert body["filename"].endswith(".db.gz")
        assert len(body["sha256"]) == 64
        assert body["size_bytes"] > 0
        assert (backup_dir_isolated / body["filename"]).exists()

    def test_writes_audit_log(self, with_admin_pin, backup_dir_isolated, auth):
        with_admin_pin.post("/api/admin/backups", headers=admin_headers(auth))
        from core.database import get_conn

        with get_conn() as conn:
            row = conn.execute(
                "SELECT operator, action_type, target_table, target_id "
                "FROM audit_log WHERE action_type='backup_created' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            assert row is not None
            assert row[0] == "admin"
            assert row[1] == "backup_created"
            assert row[2] == "system"

    def test_404_when_db_missing(self, with_admin_pin, backup_dir_isolated, monkeypatch, auth):
        import routers.backups as br

        monkeypatch.setattr(br, "DB_PATH_PATH", Path("/nonexistent/ics.db"))
        r = with_admin_pin.post("/api/admin/backups", headers=admin_headers(auth))
        assert r.status_code == 404


class TestVerifyEndpoint:
    def test_verify_valid_backup(self, with_admin_pin, backup_dir_isolated, auth):
        headers = admin_headers(auth)
        body = with_admin_pin.post("/api/admin/backups", headers=headers).json()
        r = with_admin_pin.post(f"/api/admin/backups/{body['name']}/verify", headers=headers)
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_verify_nonexistent_backup_404(self, with_admin_pin, backup_dir_isolated, auth):
        r = with_admin_pin.post("/api/admin/backups/2099-01-01T00-00-00Z/verify", headers=admin_headers(auth))
        assert r.status_code == 404

    def test_path_traversal_rejected(self, with_admin_pin, backup_dir_isolated, auth):
        # 注意：純 `..` 會被 HTTP client / Starlette 自動 normalize，到不了 endpoint
        # （框架已處理）；這裡只測會到 endpoint 但內含可疑字元的樣式
        for evil in ["foo..bar", "a..b..c", "x\\y"]:
            r = with_admin_pin.post(f"/api/admin/backups/{evil}/verify", headers=admin_headers(auth))
            assert r.status_code == 400, f"應拒絕 {evil!r}，實得 {r.status_code}"


class TestPreviewEndpoint:
    def test_preview_returns_schema_and_counts(self, with_admin_pin, backup_dir_isolated, auth):
        headers = admin_headers(auth)
        body = with_admin_pin.post("/api/admin/backups", headers=headers).json()
        r = with_admin_pin.get(f"/api/admin/backups/{body['name']}/preview", headers=headers)
        assert r.status_code == 200
        b = r.json()
        # 預期含 M001-M005（init_db 跑完）
        assert len(b["schema_migrations"]) >= 5
        assert b["table_counts"]["accounts"] >= 1  # tmp_db 起來會建至少一個 admin
        assert "schema_migrations" in b["table_counts"]

    def test_preview_does_not_leak_pii(self, with_admin_pin, backup_dir_isolated, auth):
        """preview 只回 count，不回 row data — 確保不洩漏 PII。"""
        headers = admin_headers(auth)
        body = with_admin_pin.post("/api/admin/backups", headers=headers).json()
        r = with_admin_pin.get(f"/api/admin/backups/{body['name']}/preview", headers=headers)
        b = r.json()
        # 確保回應不含敏感欄位（pin_hash / username 等）
        text = str(b)
        assert "pin_hash" not in text
        assert "pin_salt" not in text


class TestRestoreCmdEndpoint:
    def test_returns_cli_command(self, with_admin_pin, backup_dir_isolated, auth):
        headers = admin_headers(auth)
        body = with_admin_pin.post("/api/admin/backups", headers=headers).json()
        r = with_admin_pin.get(f"/api/admin/backups/{body['name']}/restore-cmd", headers=headers)
        assert r.status_code == 200
        b = r.json()
        assert "systemctl stop ics-command" in b["cli_command"]
        assert "scripts/restore_db.py" in b["cli_command"]
        assert "--overwrite" in b["cli_command"]
        assert "playbook_ref" in b
        assert "disaster_recovery.md" in b["playbook_ref"]

    def test_writes_audit_for_restore_cmd_issued(self, with_admin_pin, backup_dir_isolated, auth):
        headers = admin_headers(auth)
        body = with_admin_pin.post("/api/admin/backups", headers=headers).json()
        with_admin_pin.get(f"/api/admin/backups/{body['name']}/restore-cmd", headers=headers)
        from core.database import get_conn

        with get_conn() as conn:
            row = conn.execute(
                "SELECT action_type FROM audit_log "
                "WHERE action_type='backup_restore_cmd_issued' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            assert row is not None
