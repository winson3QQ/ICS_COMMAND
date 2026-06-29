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
# 前置：docker 要在 PATH 上。從 cmd 打 'bash' 會進 WSL2，若該 distro 沒開 Docker Desktop WSL 整合就找不到
# docker → 請在 Docker Desktop 開 WSL Integration，或改用 Git Bash 跑（docker.exe 已在 PATH）。
command -v docker >/dev/null 2>&1 || {
  echo "FAIL: 找不到 docker。"
  echo "  → 在 Docker Desktop 開 WSL Integration（Settings→Resources→WSL Integration），或改用 Git Bash 跑本腳本。"
  exit 1
}
docker compose up -d --build || { echo "FAIL: docker compose up（若 'ip link add wg0' 報錯＝核心無 WireGuard 模組）"; exit 1; }

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
alerts = c.execute("SELECT kind, severity, delivered FROM alerts").fetchall()
print("alerts（kind, severity, delivered）:", alerts)
print()
osc = [a for a in alerts if a[0] == "endpoint_oscillation"]
if osc:
    print(f"PASS：真 WireGuard 上偵測到 cloned-key endpoint 擺盪 → {osc[0][:2]}")
    if any(a[2] == 1 for a in alerts):
        print("PASS：告警已成功投遞自架 ntfy（delivered=1）。")
    else:
        print("注意：偵測 OK 但投遞未標 delivered（ntfy 未就緒?）。")
else:
    print("尚未觸發；可再跑一次，或 docker exec wgtest-server wg show wg0 看 endpoint 是否在 A/B 間變。")
PY

echo
echo "── ntfy 推播內容（自架 server 已收到的訊息）──"
curl -s "http://localhost:8080/wg-alerts/json?poll=1" 2>/dev/null | head -5 || echo "（curl 不可用；改在瀏覽器開 http://localhost:8080/wg-alerts）"
echo
echo "→ 手機驗推播：ntfy app 訂閱  http://<本機LAN-IP>:8080/wg-alerts  後重跑，即時收到通知。"
echo "清理：docker compose -f \"$(pwd)/docker-compose.yml\" down -v"
