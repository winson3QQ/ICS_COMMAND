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


def _store_attachment(marker_uid: str, img: dict, *, pkg_hash: str | None = None, direction: str | None = None) -> bool:
    """寫照片檔（檔名=sha256）+ cop_entity_links 記連結。dedup：同 (marker, sha256) 已存 → skip。回是否新存。

    #518：`pkg_hash`＝此照片所屬 mission-package zip 的 Enterprise Sync hash（L2 刪除目標 + 立墓碑鍵）；
    `direction`＝'uplink'（現場→ICS）/'downlink'（ICS→現場）——決定刪除預設。兩者記進 link 供方向感知刪除。
    """
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
            direction=direction,
            pkg_hash=pkg_hash if (pkg_hash and _valid_sha256(pkg_hash)) else None,
        )
    )
    log.info("[tak] #509 現場照片存本地並掛 marker uid=%s sha=%s dir=%s", marker_uid, sha[:12], direction or "?")
    return True


# 已處理的 mission-package zip hash（announce + poll 共用去重；避免同一包重抓/重解）。in-memory：
# 重啟後清空 → 重掃，但 _store_attachment 以 (marker,photo-sha) 去重，重跑不生重複附件（idempotent）。
_seen_hashes: set[str] = set()


async def _bridge_package_by_hash(client, file_hash: str) -> str | None:
    """#509 核心：抓 mission-package zip（by hash）→ 解 → ingest 內含 marker → 抽照片掛 marker。

    回掛上照片/找到的 marker_uid；無 CoT/marker → None。**下載/網路層錯往上拋**（讓 caller 決定重試、
    不誤標 seen）；解析/內容永久壞 → 回 None（已定讞、不重試）。best-effort。
    """
    from services import cop_service, tak_files, tak_mission_package, tak_service

    # #518：墓碑守門置於**共用橋接**——同時擋主動輪詢與被動 `handle_fileshare`（b-f-t-r 通告）兩路徑。
    # 只在 poll 端擋會漏被動路徑：iTAK 重連廣播 b-f-t-r → handle_fileshare → 此處，若無守門會把已刪照片
    # 重橋復活（正是「刪了又回來」）。已刪的 zip 一律不再抓/解/掛。
    if cop_entity_repo.is_pkg_tombstoned(file_hash):
        log.debug("[tak] #518 已刪墓碑 zip 跳過（不復活）hash=%s", file_hash[:12])
        return None

    data = await tak_files.download_content(client, file_hash)  # 網路/HTTP 錯 → 拋給 caller
    if not data:
        return None
    try:
        pkg = tak_mission_package.parse_mission_package(data)
    except tak_mission_package.MissionPackageError as e:
        log.info("[tak] #509 mission-package 解析失敗（跳過）hash=%s：%s", file_hash[:12], e)
        return None

    # ① ingest 內含 marker CoT（上圖；#508 分流器讓實體 CoT 過、存為 entity）。
    marker_uid: str | None = None
    if pkg["cot_xml"]:
        try:
            ev = tak_service.parse_cot_xml(pkg["cot_xml"])  # mission-package 內是單一 <event>
            await cop_service.ingest_cot_event(ev)  # new→create / newer→update / 舊→no-op（皆可）
            # **不倚賴 ingest 回傳**：主動輪詢時 package 內 CoT 常比 live marker 舊 → ingest 回 None，
            # 但 marker 仍在。只要該 marker 存在且未刪即可掛照片（解 announce/poll 時序差異）。
            row = cop_entity_repo.get_cop_entity(ev.uid) if ev.uid else None
            if row and not row.get("deleted"):
                marker_uid = ev.uid
                # dogfood 觀測：marker 常無 producer 連結 → faction 可能 NULL；NULL 時演習中受限操作員
                # （非 sysadmin）看不到此照片。log 供現場對照「照片有進但看不到」。
                _faction = row.get("faction")
                log.info(
                    "[tak] #509 fileshare marker ingest uid=%s faction=%s",
                    marker_uid,
                    _faction if _faction is not None else "NULL(全域可見受限)",
                )
        except Exception:  # noqa: BLE001 — 內含 CoT 解析/ingest 失敗 best-effort
            log.warning("[tak] #509 fileshare 內 CoT ingest 失敗", exc_info=True)
    if not marker_uid:
        # 無可掛載 marker（cot 缺 / 被來源所有權守門拒 / 該 marker 已刪）→ 照片不孤兒落地。
        log.info("[tak] #509 無可掛 marker，照片不落地 hash=%s", file_hash[:12])
        return None

    # ② 抽照片存本地 + 掛 marker。存檔/連結失敗屬本地永久性錯（非下載暫斷）→ 內部吞、不外拋
    #    （否則 caller 會誤判為可重試的網路錯而永遠重抓同一包）。
    try:
        # #518：現場橋接＝上行（uplink）；記所屬 zip hash（file_hash）供方向感知刪除 + L2 + 墓碑。
        stored = sum(
            _store_attachment(marker_uid, img, pkg_hash=file_hash, direction="uplink") for img in pkg["images"]
        )
    except Exception:  # noqa: BLE001 — 本地存/連結失敗 best-effort，不冒充下載暫斷
        log.warning("[tak] #509 照片存本地/掛 marker 失敗 marker=%s", marker_uid, exc_info=True)
        return marker_uid
    if stored:
        log.info("[tak] #509 現場照片橋成：marker=%s 新掛 %d 張", marker_uid, stored)
    return marker_uid


