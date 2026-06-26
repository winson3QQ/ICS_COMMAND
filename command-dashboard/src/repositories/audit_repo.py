# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
from core.database import get_conn

from ._helpers import NULL_SCOPE, row_to_dict

_AUDIT_SELECT = """
    SELECT a.*, e.event_code AS _event_code, e.description AS _event_desc
    FROM   audit_log a
    LEFT JOIN events e ON a.target_table='events' AND a.target_id=e.id
"""


def get_audit_log(limit: int = 100, exercise_id=None) -> list[dict]:
    # P1-14：exercise_id 三態（見 _helpers.NULL_SCOPE）——
    #   int → exact / NULL_SCOPE → IS NULL（實戰池）/ None → 不過濾（內部 caller）
    where, params = "", []
    if exercise_id is NULL_SCOPE:
        where = "WHERE a.exercise_id IS NULL"
    elif exercise_id is not None:
        where = "WHERE a.exercise_id=?"
        params.append(exercise_id)
    params.append(limit)
    sql = _AUDIT_SELECT + where + " ORDER BY a.created_at DESC LIMIT ?"
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()  # nosec B608 — clause 全常數，值 parameterized
    return [row_to_dict(r) for r in rows]
