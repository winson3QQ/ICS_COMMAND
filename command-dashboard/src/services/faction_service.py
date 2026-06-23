"""services/faction_service.py — #343 紅藍隔離 admin 分類層（PR-5）。

admin 對「連線 client（裝置）」指派陣營：
- list_clients：列本場觀測到的 producer（裝置）+ 目前分類（接 cop_entities 解 client_key 聚合，
  **非** team_color 聚合——隊伍≠faction，見設計 §8.1）。
- classify：驗場存在 → upsert client_faction → 重解析該 producer 名下 auto entity → resync 廣播。
- override_entity：對單一 entity 手動點陣營（iTAK 繪圖等無 producer 物件）→ resync。

歸屬鏈核心 = cop_service.resolve_client_key_from_parts（與 ingest 同一套，避免漂移）。
"""

from __future__ import annotations

from repositories import client_faction_repo, cop_entity_repo
from repositories._helpers import NULL_SCOPE
from services import cop_service, exercise_service
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


def list_clients(exercise_id: int | None) -> list[dict]:
    """列本場觀測到的連線 client（producer）+ 目前分類。

    回 [{client_key, callsign, last_seen, faction, classified}]，未分類者 faction=None/classified=False，
    依 callsign/client_key 排序。

    callsign（顯示）優先取**裝置 self-SA**（uid == client_key，裝置自身態勢，呼號即裝置呼號），
    而非該 producer 名下最新一筆——否則裝置標一個 marker 後，marker 較新會把裝置呼號蓋成 marker 名
    （dogfood 實證：ATAK 標 marker 後分類面板顯示成「N.23.…」而非「3QQ-atak」）。無 self-SA 時
    才 fallback 到最新一筆 callsign。last_seen 仍取名下任一最新（活動時間語意不變）。
    """
    fmap = client_faction_repo.get_faction_map(exercise_id)
    agg: dict[str, dict] = {}
    for e in _producer_entities(exercise_id):
        ck = cop_service.resolve_client_key_from_parts(e["uid"], e.get("attributes") or {})
        seen = e.get("received_at") or ""
        is_self = e["uid"] == ck  # 裝置 self-SA：自身 uid 即 client_key（marker 的 uid 不同）
        d = agg.setdefault(ck, {"client_key": ck, "last_seen": "", "_self_seen": "", "_any_seen": ""})
        if seen > d["last_seen"]:
            d["last_seen"] = seen
        # 呼號優先序：最新的 self-SA > 最新的非 self（marker fallback）
        if is_self and seen >= d["_self_seen"]:
            d["_self_seen"], d["_self_cs"] = seen, e.get("callsign")
        elif not is_self and seen >= d["_any_seen"]:
            d["_any_seen"], d["_any_cs"] = seen, e.get("callsign")
    out = []
    for ck, d in agg.items():
        callsign = d.get("_self_cs") or d.get("_any_cs")
        out.append(
            {
                "client_key": ck,
                "callsign": callsign,
                "last_seen": d["last_seen"],
                "faction": fmap.get(ck),
                "classified": ck in fmap,
            }
        )
    out.sort(key=lambda d: (d["callsign"] or d["client_key"]))
    return out


def _reresolve_producer(exercise_id: int | None, client_key: str, faction: str | None) -> int:
    """把該場名下「歸屬到 client_key」的 auto entity faction 改為新值。回更新筆數。

    歸屬鏈在 Python（creator/link JSON 巢狀，SQL 解不了）→ 撈場內 tak entity、篩 client_key 命中者、
    批次 UPDATE（repo 端只動 faction_source='auto'，保護 manual override）。
    """
    uids = [
        e["uid"]
        for e in _producer_entities(exercise_id)
        if cop_service.resolve_client_key_from_parts(e["uid"], e.get("attributes") or {}) == client_key
    ]
    return cop_entity_repo.set_faction_for_uids(uids, faction)


async def classify(exercise_id: int | None, client_key: str, faction: str, callsign: str | None, operator: str) -> dict:
    """指派 / 改 client 陣營。驗場存在（D）→ upsert → 重解析名下 auto entity → resync 廣播。

    回 {client_key, faction, exercise_id, reresolved}（reresolved=受影響 entity 數）。
    """
    if exercise_id is not None and not exercise_service.get(exercise_id):
        from fastapi import HTTPException

        raise HTTPException(404, f"演練不存在：{exercise_id}")
    client_faction_repo.upsert_faction(exercise_id, client_key, faction, callsign, operator)
    n = _reresolve_producer(exercise_id, client_key, faction)
    # resync：commander 連線重新 GET /api/cop/*（已 faction 過濾）→ 視圖即時增/減。沿用 reset 同管線。
    await cop_hub.broadcast_all({"op": "resync"})
    return {"client_key": client_key, "faction": faction, "exercise_id": exercise_id, "reresolved": n}


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
