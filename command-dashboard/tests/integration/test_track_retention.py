# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""integration/test_track_retention.py — P2-20 收尾（#207）軌跡 PII TTL retention

驗證：
- cleanup_expired_tracks：過期刪 / 未過期留 / 開關關閉 no-op / 刪除留 RETENTION_CLEANUP audit
- TTL 天數防呆（≥1）
- /api/admin/retention：GET 狀態 / POST 開關（持久化 + RETENTION_TOGGLE audit + 立即清理）/ 非 sysadmin 403
- 預設啟用（未設 config key → True，政策出廠生效）
"""

import pytest

from auth.role_enum import ROLE_OPERATOR_ZH
from core.database import get_conn
from repositories.account_repo import create_account
from repositories.cop_entity_repo import insert_cop_entity
from repositories.exercise_repo import create_exercise
from schemas.cop import CoPEntity
from services import retention_service

pytestmark = pytest.mark.integration


def _seed_tracks(exid):
    """一筆過期（200 天前）+ 一筆新（現在）軌跡。"""
    insert_cop_entity(
        CoPEntity(
            uid="ret-u1",
            type="a-f-G-U-C",
            time="2026-01-01T00:00:00Z",
            start="2026-01-01T00:00:00Z",
            stale="2099-01-01T00:00:00Z",
            how="m-g",
            lat=24.0,
            lon=120.0,
            source="tak",
            exercise_id=exid,
        )
    )
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO cop_entity_tracks (uid,t,lat,lon,hae) VALUES "
            "('ret-u1', strftime('%Y-%m-%dT%H:%M:%SZ','now','-200 days'), 24.0, 120.0, 0),"
            "('ret-u1', strftime('%Y-%m-%dT%H:%M:%SZ','now'), 24.1, 120.1, 0)"
        )


def _track_count():
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM cop_entity_tracks WHERE uid='ret-u1'").fetchone()[0]


class TestCleanup:
    def test_default_enabled_and_expired_deleted_recent_kept(self, tmp_db):
        exid = create_exercise({"name": "R", "type": "ttx"})["id"]
        _seed_tracks(exid)
        assert retention_service.ttl_enabled() is True  # 政策預設生效
        deleted = retention_service.cleanup_expired_tracks()
        assert deleted == 1 and _track_count() == 1  # 過期刪、新留
        with get_conn() as conn:
            n = conn.execute("SELECT COUNT(*) FROM audit_log WHERE action_type='RETENTION_CLEANUP'").fetchone()[0]
        assert n == 1  # 個資刪除留痕

    def test_disabled_is_noop(self, tmp_db):
        exid = create_exercise({"name": "R", "type": "ttx"})["id"]
        _seed_tracks(exid)
        retention_service.set_ttl_enabled(False)
        assert retention_service.cleanup_expired_tracks() == 0
        assert _track_count() == 2  # 全留

    def test_ttl_days_floor_guard(self, tmp_db, monkeypatch):
        """TTL 誤設 0/負值 → 夾到 ≥1 天，拒絕「全清」誤設。"""
        from core import config

        exid = create_exercise({"name": "R", "type": "ttx"})["id"]
        _seed_tracks(exid)
        monkeypatch.setattr(config, "TRACKS_TTL_DAYS", 0)
        retention_service.cleanup_expired_tracks()
        assert _track_count() >= 1  # 「現在」那筆不會被 TTL=0 清掉


class TestEndpoint:
    def test_get_post_persist_audit(self, client, auth):
        r = client.get("/api/admin/retention", headers=auth)
        assert r.status_code == 200
        assert r.json()["tracks_ttl_enabled"] is True
        r = client.post("/api/admin/retention", json={"enabled": False}, headers=auth)
        assert r.status_code == 200 and r.json()["enabled"] is False
        assert retention_service.ttl_enabled() is False  # 持久化
        with get_conn() as conn:
            n = conn.execute("SELECT COUNT(*) FROM audit_log WHERE action_type='RETENTION_TOGGLE'").fetchone()[0]
        assert n == 1

    def test_non_sysadmin_403(self, client):
        create_account("op_ret", "5678", ROLE_OPERATOR_ZH, "Op", "operator")
        login = client.post("/api/auth/login", json={"username": "op_ret", "pin": "5678"})
        headers = {"X-Session-Token": login.json()["session_id"]}
        assert client.get("/api/admin/retention", headers=headers).status_code == 403
        assert client.post("/api/admin/retention", json={"enabled": False}, headers=headers).status_code == 403


class TestSystemScopeAudit:
    def test_retention_audits_not_bound_to_active_exercise(self, tmp_db):
        """review #207：RETENTION_* 為跨演習系統掃除 → audit exercise_id 須 NULL，
        否則被 active 場 cascade 刪掉（PII 刪除證明遺失）。"""
        from repositories.exercise_repo import create_exercise
        from services.exercise_service import set_active

        exid = create_exercise({"name": "active", "type": "ttx"})["id"]
        set_active(exid, "admin")  # 有 active 場 → Model B 會想自動戳
        _seed_tracks(create_exercise({"name": "old", "type": "ttx"})["id"])
        retention_service.cleanup_expired_tracks()  # → RETENTION_CLEANUP audit
        retention_service.set_ttl_enabled(True)
        with get_conn() as conn:
            rows = conn.execute("SELECT exercise_id FROM audit_log WHERE action_type LIKE 'RETENTION_%'").fetchall()
        assert rows and all(r[0] is None for r in rows)  # 全 NULL（系統層、不綁場）
