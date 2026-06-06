"""
repositories/cop_entity_repo.py — COP entity 資料存取層（P1-03 v1）

對應 issue #15 schema freeze v1：
- cop_entities  CRUD
- cop_entity_tracks  insert / list
- cop_entity_links  insert / list

設計：所有寫入透過 CoPEntity/CoPEntityTrack/CoPEntityLink Pydantic 驗證；
讀取回傳 dict (row_to_dict) 而非 Pydantic（避免 caller 強迫升版模型）。
"""

import json
import logging
from datetime import UTC, datetime

from core.database import get_conn
from schemas.cop import CoPEntity, CoPEntityLink, CoPEntityTrack

from ._helpers import NULL_SCOPE, row_to_dict

_log = logging.getLogger(__name__)


# ── cop_entities ──────────────────────────────────────────────────────────────


def insert_cop_entity(entity: CoPEntity) -> dict:
    """寫入 CoP entity 並回傳 DB 完整 row（含 DB DEFAULT 填值）。

    uid 衝突會 raise sqlite3.IntegrityError（caller 決定是否 upsert）。
    若 received_at=None，omit 該欄位讓 SQLite DEFAULT 自動填當下 UTC（NOT NULL
    constraint 不允許顯式 NULL 覆蓋 DEFAULT，故必須從 INSERT 拿掉）。

    P1-03 PR #16 /verify finding：先前實作在 with get_conn() 內呼叫
    get_cop_entity()，因 SQLite WAL 第二個 connection 看不到 outer connection
    尚未 commit 的 row，回傳值會是 None 而非 dict。修正：with block 退出
    (auto-commit) 後再呼叫 get_cop_entity。
    """
    payload = entity.model_dump()
    payload["visible_to"] = json.dumps(payload["visible_to"], ensure_ascii=False)
    payload["attributes"] = json.dumps(payload["attributes"], ensure_ascii=False)

    # 讓 SQLite DEFAULT 生效的欄位：None → 從 INSERT 移除
    if payload.get("received_at") is None:
        payload.pop("received_at")

    cols = list(payload.keys())
    placeholders = ", ".join(f":{c}" for c in cols)
    col_list = ", ".join(cols)
    with get_conn() as conn:
        conn.execute(
            f"INSERT INTO cop_entities ({col_list}) VALUES ({placeholders})",
            payload,
        )
    # 必須在 with 外讀取，否則 second connection 看不到 uncommitted row
    return get_cop_entity(entity.uid)


