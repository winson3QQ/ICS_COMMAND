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

from core.config import COP_STALE_REMOVE_WINDOW_S
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
        # 墓碑：明確刪除（t-x-d-d / 操作員 DELETE）預設一律排除，不論 how/stale（#161 正解）。
        # include_stale=True（audit/回放）則連刪除的也撈得到。
        clauses.append("COALESCE(deleted, 0) = 0")
        # 新鮮度治理——分兩類（#161 reality check 2026-06-08）：
        #  - manual/command（指揮部自建）：維持嚴格 stale>now（既有行為；刪除走 deleted 墓碑）。
        #  - 外部來源（tak / pi-node / waveink）：存活看「**最後聽到**」= CoT `time`，**不信 client `stale`**。
        #    ★ TAK parity（核心原則）：ICS 看 TAK 要跟「TAK client 看 TAK」一致 = 「持續聽得到就持續顯示」。
        #      dogfood 實證 client `stale` 不是可靠存活訊號：iTAK 對**繪圖**(u-d-*) 每次 keep-alive 只推進
        #      `time`、卻把 `start`/`stale` **凍結在建立當下**（time=04:47 但 stale=02:57，仍持續重送 vc>1）；
        #      標記(a-*) 才會送新 stale。故改以 `time`（最後廣播時刻，標記/繪圖皆隨 keep-alive 前進，系統本就
        #      信任 time 做版本排序）+ 窗口判存活：最近聽得到(time > now-窗口) → 保留；停止重送(刪除/離線)
        #      → time 老化過窗口 → 移除。另保留 `stale > now`：尊重 client 明確宣告的遠期有效（place-once）。
        #      （舊版「h-* 永生豁免 stale」會讓場端刪的標記永遠賴著＝態勢圖不同步；且無界成長資安洞 P2-14(A)。）
        clauses.append(
            "((source IN ('manual','command') AND stale > strftime('%Y-%m-%dT%H:%M:%SZ','now')) "
            "OR (source NOT IN ('manual','command') AND "
            "(time > strftime('%Y-%m-%dT%H:%M:%SZ','now',?) "
            "OR stale > strftime('%Y-%m-%dT%H:%M:%SZ','now'))))"
        )
        params.append(f"-{COP_STALE_REMOVE_WINDOW_S} seconds")
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


