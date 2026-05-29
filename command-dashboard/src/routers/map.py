import json
import re
import sqlite3
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response

from core.config import MAP_CONFIG_PATH, MBTILES_DIR, SRC_DIR, STATIC_DIR
from services import map_config_store

router = APIRouter(tags=["map"])

_CERT_PATH = SRC_DIR.parent.parent / "certs" / "rootCA.pem"

# map_config schema 防護（issue #24 security review）
# 背景：α PR 把 POST /api/map_config 從 COMMAND_ROLES 開放給 WRITE_ROLES（operator）後，
# 前端 renderer（map.js innerHTML / Leaflet bindTooltip / cop.js openModal title）的
# 既有 HTML sink 變成 operator → commander/sysadmin 提權路徑：operator 在 zone.label / id
# / sub / event_code / flow.label 等字串裡注入 `<img onerror=...>` → 上層 role 載入地圖時
# 被執行。Recursive 走 JSON 把所有 string value 過 HTML-unsafe 字元白名單。
#
# 策略選擇：recursive validator 而非 Pydantic schema —
# (1) map_config 既有結構鬆散（legacy image/label 欄位、未來會加新 entity 類型）
# (2) 攻擊面只在 string content，不在 structure
# (3) 未來新增 sink 自動被保護，不需要再回頭補 schema
_UNSAFE_CHAR_RE = re.compile(r"[<>`{}]|&#|&\w+;|javascript:|data:|vbscript:", re.IGNORECASE)
_MAX_STRING_LEN = 512
_MAX_BODY_BYTES = 256 * 1024  # 256 KB — 一份正常 map_config 約 2-10 KB，給足 polygon/route


def _validate_map_config_strings(obj: object, path: str = "$") -> None:
    """遞迴檢查所有 string value 不含 HTML / JS context-escape 危險字元。
    違反 → 422 reject，aw aw 不寫入 disk。"""
    if isinstance(obj, str):
        if len(obj) > _MAX_STRING_LEN:
            raise HTTPException(
                422,
                f"map_config: 字串過長（{len(obj)} > {_MAX_STRING_LEN}）at {path}",
            )
        if _UNSAFE_CHAR_RE.search(obj):
            raise HTTPException(
                422,
                f"map_config: 含 HTML / JS 危險字元 at {path}：{obj[:50]!r}",
            )
    elif isinstance(obj, dict):
        for key, value in obj.items():
            # key 也要驗（雖然極少被 render，但同樣可能流入 sink）
            if isinstance(key, str) and _UNSAFE_CHAR_RE.search(key):
                raise HTTPException(422, f"map_config: 危險 key at {path}：{key!r}")
            _validate_map_config_strings(value, f"{path}.{key}")
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _validate_map_config_strings(item, f"{path}[{i}]")
    # numbers, booleans, None → 安全，pass


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
    path = MBTILES_DIR / filename
    if not path.exists() or not filename.endswith(".pmtiles"):
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
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"無效 JSON：{e}") from e
    # XSS hardening — 詳見模組頂 _validate_map_config_strings 註釋
    _validate_map_config_strings(body)
    # P1-13：寫 data/map_config.json（atomic write）取代直寫 static/
    map_config_store.write_atomic(body)
    return {"ok": True, "path": str(MAP_CONFIG_PATH)}


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
