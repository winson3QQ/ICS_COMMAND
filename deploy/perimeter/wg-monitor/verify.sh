#!/usr/bin/env bash
# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
#
# verify.sh — #447 一鍵真機驗證（在 prod 機器上跑）。
# 自動找 ics-wg 容器 → 起監測 → 讀一輪 wg → 把「真機 peer」對「監測記錄」→ 印 PASS/FAIL。
# 只做唯讀檢查 + compose up；不碰 ICS、不改 live WG。
set -uo pipefail
cd "$(dirname "$0")"

echo "── #447 wg-monitor 真機驗證 ──"

# 1) 找 ics-wg 容器名（共享其 netns 才讀得到 wg）
WG="$(docker ps --filter "name=ics-wg" --format '{{.Names}}' | head -1)"
if [ -z "$WG" ]; then
  echo "FAIL: 找不到 ics-wg 容器。先把它起來：docker compose --profile wg up -d（見 deploy/prod）"
  exit 1
fi
echo "✓ ics-wg 容器 = $WG"

# 2) 備 .env（不覆蓋既有）+ 寫入偵測到的容器名（冪等）
[ -f .env ] || cp .env.example .env
if grep -q '^WGMON_WG_CONTAINER=' .env; then
  sed -i "s|^WGMON_WG_CONTAINER=.*|WGMON_WG_CONTAINER=$WG|" .env
else
  echo "WGMON_WG_CONTAINER=$WG" >>.env
fi

# 3) build + 起監測
echo "── build + 啟動監測 ──"
docker compose --env-file .env up -d --build || { echo "FAIL: docker compose up"; exit 1; }

# 4) 等一輪輪詢（poll 預設 20s）
echo "等 25s 讓監測讀一輪 wg show…"
sleep 25

echo "── 監測 log（最後 10 行）──"
docker compose logs --tail=10 wg-monitor

# 5) 真相：live wg 的 peer 數（跳第一行 interface）
LIVE="$(docker exec "$WG" wg show wg0 dump | tail -n +2 | grep -c . || true)"
echo "── 真機 live WG：$LIVE 個 peer ──"

# 6) 監測讀到並記錄的 peer_state
echo "── 監測記錄的 peer_state ──"
docker compose exec -T wg-monitor python3 - "$LIVE" <<'PY'
import sqlite3, sys
live = int(sys.argv[1])
c = sqlite3.connect("/data/wg-monitor.db")
rows = c.execute("SELECT substr(pubkey,1,12)||'…', last_endpoint_ip, online FROM peer_state").fetchall()
print(f"監測記錄 {len(rows)} 個 peer（pubkey前綴 / endpoint / online）：")
for r in rows:
    print("   ", r)
ev = c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
al = c.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
print(f"events={ev}  alerts={al}")
print()
if len(rows) == live and live > 0:
    print(f"PASS：監測讀到的 peer 數（{len(rows)}）= 真機 live peer 數（{live}）→ 核心管線通（parser + netns + 落帳）。")
elif live == 0:
    print("注意：目前沒有任何真機 peer 在線（先讓一台裝置連 WG），再重跑本腳本。")
else:
    print(f"FAIL：監測記錄 {len(rows)} ≠ 真機 {live}。貼 log + 此輸出到 PR #451（VERIFY-FAIL）。")
PY

echo
echo "── 私鑰紅線 ──"
echo "✓ parser 只取 8 欄 peer 行、丟含 server 私鑰的 4 欄 interface 行；單測 test_server_private_key_never_persisted 已守。"
echo
echo "── 進階（選做，驗偵測器真的會動）──"
echo "  liveness：關掉一台手機的 WG，等 > 180s，再："
echo "    docker compose exec -T wg-monitor python3 -c \"import sqlite3;print(sqlite3.connect('/data/wg-monitor.db').execute('SELECT kind,severity FROM events ORDER BY id DESC LIMIT 5').fetchall())\""
echo "    → 預期出現 ('offline','info')"
echo "  盜鑰：把同一把 WG config 匯入第二台裝置、兩台同時開 → endpoint 來回擺盪 →"
echo "    docker compose exec -T wg-monitor python3 -c \"import sqlite3;print(sqlite3.connect('/data/wg-monitor.db').execute('SELECT kind,severity FROM alerts ORDER BY id DESC LIMIT 3').fetchall())\""
echo "    → 預期出現 ('endpoint_oscillation','critical')。驗完移除第二台。"
echo
echo "全過 → 在 PR #451 留 VERIFY-PASS；哪步掛 → VERIFY-FAIL: <step> <現象>。"
