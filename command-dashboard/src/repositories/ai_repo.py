"""
ai_repo.py — AI 建議紀錄（法律稽核 + 訓練資料）
C0：stub，Wave 5 由 ai_service 填入實際 LLM 呼叫
"""

from core.database import get_conn

from ._helpers import now_utc, row_to_dict


def create_recommendation(exercise_id: int | None, recommendation_type: str,
                           content: str, confidence: float | None = None) -> dict:
    now = now_utc()
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO ai_recommendations
                (exercise_id, made_at, recommendation_type, content, confidence)
            VALUES (?,?,?,?,?)
        """, (exercise_id, now, recommendation_type, content, confidence))
    return {"id": cur.lastrowid, "made_at": now}


def update_outcome(recommendation_id: int, accepted: bool,
                   related_decision_id: int | None = None,
                   outcome_notes: str | None = None) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE ai_recommendations "
            "SET accepted=?, related_decision_id=?, outcome_notes=? WHERE id=?",
            (1 if accepted else 0, related_decision_id, outcome_notes, recommendation_id))
    return cur.rowcount > 0


def get_recommendations(exercise_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM ai_recommendations WHERE exercise_id=? ORDER BY made_at DESC",
            (exercise_id,)).fetchall()
    return [row_to_dict(r) for r in rows]


def get_training_export(exercise_id: int) -> list[dict]:
    """回傳 (state, action, outcome) 格式訓練資料（AI 護城河）"""
    recs = get_recommendations(exercise_id)
    return [
        {
            "recommendation_id": r["id"],
            "recommendation_type": r["recommendation_type"],
            "content": r["content"],
            "confidence": r["confidence"],
            "accepted": r["accepted"],
            "made_at": r["made_at"],
            "related_decision_id": r.get("related_decision_id"),
            "outcome_notes": r.get("outcome_notes"),
        }
        for r in recs
    ]
