#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="ics-command"
SERVICE_FILE="$REPO_ROOT/systemd/ics-command.service"
LOGROTATE_SOURCE="$REPO_ROOT/deploy/logrotate.ics"
LOGROTATE_TARGET="/etc/logrotate.d/ics-command"
COMMAND_DASHBOARD_DIR="$REPO_ROOT/command-dashboard"
LEGACY_DB="$COMMAND_DASHBOARD_DIR/data/ics.db"
ICS_DB_DIR="/var/lib/ics"
ICS_DB_PATH="$ICS_DB_DIR/ics.db"
ICS_LOG_DIR="/var/log/ics"
ICS_CONFIG_DIR="/etc/ics"
INSTALL_USER="ics"
INSTALL_GROUP="ics"

on_error() {
  local line="$1"
  echo "[setup] ERROR at line ${line}." >&2
  echo "[setup] Review the message above, then inspect partial state with:" >&2
  echo "  id ${INSTALL_USER} || true" >&2
  echo "  ls -ld ${ICS_LOG_DIR} ${ICS_CONFIG_DIR} ${ICS_DB_DIR} 2>/dev/null || true" >&2
  echo "  systemctl status ${SERVICE_NAME} --no-pager || true" >&2
  echo "[setup] Manual cleanup, if needed: systemctl disable --now ${SERVICE_NAME}; userdel ${INSTALL_USER}" >&2
}
trap 'on_error $LINENO' ERR

require_root() {
  if [[ "$(id -u)" -ne 0 ]]; then
    echo "[setup] Please run as root: sudo bash deploy/setup.sh" >&2
    exit 1
  fi
}

pick_shell() {
  if [[ -x /usr/sbin/nologin ]]; then
    echo "/usr/sbin/nologin"
  else
    echo "/bin/false"
  fi
}

ensure_ics_user() {
  local shell
  shell="$(pick_shell)"
  if id "${INSTALL_USER}" >/dev/null 2>&1; then
    local current_shell
    current_shell="$(getent passwd "${INSTALL_USER}" | cut -d: -f7)"
    case "${current_shell}" in
      /bin/false|/usr/sbin/nologin)
        echo "[setup] user ${INSTALL_USER} already exists with non-login shell"
        ;;
      *)
        echo "[setup] Existing ${INSTALL_USER} user has login shell ${current_shell}; refusing to change it automatically." >&2
        exit 1
        ;;
    esac
  else
    useradd --system --no-create-home --shell "${shell}" "${INSTALL_USER}"
    echo "[setup] created system user ${INSTALL_USER}"
  fi
}

ensure_directories() {
  install -d -o "${INSTALL_USER}" -g "${INSTALL_GROUP}" -m 0755 "${ICS_LOG_DIR}"
  install -d -o "${INSTALL_USER}" -g "${INSTALL_GROUP}" -m 0700 "${ICS_CONFIG_DIR}"
  install -d -o "${INSTALL_USER}" -g "${INSTALL_GROUP}" -m 0700 "${ICS_DB_DIR}"
  # log 檔案若由 root 建立，ics user 將無法寫入；預先以 install 建立並設定 owner
  install -o "${INSTALL_USER}" -g "${INSTALL_GROUP}" -m 0640 /dev/null "${ICS_LOG_DIR}/command.log"
}

warn_legacy_db() {
  if [[ -f "${LEGACY_DB}" && ! -f "${ICS_DB_PATH}" ]]; then
    echo "WARNING: Legacy DB at ${LEGACY_DB} detected. ${ICS_DB_PATH} does not exist."
    echo "WARNING: setup.sh will not migrate DB data automatically."
    echo "  If you need to preserve existing data, migrate manually before starting the service:"
    echo "    sudo mkdir -p ${ICS_DB_DIR}"
    echo "    sudo chown ${INSTALL_USER}:${INSTALL_GROUP} ${ICS_DB_DIR}"
    echo "    sudo chmod 700 ${ICS_DB_DIR}"
    echo "    sudo mv ${LEGACY_DB} ${ICS_DB_PATH}"
    echo "    sudo chown ${INSTALL_USER}:${INSTALL_GROUP} ${ICS_DB_PATH}"
    echo "    sudo chmod 600 ${ICS_DB_PATH}"
  fi
}

