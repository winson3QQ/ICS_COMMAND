"""services/faction_service.py — #343 紅藍隔離 admin 分類層（PR-5）。

admin 對「連線 client（裝置）」指派陣營：
- list_clients：列本場觀測到的 producer（裝置）+ 目前分類（接 cop_entities 解 client_key 聚合，
  **非** team_color 聚合——隊伍≠faction，見設計 §8.1）。
- classify：驗場存在 → upsert client_faction → 重解析該 producer 名下 auto entity → resync 廣播。
- override_entity：對單一 entity 手動點陣營（iTAK 繪圖等無 producer 物件）→ resync。

歸屬鏈核心 = cop_service.resolve_client_key_from_parts（與 ingest 同一套，避免漂移）。
"""

from __future__ import annotations

from datetime import UTC, datetime

from repositories import client_faction_repo, cop_entity_repo
from repositories._helpers import NULL_SCOPE
from services import cop_service, exercise_service, tak_group_sync
from services.realtime_hub import cop_hub

# 重解析 / 列舉時撈該場 tak entity 的上限（場域 10–30 裝置、數百 entity；含 stale/已刪以求完整盤點）。
_SCAN_LIMIT = 10000

# #389：以 last_seen 時效近似「在線」（heuristic，非真 TCP 連線態——靜止裝置可能較久未送 SA，
# 故取較寬的 5 分窗口；精準在線狀態待 (a) 交叉 live TAK presence clientEndPoints）。
ONLINE_WINDOW_SEC = 300


def _is_online(last_seen: str) -> bool:
    if not last_seen:
        return False
    try:
        ts = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
        if ts.tzinfo is None:  # 容錯：無時區的 last_seen 視為 UTC（避免 aware-naive 相減 TypeError → 500）
            ts = ts.replace(tzinfo=UTC)
        return (datetime.now(UTC) - ts).total_seconds() <= ONLINE_WINDOW_SEC
    except (ValueError, TypeError):
        return False


def _scope(exercise_id: int | None):
    """exercise_id（admin 指定場）→ repo scope：int → 該場 / None → NULL_SCOPE（實戰池）。"""
    return exercise_id if exercise_id is not None else NULL_SCOPE


def _producer_entities(exercise_id: int | None) -> list[dict]:
    """該場全部 tak 來源 entity（含 stale，求盤點完整）。"""
    return cop_entity_repo.list_cop_entities(
        source="tak", exercise_id=_scope(exercise_id), include_stale=True, limit=_SCAN_LIMIT
    )


def list_clients(exercise_id: int | None) -> list[dict]:
    """列本場**觀測到的 producer 裝置（含已離線）** + 目前分類（#389：非「即時連線」清單——
    含 stale，故補 online 旗標近似在線/離線）。

    回 [{client_key, callsign, last_seen, online, faction, classified}]，未分類者 faction=None/classified=False，
    依 callsign/client_key 排序。

    callsign（顯示）優先取**裝置 self-SA**（uid == client_key，裝置自身態勢，呼號即裝置呼號），
    而非該 producer 名下最新一筆——否則裝置標一個 marker 後，marker 較新會把裝置呼號蓋成 marker 名
    （dogfood 實證：ATAK 標 marker 後分類面板顯示成「N.23.…」而非「3QQ-atak」）。無 self-SA 時
    才 fallback 到最新一筆 callsign。

    last_seen/online（#389 修正）取 **updated_at（最後活動）** 而非 received_at——後者是「首見」時間、
    再廣播不更新（dogfood：live 裝置 received_at 停在昨天、online 誤判離線）。updated_at 每次更新都 bump、
    且不會被設成未來（不像 archived marker 的 stale=2099），故「max(updated_at)」即裝置最後活動的可靠近似。
    """
    fmap = client_faction_repo.get_faction_map(exercise_id)
    agg: dict[str, dict] = {}
    for e in _producer_entities(exercise_id):
        ck = cop_service.resolve_client_key_from_parts(e["uid"], e.get("attributes") or {})
        seen = e.get("received_at") or ""  # 首見（呼號排序用，穩定）
        upd = e.get("updated_at") or seen  # 最後活動（last_seen/online 用）
        is_self = e["uid"] == ck  # 裝置 self-SA：自身 uid 即 client_key（marker 的 uid 不同）
        d = agg.setdefault(ck, {"client_key": ck, "last_seen": "", "_self_seen": "", "_any_seen": ""})
        if upd > d["last_seen"]:
            d["last_seen"] = upd
        # 呼號優先序：最新的 self-SA > 最新的非 self（marker fallback）
        if is_self and seen >= d["_self_seen"]:
            d["_self_seen"], d["_self_cs"] = seen, e.get("callsign")
        elif not is_self and seen >= d["_any_seen"]:
            d["_any_seen"], d["_any_cs"] = seen, e.get("callsign")
    out = []
    for ck, d in agg.items():
        # self-SA 存在就用其呼號（即使為 None → 前端 fallback 顯 uid），**不退回 marker 名**；
        # 無 self-SA 才用 marker（_any_cs）。用「key 是否存在」判定 self-SA 出現過（值可為 None）。
        callsign = d["_self_cs"] if "_self_cs" in d else d.get("_any_cs")
        out.append(
            {
                "client_key": ck,
                "callsign": callsign,
                "last_seen": d["last_seen"],
                "online": _is_online(d["last_seen"]),  # #389：在線指示（last_seen=updated_at 最後活動，時效近似）
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
    # #344：同步到 TAK 現場層 group（一個分類動作、兩層隔離）。best-effort——未配置 admin cert /
    # device 離線 / TAK 錯皆不 raise，不拖垮 ICS 視圖層分類（#343）。
    tak_group = await tak_group_sync.sync_client_faction(client_key, faction)
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
