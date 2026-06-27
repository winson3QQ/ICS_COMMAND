# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
pi_push_service.py — Pi push 接收處理
接收各組定時推送，寫入 pi_received_batches 並更新 resource_snapshots。

kept for upstream node federation (PWA / Pi reconnection); see ROADMAP P1-04 / Wave 7+
Shelter / Medical PWA 未來回流時，本層介面直接承接，無需架構翻修。
"""

import json
from datetime import UTC, datetime

import structlog

from repositories.pi_batch_repo import insert_pi_batch
from repositories.pi_node_repo import touch_pi_node, validate_pi_push
from repositories.resource_snapshot_repo import insert_resource_snapshot
from services.exercise_service import current_exercise_id

_log = structlog.get_logger()


def process_push(unit_id: str, bearer_token: str, body: dict) -> dict:
    """
    驗證 Bearer token、寫入批次、更新 resource_snapshot。
    回傳 {ok, batch_id, records_count} 或 {ok, heartbeat}。
    """
    if not validate_pi_push(unit_id, bearer_token):
        _log.error("pi_push_rejected", msg="Pi push 驗證失敗", detail={"unit_id": unit_id, "reason": "invalid_token"})
        raise PermissionError("API 金鑰驗證失敗或 unit_id 不符")

    records = body.get("records", [])
    pushed_at = body.get("pushed_at") or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    if not records or body.get("heartbeat"):
        touch_pi_node(unit_id)
        _log.info("pi_push_received", msg="Pi push heartbeat", detail={"unit_id": unit_id, "heartbeat": True})
        return {"ok": True, "heartbeat": True}

    batch_id = insert_pi_batch(
        unit_id=unit_id,
        pushed_at=pushed_at,
        records_json=json.dumps(records, ensure_ascii=False),
    )

    # 寫入正規化 resource_snapshot（C0 新增，供 AI 訓練和跨場次比較）
    ex_id = current_exercise_id()
    snap_data = _extract_resource_snapshot(unit_id, records)
    if snap_data:
        insert_resource_snapshot(ex_id, unit_id, snap_data, source="pi_push")

    _log.info(
        "pi_push_received",
        msg="Pi push 接收成功",
        detail={"unit_id": unit_id, "batch_id": batch_id, "records_count": len(records)},
    )
    return {"ok": True, "batch_id": batch_id, "records_count": len(records)}


def _extract_resource_snapshot(unit_id: str, records: list) -> dict | None:
    """從 Pi push records 提取正規化數值（for resource_snapshots 表）"""
    by_table: dict[str, list] = {}
    for r in records:
        tbl = r.get("table_name", "")
        rec = r.get("record", {})
        if tbl and rec:
            by_table.setdefault(tbl, []).append(rec)

    if unit_id == "medical":
        patients = by_table.get("patients", [])
        active = [p for p in patients if p.get("current_zone") != "已離區"]
        colors = {"red": 0, "yellow": 0, "green": 0, "black": 0}
        for p in active:
            c = p.get("triage_color", "")
            if c in colors:
                colors[c] += 1
        return {
            "total_beds": max(len(active) + 5, 20),
            "occupied_beds": len(active),
            "light_count": colors["green"],
            "medium_count": colors["yellow"],
            "severe_count": colors["red"],
            "deceased_count": colors["black"],
        }

    elif unit_id == "shelter":
        persons = by_table.get("persons", [])
        beds = by_table.get("beds", [])
        beds_meta = by_table.get("beds_meta", [])
        placed = [p for p in persons if p.get("status") == "已安置"]
        capacity_max = next(
            (int(m["capacity_max"]) for m in beds_meta if m.get("_id") == "capacity" and m.get("capacity_max")), None
        )
        active_beds = [b for b in beds if b.get("status") != "suspended"]
        total_beds = (
            capacity_max
            if capacity_max is not None
            else (len(active_beds) if active_beds else max(len(placed) + 5, 12))
        )
        return {
            "total_beds": total_beds,
            "occupied_beds": len(placed),
            "light_count": None,
            "medium_count": None,
            "severe_count": None,
            "deceased_count": None,
        }

    return None
