# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""monitor.py — wg-monitor 主迴圈（#447 collector）。

形狀：站在 ics-wg 的 netns 旁，定時 `wg show <iface> dump` → 解析 → 落帳 → 偵測 → 告警。
**獨立小系統、零 import ICS code、stdlib only（urllib/sqlite3/subprocess）。**

  while True:
    raw = wg show wg0 dump
    samples = parse（丟棄含 server 私鑰的 interface 行）
    for peer: 記 endpoint history → 偵測 → events（全紀錄）→ alertable 收集 → 更新 peer_state
    for alertable: AlertManager.handle（去抖/升級/落 alerts/投遞 ntfy）
    prune 過期 history/events
    sleep
"""

from __future__ import annotations

import subprocess
import sys
import time

import config
from alert import AlertManager
from detect import detect_peer, is_online, next_baseline
from peermap import load_map
from store import Store
from wg_dump import parse_wg_dump


def _log(msg: str) -> None:
    print(f"[wg-monitor {int(time.time())}] {msg}", file=sys.stderr, flush=True)


def run_wg_dump(iface: str, timeout_s: int = 10) -> str:
    """跑 `wg show <iface> dump`（需與 ics-wg 共享 netns + NET_ADMIN）。回原始 stdout。"""
    proc = subprocess.run(
        ["wg", "show", iface, "dump"],
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"wg show 失敗 rc={proc.returncode}: {proc.stderr.strip()}")
    return proc.stdout


def run_once(store: Store, cfg, now: int, dump_text: str) -> list:
    """處理單輪 dump 文字（注入式，便於測試）。回 alertable detections（warning/critical）。"""
    samples = parse_wg_dump(dump_text)
    pmap = load_map(cfg.peermap_path)  # 輕耦合：pubkey→callsign（空檔 → {}，退回只顯 pubkey）
    alertable = []
    for s in samples:
        online = is_online(s.last_handshake, now, cfg.offline_after_s)
        prior = store.load_prior(s.pubkey)
        prev_ip = prior.last_endpoint_ip if prior else None
        # 只在「endpoint 變更」時記 history（on-change）：省 DB、且讓重訪（A→B→A）= 序列長於 distinct，
        # 成為 cloned-key 的乾淨訊號（單次漫遊不會回頭、不誤判）。
        if online and s.endpoint_ip and s.endpoint_ip != prev_ip:
            store.record_endpoint(s.pubkey, now, s.endpoint_ip)
        recent_ips = store.recent_endpoint_history(s.pubkey, now - cfg.oscillation_window_s)

        callsign = pmap.get(s.pubkey)
        for det in detect_peer(s, prior, recent_ips, now, cfg):
            if callsign:
                det.detail["callsign"] = callsign  # 豐富化：人話標籤入 detail（流向 events/alerts/ntfy）
            store.add_event(now, det)  # 全紀錄（含 info）
            if det.severity in ("warning", "critical"):
                alertable.append(det)

        baseline, samples_n = next_baseline(prior, s)
        # first_seen_ts 只在 INSERT（首見）生效；upsert 的 ON CONFLICT 不更新它 → 用 now 即可。
        store.upsert_peer_state(s, now, online, baseline, samples_n, now)
    return alertable


def main() -> int:
    cfg = config.from_env()
    store = Store(cfg.db_path)
    store.init()
    am = AlertManager(store, cfg)
    ntfy_state = "on" if cfg.ntfy_url else "off(僅 log)"
    _log(f"啟動：iface={cfg.iface} poll={cfg.poll_interval_s}s db={cfg.db_path} ntfy={ntfy_state}")
    while True:
        now = int(time.time())
        try:
            dump_text = run_wg_dump(cfg.iface)
            alertable = run_once(store, cfg, now, dump_text)
            for det in alertable:
                am.handle(det, now)
            store.prune(now - cfg.retention_days * 86400)
        except Exception as exc:  # noqa: BLE001 — 單輪錯誤不可殺掉 loop（監測要長活）
            _log(f"本輪錯誤（續行）：{exc}")
        time.sleep(cfg.poll_interval_s)


if __name__ == "__main__":
    raise SystemExit(main())
