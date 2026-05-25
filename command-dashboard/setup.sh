#!/bin/bash
# setup.sh — 指揮部 Pi 安裝腳本
# 執行方式：bash setup.sh

set -e

echo "=== ICS 指揮部後端安裝 ==="

# 安裝 Python 套件
pip3 install -r requirements.txt

# 建立必要資料夾
mkdir -p data

# 建立 DB（C0 重構後使用 core.database.init_db）
PYTHONPATH=. python3 -c "from src.core.database import init_db; init_db(); print('✓ 資料庫建立完成')"

echo ""
echo "=== 啟動方式 ==="
echo "  開發模式（自動重載）："
echo "    cd src && uvicorn main:app --host 0.0.0.0 --port 8000 --reload"
echo ""
echo "  演訓正式模式："
echo "    cd src && uvicorn main:app --host 0.0.0.0 --port 8000 --workers 2"
echo ""
echo "  背景執行（演訓期間）："
echo "    cd src && nohup uvicorn main:app --host 0.0.0.0 --port 8000 &"
echo ""
echo "  各組 Pi 推送端點："
echo "    POST http://$(hostname -I | awk '{print $1}'):8000/api/snapshots"
echo ""
echo "  iPad 儀表板："
echo "    http://$(hostname -I | awk '{print $1}'):8000/static/staff_dashboard.html"
