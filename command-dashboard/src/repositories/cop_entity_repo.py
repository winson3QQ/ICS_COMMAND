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

from ._helpers import row_to_dict

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
        raise ValueError(
            f"mark_stale: stale_at 必須是 ISO 8601 格式（Z 或 +00:00），"
            f"收到 {stale_at!r}: {e}"
        ) from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    normalized = dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE cop_entities SET stale = ? WHERE uid = ?",
            (normalized, uid),
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
    limit:      int = 1000,
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
                d.get("uid"), exc_info=e,
            )
            d["visible_to"] = []                       # ← fail-closed, not ['all']
    if d.get("attributes"):
        try:
            d["attributes"] = json.loads(d["attributes"])
        except (json.JSONDecodeError, TypeError) as e:
            _log.warning(
                "cop_entities.attributes JSON corrupt for uid=%s; using empty dict",
                d.get("uid"), exc_info=e,
            )
            d["attributes"] = {}
    return d
