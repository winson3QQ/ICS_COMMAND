from typing import Literal

from pydantic import BaseModel


class EnrollIn(BaseModel):
    """#267 納編/退編：把一個 cop entity 的 exercise_id 移進當前 active 場 / 退回 NULL 常駐。"""

    uid: str
    action: Literal["enroll", "unenroll"]


class ExerciseCreateIn(BaseModel):
    name: str
    type: str = "ttx"  # 'real' | 'ttx'
    date: str | None = None
    location: str | None = None
    scenario_summary: str | None = None
    weather: str | None = None
    participant_count: int | None = None
    organizing_body: str | None = None
    # TTX 專屬
    facilitator: str | None = None
    scenario_id: str | None = None


class ExerciseStatusIn(BaseModel):
    status: str  # 'setup' | 'active' | 'archived'


class AAREntryIn(BaseModel):
    category: str  # 'well' | 'improve' | 'recommend' | 'bookmark'（P2-21 #204）
    content: str
    created_by: str | None = None
    ref_t: str | None = None  # bookmark 連結的回放時間點（ISO Z；一般條目 NULL）
