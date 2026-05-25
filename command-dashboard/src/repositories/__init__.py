"""repositories/ — DB CRUD layer

明文 re-export 子模組，提供 `from repositories import xxx_repo` 介面。
"""
from . import (
    aar_repo,
    account_repo,
    ai_repo,
    audit_repo,
    config_repo,
    decision_repo,
    event_repo,
    exercise_repo,
    manual_repo,
    pi_batch_repo,
    pi_node_repo,
    resource_snapshot_repo,
    snapshot_repo,
    sync_repo,
)

__all__ = [
    "aar_repo",
    "account_repo",
    "ai_repo",
    "audit_repo",
    "config_repo",
    "decision_repo",
    "event_repo",
    "exercise_repo",
    "manual_repo",
    "pi_batch_repo",
    "pi_node_repo",
    "resource_snapshot_repo",
    "snapshot_repo",
    "sync_repo",
]
