"""
unit/test_tak_device_cert_repo.py — #317：TAK 裝置證盤點表（record / list / mark_revoked）。
"""

import pytest

pytestmark = pytest.mark.unit


class TestRecordAndList:
    def test_record_then_list(self, tmp_db):
        from repositories.tak_device_cert_repo import list_device_certs, record_issued

        rec = record_issued("itak-01", "AABBCC", "aware", "admin")
        assert rec["callsign"] == "itak-01" and rec["serial"] == "AABBCC" and rec["status"] == "active"
        rows = list_device_certs()
        assert len(rows) == 1 and rows[0]["mode"] == "aware" and rows[0]["operator"] == "admin"

    def test_callsign_not_unique(self, tmp_db):
        """同 callsign 可重發（TAK 允許），每發一張一列。"""
        from repositories.tak_device_cert_repo import list_device_certs, record_issued

        record_issued("dup", "S1", "atak", "admin")
        record_issued("dup", "S2", "atak", "admin")
        rows = [r for r in list_device_certs() if r["callsign"] == "dup"]
        assert len(rows) == 2 and {r["serial"] for r in rows} == {"S1", "S2"}

    def test_list_newest_first(self, tmp_db):
        from repositories.tak_device_cert_repo import list_device_certs, record_issued

        record_issued("a", "S1", "atak", "admin")
        record_issued("b", "S2", "atak", "admin")
        rows = list_device_certs()
        assert rows[0]["callsign"] == "b"  # 新到舊

    def test_record_stores_fingerprint_and_enroll_status(self, tmp_db):
        # #398：fingerprint（= TAK managed-user 鍵）+ enroll_status 一併存，供清單顯示/比對混用。
        from repositories.tak_device_cert_repo import list_device_certs, record_issued

        rec = record_issued("fp-01", "S1", "atak", "admin", fingerprint="90:10:59:AA", enroll_status="ok")
        assert rec["fingerprint"] == "90:10:59:AA" and rec["enroll_status"] == "ok"
        assert list_device_certs()[0]["fingerprint"] == "90:10:59:AA"

    def test_record_fingerprint_optional_defaults_none(self, tmp_db):
        # 升級前 / 未帶 → NULL（不破既有呼叫端）。
        from repositories.tak_device_cert_repo import record_issued

        rec = record_issued("noFp", "S1", "atak", "admin")
        assert rec["fingerprint"] is None and rec["enroll_status"] is None

    def test_record_enroll_status_in_audit_but_not_fingerprint(self, tmp_db):
        # enroll_status 進 audit（同步結果可稽核）；fingerprint 不進 audit（敏感）。
        from core.database import get_conn
        from repositories.tak_device_cert_repo import record_issued

        record_issued("aud-01", "S1", "atak", "admin", fingerprint="DE:AD:BE:EF", enroll_status="timeout")
        with get_conn() as conn:
            detail = conn.execute(
                "SELECT detail FROM audit_log WHERE action_type='tak_device_cert_record' ORDER BY id DESC LIMIT 1"
            ).fetchone()[0]
        assert "timeout" in detail and "DE:AD:BE:EF" not in detail


class TestMarkRevoked:
    def test_mark_flips_status(self, tmp_db):
        from repositories.tak_device_cert_repo import mark_revoked, record_issued

        rec = record_issued("x", "S1", "aware", "admin")
        out = mark_revoked(rec["id"], "admin2")
        assert out["status"] == "revoked" and out["revoked_at"] and out["revoked_by"] == "admin2"

    def test_mark_unknown_returns_none(self, tmp_db):
        from repositories.tak_device_cert_repo import mark_revoked

        assert mark_revoked(9999, "admin") is None

    def test_mark_already_revoked_returns_none(self, tmp_db):
        from repositories.tak_device_cert_repo import mark_revoked, record_issued

        rec = record_issued("y", "S1", "aware", "admin")
        mark_revoked(rec["id"], "admin")
        assert mark_revoked(rec["id"], "admin") is None  # 已撤再撤 → None


class TestDeleteRecord:
    """#325：刪除已撤銷盤點紀錄（僅 revoked 可刪）。"""

    def test_delete_revoked_ok(self, tmp_db):
        from repositories.tak_device_cert_repo import delete_record, list_device_certs, mark_revoked, record_issued

        rec = record_issued("d", "S1", "aware", "admin")
        mark_revoked(rec["id"], "admin")
        assert delete_record(rec["id"], "admin2") is True
        assert all(r["id"] != rec["id"] for r in list_device_certs())  # 已移除

    def test_delete_active_refused(self, tmp_db):
        """active 列不可刪（還代表在用的證）→ False、列仍在。"""
        from repositories.tak_device_cert_repo import delete_record, list_device_certs, record_issued

        rec = record_issued("a", "S1", "atak", "admin")
        assert delete_record(rec["id"], "admin") is False
        assert any(r["id"] == rec["id"] for r in list_device_certs())

    def test_delete_unknown_returns_false(self, tmp_db):
        from repositories.tak_device_cert_repo import delete_record

        assert delete_record(9999, "admin") is False
