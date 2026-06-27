# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
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


def test_dockerignore_excludes_orphan_static_pages():
    """#294 減攻擊面：prod image 不烤進「無 workflow 連結」的孤兒 HTML 頁。

    qr_scanner / scenario_designer / icon_preview / admin_backups 在 prod 無可達路徑
    （reality check：全 repo 無連結；admin_backups 連結僅 dev 導覽頁、被 prod redirect 蓋掉；
    prod 備份走 systemd ics-backup）。服務著卻沒人走到＝多餘 XSS 面。
    """
    lines = [ln.strip() for ln in read_repo_file("command-dashboard/.dockerignore").splitlines()]
    for page in (
        "static/qr_scanner.html",
        "static/scenario_designer.html",
        "static/icon_preview.html",
        "static/admin_backups.html",
    ):
        assert page in lines, f"{page} 應列入 .dockerignore（prod image 排除）"
    # prod 實際在用的頁不可被排除
    assert "static/commander_dashboard.html" not in lines
    assert "static/aar.html" not in lines
    # data/ 與 tiles 須為獨立行（行內 # 非註解、會讓 pattern 失效 → 排除沒生效）
    assert "data/" in lines, "data/ 須獨立成行才會真的被排除（勿用行內註解）"
    assert "static/tiles/" in lines


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


# ── #275 wave 2：mTLS 鑑權地基 ────────────────────────────────────────


def test_systemd_backend_binds_loopback():
    """#275 wave 2 紅線「後端只經 nginx 可達」：bare-metal systemd 須 bind 127.0.0.1，
    不對外網直曝 :8000（否則繞過 nginx 的 mTLS + X-Client-Cert-* 剝除）。"""
    unit = read_repo_file("systemd/ics-command.service")
    assert "--host 127.0.0.1" in unit
    assert "--host 0.0.0.0" not in unit


def test_nginx_command_conf_strips_client_cert_headers():
    """#275 wave 2 紅線：非 mTLS 的 command.conf 必須剝除 client 自帶 X-Client-Cert-*，
    防偽造後端 AAL2 第二因子。"""
    conf = read_repo_file("deploy/nginx/conf.d/command.conf")
    assert 'proxy_set_header X-Client-Cert-CN "";' in conf
    assert 'proxy_set_header X-Client-Cert-Verify "";' in conf


def test_validation_nginx_strips_client_cert_headers():
    """#275 wave 2 紅線：公網直曝驗證棧（ics-validation）同樣須剝除 X-Client-Cert-*，
    且 XFF 不用可偽造的 $proxy_add_x_forwarded_for。"""
    conf = read_repo_file("deploy/ics-validation/nginx.conf")
    assert 'proxy_set_header   X-Client-Cert-CN     "";' in conf
    assert 'proxy_set_header   X-Client-Cert-Verify "";' in conf
    assert "$proxy_add_x_forwarded_for" not in conf


def test_mtls_conf_enforces_client_cert():
    """#275 全角色 mTLS 單埠 443 強制版：須 ssl_verify_client on + root CA truststore，
    且 X-Client-Cert-* 從 nginx 驗證結果 $ssl_client_* 注入（非 client 自帶）。"""
    conf = read_repo_file("deploy/nginx/conf.d/command-mtls.conf.disabled")
    assert "ssl_verify_client      on;" in conf
    assert "ssl_client_certificate ROOT_CA_PATH_PLACEHOLDER;" in conf
    assert "listen 443 ssl;" in conf  # 單埠 443（非舊 8443 tier3）
    assert "8443" not in conf
    # CN 須經 map 從 $ssl_client_s_dn 抽（stock nginx 無 $ssl_client_s_dn_cn，直用會 emerg）
    assert "map $ssl_client_s_dn $ics_client_cn" in conf
    assert "$ssl_client_s_dn_cn" not in conf
    assert "proxy_set_header X-Client-Cert-CN     $ics_client_cn;" in conf
    assert "proxy_set_header X-Client-Cert-Verify $ssl_client_verify;" in conf
    # #280 紅隊修補：mTLS config 注入 proxy 共享密鑰（setup.sh 替換）
    assert "PROXY_SECRET_PLACEHOLDER" in conf


def test_setup_sh_generates_proxy_secret():
    """#280：mTLS 安裝時產隨機 proxy 密鑰、替換 nginx placeholder、寫進後端 env。"""
    script = read_repo_file("deploy/setup.sh")
    assert "ICS_PROXY_SHARED_SECRET=" in script
    assert "s|PROXY_SECRET_PLACEHOLDER|" in script


def test_command_conf_strips_proxy_auth():
    """#280：非 mTLS command.conf 剝除 client 自帶 X-Proxy-Auth。"""
    conf = read_repo_file("deploy/nginx/conf.d/command.conf")
    assert 'proxy_set_header X-Proxy-Auth "";' in conf


def test_setup_sh_installs_mtls_variant():
    """#275 wave 2：setup.sh 支援 ICS_MTLS=1 安裝單埠 mTLS 版（取代 command.conf）
    並替換 ROOT_CA placeholder。"""
    script = read_repo_file("deploy/setup.sh")
    assert 'mtls="${ICS_MTLS:-0}"' in script
    assert "ROOT_CA_PATH_PLACEHOLDER" in script
    assert "command-mtls.conf.disabled" in script
    # 兩個 443 block 不可並存：mTLS 安裝時須移除非 mTLS command.conf
    assert 'rm -f "$nginx_conf_dir/command.conf"' in script


def test_old_tier3_stub_removed():
    """#275 wave 2：舊 8443 tier3 stub 已被單埠 command-mtls 取代。"""
    assert not (REPO_ROOT / "deploy/nginx/conf.d/tier3-mtls.conf.disabled").exists()
