# `ics-command.service` drop-in 配置

systemd 支援用 drop-in 檔案**覆寫**或**追加** unit 設定，**不需動原 service 檔**。

## 使用時機

| 情境 | 用 EnvironmentFile？ | 用 drop-in？ |
|---|---|---|
| 環境變數（DB 路徑、log 目錄、API key）| ✅ `/etc/ics/command.env` | ❌ |
| 改 port / ExecStart / User / WorkingDirectory | ❌ | ✅ |
| 加 ExecStartPre / ExecStartPost hook | ❌ | ✅ |
| 改 Restart 策略 | ❌ | ✅ |

## EnvironmentFile（最常用）

```bash
sudo mkdir -p /etc/ics
sudo tee /etc/ics/command.env <<'EOF'
ICS_DB_PATH=/var/lib/ics/ics.db
ICS_LOG_DIR=/var/log/ics
HEALTH_DISK_DEGRADED_PCT_THRESHOLD=10
COMMAND_URL=
EOF
sudo chmod 600 /etc/ics/command.env
sudo systemctl restart ics-command
```

## Drop-in 覆寫（進階）

例：改 port 為 8443，安裝路徑改為 `/opt/ics-command`：

```bash
sudo mkdir -p /etc/systemd/system/ics-command.service.d
sudo tee /etc/systemd/system/ics-command.service.d/override.conf <<'EOF'
[Service]
WorkingDirectory=/opt/ics-command/command-dashboard
ExecStart=
ExecStart=/opt/ics-command/command-dashboard/.venv/bin/uvicorn main:app --app-dir src --host 0.0.0.0 --port 8443
EOF
sudo systemctl daemon-reload
sudo systemctl restart ics-command
```

**注意**：`ExecStart=` 空一行是必要的——systemd 規則：若不清空，原 ExecStart 跟 override 會**疊加執行**（跑兩次）。

## 確認覆寫生效

```bash
sudo systemctl cat ics-command          # 看 merged 後的完整設定
sudo systemctl show ics-command -p ExecStart -p Environment
journalctl -u ics-command -n 50         # 看實際啟動 log
```

## 不要做的事

- ❌ 直接編輯 `/etc/systemd/system/ics-command.service`（git pull 會被覆蓋）
- ❌ 把 site-specific 設定 commit 到 repo
- ❌ 把 secrets 寫進 drop-in `.conf`（用 EnvironmentFile + chmod 600 隔離）
