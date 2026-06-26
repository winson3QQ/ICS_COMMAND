"""integration/test_exercise_kpis.py — P2-21 子集（#204）KPI API + bookmark ref_t + AAR_EXPORT audit

驗證：
- build_kpis 數字正確性（種資料對帳：事件處置時長/通聯量 by 組/決策裁示時長/AAR 條目數）
- 量不出的指標 = null + reason（medevac_reaction / excheck_completion，不假裝）
- aar_entries.ref_t：bookmark category + iso_utc 正規化 + 一般條目 NULL
- /kpis endpoint：200 shape / 404 / operator 403（COMMAND_ROLES 中央 gate）
- AAR_EXPORT audit：report/export 必留痕
"""

import pytest

from auth.role_enum import ROLE_OPERATOR_ZH
from core.database import get_conn
from repositories.aar_repo import create_aar_entry, get_aar_entries
from repositories.account_repo import create_account
from repositories.chat_repo import insert_chat
from repositories.decision_repo import create_decision, decide
from repositories.event_repo import create_event, update_event_status
from repositories.exercise_repo import create_exercise
from schemas.chat import ChatIn
from services.kpi_service import build_kpis

pytestmark = pytest.mark.integration

_EV = {
    "reported_by_unit": "shelter",
    "event_type": "fire",
    "severity": "critical",
    "description": "x",
    "operator_name": "admin",
}
_DEC = {
    "decision_type": "evac",
    "severity": "high",
    "decision_title": "撤離",
    "impact_description": "i",
    "suggested_action_a": "a",
    "created_by": "admin",
}


def _mk(name="KPI 測試"):
    return create_exercise({"name": name, "type": "ttx"})["id"]


class TestBuildKpis:
    def test_numbers_add_up(self, tmp_db):
        exid = _mk()
        # 2 事件（1 結案）；occurred 固定過去 → 處置時長 > 0
        ev1 = create_event({**_EV, "occurred_at": "2026-01-01T00:00:00Z"}, exercise_id=exid)
        create_event({**_EV, "severity": "warning"}, exercise_id=exid)
        update_event_status(ev1["id"], "resolved", "admin", exercise_id=exid)
        # 2 通聯（兩組）
        insert_chat(ChatIn(sender_uid="u1", message="a", group="ops", exercise_id=exid))
        insert_chat(ChatIn(sender_uid="u2", message="b", group="medic", exercise_id=exid))
        # 1 決策（已裁示）
        dec = create_decision(_DEC.copy(), exercise_id=exid)
        decide(dec["id"], "approved", "cmd", exercise_id=exid)
        # 1 bookmark + 1 一般 AAR
        create_aar_entry(exid, "bookmark", "重點", "cmd", ref_t="2026-01-01T00:05:00Z")
        create_aar_entry(exid, "improve", "通聯紀律", "cmd")

        k = build_kpis(exid)
        assert k["events"]["total"] == 2
        assert k["events"]["by_severity"] == {"critical": 1, "warning": 1}
        assert k["events"]["resolution"]["resolved_count"] == 1
        assert k["events"]["resolution"]["avg_minutes"] > 0  # occurred 在過去 → 正時長
        assert k["chats"] == {"total": 2, "by_group": {"ops": 1, "medic": 1}}
        assert k["decisions"]["total"] == 1 and k["decisions"]["decided"] == 1
        assert k["decisions"]["avg_decide_minutes"] is not None
        assert k["aar_entries"]["by_category"] == {"bookmark": 1, "improve": 1}

    def test_uncomputable_are_honest_nulls(self, tmp_db):
        k = build_kpis(_mk())
        assert k["medevac_reaction"]["value"] is None and k["medevac_reaction"]["reason"]
        assert k["excheck_completion"]["value"] is None and k["excheck_completion"]["reason"]

    def test_empty_exercise_zeroes(self, tmp_db):
        k = build_kpis(_mk())
        assert k["events"]["total"] == 0
        assert k["events"]["resolution"]["avg_minutes"] is None
        assert k["tracks"] == {"points": 0, "distinct_units": 0}


class TestBookmarkRefT:
    def test_ref_t_normalized_and_stored(self, tmp_db):
        exid = _mk()
        e = create_aar_entry(exid, "bookmark", "tz", "cmd", ref_t="2026-01-01T11:00:00+08:00")
        assert e["ref_t"] == "2026-01-01T03:00:00Z"  # iso_utc 正規化
        found = [x for x in get_aar_entries(exid) if x["id"] == e["id"]][0]
        assert found["ref_t"] == "2026-01-01T03:00:00Z"

    def test_plain_entry_ref_t_null_and_bad_category_rejected(self, tmp_db):
        exid = _mk()
        e = create_aar_entry(exid, "well", "好", "cmd")
        assert e["ref_t"] is None
        with pytest.raises(ValueError):
            create_aar_entry(exid, "not-a-category", "x", "cmd")


class TestKpisEndpoint:
    def test_shape_404_and_rbac(self, client, auth):
        exid = create_exercise({"name": "E", "type": "ttx"})["id"]
        r = client.get(f"/api/exercises/{exid}/kpis", headers=auth)
        assert r.status_code == 200
        assert {"events", "chats", "decisions", "medevac_reaction"} <= set(r.json())
        assert client.get("/api/exercises/99999/kpis", headers=auth).status_code == 404
        create_account("op_kpi", "5678", ROLE_OPERATOR_ZH, "Op", "operator")
        login = client.post("/api/auth/login", json={"username": "op_kpi", "pin": "5678"})
        r = client.get(f"/api/exercises/{exid}/kpis", headers={"X-Session-Token": login.json()["session_id"]})
        assert r.status_code == 403  # 統計不暴露 public / 非指揮層


class TestAarExportAudit:
    def test_report_and_export_leave_audit(self, client, auth):
        exid = create_exercise({"name": "E", "type": "ttx"})["id"]
        assert client.get(f"/api/ai/report/{exid}", headers=auth).status_code == 200
        assert client.get(f"/api/ai/export/{exid}", headers=auth).status_code == 200
        with get_conn() as conn:
            kinds = [
                r[0]
                for r in conn.execute(
                    "SELECT json_extract(detail,'$.kind') FROM audit_log "
                    "WHERE action_type='AAR_EXPORT' AND target_id=?",
                    (str(exid),),
                )
            ]
        assert sorted(kinds) == ["ml_export", "report"]


class TestReviewFixes:
    def test_group_count_null_and_empty_summed(self, tmp_db):
        """review #204-1：severity NULL 與 '' 並存 → 「（未分類）」相加非覆蓋。"""
        exid = _mk()
        with get_conn() as conn:
            for sev in (None, "", "critical"):
                conn.execute(
                    "INSERT INTO events (id, reported_by_unit, event_type, severity, "
                    "description, operator_name, exercise_id) "
                    "VALUES (hex(randomblob(8)), 'u', 'fire', ?, 'x', 'admin', ?)",
                    (sev, exid),
                )
        k = build_kpis(exid)
        assert k["events"]["by_severity"] == {"（未分類）": 2, "critical": 1}

    def test_garbage_ref_t_rejected(self, tmp_db):
        """security-review 收緊：垃圾 ref_t（iso_utc 補 Z 後仍非法）→ ValueError（422）。"""
        exid = _mk()
        with pytest.raises(ValueError):
            create_aar_entry(exid, "bookmark", "x", "cmd", ref_t="garbage")
