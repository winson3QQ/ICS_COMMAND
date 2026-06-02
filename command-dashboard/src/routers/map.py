import json
import sqlite3
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response

from core.config import (
    EVENT_TAXONOMY_PATH,
    MAP_CONFIG_PATH,
    MBTILES_DIR,
    SRC_DIR,
    STATIC_DIR,
)
from core.input_safety import validate_no_unsafe_strings
from services import event_taxonomy_store, map_config_store
from services.event_taxonomy_validate import validate_taxonomy

router = APIRouter(tags=["map"])

_CERT_PATH = SRC_DIR.parent.parent / "certs" / "rootCA.pem"

# map_config POST body 大小上限（route-level）。
# 逐字串 XSS / 長度檢查（issue #24 提權防護）已抽到 core.input_safety.validate_no_unsafe_strings，
# 與 cop entity 寫入路徑共用單一 source of truth（issue #29 PR-B）。
_MAX_BODY_BYTES = 256 * 1024  # 256 KB — 一份正常 map_config 約 2-10 KB，給足 polygon/route


def _get_tile_db(name: str) -> Path:
    path = MBTILES_DIR / f"{name}.mbtiles"
    if not path.exists():
        raise HTTPException(404, f"Tile source '{name}' not found")
    return path


@router.get("/tiles/{source}/{z}/{x}/{y}.png")
def serve_tile(source: str, z: int, x: int, y: int):
    db_path = _get_tile_db(source)
    tms_y = (2**z - 1) - y
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
            (z, x, tms_y),
        ).fetchone()
    if not row:
        raise HTTPException(204, "Tile not found")
    return Response(
        content=row[0],
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/tiles/{source}/metadata")
def tile_metadata(source: str):
    db_path = _get_tile_db(source)
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute("SELECT name, value FROM metadata").fetchall()
    return {k: v for k, v in rows}


@router.get("/tiles/pmtiles/{filename}")
def serve_pmtiles(filename: str, request: Request):
    # 路徑穿越縱深防禦（#64-2）：str path-converter 已不含 '/'，但加 resolve() 容器內檢查，
    # 不靠單一慣例（防編碼繞過/未來路由改 {filename:path}）。先驗格式+容器再 exists（不洩漏外部檔存在）。
    base = MBTILES_DIR.resolve()
    path = (MBTILES_DIR / filename).resolve()
    if not filename.endswith(".pmtiles") or not path.is_relative_to(base) or not path.exists():
        raise HTTPException(404, "PMTiles file not found")
    file_size = path.stat().st_size
    range_header = request.headers.get("Range")
    if not range_header:
        return Response(
            content=path.read_bytes(),
            media_type="application/octet-stream",
            headers={"Accept-Ranges": "bytes", "Content-Length": str(file_size)},
        )
    try:
        _, rng = range_header.split("=")
        start_str, end_str = rng.split("-")
        start = int(start_str)
        end = int(end_str) if end_str else file_size - 1
    except Exception:
        raise HTTPException(416, "Invalid Range header") from None
    if start > end or end >= file_size:
        raise HTTPException(416, "Range Not Satisfiable")
    length = end - start + 1
    with path.open("rb") as fh:
        fh.seek(start)
        data = fh.read(length)
    return Response(
        content=data,
        status_code=206,
        media_type="application/octet-stream",
        headers={
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(length),
            "Cache-Control": "public, max-age=86400",
        },
    )


@router.get("/api/map_config", tags=["system"])
def get_map_config():
    """讀 runtime（data/map_config.json）；不存在則 fallback seed。
    P1-13：前端從直讀 /static/map_config.json 改打這條，讀寫對稱。
    """
    body = map_config_store.read()
    return Response(
        content=json.dumps(body, ensure_ascii=False),
        media_type="application/json",
        # runtime 檔頻繁變動，瀏覽器不准 cache（前端原本用 ?t=timestamp cache-bust 改 API 後集中於此）
        headers={"Cache-Control": "no-store"},
    )


@router.post("/api/map_config", tags=["system"])
async def save_map_config(request: Request):
    # body 大小硬上限（防 disk fill；α PR operator 開放後不該毫無防護地讓任何 writer 灌資料）
    raw = await request.body()
    if len(raw) > _MAX_BODY_BYTES:
        raise HTTPException(
            413,
            f"map_config 過大（{len(raw)} > {_MAX_BODY_BYTES}）",
        )
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, RecursionError, ValueError) as e:
        # RecursionError：深層巢狀 JSON（~20KB 可達）會讓 json.loads 爆 recursion，
        # 非 JSONDecodeError → 原本會 500（DoS）。一併當無效 JSON 擋（review #68 MED）。
        raise HTTPException(400, f"無效 JSON：{e}") from e
    # XSS hardening — 見 core.input_safety.validate_no_unsafe_strings（issue #24 / #29 PR-B）
    try:
        validate_no_unsafe_strings(body, label="map_config")
    except RecursionError as e:
        raise HTTPException(400, "結構過深") from e
    # P1-13：寫 data/map_config.json（atomic write）取代直寫 static/
    map_config_store.write_atomic(body)
    return {"ok": True, "path": str(MAP_CONFIG_PATH)}


