"""event_taxonomy_validate.py — POST /api/event_taxonomy 的 schema + 參照完整性驗證。

#66 編輯器地基（PR-A）：把原本「整包覆蓋、幾乎不驗」的 POST 收緊，避免 admin（或惡意/
誤用）寫進壞 taxonomy 造成前端孤兒、severity 脫鉤、TAK 無法表示。設計依據
docs/design/event-symbology-mapping.md〈雷（務必處理）〉。

守門規則：
  1. 結構：body 為 dict，含 groups[] / events[]。
  2. key 格式 `^[a-z0-9_]+$`、events/groups 內各自唯一。
  3. severity 固定 3 級（critical/warning/info）——只可指派，不可新增級別。
  4. cot_type 必填（TAK 互通；精確 suffix 留 P2-04，預設 a-u-G）。
  5. 參照完整性：event.group 必須指向存在的 group.key。
  6. **禁改 key / 禁硬刪**（key superset）：新 body 的 key 集合必須 ⊇ 既有 → 既有 events
     紀錄用 event_type 字串引用，移除/改名會造成孤兒（顯示 '?'）。刪除一律走 soft-delete
     （標 deleted=true，保留 key）。
  7. 禁刪非空 group：被 soft-delete 的 group 下不可有未刪除的 event（先搬移或一併刪）。

違規一律 raise ValueError(中文訊息)；caller（router）轉 HTTPException(400)。
"""

from __future__ import annotations

import re
from typing import Any

SEVERITIES = frozenset({"critical", "warning", "info"})
_KEY_RE = re.compile(r"^[a-z0-9_]+$")
# regex 允許底線 → __proto__/constructor/prototype 會通過格式檢查；後端一併擋（縱深防禦，
# 不只靠前端 applyTaxonomy/_bakeGlyphs 的 guard）。security review #76 MED。
_UNSAFE_KEYS = frozenset({"__proto__", "constructor", "prototype"})


def _keys_of(items: Any) -> set[str]:
    """取 list[dict] 中合法 str key 的集合（供 superset 比對；非 list/dict/無 key 略過）。"""
    out: set[str] = set()
    if not isinstance(items, list):  # previous 檔被手改成非 list 時不 TypeError（code review #76 LOW）
        return out
    for it in items:
        if isinstance(it, dict) and isinstance(it.get("key"), str):
            out.add(it["key"])
    return out


def validate_taxonomy(body: Any, previous: Any = None) -> None:
    """驗證 event_taxonomy body；違規 raise ValueError。

    previous：既有 taxonomy（做 key superset 檢查）。None 則略過 superset（首次寫入）。
    """
    if not isinstance(body, dict):
        raise ValueError("需為物件")
    groups = body.get("groups")
    events = body.get("events")
    if not isinstance(groups, list) or not isinstance(events, list):
        raise ValueError("需含 groups[] 與 events[]")

    # ── groups ──
    group_keys: set[str] = set()
    for g in groups:
        if not isinstance(g, dict):
            raise ValueError("group 需為物件")
        k = g.get("key")
        if not isinstance(k, str) or not _KEY_RE.match(k):
            raise ValueError(f"group key 格式錯誤：{k!r}（限小寫英數底線）")
        if k in _UNSAFE_KEYS:
            raise ValueError(f"group key 不可為保留字：{k}")
        if k in group_keys:
            raise ValueError(f"group key 重複：{k}")
        group_keys.add(k)
        if not isinstance(g.get("label"), str) or not g["label"].strip():
            raise ValueError(f"group {k} 缺 label")
        if "deleted" in g and not isinstance(g["deleted"], bool):
            raise ValueError(f"group {k} 的 deleted 需為 bool")

    # ── events ──
    event_keys: set[str] = set()
    for e in events:
        if not isinstance(e, dict):
            raise ValueError("event 需為物件")
        k = e.get("key")
        if not isinstance(k, str) or not _KEY_RE.match(k):
            raise ValueError(f"event key 格式錯誤：{k!r}（限小寫英數底線）")
        if k in _UNSAFE_KEYS:
            raise ValueError(f"event key 不可為保留字：{k}")
        if k in event_keys:
            raise ValueError(f"event key 重複：{k}")
        event_keys.add(k)
        if not isinstance(e.get("label"), str) or not e["label"].strip():
            raise ValueError(f"event {k} 缺 label")
        if e.get("severity") not in SEVERITIES:
            raise ValueError(
                f"event {k} severity 非法：{e.get('severity')!r}"
                "（固定 3 級：critical/warning/info）"
            )
        if e.get("group") not in group_keys:
            raise ValueError(f"event {k} 的 group 不存在：{e.get('group')!r}")
        ct = e.get("cot_type")
        if not isinstance(ct, str) or not ct.strip():
            raise ValueError(f"event {k} 缺 cot_type（TAK 互通必填，預設 a-u-G）")
        if "deleted" in e and not isinstance(e["deleted"], bool):
            raise ValueError(f"event {k} 的 deleted 需為 bool")

    # ── 參照完整性：禁改 key / 禁硬刪（key superset）──
    if isinstance(previous, dict):
        missing_ev = _keys_of(previous.get("events", [])) - event_keys
        if missing_ev:
            raise ValueError(
                "不可移除或改名既有 event key（保參照完整性，刪除請用 deleted soft-delete）："
                f"{sorted(missing_ev)}"
            )
        missing_grp = _keys_of(previous.get("groups", [])) - group_keys
        if missing_grp:
            raise ValueError(
                f"不可移除或改名既有 group key（刪除請用 deleted soft-delete）：{sorted(missing_grp)}"
            )

    # ── 禁刪非空 group：被 soft-delete 的 group 下不可有未刪除 event ──
    deleted_groups = {
        g["key"] for g in groups if isinstance(g, dict) and g.get("deleted") is True
    }
    for e in events:
        if e.get("deleted") is not True and e.get("group") in deleted_groups:
            raise ValueError(
                f"group {e.get('group')!r} 已標記刪除，但其下仍有未刪除 event "
                f"{e.get('key')!r}（請先搬移或一併刪除）"
            )
