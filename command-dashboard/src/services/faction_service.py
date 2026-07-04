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

# #475：未分隊在線裝置計數快取——背景 poller（一次 subscriptions/all poll）更新，端點讀快取（不加 TAK 呼叫）
# → 演習 chip / 面板顯「⚠ N 台未分隊在線」提示白隊去分類。未分隊 fail-closed（藏）不變，此為提醒非強制。
_UNCLASSIFIED_ONLINE: dict = {"count": 0, "exercise_id": None}


def compute_unclassified_online(subs: list[dict], exercise_id: int | None) -> int:
    """數「在線 + 已發證 + 未分類（該場）」的裝置。online=subs 的 username 集；issued=active 發證的
    callsign(=CN)；classified=該場 client_faction。回未分類數。"""
    online = {s["username"] for s in subs if s.get("username")}
    issued = {c["callsign"] for c in tak_device_cert_repo.list_device_certs() if c.get("status") == "active"}
    classified = set(client_faction_repo.get_faction_map(exercise_id).keys())
    return len(online & issued - classified)


def refresh_unclassified_count(subs: list[dict]) -> int:
    """#475：背景 poller 呼叫——依當前 active 場算未分隊在線數、寫快取。回數。best-effort（poller 已包 try）。"""
    ex_id = exercise_service.current_exercise_id()
    n = compute_unclassified_online(subs, ex_id)
    _UNCLASSIFIED_ONLINE["count"] = n
    _UNCLASSIFIED_ONLINE["exercise_id"] = ex_id
    return n


def get_unclassified_online() -> dict:
    """#475：讀未分隊在線快取（端點用，不打 TAK）。回 {count, exercise_id}。"""
    return dict(_UNCLASSIFIED_ONLINE)


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
    # #477b：改用 list_online_subscriptions（含每台**實際 TAK 群**）——同一 subscriptions/all 視圖，
    # 順帶拿 groups 做「分類 vs 實際群」比對，讓白隊看得到隔離有沒有真的生效。uid→CN 對照從 subs 派生
    # （等同舊 online_uid_to_username，同資料源）。
    subs = await tak_group_sync.list_online_subscriptions()
    uid2cn = {s["client_uid"]: s["username"] for s in subs if s.get("client_uid") and s.get("username")}
    cn_groups = {s["username"]: (s.get("groups") or []) for s in subs if s.get("username")}
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
        faction = fmap.get(cn)
        groups = cn_groups.get(cn, [])
        # #477b isolated：已分類者才有隔離期望——「只在自己陣營群」= 正確隔離（sync_client_faction 做
        # exclusive replace，正確者實際群恰為 [群名]）。非此（含 __ANON__/別群/多群）= 未隔離 → 面板 ⚠。
        # 未分類 → None（無期望，不顯 ⚠）。群名經 group_name_for 映射（不硬編陣營名==群名，#477b 硬化）。
        isolated = (groups == [tak_group_sync.group_name_for(faction)]) if cn in fmap else None
        out.append(
            {
                "client_key": cn,  # 穩定鍵 = cert CN（classify 綁此）
                "callsign": live_callsign,  # 顯示 = live 角色名（fallback CN）
                "cn": cn,  # 穩定身分（前端與角色名並顯，便於辨識「誰扮這角色」）
                "last_seen": "",  # 在線視圖即時，無需 last_seen 時效近似
                "online": True,  # 來源即在線訂閱
                "faction": faction,
                "classified": cn in fmap,
                "actual_groups": groups,  # #477b：該裝置目前實際訂閱的 TAK 群（供比對顯示）
                "isolated": isolated,  # #477b：True=正確隔離 / False=分類了但實際群不符（⚠）/ None=未分類
            }
        )
    out.sort(key=lambda d: d["cn"])
    return out


