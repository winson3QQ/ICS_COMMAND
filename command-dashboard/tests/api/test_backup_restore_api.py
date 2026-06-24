"""
test_backup_restore_api.py — P1-12b 整包備份 / 還原 API + 觸發 + OP-2（#228）

涵蓋：
- POST/GET /api/admin/user-data-backups（role gate / 加密 / manifest）
- POST /api/admin/restore（active 演習 409 / 壞檔 422 不動 current / pre-restore）
- OP-2：reset-db/reset-exercise 強制 confirm:"RESET"（422）+ RBAC 仍先（403）
- L2：archive 自動觸發整包備份（key 有設時）
"""

from __future__ import annotations

import io
import sys

import pytest
from cryptography.fernet import Fernet

from repositories.account_repo import create_account


@pytest.fixture
def keyed(monkeypatch):
    """設 BACKUP_KEY，讓整包備份端點可加密。"""
    monkeypatch.setenv("BACKUP_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("BACKUP_ENCRYPTION_KEY", raising=False)


@pytest.fixture
def data_isolated(tmp_db, monkeypatch):
    """把 backup_restore 的 DATA_DIR / BACKUP_DIR 指到 tmp 避免汙染真實 data/。

    也 patch core.config.DATA_DIR：lifespan shutdown 的 best-effort 備份在
    call-time `from core.config import DATA_DIR`，不 patch 會寫進真實 repo data/。
    """
    import core.config
    import routers.backup_restore as br

    data_dir = tmp_db.parent
    backup_dir = data_dir / "backups"
    monkeypatch.setattr(br, "DATA_DIR", data_dir)
    monkeypatch.setattr(br, "BACKUP_DIR", backup_dir)
    monkeypatch.setattr(core.config, "DATA_DIR", data_dir)
    return data_dir, backup_dir


# ── role gate ────────────────────────────────────────────────────────────────


class TestRoleGate:
    def test_requires_session(self, client, data_isolated):
        assert client.post("/api/admin/user-data-backups").status_code == 401
        assert client.get("/api/admin/user-data-backups").status_code == 401
        assert client.post("/api/admin/restore").status_code == 401

    def test_operator_forbidden(self, client, data_isolated, keyed):
        create_account("op_b", "1234", "操作員", "Op", "operator")
        r = client.post("/api/auth/login", json={"username": "op_b", "pin": "1234"})
        h = {"X-Session-Token": r.json()["session_id"]}
        assert client.post("/api/admin/user-data-backups", headers=h).status_code == 403


# ── 整包備份 ─────────────────────────────────────────────────────────────────


class TestUserDataBackup:
    def test_create_and_list_encrypted(self, client, auth, data_isolated, keyed):
        r = client.post("/api/admin/user-data-backups", headers=auth)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["name"].endswith(".tar.gz.enc")
        assert body["manifest"]["schema"].startswith("ics-userdata-backup")
        # tmp_db fixture 的 DB 檔名為 test_ics.db
        assert any(f.endswith("ics.db") for f in body["manifest"]["files"]), body["manifest"]["files"]

        lst = client.get("/api/admin/user-data-backups", headers=auth).json()
        assert lst["total"] >= 1
        assert any(b["name"] == body["name"] for b in lst["backups"])

    def test_manifest_preview(self, client, auth, data_isolated, keyed):
        name = client.post("/api/admin/user-data-backups", headers=auth).json()["name"]
        r = client.get(f"/api/admin/user-data-backups/{name}/manifest", headers=auth)
        assert r.status_code == 200
        assert r.json()["manifest"]["schema"].startswith("ics-userdata-backup")

    def test_manifest_rejects_traversal(self, client, auth, data_isolated, keyed):
        r = client.get("/api/admin/user-data-backups/..%2Fevil/manifest", headers=auth)
        assert r.status_code in (400, 404)


# ── 還原 ─────────────────────────────────────────────────────────────────────


class TestRestore:
    def _make_backup_bytes(self, client, auth) -> bytes:
        name = client.post("/api/admin/user-data-backups", headers=auth).json()["name"]
        return name

    def test_restore_rejects_when_active_exercise(self, client, auth, data_isolated, keyed, active_exercise):
        # 有 active 演習 → 409（即使檔案沒附也應先擋 active）
        files = {"file": ("x.tar.gz.enc", io.BytesIO(b"dummy"), "application/octet-stream")}
        r = client.post("/api/admin/restore", headers=auth, files=files)
        assert r.status_code == 409
        assert "進行中演習" in r.json()["detail"]

    def test_restore_rejects_bad_extension(self, client, auth, data_isolated, keyed):
        files = {"file": ("x.zip", io.BytesIO(b"dummy"), "application/octet-stream")}
        r = client.post("/api/admin/restore", headers=auth, files=files)
        assert r.status_code == 400

    def test_restore_bad_payload_422_current_untouched(self, client, auth, data_isolated, keyed):
        # 壞檔 → 422 且不還原（不覆蓋 current DB）。
        # #367：不比對原始 DB bytes —— (1) 被拒的 restore 會合法寫一筆 audit_log（留痕被拒），
        # bytes 本就該變；(2) SQLite WAL checkpoint 把該寫入刷進主檔的時機非決定性 → raw-byte
        # 比對 flaky（base 上即 ~66% 偽失敗）。改驗邏輯不變量：寫入 current DB 的 sentinel 應存活
        # （成功 restore 會以備份內容整檔覆蓋 → sentinel 消失；422 不還原 → sentinel 仍在）。
        from repositories.config_repo import get_config, set_config

        set_config("restore_guard_sentinel_367", "alive")
        files = {"file": ("x.tar.gz.enc", io.BytesIO(b"NOT_ENCRYPTED"), "application/octet-stream")}
        r = client.post("/api/admin/restore", headers=auth, files=files)
        assert r.status_code == 422
        assert get_config("restore_guard_sentinel_367") == "alive"  # current DB 未被壞檔覆蓋

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="Windows 鎖住開啟中的 DB 檔，無法原地替換熱 data/（Linux/CI 可 unlink 開啟檔）；"
        "restore 邏輯由 tests/integration/test_user_data_backup.py 跨平台覆蓋",
    )
    def test_restore_roundtrip(self, client, auth, data_isolated, keyed):
        data_dir, backup_dir = data_isolated
        # 種一個可辨識檔
        (data_dir / "marker.txt").write_text("ORIGINAL", encoding="utf-8")
        name = client.post("/api/admin/user-data-backups", headers=auth).json()["name"]
        enc_bytes = (backup_dir / name).read_bytes()

        # 竄改 current
        (data_dir / "marker.txt").write_text("CHANGED", encoding="utf-8")

        files = {"file": (name, io.BytesIO(enc_bytes), "application/octet-stream")}
        r = client.post("/api/admin/restore", headers=auth, files=files)
        assert r.status_code == 200, r.text
        assert r.json()["restart_required"] is True
        assert r.json()["pre_restore"] is not None
        assert (data_dir / "marker.txt").read_text(encoding="utf-8") == "ORIGINAL"


