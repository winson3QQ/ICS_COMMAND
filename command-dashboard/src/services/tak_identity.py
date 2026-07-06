# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""services/tak_identity.py — ICS 的 TAK 身分模型 SoT（#507 縫A「兩面一軸」）。

ICS 以多張 infra 憑證連 TAK，各證的 **TAK group 歸屬**決定它讀得到哪些 faction 的資料。
本模組是「**消費半身**」的宣告式 SoT（進 git）：明寫每張 ICS 自身 infra 證**應在**哪些 group，
開機斷言 / 面板據此**對帳** TAK 實際狀態、抓漂移。與「權威半身」（`faction_service` 替現場
裝置分群）共用同一根 faction/group 軸（blue/red/neutral）。

**為何 ICS 自身證要在「全群」**：ICS 是指揮站，須跨陣營讀取（上帝視角）；faction 邊界由
**ICS 這側**施加（per-session 可見度、`routers/tak.py` for-entity 綁 entity 可見度），非靠
TAK group 對 ICS 分艙。這是 ICS 對它替現場裝置施加的「單群隔離」的**權威豁免**（兩面不對稱）。

**背景（#507 reality-check，2026-07-06）**：`ics-marti-read` 過去不在任何 group（不在
UserAuthenticationFile）→ 只讀 public → 現場照片（blue 群）404。修法＝把 read/write 註冊成
managed user + 三群（比照 ics-cot）。此宣告即該修法的 SoT。

**紅線**：infra 證群歸屬**照宣告對帳、不手改**（守 server-authoritative）；ics-* 證絕不 deregister
（見 `tak_user_enroll` / registrar `is_infra_user`）。
"""

from __future__ import annotations

# faction 三群（ICS 全群豁免 = 這三個都在）。__ANON__ 不列入宣告（隔離破口，見 #404）。
ALL_FACTION_GROUPS: frozenset[str] = frozenset({"blue", "red", "neutral"})


# callsign(=cert CN) → {groups: 宣告應在的群, plane: 資料平面, streams: 是否訂閱 :8089 串流,
#                        cert_env: 該證 PEM 路徑的 config 屬性名（供編排算 fingerprint 註冊未在名冊的證）}
#
# streams=True 的證若落 __ANON__ 會洩漏串流資料（#404 隔離破口）；streams=False（REST-only）
# 的證落 __ANON__ 無串流洩漏（良性），但仍可能因缺群而讀不到 group 內的檔（#507 要修的）。
ICS_INFRA_IDENTITY: dict[str, dict] = {
    "ics-cot": {
        "groups": ALL_FACTION_GROUPS,
        "plane": "streaming",
        "streams": True,
        "cert_env": "TAK_CLIENT_CERT",
    },
    "ics-marti-read": {
        "groups": ALL_FACTION_GROUPS,
        "plane": "rest-read",
        "streams": False,
        "cert_env": "TAK_MARTI_READ_CERT",
    },
    "ics-marti-write": {
        "groups": ALL_FACTION_GROUPS,
        "plane": "rest-write",
        "streams": False,
        "cert_env": "TAK_MARTI_WRITE_CERT",
    },
    # admin＝純管理面（update-groups / user-management），不讀資料檔、不訂閱串流 → 不需 faction 群；
    # 落 __ANON__ 良性（REST-only）。宣告空群 = 「不預期在任何 faction 群」。
    "ics-tak-admin": {
        "groups": frozenset(),
        "plane": "rest-admin",
        "streams": False,
        "cert_env": "TAK_MARTI_ADMIN_CERT",
    },
}


def infra_callsigns() -> frozenset[str]:
    """所有 ICS 自身 infra 證的 callsign（=cert CN）。取代散落的硬編 `_TAK_INFRA_USERS`。"""
    return frozenset(ICS_INFRA_IDENTITY)


def is_infra(callsign: str | None) -> bool:
    """是否為 ICS 保留 infra 身分（不可發/撤/移除）。大小寫不敏感。"""
    return (callsign or "").strip().lower() in ICS_INFRA_IDENTITY


def is_rest_only(callsign: str | None) -> bool:
    """是否為 REST-only infra（不訂閱 :8089 串流）→ 其 __ANON__ 對串流良性（#404）。"""
    ent = ICS_INFRA_IDENTITY.get((callsign or "").strip().lower())
    return bool(ent) and not ent["streams"]