warn_existing_db_accounts() {
  # 若 DB 已存在且含帳號，ensure_initial_admin_token() 會跳過 first-run token 產生。
  # 操作員必須提前知道 admin PIN，否則部署後無法登入。
  [[ -f "${ICS_DB_PATH}" ]] || return 0
  command -v sqlite3 >/dev/null 2>&1 || return 0
  local count
  count=$(sqlite3 "${ICS_DB_PATH}" \
    "SELECT COUNT(*) FROM accounts;" 2>/dev/null || echo "")
  [[ -z "${count}" || "${count}" -eq 0 ]] && return 0

  echo ""
  echo "╔══════════════════════════════════════════════════════════════════╗"
  echo "║  ⚠️  WARNING: 現有 DB 已含 ${count} 個帳號                            ║"
  echo "║                                                                  ║"
  echo "║  first-run token 不會產生（ensure_initial_admin_token 跳過）    ║"
  echo "║  請確認你知道 admin 的目前 PIN，否則部署後無法登入系統。        ║"
  echo "║                                                                  ║"
  echo "║  若要全新部署（清除所有帳號與資料），請先執行：                 ║"
  printf "║    sudo rm %s\n" "${ICS_DB_PATH}"
  echo "║    然後重新執行 sudo bash deploy/setup.sh                        ║"
  echo "╚══════════════════════════════════════════════════════════════════╝"
  echo ""
}

install_python_deps() {
  local venv_pip="${COMMAND_DASHBOARD_DIR}/.venv/bin/pip"
  if [[ ! -x "${venv_pip}" ]]; then
    echo "[setup] ERROR: .venv 不存在，請先建立 venv 並安裝依賴：" >&2
    echo "  cd ${COMMAND_DASHBOARD_DIR}" >&2
    echo "  python3 -m venv .venv" >&2
    echo "  .venv/bin/pip install -r requirements.txt" >&2
    exit 1
  fi
  echo "[setup] Installing Python dependencies..."
  "${venv_pip}" install -q -r "${COMMAND_DASHBOARD_DIR}/requirements.txt"
  echo "[setup] Python dependencies OK"
}

install_logrotate() {
  install -m 0644 "${LOGROTATE_SOURCE}" "${LOGROTATE_TARGET}"
}

