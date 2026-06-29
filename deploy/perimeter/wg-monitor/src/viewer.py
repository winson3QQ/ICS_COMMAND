# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""viewer.py — wg-monitor 唯讀網頁（#447 Phase 5）。

獨立第二支容器：只**唯讀**監測自己的 SQLite（collector 寫、viewer 看），自己的埠出一頁
HTML（peer 連線狀態 + 近期告警/事件）。stdlib only（http.server + sqlite3），無外部依賴。

紅線：
- DB 以 read-only 模式開（`mode=ro`），viewer 不寫。
- endpoint = 真實公網 IP（PII，#388）→ **預設綁 localhost**（VIEWER_BIND），勿曝公網；
  所有 DB 值 html.escape 後才入 DOM（防注入，縱深）。
"""

from __future__ import annotations

import html
import os
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DB = os.environ.get("WGMON_DB", "/data/wg-monitor.db")
PORT = int(os.environ.get("VIEWER_PORT", "8088"))
BIND = os.environ.get("VIEWER_BIND", "0.0.0.0")  # 容器內綁全介面；對外可達範圍由 compose port 綁定決定
REFRESH_S = int(os.environ.get("VIEWER_REFRESH_S", "5"))

_SEV_COLOR = {"critical": "#ff5252", "warning": "#ffb300", "info": "#9e9e9e"}


def _q(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    """唯讀查詢；DB 不存在（collector 還沒建）時回空。"""
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return []
    con.row_factory = sqlite3.Row
    try:
        return con.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []  # 表還沒建
    finally:
        con.close()


def _ago(ts: int, now: int) -> str:
    if not ts:
        return "—"
    d = now - ts
    if d < 0:
        return "未來?"
    if d < 60:
        return f"{d}s 前"
    if d < 3600:
        return f"{d // 60}m 前"
    if d < 86400:
        return f"{d // 3600}h 前"
    return f"{d // 86400}d 前"


def _bytes(n: int) -> str:
    f = float(n)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if f < 1024 or unit == "GiB":
            return f"{f:.0f} {unit}" if unit == "B" else f"{f:.1f} {unit}"
        f /= 1024
    return f"{n} B"


def _esc(v) -> str:
    return html.escape(str(v if v is not None else "—"))


def _dot(online: int) -> str:
    color = "#37d67a" if online else "#888"
    label = "線上" if online else "離線"
    return f'<span style="color:{color}">●</span> {label}'


def render() -> str:
    now = int(time.time())
    peers = _q("SELECT * FROM peer_state ORDER BY online DESC, last_seen_ts DESC")
    alerts = _q("SELECT * FROM alerts ORDER BY id DESC LIMIT 20")
    events = _q("SELECT * FROM events ORDER BY id DESC LIMIT 30")

    rows_p = (
        "".join(
            f"<tr><td>{_dot(p['online'])}</td><td class=mono>{_esc(p['pubkey'][:16])}…</td>"
            f"<td class=mono>{_esc(p['last_endpoint_ip'])}</td><td>{_ago(p['last_handshake'], now)}</td>"
            f"<td>↓{_bytes(p['last_rx'])} ↑{_bytes(p['last_tx'])}</td></tr>"
            for p in peers
        )
        or "<tr><td colspan=5 class=empty>尚無 peer（等 collector 讀一輪 wg）</td></tr>"
    )

    rows_a = (
        "".join(
            f"<tr><td>{_ago(a['ts'], now)}</td>"
            f"<td><b style='color:{_SEV_COLOR.get(a['severity'], '#ccc')}'>{_esc(a['severity'])}</b></td>"
            f"<td class=mono>{_esc(a['kind'])}</td><td>{_esc(a['summary'])}</td>"
            f"<td>{'✓' if a['delivered'] else '—'}</td></tr>"
            for a in alerts
        )
        or "<tr><td colspan=5 class=empty>尚無告警</td></tr>"
    )

    rows_e = (
        "".join(
            f"<tr><td>{_ago(e['ts'], now)}</td>"
            f"<td style='color:{_SEV_COLOR.get(e['severity'], '#ccc')}'>{_esc(e['severity'])}</td>"
            f"<td class=mono>{_esc(e['kind'])}</td><td class=mono>{_esc(e['detail'])}</td></tr>"
            for e in events
        )
        or "<tr><td colspan=4 class=empty>尚無事件</td></tr>"
    )

    n_online = sum(1 for p in peers if p["online"])
    n_crit = sum(1 for a in alerts if a["severity"] == "critical")
    return f"""<!doctype html><html lang=zh-Hant><head><meta charset=utf-8>
<meta http-equiv=refresh content={REFRESH_S}>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>WG 連線監測</title><style>
body{{background:#11151c;color:#cdd6e0;font:14px/1.5 -apple-system,Segoe UI,system-ui,sans-serif;margin:0;padding:18px}}
h1{{font-size:18px;margin:0 0 4px}} .sub{{color:#7c8a9e;font-size:12px;margin-bottom:16px}}
h2{{font-size:14px;color:#9fb0c4;margin:22px 0 8px;border-bottom:1px solid #222c38;padding-bottom:4px}}
table{{border-collapse:collapse;width:100%;font-size:13px}}
th,td{{text-align:left;padding:6px 10px;border-bottom:1px solid #1b232e}}
th{{color:#7c8a9e;font-weight:600}} .mono{{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px}}
.empty{{text-align:center;color:#667}}
.pill{{display:inline-block;background:#1b2430;border-radius:10px;padding:2px 10px;margin-right:8px}}
</style></head><body>
<h1>WG 連線監測</h1>
<div class=sub>每 {REFRESH_S}s 自動刷新 · DB={_esc(DB)} ·
<span class=pill>線上 peer {n_online}/{len(peers)}</span>
<span class=pill>近期 critical {n_crit}</span></div>
<h2>裝置連線（peer）</h2>
<table><tr><th>狀態</th><th>pubkey</th><th>endpoint（公網IP）</th><th>最後握手</th><th>流量</th></tr>{rows_p}</table>
<h2>告警（最近 20）</h2>
<table><tr><th>時間</th><th>嚴重度</th><th>kind</th><th>摘要</th><th>投遞</th></tr>{rows_a}</table>
<h2>事件（最近 30）</h2>
<table><tr><th>時間</th><th>嚴重度</th><th>kind</th><th>detail</th></tr>{rows_e}</table>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/", "/index.html"):
            self.send_error(404)
            return
        body = render().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 靜音預設逐請求 log
        pass


def main() -> int:
    print(f"[wg-viewer] http://{BIND}:{PORT}  DB={DB}", flush=True)
    ThreadingHTTPServer((BIND, PORT), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
