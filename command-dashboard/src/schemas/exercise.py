
from pydantic import BaseModel


class ExerciseCreateIn(BaseModel):
    name:              str
    type:              str = "ttx"    # 'real' | 'ttx'
    date:              str | None = None
    location:          str | None = None
    scenario_summary:  str | None = None
    weather:           str | None = None
    participant_count: int | None = None
    organizing_body:   str | None = None
    # TTX 專屬
    facilitator:  str | None = None
    scenario_id:  str | None = None


class ExerciseStatusIn(BaseModel):
    status: str  # 'setup' | 'active' | 'archived'


class AAREntryIn(BaseModel):
    category: str    # 'well' | 'improve' | 'recommend'
    content:  str
    created_by: str | None = None
