#!/bin/bash
# ICS_Command 本機啟動腳本（Mac）
# 啟動：指揮部 FastAPI :8000
# 用法：chmod +x start_mac.sh && ./start_mac.sh

set -e
REPO="$(cd "$(dirname "$0")" && pwd)"

LAN_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "")

echo "======================================"
echo " ICS_Command 本機啟動"
echo " 指揮部 → http://127.0.0.1:8000"
[ -n "$LAN_IP" ] && echo " LAN IP → $LAN_IP"
echo "======================================"

# 終止舊服務
pid=$(lsof -ti tcp:8000 2>/dev/null || true)
if [ -n "$pid" ]; then
  echo "[清理] 終止舊 :8000 (PID $pid)"
  kill -9 $pid 2>/dev/null || true
fi
sleep 0.5

cd "$REPO/command-dashboard"

# 檢查 .venv 是否有效。
# 邏輯：用 python sys.prefix 判定 venv 真實位置（avoid 兩個 pitfall）
#   1. grep regex injection：原本 `grep "^#!$PWD/..."` 若 $PWD 含 [、+ 等 BRE
#      metachar 會 misparse，造成 infinite rebuild
#   2. symlink/realpath mismatch：$PWD 是 logical path，venv shebang 可能是
#      resolved path，過 symlink 必 mismatch；用 os.path.realpath 兩邊對齊
need_venv=false
if [ ! -x ".venv/bin/python" ]; then
  need_venv=true
elif ! .venv/bin/python -c "import sys, os; sys.exit(0 if os.path.realpath(sys.prefix) == os.path.realpath('$PWD/.venv') else 1)" 2>/dev/null; then
  echo "[偵測] .venv 指向其他路徑（可能 cp -R 帶入、symlink 變動、或 repo 搬家），重建..."
  rm -rf .venv
  need_venv=true
fi
if [ "$need_venv" = true ]; then
  echo "[安裝] 建立 Python 虛擬環境..."
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi

echo "[啟動] FastAPI :8000 ..."
.venv/bin/uvicorn main:app --app-dir src \
  --host 0.0.0.0 \
  --port 8000 \
  --reload \
  > /tmp/ics_command.log 2>&1 &
COMMAND_PID=$!
echo "[OK] PID $COMMAND_PID"

sleep 2
if curl -s http://127.0.0.1:8000/api/health > /dev/null 2>&1; then
  echo "[OK] 健康檢查通過"
else
  echo "[警告] 尚未就緒，請看 /tmp/ics_command.log"
fi

echo ""
echo "======================================"
echo " 指揮官版：http://127.0.0.1:8000/static/commander_dashboard.html"
echo " API 文件：http://127.0.0.1:8000/docs"
echo " 日誌：    tail -f /tmp/ics_command.log"
echo " 停止：    kill $COMMAND_PID"
echo "======================================"

open "http://127.0.0.1:8000/static/commander_dashboard.html" 2>/dev/null || true

trap "echo ''; echo '[停止]'; kill $COMMAND_PID 2>/dev/null; exit 0" INT
echo "[按 Ctrl+C 停止]"
wait
