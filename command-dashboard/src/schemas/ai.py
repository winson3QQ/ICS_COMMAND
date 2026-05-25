
from pydantic import BaseModel


class AIRecommendIn(BaseModel):
    """即時建議請求"""
    exercise_id:         int | None = None
    recommendation_type: str = "situation"  # situation / resource / decision


class AIOutcomeIn(BaseModel):
    """指揮官對 AI 建議的採納/否決"""
    accepted:           bool
    related_decision_id: int | None = None
    outcome_notes:      str | None  = None
