#!/bin/bash
# ICS_Command Pi / Linux 啟動腳本
# 啟動：指揮部 FastAPI :8000
# 用法：chmod +x start_pi.sh && ./start_pi.sh
#
# 與 start_mac.sh 差異：
# - 用 `hostname -I` 取 LAN IP（Mac 的 ipconfig getifaddr 沒有）
# - 無 macOS `open` 開瀏覽器（Pi 通常 headless）
# - bind 0.0.0.0 讓區網其他裝置（指揮平板）可連
# - 跳 --reload（production 用 systemd 管，不需 hot reload）

set -e
REPO="$(cd "$(dirname "$0")" && pwd)"

LAN_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "")

echo "======================================"
echo " ICS_Command Pi/Linux 啟動"
echo " 指揮部 → http://127.0.0.1:8000"
[ -n "$LAN_IP" ] && echo " LAN IP → http://$LAN_IP:8000"
echo "======================================"

# 終止舊服務
pid=$(lsof -ti tcp:8000 2>/dev/null || true)
if [ -n "$pid" ]; then
  echo "[清理] 終止舊 :8000 (PID $pid)"
  kill -9 $pid 2>/dev/null || true
fi
sleep 0.5

cd "$REPO/command-dashboard"

# 檢查 .venv 是否有效（同 start_mac.sh 邏輯，用 sys.prefix 避 regex / symlink 雷）
need_venv=false
if [ ! -x ".venv/bin/python" ]; then
  need_venv=true
elif ! .venv/bin/python -c "import sys, os; sys.exit(0 if os.path.realpath(sys.prefix) == os.path.realpath('$PWD/.venv') else 1)" 2>/dev/null; then
  echo "[偵測] .venv 指向其他路徑，重建..."
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
echo " 幕僚版：  http://127.0.0.1:8000/static/staff_dashboard.html"
[ -n "$LAN_IP" ] && {
  echo " 區網指揮：http://$LAN_IP:8000/static/commander_dashboard.html"
  echo " 區網幕僚：http://$LAN_IP:8000/static/staff_dashboard.html"
}
echo " API 文件：http://127.0.0.1:8000/docs"
echo " 日誌：    tail -f /tmp/ics_command.log"
echo " 停止：    kill $COMMAND_PID"
echo "======================================"
echo " 注意：生產環境請用 systemd（見 systemd/ics-command.service）"
echo "       本 script 適合 dev / quick test，不是 production runner"
echo "======================================"

trap "echo ''; echo '[停止]'; kill $COMMAND_PID 2>/dev/null; exit 0" INT
echo "[按 Ctrl+C 停止]"
wait