def get_cop_entity(uid: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM cop_entities WHERE uid = ?", (uid,)).fetchone()
        return _row_to_entity_dict(row) if row else None


def list_cop_entities(
    source: str | None = None,
    exercise_id=None,
    include_stale: bool = False,
    limit: int = 500,
) -> list[dict]:
    """列出 CoP entity。預設過濾 stale（stale > now）。

    exercise_id 三態（見 _helpers.NULL_SCOPE）——
      int → exact / NULL_SCOPE → IS NULL（實戰池）/ None → 不過濾（內部 caller）。
    """
    clauses, params = [], []
    if source is not None:
        clauses.append("source = ?")
        params.append(source)
    if exercise_id is NULL_SCOPE:
        clauses.append("exercise_id IS NULL")
    elif exercise_id is not None:
        clauses.append("exercise_id = ?")
        params.append(exercise_id)
    if not include_stale:
        clauses.append("stale > strftime('%Y-%m-%dT%H:%M:%SZ','now')")
    sql = "SELECT * FROM cop_entities"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    # received_at DB DEFAULT 只到秒精度，burst ingest 同秒可能 tie；
    # 加 uid 當 stable tiebreak，保證 pagination 結果一致
    sql += " ORDER BY received_at DESC, uid DESC LIMIT ?"
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [_row_to_entity_dict(r) for r in rows]


def mark_stale(uid: str, stale_at: str) -> bool:
    """強制把 entity 標為已過期（TTX 用：cop_entity_remove inject）。

    stale_at 必須是 ISO 8601 格式（Z 或 +00:00）。錯誤格式會 raise ValueError
    而非默默接受 — 因為 list_cop_entities 的 stale filter 用 lexicographic
    string compare（`stale > strftime('%Y-%m-%dT%H:%M:%SZ','now')`），
    錯格式（如 '2026/05/26'）會因 slash > dash 永遠 'live'，entity 不會過期。
    """
    # 驗證並 normalize 為 canonical ISO 8601 with Z suffix
    try:
        dt = datetime.fromisoformat(stale_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as e:
        raise ValueError(f"mark_stale: stale_at 必須是 ISO 8601 格式（Z 或 +00:00），收到 {stale_at!r}: {e}") from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    normalized = dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE cop_entities SET stale = ? WHERE uid = ?",
            (normalized, uid),
        )
        return cur.rowcount > 0


# ── per-entity 樂觀鎖（issue #29 PR-A）─────────────────────────────────────────

# patch 不可直接覆寫的欄位：由 CAS 邏輯 / DB 管理，caller 改不得
#   uid           — PK，改了等於換 entity
#   version_clock — 樂觀鎖計數，由 CAS 自行 +1
#   updated_by    — 由 actor 參數寫，不接受 patch 偽造
#   updated_at    — 由 server 時鐘寫，不接受 client 帶
#   received_at   — ingest 時間，DB DEFAULT 一次性，事後不改
_CAS_PROTECTED_COLS = {"uid", "version_clock", "updated_by", "updated_at", "received_at"}

# patch 傳 Python 物件、存 DB 前需 JSON 序列化的欄位（對齊 insert_cop_entity）
_JSON_COLS = {"visible_to", "attributes"}


def update_cop_entity_cas(
    uid: str,
    expected_version_clock: int,
    patch: dict,
    actor: str | None = None,
) -> dict:
    """Per-entity 樂觀鎖更新（TAK version_clock CAS）。

    核心是單句 `UPDATE ... WHERE uid=? AND version_clock=?` —— SQLite row-level
    atomic，**不需 asyncio.Lock**。兩個 writer 帶同一 expected_version_clock 並發
    時，只有一個 rowcount==1（勝，version_clock 被 +1），另一個 rowcount==0（敗）。
    這從根本上根除 commit 58bb5d4 的整檔 last-write-wins clobber（issue #29）。

    回傳契約（讓 PR-B router 直接對映 HTTP status）：
      {"status": "ok",       "entity": <更新後完整 row>}   # → 200
      {"status": "conflict", "entity": <DB 現值 row>}      # → 409 + server_body 讓 client merge
      {"status": "notfound", "entity": None}               # → 404

    Args:
        uid: 目標 entity。
        expected_version_clock: client 手上那份的 version_clock（If-Match 語意）。
        patch: 欄位→新值。key 必須是 cop_entities 真實欄位且非受保護欄位，
               否則 raise ValueError（防 SQL injection：column 名直接拼進 SQL）。
        actor: 變更者署名，寫入 updated_by。
    """
    if not patch:
        raise ValueError("update_cop_entity_cas: patch 不可為空")

    # 欄位白名單：column 名直接拼進 SQL，必須擋未知欄位（injection）+ 受保護欄位
    with get_conn() as conn:
        valid_cols = {row[1] for row in conn.execute("PRAGMA table_info(cop_entities)")}
    unknown = set(patch) - valid_cols
    if unknown:
        raise ValueError(f"update_cop_entity_cas: 未知欄位 {sorted(unknown)}")
    protected = set(patch) & _CAS_PROTECTED_COLS
    if protected:
        raise ValueError(f"update_cop_entity_cas: 不可 patch 受保護欄位 {sorted(protected)}")

    # JSON 欄位序列化（對齊 insert_cop_entity 的存法，下游 _row_to_entity_dict 會 decode）
    set_values = {
        col: (json.dumps(val, ensure_ascii=False) if col in _JSON_COLS else val) for col, val in patch.items()
    }
    set_clause = ", ".join(f"{col} = :{col}" for col in set_values)

    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = dict(set_values)
    params.update(
        {
            "_uid": uid,
            "_expected": expected_version_clock,
            "_actor": actor,
            "_now": now,
        }
    )
    sql = (  # nosec B608 — set_clause 欄位名已通過 PRAGMA 白名單，值全 parameterized
        f"UPDATE cop_entities SET {set_clause}, "
        "version_clock = version_clock + 1, "
        "updated_by = :_actor, updated_at = :_now "
        "WHERE uid = :_uid AND version_clock = :_expected"
    )
    with get_conn() as conn:
        changed = conn.execute(sql, params).rowcount

    # 必須在 with 外讀（WAL：second connection 看不到 uncommitted row，同 insert_cop_entity）
    if changed == 1:
        return {"status": "ok", "entity": get_cop_entity(uid)}
    # rowcount==0：分辨 notfound（uid 根本不存在）vs conflict（uid 在但 version 對不上）
    current = get_cop_entity(uid)
    if current is None:
        return {"status": "notfound", "entity": None}
    return {"status": "conflict", "entity": current}


def delete_cop_entity(
    uid: str,
    expected_version_clock: int,
    actor: str | None = None,
) -> dict:
    """TAK 風格 soft-delete：標 stale=now + bump version_clock，**不 hard delete**。

    TAK CoT 沒有「刪除」訊息——要移除一顆 entity 是送一筆 stale 已過期的 event，
    訂閱端據此把它從畫面移除。本函式對齊此語意：把 stale 設為當下（list 預設
    filter `stale > now` 會立即排除），同時 +1 version_clock 讓這次「刪除」也走
    WS broadcast 通知其他 client（PR-D）。歷史 row 保留，可 audit / 回放。

    走 update_cop_entity_cas → 同樣受樂觀鎖保護：expected_version_clock 對不上
    回 conflict（不會盲刪別人剛改過的 entity）。回傳契約同 update_cop_entity_cas。
    """
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return update_cop_entity_cas(uid, expected_version_clock, {"stale": now}, actor)


# ── cop_entity_tracks ────────────────────────────────────────────────────────


def insert_cop_track(track: CoPEntityTrack) -> int:
    """寫入 entity 軌跡點。回傳 row id。"""
    payload = track.model_dump()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO cop_entity_tracks (uid, t, lat, lon, hae, heading_deg, speed_mps)
            VALUES (:uid, :t, :lat, :lon, :hae, :heading_deg, :speed_mps)
            """,
            payload,
        )
        return cur.lastrowid


def list_cop_tracks(uid: str, since: str | None = None, limit: int = 1000) -> list[dict]:
    """撈某 entity 的軌跡，依時間正序。since 給 ISO 8601 字串。"""
    sql = "SELECT * FROM cop_entity_tracks WHERE uid = ?"
    params: list = [uid]
    if since:
        sql += " AND t >= ?"
        params.append(since)
    sql += " ORDER BY t ASC LIMIT ?"
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [row_to_dict(r) for r in rows]


def get_last_track_time(uid: str) -> str | None:
    """該 uid 最新一筆軌跡的 t（ISO 8601）；無軌跡回 None。

    供 P2-06a 的 per-uid 5s min-interval 抽樣節流用。走 idx_cop_tracks_uid_t(uid, t)
    → ORDER BY t DESC LIMIT 1 為 index 掃描，不撈全序列。
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT t FROM cop_entity_tracks WHERE uid = ? ORDER BY t DESC LIMIT 1",
            (uid,),
        ).fetchone()
        return row_to_dict(row)["t"] if row else None


