"""
schemas/ — 所有 Pydantic request/response 模型集中管理
"""

from .admin import AccountCreateIn, AccountStatusIn, AdminPinIn, ConfigIn, PiNodeCreateIn, PinResetIn, RoleUpdateIn
from .ai import AIRecommendIn
from .auth import LoginIn
from .decision import DecideIn, DecisionIn
from .event import DeadlinePatch, EventIn, EventNoteIn, EventPatch
from .exercise import AAREntryIn, ExerciseCreateIn, ExerciseStatusIn
from .manual import ManualRecordIn
from .snapshot import SnapshotIn
from .sync import ConflictResolveIn, SyncPushIn
from .ttx import TTXInjectBulkIn

__all__ = [
    "SnapshotIn",
    "EventIn", "EventPatch", "DeadlinePatch", "EventNoteIn",
    "DecisionIn", "DecideIn",
    "SyncPushIn", "ConflictResolveIn",
    "LoginIn",
    "AccountCreateIn", "AccountStatusIn", "PinResetIn",
    "AdminPinIn", "RoleUpdateIn", "PiNodeCreateIn", "ConfigIn",
    "ManualRecordIn",
    "ExerciseCreateIn", "ExerciseStatusIn", "AAREntryIn",
    "TTXInjectBulkIn",
    "AIRecommendIn",
]