def _reresolve_producer(exercise_id: int | None, client_key: str, faction: str | None) -> int:
    """#344/#473-A：把「歸屬到此 CN 任一裝置 uid」的 auto entity faction 改為新值。回更新筆數。

    client_key 現為 **cert CN**：先經 client_identity 解出該 CN 的所有裝置 uid（含換過的舊 uid），篩 producer
    uid 命中者批次 UPDATE（repo 端只動 faction_source='auto'，保護 manual override）。歸屬鏈在 Python。

    **#473-A**：entity.faction 反映「當前 active 演習」的分類（#472 共享池）→ ① 只有改到「當前 active 演習」
    的分類才影響 live entity（改歷史/非 active 場分類無 live 效果，回 0）；② 撈**全 live 池**（非按場，因
    entity 為跨場共享、多為 NULL scope）。CN 無對應 uid（未在線過）→ 0（待裝置連上、面板載入寫入後著色）。
    """
    if exercise_id != exercise_service.current_exercise_id():
        return 0  # #473-A：非當前 active 場的分類 → 不影響 live entity 可見性
    device_uids = set(client_identity_repo.uids_for_username(client_key))
    if not device_uids:
        return 0
    # #473-A：撈**全 live tak 池**（exercise_id=None＝不過濾，跨場共享）——非 _producer_entities（其 _scope(None)
    # 會退成 NULL_SCOPE 只撈常駐、漏掉綁到 active 場的 entity）。
    all_tak = cop_entity_repo.list_cop_entities(source="tak", exercise_id=None, include_stale=True, limit=_SCAN_LIMIT)
    uids = [
        e["uid"]
        for e in all_tak
        if cop_service.resolve_client_key_from_parts(e["uid"], e.get("attributes") or {}) in device_uids
    ]
    return cop_entity_repo.set_faction_for_uids(uids, faction)


def _reresolve_chats(exercise_id: int | None, client_key: str, faction: str | None) -> int:
    """#475 GeoChat 軸：把該 CN 名下裝置的既有 chats faction 重蓋（對標 _reresolve_producer 之於 entity）。
    只有改「當前 active 場」的分類才影響 live 通聯可見性（非 active 場回 0）。回影響筆數。"""
    if exercise_id != exercise_service.current_exercise_id():
        return 0
    device_uids = set(client_identity_repo.uids_for_username(client_key))
    if not device_uids:
        return 0
    from services import chat_service

    return chat_service.restamp_chats_for_client(exercise_id, device_uids, faction)


async def classify(exercise_id: int | None, client_key: str, faction: str, callsign: str | None, operator: str) -> dict:
    """指派 / 改 client 陣營。驗場存在（D）→ upsert → 重解析名下 auto entity + 既有 GeoChat → resync 廣播。

    回 {client_key, faction, exercise_id, reresolved, reresolved_chats, tak_group}（reresolved=受影響
    entity 數、reresolved_chats=受影響通聯數【#475 GeoChat 軸】；tak_group=TAK 現場層群同步結果，#344）。
    """
    if exercise_id is not None and not exercise_service.get(exercise_id):
        from fastapi import HTTPException

        raise HTTPException(404, f"演練不存在：{exercise_id}")
    client_faction_repo.upsert_faction(exercise_id, client_key, faction, callsign, operator)
    n = _reresolve_producer(exercise_id, client_key, faction)  # 地圖軸：entity faction
    n_chat = _reresolve_chats(exercise_id, client_key, faction)  # #475 GeoChat 軸：既有通聯 faction
    # resync：commander 連線重新 GET /api/cop/*（已 faction 過濾）→ 地圖 marker + 聚合視圖即時增/減；
    # 前端 resync 亦觸發 chat:resync → 通聯即時重取（#475 三軸一致）。沿用 reset 同管線。
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
        "reresolved_chats": n_chat,
        "tak_group": tak_group,
    }


# ── #477a：開場對齊分類到現場（activate/archive 呼叫）────────────────────────────
# 修「精靈平時（待命池 NULL scope）分隊、按開始記錄後進紅藍分類全變未選」——開場那刻把待命池
# 分類**繼承**進這場（各場覆寫語意保留），再由 restamp_all_factions 依這場解析、並推到 TAK 現場群。


