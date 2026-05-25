
from pydantic import BaseModel


class ManualRecordIn(BaseModel):
    form_id:    str
    operator:   str
    summary:    str
    payload:    dict
    device_id:  str | None = None
    exercise_id: int | None = None  # C0

    class Config:
        extra = "allow"
