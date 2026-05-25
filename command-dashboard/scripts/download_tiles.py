#!/usr/bin/env python3
"""下載新竹地區 OSM raster tiles 並打包成 MBTiles（SQLite）。
用法：python3 download_tiles.py [--zoom-min 10] [--zoom-max 16]
"""
import argparse, math, os, sqlite3, ssl, time, urllib.request, urllib.error
import certifi
SSL_CTX = ssl.create_default_context(cafile=certifi.where())

# 新竹地區範圍（WGS84）
BBOX = {"west": 120.85, "east": 121.20, "south": 24.65, "north": 24.95}
TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
USER_AGENT = "ICS_DMAS_PoC/1.0 (educational/disaster-drill use; contact via github.com/winson3QQ/ICS_DMAS)"
OUTPUT = os.path.join(os.path.dirname(__file__), "../static/tiles/hsinchu.mbtiles")

def deg2tile(lat, lon, zoom):
    n = 2 ** zoom
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n)
    return x, y

def tile_range(bbox, zoom):
    x0, y0 = deg2tile(bbox["north"], bbox["west"], zoom)
    x1, y1 = deg2tile(bbox["south"], bbox["east"], zoom)
    return range(min(x0,x1), max(x0,x1)+1), range(min(y0,y1), max(y0,y1)+1)

def tms_y(y, zoom):
    return (2**zoom - 1) - y  # Leaflet XYZ → TMS

def init_mbtiles(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE IF NOT EXISTS tiles (
        zoom_level INTEGER, tile_column INTEGER, tile_row INTEGER,
        tile_data BLOB, PRIMARY KEY(zoom_level, tile_column, tile_row))""")
    db.execute("""CREATE TABLE IF NOT EXISTS metadata (name TEXT, value TEXT, PRIMARY KEY(name))""")
    for k, v in [("name","Hsinchu OSM"),("format","png"),("type","baselayer"),
                 ("minzoom","10"),("maxzoom","16"),
                 ("bounds",f"{BBOX['west']},{BBOX['south']},{BBOX['east']},{BBOX['north']}"),
                 ("center","120.97,24.80,13")]:
        db.execute("INSERT OR REPLACE INTO metadata VALUES(?,?)", (k,v))
    db.commit()
    return db

def count_total(zoom_min, zoom_max):
    total = 0
    for zoom in range(zoom_min, zoom_max+1):
        xs, ys = tile_range(BBOX, zoom)
        total += len(list(xs)) * len(list(ys))
    return total

def download_tiles(zoom_min, zoom_max):
    db = init_mbtiles(OUTPUT)
    total = count_total(zoom_min, zoom_max)
    done = 0
    for zoom in range(zoom_min, zoom_max+1):
        xs, ys = tile_range(BBOX, zoom)
        for x in xs:
            for y in ys:
                row = db.execute(
                    "SELECT 1 FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
                    (zoom, x, tms_y(y, zoom))).fetchone()
                if row:
                    done += 1
                    continue
                url = TILE_URL.format(z=zoom, x=x, y=y)
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                    with urllib.request.urlopen(req, timeout=10, context=SSL_CTX) as resp:
                        data = resp.read()
                    db.execute("INSERT OR REPLACE INTO tiles VALUES(?,?,?,?)",
                               (zoom, x, tms_y(y, zoom), data))
                    db.commit()
                except urllib.error.HTTPError as e:
                    print(f"  HTTP {e.code}: z{zoom}/{x}/{y}")
                except Exception as e:
                    print(f"  Error z{zoom}/{x}/{y}: {e}")
                done += 1
                if done % 50 == 0:
                    print(f"  [{done}/{total}] zoom={zoom} x={x} y={y}")
                time.sleep(0.15)  # OSM tile usage policy: polite throttle
    db.close()
    size = os.path.getsize(OUTPUT) / 1024 / 1024
    print(f"\n完成：{OUTPUT} ({size:.1f} MB)")

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--zoom-min", type=int, default=10)
    p.add_argument("--zoom-max", type=int, default=16)
    args = p.parse_args()
    print(f"下載 zoom {args.zoom_min}~{args.zoom_max}，新竹範圍...")
    print(f"預計 tile 數：{count_total(args.zoom_min, args.zoom_max)}")
    download_tiles(args.zoom_min, args.zoom_max)
