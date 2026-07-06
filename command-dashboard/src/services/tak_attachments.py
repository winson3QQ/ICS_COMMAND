# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""services/tak_attachments.py — #509：收到的 TAK 附件（現場照片）→ COP marker + 本地存 + 顯示。

reality-check（2026-07-06 dogfood）：現場分享/廣播照片 = ATAK 打包 **mission-package zip** 上
Enterprise Sync、經 fileshare `b-f-t-r` 通告；zip 內 = MANIFEST + `b-i-x-i` 影像 marker CoT（自帶
座標）+ .jpg。故現場照片＝**有座標的真 marker**（非孤立物，解 [[cop-marker-event-decoupling]]）。

流程：`b-f-t-r` → 抓 zip（Enterprise Sync content，#507 後 read cert 有 group 權限）→ 解
（tak_mission_package，zip-slip/magic/size 全擋）→ ingest 內含 b-i-x-i marker（上圖）→ 抽照片存
ICS 本地（`DATA_DIR/tak_attachments/<sha256>`，**檔名=sha256 不採信 zip entry 名**）+ cop_entity_links
記連結（src=marker uid）→ 經既有 #503 marker 詳情面板顯示（for-entity merge、download 服務本地檔）。

**faction**：附件繼承 marker（服務端 for-entity/download 守門於 marker 可見度）。**best-effort**：任何
一步失敗只 log、不拋（TAK 選配、附件失敗不擋串流 ingest）。
"""

from __future__ import annotations

import re

import structlog

from core import config
from repositories import cop_entity_repo
from schemas.cop import CoPEntityLink

log = structlog.get_logger()

# 本地附件連結約定：relation='attachment'、url='local:<sha256>'、target_uid=<sha256>、mime=圖 mimetype。
_RELATION = "attachment"
_LOCAL_URL_PREFIX = "local:"
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _attach_dir():
    d = config.DATA_DIR / "tak_attachments"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _valid_sha256(h: str) -> bool:
    return bool(h) and bool(_SHA256_RE.match(h))


def local_attachment_path(sha256: str):
    """本地附件檔路徑（檔名=sha256，杜絕 path 注入）。非法 sha256 → None。"""
    if not _valid_sha256(sha256):
        return None
    return _attach_dir() / sha256


def _store_attachment(marker_uid: str, img: dict) -> bool:
    """寫照片檔（檔名=sha256）+ cop_entity_links 記連結。dedup：同 (marker, sha256) 已存 → skip。回是否新存。"""
    sha = img["sha256"]
    p = local_attachment_path(sha)
    if p is None:
        return False
    # dedup：此 marker 已連此 sha256 → 不重複
    for lk in cop_entity_repo.list_cop_links(src_uid=marker_uid, relation=_RELATION):
        if lk.get("target_uid") == sha:
            return False
    if not p.exists():
        p.write_bytes(img["data"])
    cop_entity_repo.insert_cop_link(
        CoPEntityLink(
            src_uid=marker_uid,
            relation=_RELATION,
            target_uid=sha,
            target_type="attachment",
            url=f"{_LOCAL_URL_PREFIX}{sha}",
            remarks=img["name"],
            mime=img["mimetype"],
        )
    )
    log.info("[tak] #509 現場照片存本地並掛 marker uid=%s sha=%s", marker_uid, sha[:12])
    return True


async def handle_fileshare(event) -> None:
    """#509：處理 `b-f-t-r` fileshare 通告——抓 mission-package → ingest b-i-x-i marker + 存照片。

    由 cop_service `_route_fileshare_cot`（#508 分流器）呼叫。best-effort：任何失敗只 log、不拋。
    """
    from services import cop_service, tak_files, tak_mission_package, tak_service

    fs = (getattr(event, "detail", None) or {}).get("fileshare") or {}
    file_hash = (fs.get("sha256") or "").strip()
    if not _valid_sha256(file_hash):
        log.debug("[tak] fileshare 無有效 sha256，跳過 uid=%s", getattr(event, "uid", "?"))
        return
    if not tak_files.filestore_enabled():
        return

    client = tak_files._build_read_client()
    try:
        data = await tak_files.download_content(client, file_hash)
    except Exception:  # noqa: BLE001 — 抓檔失敗 best-effort（未配置/群沒權限/TAK 錯）
        log.info("[tak] #509 抓 fileshare content 失敗（跳過）hash=%s", file_hash[:12], exc_info=True)
        return
    finally:
        await client.close()
    if not data:
        return

    try:
        pkg = tak_mission_package.parse_mission_package(data)
    except tak_mission_package.MissionPackageError as e:
        log.info("[tak] #509 mission-package 解析失敗（跳過）：%s", e)
        return

    # ① ingest 內含的 b-i-x-i 影像 marker CoT（marker 上圖；#508 分流器讓 b-i-x-i 過、存為 entity）。
    marker_uid: str | None = None
    if pkg["cot_xml"]:
        try:
            ev = tak_service.parse_cot_xml(pkg["cot_xml"])  # mission-package 內是單一 <event>（b-i-x-i）
            row = await cop_service.ingest_cot_event(ev)
            if row:
                marker_uid = row["uid"]
        except Exception:  # noqa: BLE001 — 內含 CoT 解析/ingest 失敗 best-effort
            log.warning("[tak] #509 fileshare 內 CoT ingest 失敗", exc_info=True)
    if not marker_uid:
        # 無可掛載 marker（cot 缺 / 被來源所有權守門拒 / 非 a-*/b-i- 實體）→ 照片不孤兒落地。
        log.info("[tak] #509 fileshare 無可掛 marker，照片不落地 uid=%s", getattr(event, "uid", "?"))
        return

    # ② 抽照片存本地 + 掛 marker。
    stored = sum(_store_attachment(marker_uid, img) for img in pkg["images"])
    if stored:
        log.info("[tak] #509 現場照片橋成：marker=%s 新掛 %d 張", marker_uid, stored)


# ── 服務端（#503 for-entity / download 復用）──────────────────────────────────────


def list_local_attachments(entity_uid: str) -> list[dict]:
    """某 marker 的本地附件（現場照片）→ 對齊 #503 Enterprise Sync 結果形狀（{hash, name, mimeType}），
    使前端 tak_photos 不用改即可一併顯示。"""
    out: list[dict] = []
    for lk in cop_entity_repo.list_cop_links(src_uid=entity_uid, relation=_RELATION):
        sha = lk.get("target_uid") or ""
        if _valid_sha256(sha):
            out.append({"hash": sha, "name": lk.get("remarks") or sha, "mimeType": lk.get("mime") or "image/jpeg"})
    return out


def local_attachment_owner(sha256: str) -> str | None:
    """此 sha256 若為本地附件，回其所屬 marker uid（供 download 端 faction 守門）；否則 None。"""
    if not _valid_sha256(sha256):
        return None
    rows = cop_entity_repo.list_cop_links(relation=_RELATION, target_uid=sha256)
    return rows[0].get("src_uid") if rows else None


def read_local_attachment(sha256: str) -> tuple[bytes, str] | None:
    """讀本地附件 bytes + mimetype（檔名=sha256）。查無 → None。"""
    owner = local_attachment_owner(sha256)
    if owner is None:
        return None
    p = local_attachment_path(sha256)
    if p is None or not p.exists():
        return None
    rows = cop_entity_repo.list_cop_links(relation=_RELATION, target_uid=sha256)
    mime = (rows[0].get("mime") if rows else None) or "image/jpeg"
    return (p.read_bytes(), mime)
