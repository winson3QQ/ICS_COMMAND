# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
import importlib
import re
import sqlite3
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def test_health_returns_200(client):
    r = client.get("/api/health")
    assert r.status_code == 200


def test_health_no_auth_required(client):
    r = client.get("/api/health")
    assert r.status_code == 200


def test_health_contains_required_fields(client, auth):
    # §8.6：完整運維欄位僅已登入可取（未登入只回 status/db_writable/version/timestamp）
    r = client.get("/api/health", headers=auth)
    body = r.json()
    assert set(body) == {
        "status",
        "version",
        "db_path",
        "db_writable",
        "schema_version",
        "disk_free_mb",
        "disk_free_pct",
        "db_latency_ms",
        "timestamp",
    }
    assert body["status"] in {"ok", "degraded", "initializing"}
    assert isinstance(body["version"], str)
    assert isinstance(body["db_path"], str)
    assert isinstance(body["db_writable"], bool)
    assert isinstance(body["disk_free_mb"], int)
    assert isinstance(body["disk_free_pct"], float)
    assert 0.0 <= body["disk_free_pct"] <= 100.0
    assert body["db_latency_ms"] is None or isinstance(body["db_latency_ms"], float)
    assert re.match(r"^\d{4}-\d{2}-\d{2}T.*Z$", body["timestamp"])


def test_health_contains_version(client):
    from core.config import APP_VERSION

    assert client.get("/api/health").json()["version"] == APP_VERSION


def test_health_status_degraded_on_unwritable_db(client, monkeypatch):
    from routers import dashboard

    monkeypatch.setattr(dashboard, "_db_writable", lambda _path: False)
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "degraded"
    assert body["db_writable"] is False


def test_health_status_degraded_on_low_disk(client, monkeypatch, auth):
    from routers import dashboard

    # disk_free_pct < HEALTH_DISK_DEGRADED_PCT_THRESHOLD（預設 20%）→ degraded
    monkeypatch.setattr(dashboard, "_disk_free_pct", lambda _path: 5.0)
    r = client.get("/api/health", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "degraded"
    assert body["disk_free_pct"] == 5.0


def test_health_status_degraded_on_high_db_latency(client, monkeypatch, auth):
    from routers import dashboard

    # db_latency_ms > HEALTH_DB_LATENCY_DEGRADED_MS（預設 500ms）→ degraded
    monkeypatch.setattr(dashboard, "_db_latency_ms", lambda _path: 999.0)
    r = client.get("/api/health", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "degraded"
    assert body["db_latency_ms"] == 999.0


def test_health_status_ok_when_disk_and_latency_normal(client, monkeypatch):
    from routers import dashboard

    monkeypatch.setattr(dashboard, "_disk_free_pct", lambda _path: 50.0)
    monkeypatch.setattr(dashboard, "_db_latency_ms", lambda _path: 1.0)
    body = client.get("/api/health").json()
    assert body["status"] == "ok"


def test_health_disk_free_mb_reflects_var_lib_ics(client, monkeypatch, auth):
    from routers import dashboard

    seen = {}

    def fake_disk_free_mb(path):
        seen["path"] = path
        return 1234

    monkeypatch.setattr(dashboard, "_disk_free_mb", fake_disk_free_mb)
    assert client.get("/api/health", headers=auth).json()["disk_free_mb"] == 1234
    assert seen["path"] == Path(dashboard.DB_PATH).parent


def test_health_schema_version_matches_db(client, auth):
    from core.database import _MIGRATIONS

    body = client.get("/api/health", headers=auth).json()
    assert body["schema_version"] == max(version for version, _, _ in _MIGRATIONS)


def test_health_schema_version_null_when_table_missing(client, monkeypatch, tmp_path, auth):
    from routers import dashboard

    db_path = tmp_path / "no_schema_migrations.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY)")

    monkeypatch.setattr(dashboard, "DB_PATH", db_path)
    body = client.get("/api/health", headers=auth).json()
    assert body["schema_version"] is None


def test_health_status_initializing_during_first_run(client, monkeypatch, auth):
    from routers import dashboard

    monkeypatch.setattr(dashboard, "_first_run_required", lambda: True)
    body = client.get("/api/health", headers=auth).json()
    assert body["status"] == "initializing"
    assert body["schema_version"] is None


def test_db_path_env_override(monkeypatch, tmp_path):
    import core.config as config

    target = tmp_path / "ics.db"
    monkeypatch.setenv("ICS_DB_PATH", str(target))
    importlib.reload(config)
    try:
        assert config.DB_PATH == target
    finally:
        monkeypatch.delenv("ICS_DB_PATH", raising=False)
        importlib.reload(config)


def test_db_path_fallback_default(monkeypatch):
    import core.config as config

    monkeypatch.delenv("ICS_DB_PATH", raising=False)
    importlib.reload(config)
    assert config.DB_PATH == config.BASE_DIR / "data" / "ics.db"


def test_get_current_schema_version_helper(tmp_db):
    from core.database import _MIGRATIONS, get_conn, get_health_schema_version

    with get_conn() as conn:
        assert get_health_schema_version(conn) == max(version for version, _, _ in _MIGRATIONS)


def test_get_current_schema_version_helper_missing_table(tmp_path):
    from core.database import get_health_schema_version

    db_path = tmp_path / "missing_table.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY)")
        assert get_health_schema_version(conn) is None