# ── OP-2：reset confirm ─────────────────────────────────────────────────────


class TestResetConfirm:
    def test_reset_db_requires_confirm(self, client, auth):
        assert client.post("/api/admin/reset-db", headers=auth).status_code == 422
        assert client.post("/api/admin/reset-db", headers=auth, json={"confirm": "no"}).status_code == 422
        assert client.post("/api/admin/reset-db", headers=auth, json={"confirm": "RESET"}).status_code == 200

    def test_reset_exercise_requires_confirm(self, client, auth):
        assert client.post("/api/admin/reset-exercise", headers=auth).status_code == 422
        assert client.post("/api/admin/reset-exercise", headers=auth, json={"confirm": "RESET"}).status_code == 200

    def test_rbac_before_confirm(self, client):
        """非 sysadmin → 403（role 先於 body 驗證；無 body 也不該變 422）。"""
        create_account("cmd_b", "1234", "指揮官", "Cmd", "commander")
        r = client.post("/api/auth/login", json={"username": "cmd_b", "pin": "1234"})
        h = {"X-Session-Token": r.json()["session_id"]}
        assert client.post("/api/admin/reset-db", headers=h).status_code == 403


# ── L2：archive 自動備份 ─────────────────────────────────────────────────────


class TestArchiveBackup:
    def test_archive_triggers_backup_when_keyed(self, client, auth, data_isolated, keyed, monkeypatch):
        # exercises router 用自己的 DATA_DIR import；archive backup 走 _l2_archive_backup
        # 內 `from core.config import DATA_DIR`，patch core.config.DATA_DIR 生效。
        import core.config

        data_dir, backup_dir = data_isolated
        monkeypatch.setattr(core.config, "DATA_DIR", data_dir)

        ex = client.post("/api/exercises", json={"name": "L2-test", "type": "ttx"}, headers=auth).json()
        client.post(f"/api/exercises/{ex['id']}/activate", json={}, headers=auth)
        r = client.post(f"/api/exercises/{ex['id']}/archive", headers=auth)
        assert r.status_code == 200, r.text
        # archive 後 backups/ 應有 archive trigger 的整包備份
        names = [p.name for p in backup_dir.iterdir()] if backup_dir.exists() else []
        assert any(n.endswith(".tar.gz.enc") for n in names), names
