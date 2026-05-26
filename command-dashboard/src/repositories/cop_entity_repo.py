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

from core.database import get_conn

from schemas.cop import CoPEntity, CoPEntityLink, CoPEntityTrack

from ._helpers import row_to_dict


# ── cop_entities ──────────────────────────────────────────────────────────────


def insert_cop_entity(entity: CoPEntity) -> dict:
    """寫入 CoP entity。uid 衝突會 raise sqlite3.IntegrityError（caller 決定 upsert）。

    若 received_at=None，omit 該欄位讓 SQLite DEFAULT 自動填當下 UTC（NOT NULL
    constraint 不允許顯式 NULL 覆蓋 DEFAULT，故必須從 INSERT 拿掉）。
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
        return get_cop_entity(entity.uid)


def get_cop_entity(uid: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM cop_entities WHERE uid = ?", (uid,)).fetchone()
        return _row_to_entity_dict(row) if row else None


def list_cop_entities(
    source:      str | None = None,
    exercise_id: int | None = None,
    include_stale: bool = False,
    limit: int = 500,
) -> list[dict]:
    """列出 CoP entity。預設過濾 stale（stale > now）。"""
    clauses, params = [], []
    if source is not None:
        clauses.append("source = ?")
        params.append(source)
    if exercise_id is not None:
        clauses.append("exercise_id = ?")
        params.append(exercise_id)
    if not include_stale:
        clauses.append("stale > strftime('%Y-%m-%dT%H:%M:%SZ','now')")
    sql = "SELECT * FROM cop_entities"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY received_at DESC LIMIT ?"
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [_row_to_entity_dict(r) for r in rows]


def mark_stale(uid: str, stale_at: str) -> bool:
    """強制把 entity 標為已過期（TTX 用：cop_entity_remove inject）。"""
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE cop_entities SET stale = ? WHERE uid = ?",
            (stale_at, uid),
        )
        return cur.rowcount > 0


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
    src_uid:    str | None = None,
    target_uid: str | None = None,
    relation:   str | None = None,
) -> list[dict]:
    """雙向查詢 entity 關係（給定 src 或 target 任一邊）。"""
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
    sql += " ORDER BY id DESC"
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [row_to_dict(r) for r in rows]


# ── 內部工具 ──────────────────────────────────────────────────────────────────


def _row_to_entity_dict(row) -> dict:
    """把 sqlite Row 轉 dict，並 JSON-decode visible_to / attributes。"""
    d = row_to_dict(row)
    if d.get("visible_to"):
        try:
            d["visible_to"] = json.loads(d["visible_to"])
        except (json.JSONDecodeError, TypeError):
            d["visible_to"] = ["all"]
    if d.get("attributes"):
        try:
            d["attributes"] = json.loads(d["attributes"])
        except (json.JSONDecodeError, TypeError):
            d["attributes"] = {}
    return d