def aggregate_squads(exercise_id=None) -> list[dict]:
    """按 team_color 分組聚合 COP entity，供小隊態勢面板 / dashboard 用（P2-06d，issue #128）。

    單句 SQL GROUP BY team_color 一次算齊每組：
      - total        = COUNT(*)
      - online       = SUM(CASE WHEN stale > now THEN 1 ELSE 0 END)（live 數）
      - offline      = total - online
      - avg_battery  = AVG(battery)（NULL battery 不計入平均；全組無 battery → None）
      - centroid_lat = AVG(lat)
      - centroid_lon = AVG(lon)

    centroid 為**簡單算術平均**（非球面加權質心）。小範圍演習場（同一城市/區域）足夠；
    跨換日線（±180° 經度）或極區會失真，本系統場域不涉及故不處理。

    **team_color IS NULL 保留為「未分隊」組**（不過濾掉），該組 dict 的 team_color 為 None。
    結果按 team_color 升序（SQLite NULL 排在最前，故未分隊組在列首）。

    exercise_id 三態（見 _helpers.NULL_SCOPE，對齊 list_cop_entities）——
      int → exact / NULL_SCOPE → IS NULL（實戰池）/ None → 不過濾（內部 caller）。
    注意：online 同時受 stale filter 影響，但 total 不過濾 stale（含已過期 entity），
    讓 offline 能反映「該隊有幾顆但失聯」——與 list_cop_entities 預設只回 live 的語意不同，
    此處刻意保留全量以呈現完整隊況。
    """
    clauses, params = [], []
    if exercise_id is NULL_SCOPE:
        clauses.append("exercise_id IS NULL")
    elif exercise_id is not None:
        clauses.append("exercise_id = ?")
        params.append(exercise_id)
    sql = [
        "SELECT team_color,",
        "       COUNT(*) AS total,",
        "       SUM(CASE WHEN stale > strftime('%Y-%m-%dT%H:%M:%SZ','now') THEN 1 ELSE 0 END) AS online,",
        "       AVG(battery) AS avg_battery,",
        "       AVG(lat) AS centroid_lat,",
        "       AVG(lon) AS centroid_lon",
        "FROM cop_entities",
    ]
    if clauses:
        sql.append("WHERE " + " AND ".join(clauses))
    sql.append("GROUP BY team_color ORDER BY team_color ASC")
    with get_conn() as conn:
        rows = conn.execute(" ".join(sql), params).fetchall()
    result = []
    for r in rows:
        d = row_to_dict(r)
        total = d["total"]
        online = d["online"] or 0  # SUM 在空組理論上不會出現（GROUP 至少一 row），保底
        result.append(
            {
                "team_color": d["team_color"],
                "total": total,
                "online": online,
                "offline": total - online,
                "avg_battery": d["avg_battery"],  # SQLite AVG 全 NULL → None
                "centroid_lat": d["centroid_lat"],
                "centroid_lon": d["centroid_lon"],
            }
        )
    return result


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
    # deleted=1：明確刪除墓碑（list 一律排除，不論 how/stale），解決持久化 how=h-* entity
    #   無法靠 stale=now 移除的問題。stale=now 一併設（兼容舊查詢/活追蹤的即時排除）。
    return update_cop_entity_cas(uid, expected_version_clock, {"deleted": 1, "stale": now}, actor)


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


def list_tracks_by_exercise(
    exercise_id: int,
    uid: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 1000,
    offset: int = 0,
) -> list[dict]:
    """某場（演習 ttx / 實戰 real）所有 entity 的軌跡時間序列（P2-06b，issue #123）。

    設計 B：tracks 不存 exercise_id，靠 uid JOIN cop_entities 取場歸屬（SoT 單一）。
    回 {uid, t, lat, lon, hae, heading_deg, speed_mps}，按 t 升序，分頁（limit/offset）。
    since/until 為 ISO 8601 UTC Z 字串（caller 應先 iso_utc 正規化，與 t 同格式才能
    正確字串比較）。WHERE 走 idx_cop_entities_exercise（driving）；跨多 uid 的全域
    ORDER BY t 走不到 idx_cop_tracks_uid_t(uid,t)（uid 在前）→ 為 temp B-tree 排序，
    故加 t.id 次序保證同秒多筆的分頁穩定（避免 LIMIT/OFFSET 頁邊界漏/重）。
    """
    sql = [
        "SELECT t.uid, t.t, t.lat, t.lon, t.hae, t.heading_deg, t.speed_mps",
        "FROM cop_entity_tracks t JOIN cop_entities e ON t.uid = e.uid",
        "WHERE e.exercise_id = ?",
    ]
    params: list = [exercise_id]
    if uid is not None:
        sql.append("AND t.uid = ?")
        params.append(uid)
    if since is not None:
        sql.append("AND t.t >= ?")
        params.append(since)
    if until is not None:
        sql.append("AND t.t <= ?")
        params.append(until)
    sql.append("ORDER BY t.t ASC, t.id ASC LIMIT ? OFFSET ?")
    params.extend([limit, offset])
    with get_conn() as conn:
        rows = conn.execute(" ".join(sql), params).fetchall()
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
    # P2-11b（#140）：SQLite 無 bool type，planned/simulated/deleted 存 INTEGER 0/1 → 轉回 bool
    for _flag in ("planned", "simulated", "deleted"):
        if d.get(_flag) is not None:
            d[_flag] = bool(d[_flag])
    return d
