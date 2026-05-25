
from pydantic import BaseModel


class DecisionIn(BaseModel):
    decision_type:      str   # initial / revision / escalation / closure
    severity:           str   # critical / warning
    decision_title:     str
    impact_description: str
    suggested_action_a: str
    created_by:         str
    suggested_action_b: str | None = None
    primary_event_id:   str | None = None
    parent_decision_id: str | None = None
    exercise_id:        int | None = None  # C0


class DecideIn(BaseModel):
    action:         str   # approved / hold / redirect / completed
    decided_by:     str
    execution_note: str = ""
