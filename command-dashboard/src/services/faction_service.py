# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""services/faction_service.py — #343 紅藍隔離 admin 分類層（PR-5）；#344 改綁 cert CN。

admin 對「連線 client（裝置）」指派陣營，以**穩定的 cert CN（= TAK username）** 為鍵（#344；舊版綁易變
的裝置 uid，重裝/重 enroll 換 uid 就丟分類）：
- list_clients：列「**發證後且在線**」的 client——來源 = `subscriptions/all`（在線視圖）∩ `tak_device_certs`
  （active 發證），per-CN 去重；順帶把 {uid: CN} 寫進 client_identity 供 ingest 翻譯。
- classify：驗場存在 → upsert client_faction（CN 鍵）→ 經 client_identity 解 CN→uids 重解析名下 auto
  entity → resync 廣播 → 同步 TAK 群（username=CN 直傳）。
- override_entity：對單一 entity 手動點陣營（iTAK 繪圖等無 producer 物件）→ resync。

歸屬鏈核心 = cop_service.resolve_client_key_from_parts（解 producer uid，與 ingest 同一套）；uid→CN 翻譯
見 cop_service._resolve_faction + repositories.client_identity_repo。
"""

from __future__ import annotations

from repositories import client_faction_repo, client_identity_repo, cop_entity_repo, tak_device_cert_repo
from repositories._helpers import NULL_SCOPE
from services import cop_service, exercise_service, tak_group_sync
from services.realtime_hub import cop_hub

# 重解析 / 列舉時撈該場 tak entity 的上限（場域 10–30 裝置、數百 entity；含 stale/已刪以求完整盤點）。
_SCAN_LIMIT = 10000


def _scope(exercise_id: int | None):
    """exercise_id（admin 指定場）→ repo scope：int → 該場 / None → NULL_SCOPE（實戰池）。"""
    return exercise_id if exercise_id is not None else NULL_SCOPE


def _producer_entities(exercise_id: int | None) -> list[dict]:
    """該場全部 tak 來源 entity（含 stale，求盤點完整）。"""
    return cop_entity_repo.list_cop_entities(
        source="tak", exercise_id=_scope(exercise_id), include_stale=True, limit=_SCAN_LIMIT
    )


async def list_clients(exercise_id: int | None) -> list[dict]:
    """#344：列「**發證後且有連線上**」的 TAK client，以穩定的 **cert CN（= TAK username）** 為鍵。

    取代舊「cop_entities 聚合、uid 鍵、含已離線」清單（裝置重裝/重 enroll 換 uid 就丟分類、且殭屍堆積）。
    來源 = `subscriptions/all`（可靠在線視圖）的 {uid: username} ∩ `tak_device_certs`（active 發證集合），
    去重成 per-CN。順帶把 {uid: username} 寫進 client_identity（供 ingest 同步把 uid 翻 CN 著色）。

    回 [{client_key=CN, callsign=live角色名, cn=CN, last_seen, online=True, faction, classified}]，依 CN 排序。
    **鍵（client_key）= cert CN（穩定身分）**；**顯示（callsign）= 裝置 self-SA 的 live in-app callsign（這場
    扮的角色，使用者可隨手改）**——指揮認的是角色名，但分類綁 CN（改 callsign/換 uid 都不丟）。faction 為
    **per-exercise**（同一 CN 跨場可不同陣營，由 exercise_id scope 決定）。
    best-effort：TAK 未配置 / 離線 → uid2cn={} → 回 []（前端顯空狀態，指引去開 TAK）。
    """
    uid2cn = await tak_group_sync.online_uid_to_username()  # {uid: username(CN)}；在線視圖
    client_identity_repo.upsert_many(uid2cn)  # 持久化供 ingest 翻譯（離線裝置的舊對照保留）
    issued = {c["callsign"] for c in tak_device_cert_repo.list_device_certs() if c.get("status") == "active"}
    fmap = client_faction_repo.get_faction_map(exercise_id)
    out, seen = [], set()
    for uid, cn in uid2cn.items():
        if cn not in issued or cn in seen:  # 只列發證後（active 證）的；per-CN 去重（取首見 uid 的角色名）
            continue
        seen.add(cn)
        ent = cop_entity_repo.get_cop_entity(uid)  # 裝置 self-SA（uid==裝置uid）→ live in-app callsign（角色）
        live_callsign = (ent.get("callsign") if ent else None) or cn
        out.append(
            {
                "client_key": cn,  # 穩定鍵 = cert CN（classify 綁此）
                "callsign": live_callsign,  # 顯示 = live 角色名（fallback CN）
                "cn": cn,  # 穩定身分（前端與角色名並顯，便於辨識「誰扮這角色」）
                "last_seen": "",  # 在線視圖即時，無需 last_seen 時效近似
                "online": True,  # 來源即在線訂閱
                "faction": fmap.get(cn),
                "classified": cn in fmap,
            }
        )
    out.sort(key=lambda d: d["cn"])
    return out


def _reresolve_producer(exercise_id: int | None, client_key: str, faction: str | None) -> int:
    """#344：把該場名下「歸屬到此 CN 任一裝置 uid」的 auto entity faction 改為新值。回更新筆數。

    client_key 現為 **cert CN**：先經 client_identity 解出該 CN 的所有裝置 uid（含換過的舊 uid），再撈場內
    tak entity、篩 producer uid 命中該集合者批次 UPDATE（repo 端只動 faction_source='auto'，保護 manual override）。
    歸屬鏈在 Python（creator/link JSON 巢狀，SQL 解不了）。CN 無對應 uid（未在線過）→ 0（待裝置連上、
    面板載入寫入 client_identity 後即可著色）。
    """
    device_uids = set(client_identity_repo.uids_for_username(client_key))
    if not device_uids:
        return 0
    uids = [
        e["uid"]
        for e in _producer_entities(exercise_id)
        if cop_service.resolve_client_key_from_parts(e["uid"], e.get("attributes") or {}) in device_uids
    ]
    return cop_entity_repo.set_faction_for_uids(uids, faction)


async def classify(exercise_id: int | None, client_key: str, faction: str, callsign: str | None, operator: str) -> dict:
    """指派 / 改 client 陣營。驗場存在（D）→ upsert → 重解析名下 auto entity → resync 廣播。

    回 {client_key, faction, exercise_id, reresolved, tak_group}（reresolved=受影響 entity 數；
    tak_group=TAK 現場層群同步結果，#344）。
    """
    if exercise_id is not None and not exercise_service.get(exercise_id):
        from fastapi import HTTPException

        raise HTTPException(404, f"演練不存在：{exercise_id}")
    client_faction_repo.upsert_faction(exercise_id, client_key, faction, callsign, operator)
    n = _reresolve_producer(exercise_id, client_key, faction)
    # resync：commander 連線重新 GET /api/cop/*（已 faction 過濾）→ 視圖即時增/減。沿用 reset 同管線。
    await cop_hub.broadcast_all({"op": "resync"})
    # #344：同步到 TAK 現場層 group（一個分類動作、兩層隔離）。client_key 即 cert CN(=TAK username) →
    # 直接傳 username，免再經 subscriptions 解 uid→username。best-effort——未配置 / device 離線 / TAK 錯
    # 皆不 raise，不拖垮 ICS 視圖層分類（#343）。
    tak_group = await tak_group_sync.sync_client_faction(client_key, faction, username=client_key)
    return {
        "client_key": client_key,
        "faction": faction,
        "exercise_id": exercise_id,
        "reresolved": n,
        "tak_group": tak_group,
    }


async def override_entity(uid: str, faction: str, operator: str) -> dict:
    """對單一 entity 手動點陣營（無 producer 可歸屬者，如 iTAK 繪圖）→ resync。回更新後 entity。"""
    from repositories._helpers import audit

    row = cop_entity_repo.set_entity_faction_manual(uid, faction)
    if row is None:
        from fastapi import HTTPException

        raise HTTPException(404, f"entity 不存在：{uid}")
    audit(operator, None, "cop_entity_faction_override", "cop_entities", uid, {"faction": faction})
    await cop_hub.broadcast_all({"op": "resync"})
    return row
