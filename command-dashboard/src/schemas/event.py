
from pydantic import BaseModel


class EventIn(BaseModel):
    reported_by_unit:         str
    event_type:               str
    severity:                 str = "info"
    description:              str
    operator_name:            str
    location_desc:            str | None = None
    location_zone_id:         str | None = None
    response_type:            str | None = None
    needs_commander_decision: bool = False
    related_person_name:      str | None = None
    assigned_unit:            str | None = None
    occurred_at:              str | None = None
    session_type:             str = "real"
    exercise_id:              int | None = None  # C0：關聯演練


class EventPatch(BaseModel):
    assigned_unit:   str | None = None
    location_desc:   str | None = None
    location_zone_id: str | None = None


class DeadlinePatch(BaseModel):
    delta_minutes: int
    operator:      str


class EventNoteIn(BaseModel):
    text:     str
    operator: str