async def handle_fileshare(event) -> None:
    """#509：處理 `b-f-t-r` fileshare 通告（被動）——抓 mission-package → ingest marker + 存照片。

    由 cop_service `_route_fileshare_cot`（#508 分流器）呼叫。best-effort：任何失敗只 log、不拋。
    """
    from services import tak_files
    from services.tak_downlink import ICS_SELF_UID

    fs = (getattr(event, "detail", None) or {}).get("fileshare") or {}
    # #509-P3：ICS 自己下行推的 b-f-t-r 會廣播回自己的 subscriber → **不得 re-ingest**（否則用重建的
    # 精簡 marker CoT 覆寫既有 marker attributes；照片推送時已本地掛）。對齊 #509-P2 輪詢跳過 ICS 自傳。
    if (fs.get("senderUid") or "") == ICS_SELF_UID:
        log.debug("[tak] #509-P3 略過 ICS 自己下行推的 b-f-t-r（防 echo re-ingest）uid=%s", getattr(event, "uid", "?"))
        return
    file_hash = (fs.get("sha256") or "").strip()
    if not _valid_sha256(file_hash):
        log.debug("[tak] fileshare 無有效 sha256，跳過 uid=%s", getattr(event, "uid", "?"))
        return
    if not tak_files.filestore_enabled():
        return

    client = tak_files._build_read_client()
    try:
        await _bridge_package_by_hash(client, file_hash)
        # **只在成功處理後標 seen**：下載暫斷（下面 except）不標 → 保留給主動輪詢重試，不永久漏
        # （review：原無條件標 seen 會讓 announce 路徑的暫時性下載失敗永久打死輪詢後備）。
        _seen_hashes.add(file_hash)  # 通告已處理 → 主動輪詢不重抓（announce/poll 共用去重）
    except Exception:  # noqa: BLE001 — 抓檔失敗 best-effort（未配置/群沒權限/TAK 錯）→ 不標 seen
        log.info("[tak] #509 抓 fileshare content 失敗（跳過）hash=%s", file_hash[:12], exc_info=True)
    finally:
        await client.close()


# ── #509-P2：Enterprise Sync 主動輪詢橋 ────────────────────────────────────────────
# 為何要輪詢：現場照片都上 Enterprise Sync，但 `b-f-t-r` 通告的定址因 client 而異（iTAK 廣播 → ICS
# 收得到；ATAK 不廣播 → ICS 等不到），只靠被動 handle_fileshare 會漏 ATAK。主動列舉 file store 的
# missionpackage 對兩家一視同仁，且重用同一套抓/解/ingest/掛（_bridge_package_by_hash）。


def filestore_poll_enabled() -> bool:
    """#509-P2 主動輪詢是否啟用：開關開 + file store 讀側可用（缺任一停用）。"""
    from services import tak_files

    return bool(config.TAK_FILESTORE_POLL_ENABLED) and tak_files.filestore_enabled()


