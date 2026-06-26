# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""timeline_service — AAR 統一時間軸（P2-20(A) / #199）。

把一場演習散在四張表的「發生過的事」合併成**按時間排序的單一事件流**（唯讀投影，
不另存、不做定時落盤快照——設計定案與業界調查見 #199）：

| 來源 | type | 時間 | scope |
|---|---|---|---|
| cop_entity_tracks | track | t | JOIN cop_entities 取 exercise_id（tracks 無此欄，設計 B；#125 caveat 不在此擴大處理）|
| events | event | COALESCE(occurred_at, created_at) | exercise_id |
| chats | chat | COALESCE(time, received_at) | exercise_id |
| audit_log（白名單） | decision / command / event_status | created_at | exercise_id |

audit 走**白名單**：只取決策（decision_*）、下行/分享指令（TAK_DOWNLINK / COP_SHARE_TAK）、
事件狀態轉移（event_status_updated）——事件「建立」由 events 表承載，audit 的 event_created
不取（避免同一事實雙計）；其餘 audit（session/帳號/備份…）非戰情時間軸素材。

排序：t 字串升序（全源皆 ISO 8601 UTC Z，字典序＝時序）；同秒以 (type 固定優先序, 來源 id)
決定**穩定次序**——回放每次重排結果一致，不會同秒事件跳動。

