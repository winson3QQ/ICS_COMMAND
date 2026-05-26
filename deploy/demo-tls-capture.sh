#!/usr/bin/env bash
# demo-tls-capture.sh — TLS 加密效果可視化 demo（C1-B 演練 / 客戶簡報用）
#
# 對照組：HTTP（明文）vs HTTPS（密文）
#
# 用法：sudo bash deploy/demo-tls-capture.sh
#
# 前置：
#   - ./start_mac_https.sh 已在跑（HTTPS stack）
#   - 並另開 terminal 跑 ./start_mac.sh 也在跑（HTTP fast path），或腳本會自動啟臨時 HTTP server
#
# 輸出：
#   /tmp/ics-tls-demo/http.pcap  + http.txt（明文可讀）
#   /tmp/ics-tls-demo/https.pcap + https.txt（密文 gibberish）
#   並在 terminal 印 side-by-side 對照

set -eo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "✗ 需 sudo 跑 tcpdump on lo0" >&2
  echo "  執行：sudo bash $0" >&2
  exit 1
fi

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="/tmp/ics-tls-demo"
mkdir -p "$OUT_DIR"
rm -f "$OUT_DIR"/*

# sudo 會把 $HOME 改成 /var/root，需用 SUDO_USER 還原真實 home
REAL_HOME=$(eval echo "~${SUDO_USER:-$USER}")
ROOT_CA="$REAL_HOME/.step/certs/root_ca.crt"

if [[ ! -f "$ROOT_CA" ]]; then
  echo "✗ 找不到 root CA：$ROOT_CA" >&2
  echo "  檢查：ls -la $REAL_HOME/.step/certs/" >&2
  exit 1
fi

echo "======================================"
echo "  C1-B TLS 加密效果 demo"
echo "======================================"

# ── 確認 HTTPS stack 在跑 ────────────────────────────────
if ! curl -s --max-time 2 --cacert "$ROOT_CA" --resolve command.ics.local:443:127.0.0.1 https://command.ics.local/api/health > /dev/null 2>&1; then
  echo "✗ HTTPS stack 沒跑。先在另一個 terminal 起 ./start_mac_https.sh" >&2
  exit 1
fi
echo "✓ HTTPS stack 在跑"

# ── 啟臨時 plain HTTP server（如果 8000 沒人）────────────
TEMP_HTTP_PID=""
if ! curl -s --max-time 2 http://127.0.0.1:8001/api/health > /dev/null 2>&1; then
  echo "[啟臨時 HTTP] 沒找到 :8001 上的 plain HTTP，啟一個給對照用..."
  cd "$REPO/command-dashboard"
  sudo -u $SUDO_USER .venv/bin/uvicorn main:app --app-dir src \
    --host 127.0.0.1 --port 8001 > /tmp/ics-demo-http.log 2>&1 &
  TEMP_HTTP_PID=$!
  sleep 3
fi

# ── 抓 HTTP（plain）──────────────────────────────────────
echo ""
echo "[1/2] 抓 HTTP plain（port 8001）..."
tcpdump -i lo0 -w "$OUT_DIR/http.pcap" -c 30 'tcp port 8001' &
HTTP_TCPDUMP_PID=$!
sleep 1
curl -s http://127.0.0.1:8001/api/health > /dev/null
sleep 1
kill $HTTP_TCPDUMP_PID 2>/dev/null || true
wait $HTTP_TCPDUMP_PID 2>/dev/null || true

# ── 抓 HTTPS ─────────────────────────────────────────────
echo "[2/2] 抓 HTTPS（port 443）..."
tcpdump -i lo0 -w "$OUT_DIR/https.pcap" -c 80 'tcp port 443' &
HTTPS_TCPDUMP_PID=$!
sleep 1
curl -s --cacert "$ROOT_CA" --resolve command.ics.local:443:127.0.0.1 https://command.ics.local/api/health > /dev/null
sleep 1
kill $HTTPS_TCPDUMP_PID 2>/dev/null || true
wait $HTTPS_TCPDUMP_PID 2>/dev/null || true

# ── 解析（ASCII dump）────────────────────────────────────
tcpdump -r "$OUT_DIR/http.pcap"  -A -nn 2>/dev/null > "$OUT_DIR/http.txt"
tcpdump -r "$OUT_DIR/https.pcap" -A -nn 2>/dev/null > "$OUT_DIR/https.txt"

# ── 收掉臨時 server ──────────────────────────────────────
[[ -n "$TEMP_HTTP_PID" ]] && kill $TEMP_HTTP_PID 2>/dev/null || true

# ── 印對照 ─────────────────────────────────────────────
echo ""
echo "======================================"
echo "  HTTP（明文）— 看得到 GET 路徑與 JSON 回應"
echo "======================================"
grep -E "GET|HTTP/1.1|status|version" "$OUT_DIR/http.txt" | head -10

echo ""
echo "======================================"
echo "  HTTPS（密文）— 同樣請求，但只剩二進位 gibberish"
echo "======================================"
echo "（搜 GET / status / api 結果應為 0 行）"
grep -cE "GET /api/health|\"status\":\"ok\"|\{\"status\"" "$OUT_DIR/https.txt" | xargs -I{} echo "  HTTPS pcap 中找到「明文敏感字串」次數：{}"

echo ""
echo "（HTTPS 包前 200 bytes — 應為 TLS handshake + Application Data，無 plain text）"
head -c 500 "$OUT_DIR/https.txt" | head -20
echo ""

echo ""
echo "======================================"
echo "  證據檔位置（Wireshark 可載入）"
echo "======================================"
echo "  HTTP  pcap: $OUT_DIR/http.pcap"
echo "  HTTPS pcap: $OUT_DIR/https.pcap"
echo "  HTTP  txt:  $OUT_DIR/http.txt"
echo "  HTTPS txt:  $OUT_DIR/https.txt"
echo ""
echo "  用 Wireshark 開啟：open -a Wireshark $OUT_DIR/https.pcap"
echo "  在 Wireshark 看 Application Data packet 雙擊：只有 hex bytes，無明文"