async def poll_filestore_once() -> int:
    """列舉 file store 的 missionpackage → 對未見過的 zip 走 `_bridge_package_by_hash`。回本輪新掛數。

    只處理帶 `missionpackage` keyword 者（避開 ICS 自傳的 #503 檔——keyword=marker_uid）。下載層錯
    **不標 seen**（下輪重試）；解析/內容永久壞則於 _bridge 內判定、回 None 並標 seen 不重試。best-effort：不拋。

    **不濾 tool**（真機 dogfood：ATAK 分享落 `tool=private`、iTAK 落 `tool=null`、部分 `tool=public`——
    分享方式而異，不可靠）→ 列舉全部、只靠 `missionpackage` keyword 辨識現場包。原濾 `tool=public`
    會漏掉新 ATAK 分享（tool=private），正是 code-review 曾點名、被歷史資料誤導而漏修之處。
    """
    from services import tak_files

    client = tak_files._build_read_client()
    bridged = 0
    try:
        results = await tak_files.search_files(client)
        from services.tak_downlink import ICS_SELF_UID

        for meta in results:
            h = tak_files.extract_hash(meta)
            if not h or not tak_files.is_valid_hash(h) or h in _seen_hashes:
                continue
            # #518：持久墓碑——指揮層刪過的 zip 永久跳過（跨重啟，補 in-memory `_seen_hashes` 之漏），
            # 止住「刪了又回來」。查中即記入 in-memory set，本 process 後續輪不再打 DB。
            if cop_entity_repo.is_pkg_tombstoned(h):
                _seen_hashes.add(h)
                continue
            if "missionpackage" not in [k.lower() for k in tak_files.extract_keywords(meta)]:
                continue
            if tak_files.extract_creator_uid(meta) == ICS_SELF_UID:
                # #509-P3：ICS 自己下行推的 zip（creatorUid=ICS-CMD）→ 不 re-ingest（否則 marker CoT
                # 自我 re-ingest 會覆寫既有 marker attributes）。ICS 側照片於推送時已本地掛。
                _seen_hashes.add(h)
                continue
            try:
                res = await _bridge_package_by_hash(client, h)
            except Exception:  # noqa: BLE001 — 下載/網路層錯 → 不標 seen，下輪重試
                log.info("[tak] #509-P2 輪詢處理失敗（下輪重試）hash=%s", h[:12], exc_info=True)
                continue
            _seen_hashes.add(h)
            if res:
                bridged += 1
    except Exception:  # noqa: BLE001 — 列舉失敗 best-effort（TAK 未配置/暫斷）
        log.info("[tak] #509-P2 file store 列舉失敗（本輪跳過）", exc_info=True)
    finally:
        await client.close()
    return bridged


async def filestore_poll_loop(stop_event) -> None:
    """#509-P2 週期輪詢直到 stop_event。best-effort：單輪失敗只 log、續跑（同 presence/mission poll）。
    sleep 走 stop_event.wait 可即時中斷（軟停立即收）。"""
    import asyncio

    interval = max(15, int(config.TAK_FILESTORE_POLL_INTERVAL_S))
    log.info("tak.filestore_poll_start", interval_s=interval)
    while not stop_event.is_set():
        try:
            n = await poll_filestore_once()
            if n:
                log.info("[tak] #509-P2 主動輪詢新橋 %d 張現場照片", n)
        except Exception:  # noqa: BLE001 — 輪詢絕不拖垮 runtime
            log.warning("[tak] #509-P2 輪詢迴圈異常", exc_info=True)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except TimeoutError:
            pass  # 間隔到 → 續下一輪
    log.info("tak.filestore_poll_stop")


# ── 服務端（#503 for-entity / download 復用）──────────────────────────────────────


def list_local_attachments(entity_uid: str) -> list[dict]:
    """某 marker 的本地附件（現場照片）→ 對齊 #503 Enterprise Sync 結果形狀（{hash, name, mimeType}），
    使前端 tak_photos 不用改即可一併顯示。

    #518：額外帶 `direction`（'uplink'/'downlink'/None）與 `server_purgeable`（是否有 pkg_hash 可走 L2）——
    供前端渲染方向徽記 + 決定清除預設（下行預設連 server 清、上行預設只清本地）。`local=True` 標本地附件
    （相對於純 Enterprise Sync 結果），前端據此才顯示刪除鈕（只有本地附件可經 ICS 刪）。
    """
    out: list[dict] = []
    for lk in cop_entity_repo.list_cop_links(src_uid=entity_uid, relation=_RELATION):
        sha = lk.get("target_uid") or ""
        if _valid_sha256(sha):
            out.append(
                {
                    "hash": sha,
                    "name": lk.get("remarks") or sha,
                    "mimeType": lk.get("mime") or "image/jpeg",
                    "direction": lk.get("direction"),
                    "server_purgeable": bool(lk.get("pkg_hash")),
                    "local": True,
                }
            )
    return out


