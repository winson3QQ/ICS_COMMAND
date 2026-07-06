# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""services/tak_photo_push.py — #509-P3 下行：把「照片（掛在 marker M 上）」推到現場 TAK client。

真機定讞（2026-07-06，見 [[tak-filestore-image-uplink]]）：現場 client 顯示照片＝收到指向
**mission-package zip** 的 `b-f-t-r`（推裸 jpg 不吃）。故下行三步：
  ① 取 marker M 的 entity → 重建 marker CoT（帶 `<archive/>`）
  ② `build_mission_package`（MANIFEST + `<uid>/<uid>.cot` + 照片）→ 上傳 Enterprise Sync（write cert，
     keyword=`missionpackage`，讓收方＋ICS #509-P2 輪詢都認得）
  ③ `build_fileshare_cot` 廣播（或點對點）b-f-t-r 指向該 zip → `send_cot`（:8089，proven transport）

**甲/乙同一套**：M 是現有現場 marker（甲＝為既有標記補照片）或 ICS 新建 marker（乙＝無線電等非 TAK
來源情報，ICS 建標記配圖）——差別只在 M 的來源，本編排不分兩套。

RBAC（COMMAND_ROLES）+ faction 守門 + magic-byte + audit 在 `routers/tak.py`；本模組純編排。
推送失敗**往上拋**（非 best-effort：指令未送達要讓操作員知道 → router 回 5xx）。
"""

from __future__ import annotations

import hashlib

import structlog

from core import config
from repositories import cop_entity_repo
from services import tak_downlink, tak_files, tak_mission_package

log = structlog.get_logger()


class PhotoPushError(Exception):
    """下行推送失敗（marker 不存在 / 缺座標 / 未配置 / 上傳或送出失敗）。"""


async def push_photo_to_marker(
    *,
    marker_uid: str,
    photo_bytes: bytes,
    filename: str,
    mimetype: str = "image/jpeg",
    dest_callsigns: list[str] | None = None,
) -> dict:
    """把一張照片掛在 marker M 上推到現場。回 {zip_hash, marker_uid, dest, size}。

    dest_callsigns 給定 → 點對點送指定 client；否則廣播（送 ICS 所在 group）。
    成功送出後，照片也**在 ICS COP 本地掛上 marker M**（指揮官自己看得到自己推的照片；且 ICS 的
    #509-P2 輪詢會跳過 ICS 自傳 → 不靠 marker CoT 自我 re-ingest，避免覆寫既有 marker 的 attributes）。
    """
    entity = cop_entity_repo.get_cop_entity(marker_uid)
    if not entity or entity.get("deleted"):
        raise PhotoPushError(f"marker 不存在：{marker_uid}")
    lat, lon = entity.get("lat"), entity.get("lon")
    if lat is None or lon is None:
        raise PhotoPushError(f"marker 無座標，無法推送（幾何/route 不支援）：{marker_uid}")
    if not config.TAK_DEVICE_CONNECT_HOST:
        # senderUrl 須指向裝置搆得到的位址；缺此 → 送出的 b-f-t-r 現場抓不到（靜默失敗）→ fail loud。
        raise PhotoPushError("未配置 TAK_DEVICE_CONNECT_HOST（裝置面對位址），現場無法抓取，拒絕推送")

    # ① 重建 marker CoT（點標記；帶 <archive/> 持久 + callsign/隊色，收方建 marker 用）。
    cot_xml = tak_downlink.build_command_cot(
        uid=marker_uid,
        type_=entity["type"],
        lat=float(lat),
        lon=float(lon),
        callsign=entity.get("callsign"),
        remarks=entity.get("remarks"),
        team_color=entity.get("team_color"),
        role=entity.get("role"),
    )

    # ② 打包 mission-package zip。
    safe_name = (filename or "photo.jpg").rsplit("/", 1)[-1] or "photo.jpg"
    stem = safe_name.rsplit(".", 1)[0] or "photo"
    zip_bytes = tak_mission_package.build_mission_package(
        marker_uid=marker_uid, cot_xml=cot_xml, photo_name=safe_name, photo_bytes=photo_bytes
    )
    local_hash = hashlib.sha256(zip_bytes).hexdigest()

    # ③ 上傳 Enterprise Sync（write cert）。
    if not tak_files.filestore_write_enabled():
        raise PhotoPushError("TAK file store 上傳未配置（缺 Marti URL 或寫 cert）")
    client = tak_files._build_write_client()
    try:
        resp = await tak_files.upload_file(
            client,
            content=zip_bytes,
            filename=f"{stem}.zip",
            mimetype="application/x-zip-compressed",
            creator_uid=tak_downlink.ICS_SELF_UID,
            keywords="missionpackage",
        )
    finally:
        await client.close()
    zip_hash = tak_files.extract_hash(resp) or local_hash

    # ④ 廣播/點對點 b-f-t-r 指向 zip → :8089 送出（send_cot 失敗會 raise）。
    fs_cot = tak_downlink.build_fileshare_cot(
        file_hash=zip_hash,
        filename=f"{stem}.zip",
        size_bytes=len(zip_bytes),
        name=stem,
        lat=float(lat),
        lon=float(lon),
        dest_callsigns=dest_callsigns,
    )
    await tak_downlink.send_cot(fs_cot)

    # ⑤ 照片也在 ICS COP 本地掛 marker M（指揮官看得到自己推的；#509-P2 輪詢跳過 ICS 自傳，
    #    故**不靠** marker CoT 自我 re-ingest → 不覆寫既有 marker 的 attributes）。best-effort：
    #    本地掛失敗不推翻「已成功送達現場」的結果，只 log。
    from services import tak_attachments

    try:
        tak_attachments._store_attachment(
            marker_uid,
            {
                "sha256": hashlib.sha256(photo_bytes).hexdigest(),
                "data": photo_bytes,
                "name": safe_name,
                "mimetype": mimetype or "image/jpeg",
            },
        )
    except Exception:  # noqa: BLE001 — 本地掛失敗不影響現場推送結果
        log.warning("[tak] #509-P3 推送後本地掛 marker 失敗 marker=%s", marker_uid, exc_info=True)

    dest = dest_callsigns if dest_callsigns else "broadcast"
    log.info("[tak] #509-P3 下行照片推送：marker=%s dest=%s zip=%s", marker_uid, dest, zip_hash[:12])
    return {"zip_hash": zip_hash, "marker_uid": marker_uid, "dest": dest, "size": len(zip_bytes)}
