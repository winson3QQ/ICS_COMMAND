# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""store.py — wg-monitor 自己的 SQLite（**獨立於 ICS，零 import ICS code**）。

四張表：
  peer_state        每個 pubkey 的當前狀態（供 diff）
  endpoint_history  (pubkey, ts, ip) 觀測流水 → 算振盪窗 distinct IP + 給 viewer
  events            所有偵測轉換（含 info）；稽核全紀錄
  alerts            append-only 告警 log（warning/critical）；**權威真相來源、不依賴投遞成功**

紅線：endpoint IP 是裝置真實公網 IP（PII，#388）；server 私鑰永不進此庫（parser 已濾）。
"""

from __future__ import annotations

import json
import sqlite3

from detect import Detection, PriorState
from wg_dump import PeerSample

_SCHEMA = """
CREATE TABLE IF NOT EXISTS peer_state (
    pubkey            TEXT PRIMARY KEY,
    last_handshake    INTEGER NOT NULL DEFAULT 0,
    last_endpoint_ip  TEXT,
    last_rx           INTEGER NOT NULL DEFAULT 0,
    last_tx           INTEGER NOT NULL DEFAULT 0,
    last_seen_ts      INTEGER NOT NULL DEFAULT 0,
    online            INTEGER NOT NULL DEFAULT 0,
    vol_baseline      REAL    NOT NULL DEFAULT 0,
    vol_samples       INTEGER NOT NULL DEFAULT 0,
    first_seen_ts     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS endpoint_history (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    pubkey  TEXT NOT NULL,
    ts      INTEGER NOT NULL,
    ip      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_eph_pubkey_ts ON endpoint_history(pubkey, ts);
CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        INTEGER NOT NULL,
    pubkey    TEXT NOT NULL,
    kind      TEXT NOT NULL,
    severity  TEXT NOT NULL,
    detail    TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE TABLE IF NOT EXISTS alerts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         INTEGER NOT NULL,
    pubkey     TEXT NOT NULL,
    kind       TEXT NOT NULL,
    severity   TEXT NOT NULL,
    summary    TEXT NOT NULL,
    detail     TEXT,
    delivered  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_alerts_lookup ON alerts(pubkey, kind, ts);
"""


class Store:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def init(self) -> None:
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ── endpoint history（振盪偵測 + viewer）──
    def record_endpoint(self, pubkey: str, ts: int, ip: str) -> None:
        self.conn.execute("INSERT INTO endpoint_history(pubkey, ts, ip) VALUES (?, ?, ?)", (pubkey, ts, ip))
        self.conn.commit()

    def recent_endpoint_history(self, pubkey: str, since_ts: int) -> list[str]:
        """窗內 endpoint 變更序列（依時間排序、含重訪）。重訪（A→B→A）是 cloned-key 的關鍵訊號。"""
        rows = self.conn.execute(
            "SELECT ip FROM endpoint_history WHERE pubkey=? AND ts>=? ORDER BY ts", (pubkey, since_ts)
        ).fetchall()
        return [r["ip"] for r in rows]

    # ── peer_state ──
    def load_prior(self, pubkey: str) -> PriorState | None:
        r = self.conn.execute("SELECT * FROM peer_state WHERE pubkey=?", (pubkey,)).fetchone()
        if r is None:
            return None
        return PriorState(
            pubkey=r["pubkey"],
            last_handshake=r["last_handshake"],
            last_endpoint_ip=r["last_endpoint_ip"],
            last_rx=r["last_rx"],
            last_tx=r["last_tx"],
            last_seen_ts=r["last_seen_ts"],
            online=bool(r["online"]),
            vol_baseline=r["vol_baseline"],
            vol_samples=r["vol_samples"],
        )

    def upsert_peer_state(
        self,
        cur: PeerSample,
        now: int,
        online: bool,
        vol_baseline: float,
        vol_samples: int,
        first_seen_ts: int,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO peer_state
                (pubkey, last_handshake, last_endpoint_ip, last_rx, last_tx, last_seen_ts,
                 online, vol_baseline, vol_samples, first_seen_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(pubkey) DO UPDATE SET
                last_handshake=excluded.last_handshake,
                last_endpoint_ip=excluded.last_endpoint_ip,
                last_rx=excluded.last_rx,
                last_tx=excluded.last_tx,
                last_seen_ts=excluded.last_seen_ts,
                online=excluded.online,
                vol_baseline=excluded.vol_baseline,
                vol_samples=excluded.vol_samples
            """,
            (
                cur.pubkey,
                cur.last_handshake,
                cur.endpoint_ip,
                cur.rx_bytes,
                cur.tx_bytes,
                now,
                1 if online else 0,
                vol_baseline,
                vol_samples,
                first_seen_ts,
            ),
        )
        self.conn.commit()

    # ── events（全紀錄）──
    def add_event(self, ts: int, det: Detection) -> None:
        self.conn.execute(
            "INSERT INTO events(ts, pubkey, kind, severity, detail) VALUES (?, ?, ?, ?, ?)",
            (ts, det.pubkey, det.kind, det.severity, json.dumps(det.detail, ensure_ascii=False)),
        )
        self.conn.commit()

    # ── alerts（append-only，權威）──
    def add_alert(self, ts: int, det: Detection, severity: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO alerts(ts, pubkey, kind, severity, summary, detail) VALUES (?, ?, ?, ?, ?, ?)",
            (ts, det.pubkey, det.kind, severity, det.summary, json.dumps(det.detail, ensure_ascii=False)),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def mark_delivered(self, alert_id: int) -> None:
        self.conn.execute("UPDATE alerts SET delivered=1 WHERE id=?", (alert_id,))
        self.conn.commit()

    def last_alert_ts(self, pubkey: str, kind: str) -> int | None:
        r = self.conn.execute("SELECT MAX(ts) AS t FROM alerts WHERE pubkey=? AND kind=?", (pubkey, kind)).fetchone()
        return r["t"] if r and r["t"] is not None else None

    def recent_alert_count(self, pubkey: str, kind: str, since_ts: int) -> int:
        r = self.conn.execute(
            "SELECT COUNT(*) AS c FROM alerts WHERE pubkey=? AND kind=? AND ts>=?",
            (pubkey, kind, since_ts),
        ).fetchone()
        return int(r["c"]) if r else 0

    # ── 保留：清過期 history/events（alerts 永久保留＝稽核存證）──
    def prune(self, older_than_ts: int) -> None:
        self.conn.execute("DELETE FROM endpoint_history WHERE ts < ?", (older_than_ts,))
        self.conn.execute("DELETE FROM events WHERE ts < ?", (older_than_ts,))
        self.conn.commit()
