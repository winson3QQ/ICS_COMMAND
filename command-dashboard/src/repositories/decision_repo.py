# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
import uuid

from core.database import get_conn

from ._helpers import NULL_SCOPE, audit, now_utc, row_to_dict, scope_clause


def create_decision(data: dict, exercise_id: int | None = None) -> dict:
    did = str(uuid.uuid4())
    now = now_utc()

    seq = 1
    if data.get("parent_decision_id"):
        with get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM decisions WHERE (id=? OR parent_decision_id=?)",
                (data["parent_decision_id"], data["parent_decision_id"]),
            ).fetchone()
        seq = (row["cnt"] or 0) + 2

    sql = """
        INSERT INTO decisions
            (id, primary_event_id, decision_seq, parent_decision_id,
             decision_type, severity, decision_title, impact_description,
             suggested_action_a, suggested_action_b, status,
             created_by, created_at, exercise_id)
        VALUES (?,?,?,?, ?,?,?,?, ?,?,?, ?,?,?)
    """
    with get_conn() as conn:
        conn.execute(
            sql,
            (
                did,
                data.get("primary_event_id"),
                seq,
                data.get("parent_decision_id"),
                data["decision_type"],
                data["severity"],
                data["decision_title"],
                data["impact_description"],
                data["suggested_action_a"],
                data.get("suggested_action_b"),
                "pending",
                data["created_by"],
                now,
                exercise_id,
            ),
        )
        if data.get("parent_decision_id"):
            conn.execute(
                "UPDATE decisions SET status='superseded', superseded_by=? WHERE id=?",
                (did, data["parent_decision_id"]),
            )

    audit(
        data["created_by"],
        None,
        "decision_created",
        "decisions",
        did,
        {"title": data["decision_title"], "severity": data["severity"]},
        exercise_id,
    )
    return {"id": did}


def decide(
    decision_id: str, action: str, decided_by: str, execution_note: str = "", exercise_id: int | None = None
) -> dict:
    valid_actions = {"approved", "hold", "redirect", "completed"}
    if action not in valid_actions:
        raise ValueError(f"Invalid action: {action}")

    now = now_utc()
    # #288 H3：scope 併入存在性查詢——跨演習的 decision 視同不存在（router 將回 404）。
    sc, sp = scope_clause(exercise_id)
    with get_conn() as conn:
        row = conn.execute(f"SELECT status FROM decisions WHERE id=?{sc}", [decision_id] + sp).fetchone()
        if not row:
            raise ValueError("Decision not found")
        if row["status"] != "pending":
            raise ValueError(f"Decision already decided: {row['status']}")
        conn.execute(
            f"UPDATE decisions SET status=?, decided_by=?, decided_at=?, execution_note=? WHERE id=?{sc}",  # nosec B608
            [action, decided_by, now, execution_note, decision_id] + sp,
        )

    audit(decided_by, None, "decision_made", "decisions", decision_id, {"action": action}, exercise_id)
    return {"id": decision_id, "status": action, "decided_at": now}


def get_decisions(status: str | None = None, exercise_id=None) -> list[dict]:
    # P1-14：exercise_id 三態（見 _helpers.NULL_SCOPE）——
    #   int → exact / NULL_SCOPE → IS NULL（實戰池）/ None → 不過濾（內部 caller）
    clauses, params = [], []
    if status:
        clauses.append("status=?")
        params.append(status)
    if exercise_id is NULL_SCOPE:
        clauses.append("exercise_id IS NULL")
    elif exercise_id is not None:
        clauses.append("exercise_id=?")
        params.append(exercise_id)
    sql = "SELECT * FROM decisions"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at DESC"
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()  # nosec B608 — clause 全常數，值 parameterized
    return [row_to_dict(r) for r in rows]