# ── cop_entity_links ─────────────────────────────────────────────────────────


def insert_cop_link(link: CoPEntityLink) -> int:
    payload = link.model_dump()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO cop_entity_links (
                src_uid, relation, target_uid, target_type, url, remarks, mime
            ) VALUES (
                :src_uid, :relation, :target_uid, :target_type, :url, :remarks, :mime
            )
            """,
            payload,
        )
        return cur.lastrowid


def list_cop_links(
    src_uid: str | None = None,
    target_uid: str | None = None,
    relation: str | None = None,
    limit: int = 1000,
) -> list[dict]:
    """雙向查詢 entity 關係（給定 src 或 target 任一邊）。

    limit 預設 1000 與 list_cop_tracks 一致；防止 federation/TAK 累積到大量
    rows 後一次全 materialize 進 Python 記憶體。
    """
    clauses, params = [], []
    if src_uid is not None:
        clauses.append("src_uid = ?")
        params.append(src_uid)
    if target_uid is not None:
        clauses.append("target_uid = ?")
        params.append(target_uid)
    if relation is not None:
        clauses.append("relation = ?")
        params.append(relation)
    sql = "SELECT * FROM cop_entity_links"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [row_to_dict(r) for r in rows]


# ── 內部工具 ──────────────────────────────────────────────────────────────────


def _row_to_entity_dict(row) -> dict:
    """把 sqlite Row 轉 dict，並 JSON-decode visible_to / attributes。

    JSON 損毀策略：
    - visible_to corrupt → **fail-closed**（設為 []，no one sees this entity）
      絕不能 default 成 ['all']（fail-open ACL 升級 = 安全事故）
    - attributes corrupt → 設為 {}（非 ACL，僅 metadata 遺失）
    - 兩種情況都 log warning，提示維運查 row UID 找出 corruption 源
    """
    d = row_to_dict(row)
    if d.get("visible_to"):
        try:
            d["visible_to"] = json.loads(d["visible_to"])
        except (json.JSONDecodeError, TypeError) as e:
            _log.warning(
                "cop_entities.visible_to JSON corrupt for uid=%s; fail-closed to []",
                d.get("uid"),
                exc_info=e,
            )
            d["visible_to"] = []  # ← fail-closed, not ['all']
    if d.get("attributes"):
        try:
            d["attributes"] = json.loads(d["attributes"])
        except (json.JSONDecodeError, TypeError) as e:
            _log.warning(
                "cop_entities.attributes JSON corrupt for uid=%s; using empty dict",
                d.get("uid"),
                exc_info=e,
            )
            d["attributes"] = {}
    return d