async def delete_attachment(
    sha256: str, *, marker_uid: str | None = None, actor: str | None = None, purge_server: bool = False
) -> dict:
    """#518：刪一張本地附件（現場照片）。方向感知、L1（一律）+ L2（選配）。

    **faction 邊界**：`marker_uid` 給定 → **只刪該 marker 對此 sha 的連結**（caller 已對這個 marker 做
    faction 守門；不得波及同 sha 掛在其他〔可能不可見〕marker 上的連結）。省略時退回全 marker（僅
    內部/測試用；HTTP 路徑一律帶 marker_uid）。

    L1（一律做）：刪該 marker 的 cop_entity_links 連結 + 對其 `pkg_hash` 立**持久墓碑**（擋 #509-P2
    輪詢跨重啟重抓復活）+ 若已無任一 marker 連此 sha 才刪本地檔（`DATA_DIR/tak_attachments/<sha256>`）。
    L2（`purge_server=True` 才做）：`DELETE /Marti/api/files/{pkg_hash}` 從 Enterprise Sync 清整包 zip
    （write cert）。**best-effort、狀態分開回報**：server 刪失敗（未配置/404/HTTP 錯）不推翻 L1——本地
    已刪、墓碑已擋復活；只在回傳標 `server_purged=False` + `server_error`。

    ⚠ 舊資料限制：migration 41 之前存的附件 `pkg_hash` 為 NULL（未記所屬 zip）→ 刪除無從立墓碑，
    被動/輪詢仍可能重橋復活（僅影響本次部署前的既有附件）；新附件皆有 pkg_hash 不受此限。
    誠實紅線：任何刪除都不會讓照片從現場持有裝置本機消失（TAK client 硬限制）——UI 須明講。
    回 dict：{sha256, marker_uid, direction, pkg_hash, local_deleted, server_purged, server_error}。
    查無此本地附件 → local_deleted=False（caller 回 404）。
    """
    from services import tak_files

    result = {
        "sha256": sha256,
        "marker_uid": None,
        "direction": None,
        "pkg_hash": None,
        "local_deleted": False,
        "server_purged": None,  # None=未嘗試（未要求 purge）/ True/False=嘗試結果
        "server_error": None,
    }
    if not _valid_sha256(sha256):
        return result
    # faction 邊界：限定在 caller 守門過的 marker（給定時），不撈同 sha 的其他 marker 連結。
    links = cop_entity_repo.list_cop_links(src_uid=marker_uid, relation=_RELATION, target_uid=sha256)
    if not links:
        return result  # 查無本地附件連結（或該 marker 未連此 sha）
    lk = links[0]
    marker_uid = lk.get("src_uid")
    pkg_hash = lk.get("pkg_hash")
    result.update({"marker_uid": marker_uid, "direction": lk.get("direction"), "pkg_hash": pkg_hash})

    # ── L1：刪連結 + 本地檔 + 立墓碑 ──────────────────────────────────────────
    # 先立墓碑（即使後續步驟中斷，輪詢也不再復活此包）；pkg_hash 缺（舊資料）則跳過墓碑（無從擋）。
    if pkg_hash and _valid_sha256(pkg_hash):
        cop_entity_repo.add_pkg_tombstone(pkg_hash, deleted_by=actor)
    # 刪此 marker 對此 sha 的所有連結列（通常 1 筆）。
    for _link in links:
        cop_entity_repo.delete_cop_link(_link.get("src_uid"), sha256, _RELATION)
    # 本地檔：僅當**沒有其他 marker 仍連此 sha** 才刪實體檔（同一張圖可能被多 marker 共用，罕見但防呆）。
    if not cop_entity_repo.list_cop_links(relation=_RELATION, target_uid=sha256):
        p = local_attachment_path(sha256)
        if p is not None and p.exists():
            try:
                p.unlink()
            except OSError:
                log.warning("[tak] #518 刪本地附件檔失敗（連結已移除）sha=%s", sha256[:12], exc_info=True)
    result["local_deleted"] = True
    log.info("[tak] #518 附件本地刪除 sha=%s marker=%s dir=%s", sha256[:12], marker_uid, result["direction"] or "?")

    # ── L2：連 Enterprise Sync 檔庫清整包（選配、best-effort）──────────────────
    if purge_server:
        if not (pkg_hash and _valid_sha256(pkg_hash)):
            result["server_purged"] = False
            result["server_error"] = "無所屬 zip hash（舊資料未記 pkg_hash），無法定位 server 檔"
        elif not tak_files.filestore_write_enabled():
            result["server_purged"] = False
            result["server_error"] = "TAK file store 寫入未配置（缺 Marti URL 或寫 cert）"
        else:
            client = tak_files._build_write_client()
            try:
                await tak_files.delete_file(client, pkg_hash)
                result["server_purged"] = True
                log.info("[tak] #518 L2 已清 Enterprise Sync zip=%s", pkg_hash[:12])
            except Exception as e:  # noqa: BLE001 — L2 best-effort：不推翻 L1，狀態分開回報
                result["server_purged"] = False
                result["server_error"] = str(e)[:200]
                log.warning("[tak] #518 L2 清 server 檔失敗 zip=%s：%s", pkg_hash[:12], e)
            finally:
                await client.close()
    return result


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
