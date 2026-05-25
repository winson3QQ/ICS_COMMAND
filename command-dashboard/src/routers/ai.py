"""
ai.py — AI 整合 API stub（C0）

Wave 5 實裝：連接 Breeze/Ollama，補充即時狀態讀取。
"""

from fastapi import APIRouter, HTTPException, Request

from auth.service import validate_session
from repositories.ai_repo import update_outcome
from schemas.ai import AIOutcomeIn, AIRecommendIn
from services.ai_service import get_ml_export, get_post_exercise_report, get_recommendation

router = APIRouter(prefix="/api/ai", tags=["AI"])


@router.post("/recommend")
def recommend(body: AIRecommendIn, request: Request):
    """即時建議（讀當下 COP 狀態）"""
    validate_session(request)
    return get_recommendation(body.exercise_id, body.recommendation_type)


@router.get("/report/{exercise_id}")
def report(exercise_id: int, request: Request):
    """演練後分析"""
    validate_session(request)
    return get_post_exercise_report(exercise_id)


@router.get("/export/{exercise_id}")
def export_ml(exercise_id: int, request: Request):
    """ML 訓練資料匯出（state/action/outcome）"""
    validate_session(request)
    return get_ml_export(exercise_id)


@router.post("/recommendations/{rec_id}/outcome")
def record_outcome(rec_id: int, body: AIOutcomeIn, request: Request):
    """指揮官採納/否決 AI 建議"""
    validate_session(request)
    if not update_outcome(rec_id, body.accepted,
                          body.related_decision_id, body.outcome_notes):
        raise HTTPException(404, "建議記錄不存在")
    return {"ok": True}
