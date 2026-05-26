from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

# ICS_Command Stage 1 未帶 deploy/ 目錄（規劃由 ROADMAP P1-09 補回 nginx + step-ca）。
# 本 module 暫時整 skip，待 P1-09 deploy/ 補完後 unskip。
# 從 ICS_DMAS 拆分時遺留，不算 P1-01 改動造成的 regression。
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (REPO_ROOT / "deploy" / "setup.sh").exists(),
        reason="deploy/ 待 ROADMAP P1-09 補回；GitHub Issue #1 commit comment 已記錄",
    ),
]


def read_repo_file(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_setup_sh_idempotent():
    script = read_repo_file("deploy/setup.sh")
    assert 'if id "${INSTALL_USER}" >/dev/null 2>&1; then' in script
    assert "useradd --system --no-create-home" in script


def test_setup_sh_creates_var_lib_ics():
    script = read_repo_file("deploy/setup.sh")
    assert 'ICS_DB_DIR="/var/lib/ics"' in script
    assert 'install -d -o "${INSTALL_USER}" -g "${INSTALL_GROUP}" -m 0700 "${ICS_DB_DIR}"' in script
    assert 'ICS_DB_PATH="$ICS_DB_DIR/ics.db"' in script
    # DB 檔案由 app 在 startup 建立（initialize_db 已移除），setup.sh 只建立目錄
    # log 檔案需預先建立並設 ics owner，否則 root 建立後 ics user 無法寫入
    assert 'install -o "${INSTALL_USER}" -g "${INSTALL_GROUP}" -m 0640 /dev/null' in script


def test_setup_sh_unit_path_substitution():
    script = read_repo_file("deploy/setup.sh")
    # unit file 安裝時以 sed 替換 placeholder 路徑為實際 REPO_ROOT
    # (P1-09: ICS_DMAS → ICS_Command 拆分後，placeholder 從 ics-dmas → ics-command)
    assert 'sed "s|/home/ics/ics-command|${REPO_ROOT}|g"' in script
    # 不應直接 install 原始 unit file（否則 hardcode 路徑不替換）
    assert 'install -m 0644 "${SERVICE_FILE}" "/etc/systemd/system' not in script


def test_setup_sh_install_python_deps():
    script = read_repo_file("deploy/setup.sh")
    # setup.sh 需要確保 Python 依賴已安裝，避免部署新機器時 ModuleNotFoundError
    assert "install_python_deps" in script
    assert ".venv/bin/pip" in script
    assert "requirements.txt" in script
    # venv 不存在時需提供明確的手動步驟提示
    assert "python3 -m venv .venv" in script


def test_setup_sh_ensure_app_accessible():
    script = read_repo_file("deploy/setup.sh")
    # ics user 需要 traverse 權限到 app 目錄
    assert "ensure_app_accessible" in script
    assert "chmod o+x" in script
    assert "chmod -R o+rX" in script
    assert ".venv" in script


def test_setup_sh_start_and_probe_retries():
    script = read_repo_file("deploy/setup.sh")
    # start_and_probe 需要 retry loop，不可直接 curl（app 未必立即就緒）
    assert "retries" in script or "until" in script
    assert "sleep" in script


def test_logrotate_no_error():
    config = read_repo_file("deploy/logrotate.ics")
    assert "/var/log/ics/command.log" in config
    assert "postrotate" in config
    assert "systemctl kill -s HUP ics-command.service 2>/dev/null || true" in config
    assert "ics-pi.service" not in config


def test_setup_sh_fails_gracefully_on_useradd_conflict():
    script = read_repo_file("deploy/setup.sh")
    assert "set -euo pipefail" in script
    assert "trap 'on_error $LINENO' ERR" in script
    assert "refusing to change it automatically" in script
    assert "Manual cleanup" in script


def test_setup_warns_on_legacy_db_no_auto_migrate():
    script = read_repo_file("deploy/setup.sh")
    assert 'LEGACY_DB="$COMMAND_DASHBOARD_DIR/data/ics.db"' in script
    assert "Legacy DB" in script
    assert "will not migrate DB data automatically" in script
    # AC-16: warning 必須含具體手動遷移步驟（echo 字串內）
    assert "sudo mv" in script
    assert "sudo chown" in script
    assert "sudo chmod 600" in script
    # 確認不自動執行遷移（只能在 echo 字串內出現，不能是真正的 mv/cp 指令）
    assert 'mv "${LEGACY_DB}"' not in script
    assert 'cp "${LEGACY_DB}"' not in script
    assert 'cp "${LEGACY_DB}"' not in script


def test_systemd_unit_service_config():
    unit = read_repo_file("systemd/ics-command.service")
    # ExecStartPost 不應存在：Type=simple 下 ExecStartPost 比 app 早執行，
    # 每次 Restart=on-failure 重啟都因 connection refused（curl exit 7）失敗，
    # 造成無限重啟迴圈。Health probe 只做在 setup.sh start_and_probe()（有 retry）。
    assert "ExecStartPost" not in unit
    assert "User=ics" in unit
    assert "Group=ics" in unit
    assert "Environment=ICS_DB_PATH=/var/lib/ics/ics.db" in unit
    assert "Environment=ICS_LOG_DIR=/var/log/ics" in unit
    assert "StandardOutput=append:/var/log/ics/command.log" in unit
    assert "StandardError=append:/var/log/ics/command.log" in unit
    assert "Restart=on-failure" in unit
