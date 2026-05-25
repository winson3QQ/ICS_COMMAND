import json
import sqlite3
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response

from core.config import MBTILES_DIR, SRC_DIR, STATIC_DIR

router = APIRouter(tags=["map"])

_CERT_PATH = SRC_DIR.parent.parent / "certs" / "rootCA.pem"


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


@router.post("/api/map_config", tags=["system"])
async def save_map_config(request: Request):
    body = await request.json()
    config_path = STATIC_DIR / "map_config.json"
    config_path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "path": str(config_path)}


@router.post("/api/map/upload-image", tags=["system"])
async def upload_map_image(request: Request, file: UploadFile = File(...)):
    request.state.session
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