def required_groups(callsign: str | None) -> frozenset[str] | None:
    """宣告：此 infra 證**應在**哪些 group。非 infra 證回 None（不受本 SoT 管）。"""
    ent = ICS_INFRA_IDENTITY.get((callsign or "").strip().lower())
    return ent["groups"] if ent else None


def cert_env(callsign: str | None) -> str | None:
    """此 infra 證 PEM 路徑的 config 屬性名（如 `TAK_MARTI_READ_CERT`）。編排端 `getattr(config, …)`
    解析路徑 → 算 fingerprint 註冊未在 TAK 名冊的證。非 infra 回 None。"""
    ent = ICS_INFRA_IDENTITY.get((callsign or "").strip().lower())
    return ent["cert_env"] if ent else None


# 漂移狀態：ok=符合宣告；missing=缺應在的群；extra=多了不該有的 faction 群；
# unregistered=宣告要有群但 TAK 完全查無此 user（reconcile 沒列到）；
# unknown=舊式 registrar 未回群清單（groups=None，不誤判）。
_DRIFT_OK = "ok"
_DRIFT_MISSING = "missing"
_DRIFT_EXTRA = "extra"
_DRIFT_UNREGISTERED = "unregistered"
_DRIFT_UNKNOWN = "unknown"


def check_drift(reconcile_users: list[dict]) -> list[dict]:
    """對帳「宣告 vs TAK 實際」，回每張 infra 證的漂移記錄。

    `reconcile_users`＝`tak_user_enroll.reconcile_tak_users()` 的 users（[{callsign,fingerprint,groups}]，
    groups 可能為 None＝舊式未回群）。只涵蓋 ICS 自身 infra 證（現場裝置不在此對帳）。

    回 [{callsign, plane, declared: sorted list, actual: sorted list|None, missing, extra,
        status, fingerprint}]，status ∈ ok/missing/extra/unregistered/unknown。
    """
    by_cn: dict[str, dict] = {}
    for u in reconcile_users or []:
        cn = (u.get("callsign") or "").strip().lower()
        if cn:
            by_cn[cn] = u

    out: list[dict] = []
    for cn, ent in ICS_INFRA_IDENTITY.items():
        declared: frozenset[str] = ent["groups"]
        row = by_cn.get(cn)
        actual_groups = row.get("groups") if row else None  # None=未列到 or 舊式未回群
        fp = (row.get("fingerprint") if row else "") or ""

        if row is None:
            # TAK 完全查無此 managed user。宣告要有群 → 未註冊漂移；宣告空群（admin）→ 視為 ok。
            status = _DRIFT_UNREGISTERED if declared else _DRIFT_OK
            out.append(
                {
                    "callsign": cn,
                    "plane": ent["plane"],
                    "declared": sorted(declared),
                    "actual": None,
                    "missing": sorted(declared),
                    "extra": [],
                    "status": status,
                    "fingerprint": fp,
                }
            )
            continue

        if actual_groups is None:
            # 舊式 registrar 未回群清單 → 不誤判（#404 相容）。
            out.append(
                {
                    "callsign": cn,
                    "plane": ent["plane"],
                    "declared": sorted(declared),
                    "actual": None,
                    "missing": [],
                    "extra": [],
                    "status": _DRIFT_UNKNOWN,
                    "fingerprint": fp,
                }
            )
            continue

        actual = {g for g in actual_groups if g and g != "__ANON__"}
        missing = declared - actual
        # 「extra」只算不該有的 **faction** 群（blue/red/neutral 之外的自訂群不在本 SoT 管轄，不誤報）。
        extra = (actual - declared) & ALL_FACTION_GROUPS
        if missing:
            status = _DRIFT_MISSING
        elif extra:
            status = _DRIFT_EXTRA
        else:
            status = _DRIFT_OK
        out.append(
            {
                "callsign": cn,
                "plane": ent["plane"],
                "declared": sorted(declared),
                "actual": sorted(actual),
                "missing": sorted(missing),
                "extra": sorted(extra),
                "status": status,
                "fingerprint": fp,
            }
        )
    return out


def has_drift(drift_rows: list[dict]) -> bool:
    """任一 infra 證偏離宣告（missing/extra/unregistered）→ True。unknown/ok 不算漂移。"""
    return any(r["status"] in (_DRIFT_MISSING, _DRIFT_EXTRA, _DRIFT_UNREGISTERED) for r in drift_rows)
