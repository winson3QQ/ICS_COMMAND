from core.database import get_conn

from ._helpers import iso_to_dt, iso_utc, now_utc, row_to_dict


def create_aar_entry(exercise_id: int, category: str, content: str,
                     created_by: str | None = None,
                     ref_t: str | None = None) -> dict:
    """AAR 條目。P2-21（#204）：category 加 'bookmark'（回放課程標記），`ref_t` =
    連結的回放時間點（ISO Z，入庫前 iso_utc 正規化——與 timeline t 同字串序紀律）；
    一般文字條目 ref_t 為 NULL。"""
    valid_categories = {"well", "improve", "recommend", "bookmark"}
    if category not in valid_categories:
        raise ValueError(f"category 必須是 {valid_categories} 之一")
    now = now_utc()
    ref_t = iso_utc(ref_t)
    if ref_t is not None:
        # security-review #204 觀察收緊：iso_utc 對垃圾字串只補 Z（"garbageZ"）→ 回放跳轉
        # 會壞且難察覺；入庫前驗真 ISO 8601，非法 → ValueError（router 映 422）。
        try:
            iso_to_dt(ref_t)
        except Exception as e:
            raise ValueError(f"ref_t 非合法 ISO 8601 時間：{ref_t!r}") from e
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO aar_entries (exercise_id, category, content, created_by, created_at, ref_t) "
            "VALUES (?,?,?,?,?,?)",
            (exercise_id, category, content, created_by, now, ref_t))
    return {"id": cur.lastrowid, "exercise_id": exercise_id, "category": category,
            "content": content, "created_by": created_by, "created_at": now, "ref_t": ref_t}


def get_aar_entries(exercise_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM aar_entries WHERE exercise_id=? ORDER BY created_at",
            (exercise_id,)).fetchall()
    return [row_to_dict(r) for r in rows]