時間軸=回放資料源，「T 時刻的狀態」由前端折疊事件流重建（虛擬時鐘等速走；Step mode 逐事件跳）。
"""

import json

from core.database import get_conn

# audit 白名單 → timeline type 映射（#199：act/decide 層；event_created 不取，events 表已載）
_AUDIT_TYPE_MAP = {
    "decision_created": "decision",
    "decision_made": "decision",
    "TAK_DOWNLINK": "command",
    "COP_SHARE_TAK": "command",
    "event_status_updated": "event_status",
}

# 同秒多筆的固定 type 優先序（穩定回放次序；track 先於工作流事件，貼近「感知→事→決→行」）
_TYPE_ORDER = {"track": 0, "chat": 1, "event": 2, "event_status": 3, "decision": 4, "command": 5, "zone": 6}


def _time_clause(col: str, since: str | None, until: str | None, params: list) -> str:
    """組 since/until 的 AND 片段（值 parameterized；col 為本檔常數欄位表達式）。"""
    frag = ""
    if since is not None:
        frag += f" AND {col} >= ?"
        params.append(since)
    if until is not None:
        frag += f" AND {col} <= ?"
        params.append(until)
    return frag


def _tracks(conn, exercise_id: int, since, until, cap: int) -> list[dict]:
    params: list = [exercise_id]
    frag = _time_clause("t.t", since, until, params)
    params.append(cap)
    rows = conn.execute(
        # #4：多帶 e.type（CoT type）→ AAR 前端據此衍生 2525 SIDC / 敵我態畫原符號。
        # 軌跡不存歷史敵我態 → 用 entity 現值（中途改敵我態 AAR 顯示最終態，v1 可接受）。
        "SELECT t.id, t.uid, t.t, t.lat, t.lon, t.hae, t.heading_deg, t.speed_mps, e.callsign, e.type "
        "FROM cop_entity_tracks t JOIN cop_entities e ON t.uid = e.uid "
        f"WHERE e.exercise_id = ?{frag} ORDER BY t.t, t.id LIMIT ?",  # nosec B608 — frag 為常數片段
        params,
    ).fetchall()
    return [
        {
            "type": "track",
            "t": r["t"],
            "actor": r["callsign"] or r["uid"],
            "payload": {
                "uid": r["uid"],
                "lat": r["lat"],
                "lon": r["lon"],
                "hae": r["hae"],
                "heading_deg": r["heading_deg"],
                "speed_mps": r["speed_mps"],
                "cot_type": r["type"],  # #4：CoT type（前端 cotToSidc/affiliationFromCot 用）
            },
            "_seq": r["id"],
        }
        for r in rows
    ]


def _event_markers_map(conn, ev_ids: list[str]) -> dict:
    """#339：一次查該批 event 的關聯感知標記（JOIN cop_entities 取位置），分組回
    {event_id: [{uid, lat, lon, callsign, cot_type, role}]}。供 AAR 把事件畫在標記位置上
    （前端再用該 uid 折疊軌跡 → 事件 pin 跟隨移動標的；無軌跡的靜態標記用此 lat/lon 兜底）。
    event_id 已由上游 events 查詢 exercise-scoped → 此處不再重複 scope。"""
    if not ev_ids:
        return {}
    placeholders = ", ".join("?" for _ in ev_ids)
    rows = conn.execute(
        f"SELECT em.event_id, em.role, c.uid, c.lat, c.lon, c.callsign, c.type AS cot_type "
        f"FROM event_markers em JOIN cop_entities c ON c.uid = em.cop_entity_uid "
        f"WHERE em.event_id IN ({placeholders}) ORDER BY em.created_at",  # nosec B608 — placeholders 常數組成
        ev_ids,
    ).fetchall()
    out: dict = {}
    seen_uids: set = set()
    for r in rows:
        out.setdefault(r["event_id"], []).append(
            {
                "uid": r["uid"],
                "lat": r["lat"],
                "lon": r["lon"],
                "callsign": r["callsign"],
                "cot_type": r["cot_type"],
                "role": r["role"],
            }
        )
        seen_uids.add(r["uid"])
    # #339+#2：手動標記的**位置歷史**（audit cop_entity_created/updated 帶 lat/lon）→ AAR 折疊 @ T，
    # 讓事件 pin 跟隨標記移動（手動敵情標記無 tracks，移動只留在 audit；TAK 標記有 tracks 走 posMap）。
    hist = _marker_position_history(conn, list(seen_uids))
    for markers in out.values():
        for m in markers:
            m["history"] = hist.get(m["uid"], [])
    return out


def _marker_position_history(conn, uids: list[str]) -> dict:
    """標記位置歷史：audit cop_entity_created/updated 的 lat/lon 序列（按時序）。
    回 {uid: [[t, lat, lon], ...]}。手動標記移動只記在 audit（非 tracks）→ AAR 據此折疊重現移動。"""
    if not uids:
        return {}
    placeholders = ", ".join("?" for _ in uids)
    rows = conn.execute(
        f"SELECT target_id, detail, created_at AS t FROM audit_log "
        f"WHERE target_id IN ({placeholders}) "
        f"AND action_type IN ('cop_entity_created', 'cop_entity_updated') "
        "ORDER BY id",  # nosec B608 — placeholders 常數組成
        uids,
    ).fetchall()
    out: dict = {}
    for r in rows:
        try:
            d = json.loads(r["detail"]) if r["detail"] else {}
        except (TypeError, ValueError):
            continue
        lat, lon = d.get("lat"), d.get("lon")
        if lat is None or lon is None:
            continue
        out.setdefault(r["target_id"], []).append([r["t"], lat, lon])
    return out


def _events(conn, exercise_id: int, since, until, cap: int) -> list[dict]:
    tcol = "COALESCE(occurred_at, created_at)"
    params: list = [exercise_id]
    frag = _time_clause(tcol, since, until, params)
    params.append(cap)
    rows = conn.execute(
        f"SELECT id, event_code, event_type, severity, status, description, "
        f"reported_by_unit, operator_name, {tcol} AS t "
        f"FROM events WHERE exercise_id = ?{frag} ORDER BY t, id LIMIT ?",  # nosec B608 — 常數片段
        params,
    ).fetchall()
    out = [
        {
            "type": "event",
            "t": r["t"],
            "actor": r["reported_by_unit"],
            "payload": {
                "event_id": r["id"],
                "event_code": r["event_code"],
                "event_type": r["event_type"],
                "severity": r["severity"],
                "status": r["status"],
                "description": r["description"],
                "operator_name": r["operator_name"],
            },
            "_seq": 0,
        }
        for r in rows
    ]
    # #339：附關聯標記位置（AAR 把事件畫上圖）。一次查、分組附，避免 per-event N+1。
    markers_by_event = _event_markers_map(conn, [it["payload"]["event_id"] for it in out])
    for it in out:
        it["payload"]["markers"] = markers_by_event.get(it["payload"]["event_id"], [])
    return out


def _chats(conn, exercise_id: int, since, until, cap: int) -> list[dict]:
    tcol = "COALESCE(time, received_at)"
    params: list = [exercise_id]
    frag = _time_clause(tcol, since, until, params)
    params.append(cap)
    rows = conn.execute(
        f'SELECT id, sender_uid, callsign, message, "group", lat, lon, {tcol} AS t '
        f"FROM chats WHERE exercise_id = ?{frag} ORDER BY t, id LIMIT ?",  # nosec B608 — 常數片段
        params,
    ).fetchall()
    return [
        {
            "type": "chat",
            "t": r["t"],
            "actor": r["callsign"] or r["sender_uid"],
            "payload": {
                "group": r["group"],
                "message": r["message"],
                "lat": r["lat"],
                "lon": r["lon"],
            },
            "_seq": r["id"],
        }
        for r in rows
    ]


def _audit(conn, exercise_id: int, since, until, cap: int) -> list[dict]:
    placeholders = ", ".join("?" for _ in _AUDIT_TYPE_MAP)
    params: list = [exercise_id, *_AUDIT_TYPE_MAP.keys()]
    frag = _time_clause("created_at", since, until, params)
    params.append(cap)
    rows = conn.execute(
        f"SELECT id, operator, action_type, target_table, target_id, detail, created_at AS t "
        f"FROM audit_log WHERE exercise_id = ? AND action_type IN ({placeholders}){frag} "
        "ORDER BY t, id LIMIT ?",  # nosec B608 — placeholders/frag 為常數組成
        params,
    ).fetchall()
    out = []
    for r in rows:
        try:
            detail = json.loads(r["detail"]) if r["detail"] else {}
        except (TypeError, ValueError):
            detail = {"raw": r["detail"]}
        out.append(
            {
                "type": _AUDIT_TYPE_MAP[r["action_type"]],
                "t": r["t"],
                "actor": r["operator"],
                "payload": {
                    "action": r["action_type"],
                    "target": r["target_id"],
                    "detail": detail,
                },
                "_seq": r["id"],
            }
        )
    return out


# #338：polygon/route 區域生命週期 → AAR 折疊重現用。只有 polygon/route 的 audit detail 帶整包
# attributes 快照（cop.py _audit_cop），故 `detail LIKE '%"attributes"%'` 即精準選出區域列、不掃
# 單位高頻 audit。op=created/updated 帶形狀+label_anchor；deleted → 折疊時移除該 uid。
_ZONE_ACTIONS = ("cop_entity_created", "cop_entity_updated", "cop_entity_deleted")
_ZONE_OP = {"cop_entity_created": "created", "cop_entity_updated": "updated", "cop_entity_deleted": "deleted"}


def _zones(conn, exercise_id: int, since, until, cap: int) -> list[dict]:
    placeholders = ", ".join("?" for _ in _ZONE_ACTIONS)
    params: list = [exercise_id, *_ZONE_ACTIONS]
    frag = _time_clause("created_at", since, until, params)
    params.append(cap)
    rows = conn.execute(
        f"SELECT id, operator, action_type, target_id, detail, created_at AS t "
        f"FROM audit_log WHERE exercise_id = ? AND action_type IN ({placeholders}) "
        f"AND detail LIKE '%\"attributes\"%'{frag} "
        "ORDER BY t, id LIMIT ?",  # nosec B608 — placeholders/frag 為常數組成
        params,
    ).fetchall()
    out = []
    for r in rows:
        try:
            detail = json.loads(r["detail"]) if r["detail"] else {}
        except (TypeError, ValueError):
            continue
        attrs = detail.get("attributes")
        if not isinstance(attrs, dict) or attrs.get("kind") not in ("polygon", "route"):
            continue  # LIKE 預篩後再嚴格確認 kind（防 detail 偶含 attributes 字樣的非區域列）
        out.append(
            {
                "type": "zone",
                "t": r["t"],
                "actor": r["operator"],
                "payload": {"uid": r["target_id"], "op": _ZONE_OP[r["action_type"]], "attributes": attrs},
                "_seq": r["id"],
            }
        )
    return out


def _contacts(conn, exercise_id: int, since, until, cap: int) -> list[dict]:
    """手動敵我接觸標記（attributes.kind='contact'）→ AAR 顯示。感知層原子，手動放置**無 tracks**，
    移動只留在 audit（cop_entity_created/updated lat/lon）。此處從 audit 位置歷史出 **track-type**
    事件（cot_type=entity.type 帶 affiliation a-f/h/n/u-G）→ 併入單位折疊，走 #335 milsymbol 單位層
    顯示（友軍方/敵菱/中立方/不明梅花，與 live 同款）並隨 T 移動。event-linked 的 sighting 不在此
    （走 event 路徑，避免雙畫）。"""
    ents = conn.execute(
        "SELECT uid, callsign, type FROM cop_entities "
        "WHERE exercise_id = ? AND json_extract(attributes, '$.kind') = 'contact'",
        (exercise_id,),
    ).fetchall()
    if not ents:
        return []
    meta = {e["uid"]: (e["type"], e["callsign"]) for e in ents}
    # {uid: [[t,lat,lon],...]} 全歷史；下方依 since/until 濾窗（與 _tracks 的 SQL window 一致——
    # 窗起點前的點不保留，屬全 timeline 既有行為；AAR 預設不帶 since/until → 完整折疊）。
    hist = _marker_position_history(conn, list(meta))
    out: list = []
    seq = 0
    for uid, pts in hist.items():
        cot, callsign = meta[uid]
        for t, lat, lon in pts:
            if since is not None and t < since:
                continue
            if until is not None and t > until:
                continue
            out.append(
                {
                    "type": "track",
                    "t": t,
                    "actor": callsign or uid,
                    "payload": {"uid": uid, "lat": lat, "lon": lon, "cot_type": cot},
                    "_seq": seq,
                }
            )
            seq += 1
            if len(out) > cap:
                return out
    return out


def build_timeline(
    exercise_id: int,
    since: str | None = None,
    until: str | None = None,
    limit: int = 5000,
) -> dict:
    """合併四源 → 按 t 排序的事件流 + metadata。

    per-source 以 limit+1 為 cap **探測**真截斷（取到 >limit 筆才算截、剛好 limit 筆不誤報），
    合併後再裁 limit；任一源截或合併有裁 → `truncated=true`（**不靜默截斷**——呼叫端縮
    from/to 時間窗重查）。回 {meta: {count, t_start, t_end, truncated}, items: [...]}。

    註（review 裁決）：tracks **刻意不濾** soft-deleted / archived entity——AAR 回放要完整
    歷史（單位在演習中存在過就該重現），與 `/tracks`（list_tracks_by_exercise）先例一致；
    live 視圖（list_cop_entities）濾 deleted 是另一語境。
    """
    probe = limit + 1  # 多取 1 筆探測截斷，不誤報「剛好 limit 筆」
    with get_conn() as conn:
        per_source = [
            _tracks(conn, exercise_id, since, until, probe),
            _events(conn, exercise_id, since, until, probe),
            _chats(conn, exercise_id, since, until, probe),
            _audit(conn, exercise_id, since, until, probe),
            _zones(conn, exercise_id, since, until, probe),  # #338：區域生命週期（折疊重現）
            _contacts(conn, exercise_id, since, until, probe),  # 長按「TAK 標記」敵我接觸（→ track 單位）
        ]
    hit_cap = any(len(src) > limit for src in per_source)
    items = [it for src in per_source for it in src]
    items.sort(key=lambda r: (r["t"], _TYPE_ORDER.get(r["type"], 9), r["_seq"]))
    truncated = hit_cap or len(items) > limit
    items = items[:limit]
    for it in items:
        del it["_seq"]  # 內部排序鍵，不出 API
    return {
        "meta": {
            "exercise_id": exercise_id,
            "count": len(items),
            "t_start": items[0]["t"] if items else None,
            "t_end": items[-1]["t"] if items else None,
            "truncated": truncated,
        },
        "items": items,
    }
