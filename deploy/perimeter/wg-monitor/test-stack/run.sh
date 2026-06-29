#!/usr/bin/env bash
# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
# run.sh — #447 自帶整合測試棧一鍵跑。
# 起真 WireGuard（server + 兩個「同 key」client = cloned-key）+ 監測，驅動 endpoint A→B→A
# （同一把 key 從兩處輪流使用＝真實盜鑰情境），讓監測在**真 wg show** 上偵測到擺盪，印 PASS/FAIL。
# 獨立、不碰 prod/ICS/TAK。清理：docker compose down -v。
set -uo pipefail
cd "$(dirname "$0")"

echo "── #447 wg-monitor 整合測試棧（真 WireGuard + cloned-key）──"
docker compose up -d --build || { echo "FAIL: docker compose up（WSL2 需 kernel WireGuard 模組）"; exit 1; }

phase() { # $1 active, $2 idle, $3 說明
  docker compose unpause "$1" >/dev/null 2>&1 || true
  docker compose pause "$2" >/dev/null 2>&1 || true
  echo "  $3（持有者切到 $1，14s）"
  sleep 14
}

echo "等初次握手（12s）…"; sleep 12
echo "驅動 endpoint A→B→A（每段 14s 給監測輪詢）："
phase client-a client-b "Phase 1：client-a 持有"
phase client-b client-a "Phase 2：切到 client-b"
phase client-a client-b "Phase 3：切回 client-a（重訪）"
docker compose unpause client-b >/dev/null 2>&1 || true  # 還原

echo
echo "── 監測 DB ──"
docker compose exec -T wg-monitor python3 - <<'PY'
import sqlite3
c = sqlite3.connect("/data/wg-monitor.db")
print("peer_state:", c.execute("SELECT substr(pubkey,1,12)||'…', last_endpoint_ip, online FROM peer_state").fetchall())
print("監測記錄到的來源 IP（應有 2 個＝兩台 cloned 裝置）:", [r[0] for r in c.execute("SELECT DISTINCT ip FROM endpoint_history").fetchall()])
print("events:", c.execute("SELECT kind, COUNT(*) FROM events GROUP BY kind").fetchall())
alerts = c.execute("SELECT kind, severity FROM alerts").fetchall()
print("alerts:", alerts)
print()
osc = [a for a in alerts if a[0] == "endpoint_oscillation"]
if osc:
    print(f"PASS：真 WireGuard 上偵測到 cloned-key endpoint 擺盪 → {osc[0]}")
else:
    print("尚未觸發；可再跑一次，或 docker exec wgtest-server wg show wg0 看 endpoint 是否在 A/B 間變。")
PY

echo
echo "清理：docker compose -f \"$(pwd)/docker-compose.yml\" down -v"
