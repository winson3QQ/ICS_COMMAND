# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
tests/unit/test_tak_identity.py — #507 縫A：ICS TAK 身分模型 SoT（兩面一軸·消費半身）

鎖住的不變式：
- infra 清單 = {ics-cot, ics-marti-read, ics-marti-write, ics-tak-admin}；is_infra 大小寫不敏感、非 infra False
- is_rest_only：ics-cot（streams）= False；read/write/admin（REST-only）= True
- required_groups：cot/read/write = 全群；admin = 空；非 infra = None
- check_drift：ok / missing / extra / unregistered / unknown 五態正確；admin 宣告空群不誤報
- has_drift：missing/extra/unregistered 才算漂移；ok/unknown 不算
"""

from services import tak_identity


def _row(cn, groups):
    return {"callsign": cn, "fingerprint": "AA:BB", "groups": groups}


def _find(rows, cn):
    return next(r for r in rows if r["callsign"] == cn)


class TestInfraDeclaration:
    def test_infra_callsigns_是四張(self):
        assert tak_identity.infra_callsigns() == frozenset(
            {"ics-cot", "ics-marti-read", "ics-marti-write", "ics-tak-admin"}
        )

    def test_is_infra_大小寫不敏感_且非infra_False(self):
        assert tak_identity.is_infra("ics-cot")
        assert tak_identity.is_infra("ICS-Marti-Read")  # 大小寫不敏感
        assert tak_identity.is_infra("  ics-marti-write  ")  # strip
        assert not tak_identity.is_infra("3QQ-atak")  # 現場裝置非 infra
        assert not tak_identity.is_infra(None)
        assert not tak_identity.is_infra("")

    def test_is_rest_only_只有不串流的證(self):
        # ics-cot 訂閱 :8089 串流 → 非 rest-only（其 __ANON__ 是真破口）
        assert not tak_identity.is_rest_only("ics-cot")
        # read/write/admin 皆 REST-only → rest-only（__ANON__ 對串流良性）
        assert tak_identity.is_rest_only("ics-marti-read")
        assert tak_identity.is_rest_only("ics-marti-write")
        assert tak_identity.is_rest_only("ics-tak-admin")
        assert not tak_identity.is_rest_only("3QQ-atak")

    def test_required_groups_宣告(self):
        allg = frozenset({"blue", "red", "neutral"})
        assert tak_identity.required_groups("ics-cot") == allg
        assert tak_identity.required_groups("ics-marti-read") == allg
        assert tak_identity.required_groups("ics-marti-write") == allg
        assert tak_identity.required_groups("ics-tak-admin") == frozenset()  # 純管理、不需 faction 群
        assert tak_identity.required_groups("3QQ-atak") is None  # 非 infra 不受 SoT 管


class TestCheckDrift:
    def test_ok_符合宣告(self):
        rows = tak_identity.check_drift([_row("ics-marti-read", ["blue", "red", "neutral"])])
        r = _find(rows, "ics-marti-read")
        assert r["status"] == "ok"
        assert r["missing"] == [] and r["extra"] == []
        assert r["actual"] == ["blue", "neutral", "red"]

    def test_missing_缺群(self):
        rows = tak_identity.check_drift([_row("ics-marti-read", ["blue"])])
        r = _find(rows, "ics-marti-read")
        assert r["status"] == "missing"
        assert r["missing"] == ["neutral", "red"]

    def test___ANON___不算實際群_視為缺(self):
        # 只落 __ANON__ = 沒有任何 faction 群 → missing 全部（正是 #507 要修的 read cert 現況）
        rows = tak_identity.check_drift([_row("ics-marti-read", ["__ANON__"])])
        r = _find(rows, "ics-marti-read")
        assert r["status"] == "missing"
        assert r["missing"] == ["blue", "neutral", "red"]
        assert r["actual"] == []

    def test_extra_多了不該有的faction群(self):
        # admin 宣告空群，卻在 blue → extra
        rows = tak_identity.check_drift([_row("ics-tak-admin", ["blue"])])
        r = _find(rows, "ics-tak-admin")
        assert r["status"] == "extra"
        assert r["extra"] == ["blue"]

    def test_自訂非faction群_不誤報extra(self):
        # read cert 在全群 + 一個自訂群 "logistics" → 自訂群不在 SoT 管轄，不算 extra
        rows = tak_identity.check_drift([_row("ics-marti-read", ["blue", "red", "neutral", "logistics"])])
        r = _find(rows, "ics-marti-read")
        assert r["status"] == "ok"
        assert r["extra"] == []

    def test_unregistered_TAK查無此user(self):
        # reconcile 沒列到 read cert（未註冊）→ 宣告要有群 → unregistered
        rows = tak_identity.check_drift([_row("ics-cot", ["blue", "red", "neutral"])])
        r = _find(rows, "ics-marti-read")
        assert r["status"] == "unregistered"
        assert r["actual"] is None

    def test_admin_未註冊_宣告空群_視為ok(self):
        # admin 宣告空群 → 即使 TAK 查無也不算漂移（不需 faction 群）
        rows = tak_identity.check_drift([])
        r = _find(rows, "ics-tak-admin")
        assert r["status"] == "ok"

    def test_unknown_舊式registrar未回群(self):
        # groups=None（舊式兩欄 registrar）→ 不誤判
        rows = tak_identity.check_drift([_row("ics-cot", None)])
        r = _find(rows, "ics-cot")
        assert r["status"] == "unknown"

    def test_has_drift(self):
        assert tak_identity.has_drift(tak_identity.check_drift([_row("ics-marti-read", ["blue"])]))  # missing
        # 全部符合（four certs 都在對的狀態）→ 無漂移
        clean = tak_identity.check_drift(
            [
                _row("ics-cot", ["blue", "red", "neutral"]),
                _row("ics-marti-read", ["blue", "red", "neutral"]),
                _row("ics-marti-write", ["blue", "red", "neutral"]),
                # admin 宣告空群、不列即 ok
            ]
        )
        assert not tak_identity.has_drift(clean)

    def test_unknown_不算漂移(self):
        rows = tak_identity.check_drift([_row("ics-cot", None)])
        # 其餘三張未列 → read/write=unregistered（算漂移），但單看 unknown 那張不算
        assert _find(rows, "ics-cot")["status"] == "unknown"