def seed_exercise_from_baseline(exercise_id: int, operator: str) -> int:
    """開場繼承：把待命池（NULL scope）分類抄進 `exercise_id`，只填「這場尚未單獨分類」的 client。

    待命池＝全域預設名冊；各場開場時繼承之，之後可在該場單獨改（覆蓋，不回寫待命池）。回 seed 筆數。
    須在 restamp_all_factions 之前呼叫——restamp 依 current_exercise_id() 的分類解析 entity faction，
    繼承後這場才有分類可解，否則全部 fail-closed（None）＝紅藍皆藏。
    """
    baseline = client_faction_repo.list_factions(None)  # 待命池
    if not baseline:
        return 0
    already = client_faction_repo.get_faction_map(exercise_id)  # 這場已單獨分類者
    n = 0
    for row in baseline:
        ck = row["client_key"]
        if ck in already:
            continue  # 各場覆寫：這場已單獨分類 → 不抄
        # action_type=seed：開場繼承是批次動作、非真人逐台重分隊 → 不進 AAR timeline（免灌爆同一時刻）。
        client_faction_repo.upsert_faction(
            exercise_id, ck, row["faction"], row.get("callsign"), operator, action_type="client_faction_seed"
        )
        n += 1
    return n


async def sync_exercise_tak_groups(exercise_id: int | None, *, to_neutral: bool = False) -> dict:
    """把某場的分類推到 TAK 現場群（開場對齊 server 端隔離）。逐台 best-effort，個別失敗不中斷。

    to_neutral=True（收場用）→ 全部重置 neutral（現場回統一，不殘留敵我隔離）；否則推各自陣營群。
    回 {pushed, synced, failed, results}。**注意**：server 端即時，但裝置上「開場前已交換的舊標記」
    不會被 server 端刪除信號清掉（TAK client 硬限制，見 memory tak-streaming-archive-stale-vs-mission）
    → 中途改隊需請該裝置重開 app；開場前分隊則無殘影問題。
    """
    fmap = client_faction_repo.get_faction_map(exercise_id)
    results: list[dict] = []
    synced = 0
    for client_key, faction in fmap.items():
        target = "neutral" if to_neutral else faction
        # per-client 防護：sync_client_faction 已 best-effort，但任何非預期例外都不得中斷整批、
        # 更不得讓 activate/archive 500（TAK 現場同步永遠是附加動作，不擋開場/收場）。
        try:
            r = await tak_group_sync.sync_client_faction(client_key, target, username=client_key)
        except Exception as exc:  # noqa: BLE001 — 邊界吞例外，best-effort 契約
            r = {"synced": False, "reason": f"unexpected-error:{exc}"}
        results.append({"client_key": client_key, "faction": target, **r})
        if r.get("synced"):
            synced += 1
    return {"pushed": len(fmap), "synced": synced, "failed": len(fmap) - synced, "results": results}


async def reconcile_online_groups(subs: list[dict] | None = None) -> dict:
    """#477b 背景 reconciler：把「在線 + 已分類（當前場）+ 實際 TAK 群 ≠ 分類」的裝置補推群。

    離線時分類 → 群沒推成；此 reconciler 讓裝置**一連上、下一輪即自動歸位**，免手動重分。只在
    「實際群 ≠ 只在 [faction]」時才補推（省 TAK 寫；steady-state 零動作）。分類來源 = 當前 active 場
    （平時=待命池），對齊 classify/activate 語意。best-effort。`subs` 可由週期任務傳入（一次 poll 共用，
    免二次拉 subscriptions），否則自拉。回 {checked, fixed, results}。
    """
    if not tak_group_sync.is_configured():
        return {"checked": 0, "fixed": 0, "reason": "tak-not-configured"}
    fmap = client_faction_repo.get_faction_map(exercise_service.current_exercise_id())
    if not fmap:
        return {"checked": 0, "fixed": 0}
    if subs is None:
        subs = await tak_group_sync.list_online_subscriptions()
    actual = {s["username"]: (s.get("groups") or []) for s in subs if s.get("username")}
    fixed = 0
    results: list[dict] = []
    for cn, faction in fmap.items():
        groups = actual.get(cn)
        if groups is None:
            continue  # 離線 → 這輪跳過（上線後某輪補）
        if groups == [tak_group_sync.group_name_for(faction)]:
            continue  # 已正確隔離（實際群==映射群名）→ 不動（#477b 硬化：不硬編陣營名==群名）
        try:
            r = await tak_group_sync.sync_client_faction(cn, faction, username=cn)
        except Exception as exc:  # noqa: BLE001 — best-effort 邊界，個別失敗不中斷整批
            r = {"synced": False, "reason": f"unexpected-error:{exc}"}
        results.append({"client_key": cn, "faction": faction, "was": groups, **r})
        if r.get("synced"):
            fixed += 1
    return {"checked": len(fmap), "fixed": fixed, "results": results}


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