# ── 事件分類 taxonomy（P1-10d 地基，issue #60/#66）──────────────────────────
# GET 開放 READ_ROLES（前端渲染事件需要）；POST 限 sysadmin（編輯器 #66）。
# RBAC 在 auth/role_enum.allowed_roles_for 設定。


@router.get("/api/event_taxonomy", tags=["system"])
def get_event_taxonomy():
    body = event_taxonomy_store.read()
    return Response(
        content=json.dumps(body, ensure_ascii=False),
        media_type="application/json",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/api/event_taxonomy", tags=["system"])
async def save_event_taxonomy(request: Request):
    raw = await request.body()
    if len(raw) > _MAX_BODY_BYTES:
        raise HTTPException(413, f"event_taxonomy 過大（{len(raw)} > {_MAX_BODY_BYTES}）")
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, RecursionError, ValueError) as e:
        # RecursionError：深層巢狀 JSON → json.loads 爆 recursion（非 JSONDecodeError），
        # 一併當無效 JSON 擋，避免 500 DoS（review #68 MED；同 /api/map_config）。
        raise HTTPException(400, f"無效 JSON：{e}") from e
    # XSS hardening（與 map_config / cop entity 共用單一 source of truth）
    try:
        validate_no_unsafe_strings(body, label="event_taxonomy")
    except RecursionError as e:
        raise HTTPException(400, "結構過深") from e
    # #66 PR-A：schema + 參照完整性驗證（severity 3 級、cot_type 必填、group 參照、
    # key 格式/唯一、禁改 key/禁硬刪 superset、禁刪非空 group）。previous 取既有做 superset。
    # 已知限制（review #76 MED）：read→write 間無鎖，並發 POST 可 lost-update（os.replace 原子，
    # 不壞檔）。POST 限 sysadmin 單人編輯場景，風險低；version 樂觀鎖（body 已有 version 欄位）留
    # 編輯器 UI（PR-C）一併做。
    try:
        validate_taxonomy(body, previous=event_taxonomy_store.read())
    except ValueError as e:
        raise HTTPException(400, f"event_taxonomy 驗證失敗：{e}") from e
    event_taxonomy_store.write_atomic(body)
    return {"ok": True, "path": str(EVENT_TAXONOMY_PATH)}


@router.post("/api/map/upload-image", tags=["system"])
async def upload_map_image(request: Request, file: UploadFile = File(...)):
    # session 由 middleware 強制，不必再 access — 原 `request.state.session` 是 B018 dead code
    filename = file.filename or "map.jpg"
    ext = filename.rsplit(".", 1)[-1].lower()
    if ext not in {"jpg", "jpeg", "png", "gif", "webp", "svg"}:
        raise HTTPException(400, f"invalid image extension: {ext}")
    content = await file.read()
    save_path = STATIC_DIR / filename
    save_path.write_bytes(content)
    return {"ok": True, "filename": filename}


@router.get("/cert")
def download_cert():
    if not _CERT_PATH.exists():
        raise HTTPException(404, "rootCA.pem not found")
    return FileResponse(str(_CERT_PATH), filename="rootCA.pem")


@router.get("/cert/install", response_class=HTMLResponse)
def cert_install_page():
    return """<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ICS CA</title></head>
<body style="font-family:sans-serif;padding:20px">
<h2>ICS local CA</h2>
<p><a href="/cert" style="font-size:1.2em">Download rootCA.pem</a></p>
</body></html>"""
