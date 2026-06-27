# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
tests/integration/test_schema_migrations.py — C1-E schema 版本追蹤測試

驗證：
- schema_migrations 表在 init_db() 後存在且有正確紀錄
- 所有已知 migrations 都已套用
- _migrate() 為 idempotent（重複執行不會新增重複紀錄）
- get_schema_version() 回傳最高版本號
- /api/admin/schema-migrations 端點需 sysadmin session 並回傳正確格式
"""

from core.database import _MIGRATIONS, get_conn, get_schema_version, init_db


class TestMigrationsTable:
    def test_schema_migrations_table_exists(self, tmp_db):
        """init_db() 後 schema_migrations 表應存在。"""
        with get_conn() as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "schema_migrations" in tables

    def test_all_migrations_applied(self, tmp_db):
        """所有定義在 _MIGRATIONS 的版本都應已套用。"""
        with get_conn() as conn:
            applied = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
        expected = {v for v, _, _ in _MIGRATIONS}
        assert expected == applied

    def test_migration_records_have_name_and_timestamp(self, tmp_db):
        """每筆 migration 紀錄需有 name 和 applied_at。"""
        with get_conn() as conn:
            rows = conn.execute("SELECT version, name, applied_at FROM schema_migrations ORDER BY version").fetchall()
        assert len(rows) == len(_MIGRATIONS)
        for row in rows:
            assert row["name"]  # name 不為空
            assert row["applied_at"]  # applied_at 不為空

    def test_get_schema_version_returns_max(self, tmp_db):
        """get_schema_version() 應回傳最高版本號。"""
        expected_max = max(v for v, _, _ in _MIGRATIONS)
        with get_conn() as conn:
            assert get_schema_version(conn) == expected_max

    def test_migrate_is_idempotent(self, tmp_db):
        """重複呼叫 init_db() 不會產生重複 migration 紀錄。"""
        init_db()  # 再跑一次
        with get_conn() as conn:
            count = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
        assert count == len(_MIGRATIONS)

    def test_migrations_versions_are_sequential(self, tmp_db):
        """版本號應從 1 開始連續遞增（無跳號）。"""
        versions = sorted(v for v, _, _ in _MIGRATIONS)
        assert versions == list(range(1, len(_MIGRATIONS) + 1))


class TestSchemaMigrationsApi:
    def test_list_migrations_requires_session(self, client):
        """/api/admin/schema-migrations 無 session → 401（#384：僅 session 角色把關）。"""
        r = client.get("/api/admin/schema-migrations")
        assert r.status_code == 401

    def test_list_migrations_returns_all_versions(self, client, auth):
        """sysadmin session → 200，回傳所有已套用的 migrations。"""
        r = client.get("/api/admin/schema-migrations", headers=auth)
        assert r.status_code == 200
        data = r.json()
        assert len(data) == len(_MIGRATIONS)
        versions = [m["version"] for m in data]
        assert versions == sorted(versions)  # 依版本號排序

    def test_list_migrations_record_structure(self, client, auth):
        """每筆 migration 紀錄需包含 version / name / applied_at。"""
        r = client.get("/api/admin/schema-migrations", headers=auth)
        for m in r.json():
            assert "version" in m
            assert "name" in m
            assert "applied_at" in m

    def test_admin_status_includes_schema_version(self, client, auth):
        """/api/admin/status 回傳 schema_version 欄位。"""
        r = client.get("/api/admin/status", headers=auth)
        assert r.status_code == 200
        assert "schema_version" in r.json()
        assert r.json()["schema_version"] == max(v for v, _, _ in _MIGRATIONS)
