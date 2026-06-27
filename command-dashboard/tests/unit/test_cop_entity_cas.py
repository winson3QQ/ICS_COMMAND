# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
tests/unit/test_cop_entity_cas.py — issue #29 PR-A：per-entity 樂觀鎖 + soft-delete

鎖住的不變式：
- migration m014 落地：cop_entities 有 updated_by / updated_at 兩欄 + 記錄在 schema_migrations
- update_cop_entity_cas：
  - 成功 → version_clock +1、寫 updated_by/updated_at、回 {"status":"ok"}
  - 版本對不上 → {"status":"conflict"} 且 **DB 完全不動**（防 lost update）
  - 並發兩寫帶同一 expected → 一勝一敗（CAS 核心保證）
  - uid 不存在 → {"status":"notfound"}
  - 受保護欄位 / 未知欄位 patch → ValueError（injection + managed 欄位防線）
  - visible_to / attributes 走 JSON 序列化來回一致
- delete_cop_entity（TAK soft-delete）：標 stale=now + bump version_clock，list 預設過濾掉，
  歷史 row 仍在；亦受樂觀鎖保護（版本對不上 → conflict）
"""

import pytest

from core.database import get_conn
from repositories.cop_entity_repo import (
    delete_cop_entity,
    get_cop_entity,
    insert_cop_entity,
    list_cop_entities,
    update_cop_entity_cas,
)
from schemas.cop import CoPEntity


def _valid_entity_payload(uid: str = "cas-001", **overrides) -> dict:
    base = {
        "uid": uid,
        "type": "a-f-G-U-C",
        "time": "2026-05-29T10:00:00Z",
        "start": "2026-05-29T10:00:00Z",
        "stale": "2099-01-01T00:00:00Z",  # 遠未來 → list 預設視為 live
        "how": "h-e",
        "lat": 25.0330,
        "lon": 121.5654,
        "source": "manual",
    }
    base.update(overrides)
    return base


def _insert(uid: str = "cas-001", **overrides) -> dict:
    return insert_cop_entity(CoPEntity(**_valid_entity_payload(uid, **overrides)))


# ── 1. migration m014 落地 ───────────────────────────────────────────────────


class TestMigration014Landed:
    def test_updated_cols_exist(self, tmp_db):
        with get_conn() as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(cop_entities)")}
        assert "updated_by" in cols
        assert "updated_at" in cols

    def test_migration_recorded(self, tmp_db):
        with get_conn() as c:
            row = c.execute("SELECT version, name FROM schema_migrations WHERE version=14").fetchone()
        assert row is not None
        assert row[1] == "cop_entities_audit_cols"


# ── 2. update_cop_entity_cas ─────────────────────────────────────────────────


class TestUpdateCas:
    def test_success_bumps_version_and_sets_actor(self, tmp_db):
        _insert("u1")  # version_clock=1
        res = update_cop_entity_cas("u1", 1, {"callsign": "ALPHA"}, actor="cmdr")
        assert res["status"] == "ok"
        ent = res["entity"]
        assert ent["callsign"] == "ALPHA"
        assert ent["version_clock"] == 2  # +1
        assert ent["updated_by"] == "cmdr"
        assert ent["updated_at"] is not None and ent["updated_at"].endswith("Z")

    def test_conflict_leaves_db_untouched(self, tmp_db):
        _insert("u2")  # version_clock=1
        # 帶過期 expected（99）→ conflict，且 DB 一個字節都不能動
        res = update_cop_entity_cas("u2", 99, {"callsign": "GHOST"}, actor="x")
        assert res["status"] == "conflict"
        assert res["entity"]["version_clock"] == 1  # 回 DB 現值供 client merge
        persisted = get_cop_entity("u2")
        assert persisted["callsign"] is None  # patch 完全沒落地
        assert persisted["version_clock"] == 1
        assert persisted["updated_by"] is None

    def test_concurrent_one_wins_one_loses(self, tmp_db):
        """CAS 核心：兩個 writer 都拿著 version_clock=1，只有先到的勝。"""
        _insert("u3")  # version_clock=1
        first = update_cop_entity_cas("u3", 1, {"remarks": "A"}, actor="a")
        second = update_cop_entity_cas("u3", 1, {"remarks": "B"}, actor="b")
        assert first["status"] == "ok"  # 先到者勝 → version=2
        assert second["status"] == "conflict"  # 後到者持 stale version=1 → 敗
        # B 的寫入被擋下，DB 留 A
        assert get_cop_entity("u3")["remarks"] == "A"

    def test_notfound(self, tmp_db):
        res = update_cop_entity_cas("ghost-uid", 1, {"callsign": "X"}, actor="a")
        assert res["status"] == "notfound"
        assert res["entity"] is None

    @pytest.mark.parametrize("bad_col", ["uid", "version_clock", "updated_by", "updated_at", "received_at"])
    def test_rejects_protected_cols(self, tmp_db, bad_col):
        _insert("u4")
        with pytest.raises(ValueError, match="受保護"):
            update_cop_entity_cas("u4", 1, {bad_col: "x"}, actor="a")

    def test_rejects_unknown_col(self, tmp_db):
        _insert("u5")
        with pytest.raises(ValueError, match="未知欄位"):
            update_cop_entity_cas("u5", 1, {"drop_table": "x"}, actor="a")

    def test_rejects_empty_patch(self, tmp_db):
        _insert("u6")
        with pytest.raises(ValueError, match="不可為空"):
            update_cop_entity_cas("u6", 1, {}, actor="a")

    def test_json_columns_roundtrip(self, tmp_db):
        _insert("u7")
        res = update_cop_entity_cas(
            "u7",
            1,
            {"visible_to": ["command", "forward"], "attributes": {"k": "v"}},
            actor="a",
        )
        assert res["status"] == "ok"
        ent = res["entity"]
        assert ent["visible_to"] == ["command", "forward"]  # decode 回 list
        assert ent["attributes"] == {"k": "v"}  # decode 回 dict


# ── 3. delete_cop_entity（TAK soft-delete）───────────────────────────────────


class TestSoftDelete:
    def test_soft_delete_sets_stale_and_bumps(self, tmp_db):
        _insert("d1")  # version_clock=1, stale=2099
        res = delete_cop_entity("d1", 1, actor="cmdr")
        assert res["status"] == "ok"
        assert res["entity"]["version_clock"] == 2
        # list 預設過濾 stale → 不再出現
        assert "d1" not in {e["uid"] for e in list_cop_entities()}
        # 但 row 仍在（soft-delete，可 audit / 回放）
        assert get_cop_entity("d1") is not None
        assert "d1" in {e["uid"] for e in list_cop_entities(include_stale=True)}

    def test_soft_delete_conflict_does_not_delete(self, tmp_db):
        _insert("d2")
        res = delete_cop_entity("d2", 99, actor="x")  # 版本對不上
        assert res["status"] == "conflict"
        # 沒被刪：仍在預設 list 內
        assert "d2" in {e["uid"] for e in list_cop_entities()}

    def test_soft_delete_notfound(self, tmp_db):
        res = delete_cop_entity("ghost", 1, actor="x")
        assert res["status"] == "notfound"