ensure_app_accessible() {
  # ics user 需要能 traverse（chdir）到 app 目錄才能啟動服務。
  #
  # Production 最佳做法：app 部署到 /opt/ics-command/（ics user 擁有），
  # 則此步驟不需要。
  #
  # Dev / 非 /opt 部署：用 chmod o+x 授予 ics traverse 各層目錄的最小
  # 權限（execute-only，不開 read）。app src/ 和 .venv/ 需要 o+rX 讓
  # ics 能讀取 Python 檔案和執行 binary。
  local dir="${REPO_ROOT}"
  while [[ "${dir}" != "/" && "${dir}" != "/home" && "${dir}" != "/root" ]]; do
    chmod o+x "${dir}"
    dir="$(dirname "${dir}")"
  done
  # /home/<user> 本身也需要 o+x（不含 /home 本身）
  if [[ "$(dirname "${REPO_ROOT}")" == /home/* ]]; then
    chmod o+x "$(dirname "${REPO_ROOT}")"
  fi
  # app source 和 venv：ics 需要 read+execute 才能執行 Python
  chmod -R o+rX "${COMMAND_DASHBOARD_DIR}/src"   2>/dev/null || true
  chmod -R o+rX "${COMMAND_DASHBOARD_DIR}/.venv" 2>/dev/null || true
  # P1-13（issue #27）：map_config runtime 檔搬到 data/，舊 static/ 位置已不再使用。
  # data/ 是 user-data 邊界（gitignored），systemd 起的 app 必須有寫權；建目錄 + chown。
  # （舊 static/map_config.json 路徑的 touch/chown 已移除；存在的 legacy 檔 git pull 後
  # 也不會被讀，但若 fresh deploy 從 main pull 已沒這檔，純清乾淨。）
  mkdir -p "${COMMAND_DASHBOARD_DIR}/data"
  chown "${INSTALL_USER}:${INSTALL_GROUP}" "${COMMAND_DASHBOARD_DIR}/data"
  chmod 0750 "${COMMAND_DASHBOARD_DIR}/data"
  # map_config.json 由 lifespan ensure() 從 seed 自動產生；只要 data/ 有寫權即可。
  # 若已存在（既有部署 migrate 過來）也 chown 一下確保 systemd 後續寫得進去。
  if [[ -f "${COMMAND_DASHBOARD_DIR}/data/map_config.json" ]]; then
    chown "${INSTALL_USER}:${INSTALL_GROUP}" "${COMMAND_DASHBOARD_DIR}/data/map_config.json"
    chmod 0640 "${COMMAND_DASHBOARD_DIR}/data/map_config.json"
  fi
  # Legacy 清理：若舊 static/map_config.json 還存在（舊部署），印警告但不刪
  # （user 自行決定保留作備份 / 或執行 migration script 後手動清）
  if [[ -f "${COMMAND_DASHBOARD_DIR}/static/map_config.json" ]]; then
    echo "[setup] WARN: legacy ${COMMAND_DASHBOARD_DIR}/static/map_config.json 仍存在"
    echo "[setup]       P1-13 已搬到 data/；可跑 \`python3 ${COMMAND_DASHBOARD_DIR}/scripts/migrate_map_config.py\` 後手動刪除"
  fi
  echo "[setup] app path traversable by ${INSTALL_USER}: ${REPO_ROOT}"
}

install_systemd_unit() {
  # unit file 內的 placeholder 路徑在安裝時替換為實際 REPO_ROOT
  # （unit file 用 /home/ics/ics-command 作為 placeholder，部署時替換）
  sed "s|/home/ics/ics-command|${REPO_ROOT}|g" \
    "${SERVICE_FILE}" > "/etc/systemd/system/${SERVICE_NAME}.service"
  chmod 0644 "/etc/systemd/system/${SERVICE_NAME}.service"
  systemctl daemon-reload
  systemctl enable "${SERVICE_NAME}.service"
  echo "[setup] systemd unit installed (app root: ${REPO_ROOT})"
}

# ⭐ #41 Backup DR Drill: 安裝 ics-backup.timer + .service (B-1 每日 02:00 加密 backup)
# 對齊 Sync v4 production 前提: Pi LUKS unlocked state 必先 (此 deploy 跑時應已 unlock)
install_backup_timer() {
  local backup_timer_src="$REPO_ROOT/deploy/systemd/ics-backup.timer"
  local backup_service_src="$REPO_ROOT/deploy/systemd/ics-backup.service"
  if [[ ! -f "$backup_timer_src" ]] || [[ ! -f "$backup_service_src" ]]; then
    echo "[setup] WARN: backup timer/service files 不存在, 跳過 (#41 W-C1-A?)" >&2
    return 0
  fi
  # 替換 placeholder __REPO_ROOT__ + __INSTALL_USER__
  sed -e "s|__REPO_ROOT__|${REPO_ROOT}|g" \
      -e "s|__INSTALL_USER__|${INSTALL_USER:-ics}|g" \
      "$backup_service_src" > "/etc/systemd/system/ics-backup.service"
  cp "$backup_timer_src" "/etc/systemd/system/ics-backup.timer"
  chmod 0644 /etc/systemd/system/ics-backup.timer /etc/systemd/system/ics-backup.service

  # E-3 frozen: BACKUP_ENCRYPTION_KEY 必須由 EnvironmentFile 提供
  # 若 /etc/ics-backup.env 不存在, deploy SOP 須產生 (deployer 設置)
  if [[ ! -f /etc/ics-backup.env ]]; then
    echo "[setup] ⚠️ /etc/ics-backup.env 不存在 — production 必先設定 BACKUP_ENCRYPTION_KEY" >&2
    echo "[setup]    產生 key: python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'" >&2
    echo "[setup]    然後: echo 'BACKUP_ENCRYPTION_KEY=<產生的_key>' | sudo tee /etc/ics-backup.env && sudo chmod 0600 /etc/ics-backup.env" >&2
    echo "[setup]    timer 已 installed 但 service 跑時會 fail 直到 env file 設置完成" >&2
  fi

  systemctl daemon-reload
  systemctl enable --now ics-backup.timer
  echo "[setup] ⭐ #41 ics-backup.timer enabled (每日 02:00 加密 backup, 7 天保留)"
  echo "[setup]    Pi 端: production 必先 LUKS unlock 才能跑 backup (Sync v4)"
}

# initialize_db() 已移除：
#   FastAPI lifespan 在服務啟動時自動呼叫 init_db()，
#   setup.sh 不需要（也不應該）以另一個 user 身份代跑 Python。
#   DB 檔案會在 systemctl start 後由 app 本身建立於 ICS_DB_PATH。

start_and_probe() {
  systemctl start "${SERVICE_NAME}.service"
  # app 啟動需要幾秒（venv import + DB init），最多等 30s
  local retries=10
  local i=0
  until curl -sf http://localhost:8000/api/health >/tmp/ics-health.json 2>/dev/null; do
    i=$((i+1))
    if [[ "${i}" -ge "${retries}" ]]; then
      echo "[setup] ERROR: /api/health 未在 ${retries} 次內回應，請檢查服務狀態：" >&2
      echo "  journalctl -xeu ${SERVICE_NAME}.service | tail -30" >&2
      exit 1
    fi
    sleep 3
  done
  echo "[setup] /api/health probe OK (status=$(jq -r .status /tmp/ics-health.json))"
  rm -f /tmp/ics-health.json
}

print_first_run_pin() {
  # ics user 的 home dir（--no-create-home 仍有 home 路徑）
  local ics_home
  ics_home="$(getent passwd "${INSTALL_USER}" | cut -d: -f6)"
  local token_file="${ics_home}/.ics/first_run_token"
  if [[ -f "${token_file}" ]]; then
    local pin
    pin="$(cat "${token_file}")"
    echo ""
    echo "╔══════════════════════════════════════════╗"
    echo "║  首次部署：初始登入 PIN                  ║"
    echo "║                                          ║"
    echo "║  Username : admin                        ║"
    printf  "║  PIN      : %-29s║\n" "${pin}"
    echo "║                                          ║"
    echo "║  登入後系統將強制要求設定新 PIN          ║"
    echo "║  請立刻將此 PIN 交給管理員               ║"
    echo "╚══════════════════════════════════════════╝"
    echo ""
  fi
}

# 安裝 nginx config（含 cert path placeholder 替換）
# P1-09 fix: 原 nginx command.conf 留 CERT_PATH_PLACEHOLDER 從不被替換，
#            setup.sh 也完全沒處理 nginx。此函式補完。
install_nginx_configs() {
  local nginx_conf_dir="/etc/nginx/conf.d"
  local cert_path="${ICS_CONFIG_DIR}/certs/command.ics.local.cert.pem"
  local key_path="${ICS_CONFIG_DIR}/certs/command.ics.local.key.pem"

  if ! command -v nginx >/dev/null 2>&1; then
    echo "[setup] nginx 未安裝，跳過 nginx config 安裝"
    echo "[setup]    （production HTTPS 部署需先 apt install nginx）"
    return 0
  fi

  if [[ ! -d "$nginx_conf_dir" ]]; then
    echo "[setup] WARN: $nginx_conf_dir 不存在，跳過 nginx config 安裝" >&2
    return 0
  fi

  # 複製 ssl-common / security-headers 不需 placeholder 替換
  install -m 0644 "$REPO_ROOT/deploy/nginx/conf.d/ssl-common.conf"        "$nginx_conf_dir/"
  install -m 0644 "$REPO_ROOT/deploy/nginx/conf.d/security-headers.conf"  "$nginx_conf_dir/"

  # command.conf 替換憑證路徑
  sed -e "s|CERT_PATH_PLACEHOLDER|${cert_path}|g" \
      -e "s|KEY_PATH_PLACEHOLDER|${key_path}|g" \
      "$REPO_ROOT/deploy/nginx/conf.d/command.conf" > "$nginx_conf_dir/command.conf"
  chmod 0644 "$nginx_conf_dir/command.conf"

  echo "[setup] nginx config 安裝到 $nginx_conf_dir"
  if [[ ! -f "$cert_path" ]]; then
    echo "[setup] ⚠️  憑證 $cert_path 不存在 — production 必須提供" >&2
    echo "[setup]    Dev 可從 deploy/step-ca/issue-cert.sh 產生" >&2
    echo "[setup]    nginx 在憑證就位前無法 reload（systemctl reload nginx 會 fail）" >&2
  else
    nginx -t && systemctl reload nginx
    echo "[setup] nginx reloaded"
  fi
}

main() {
  require_root
  ensure_ics_user
  ensure_directories
  warn_legacy_db
  warn_existing_db_accounts   # 必須在 start_and_probe 之前：讓操作員看到警告後可選擇刪 DB 再重跑
  install_python_deps
  ensure_app_accessible
  install_logrotate
  install_systemd_unit
  install_backup_timer   # ⭐ #41: ics-backup.timer 每日 02:00 加密 backup
  install_nginx_configs  # P1-09: nginx HTTPS 反代 config（憑證需 operator 另提供）
  start_and_probe   # app 啟動時自動 init_db()，成功則 /api/health 回 200
  print_first_run_pin
  echo "[setup] Setup complete"
}

main "$@"
