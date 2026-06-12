"""
unlock_key.py — 服務啟動解鎖 CLI（P1-12a #227）

    # production：FIDO2 解鎖 → 衍生 child keys → 寫 env file
    python scripts/keymgmt/unlock_key.py --store /etc/ics/master-key.enc --output /run/ics/keys.env

    # systemd 接法（manned C2：開機需人持 token，threat_model §8.4 拍板）
    #   ics-command.service 已設 EnvironmentFile=-/run/ics/keys.env；
    #   開機後 operator 手動跑本 script（或包成 ExecStartPre oneshot）再 start 服務。

    # dev / no-FIDO2 fallback（警示明顯；CI 與 Windows dev 用）
    ICS_MASTER_KEY=<64 hex chars> python scripts/keymgmt/unlock_key.py --output .keys.env

    # rescue：失去 token 時以助記詞直接解鎖（過渡用，事後應 re-enroll）
    python scripts/keymgmt/unlock_key.py --from-mnemonic --output /run/ics/keys.env

env file 內容（label → 環境變數對照）：
    BACKUP_KEY = child[backup-v1]（Fernet urlsafe-b64，P1-12b 消費）
    DB_KEY     = child[db-v1]（hex，P1-12c SQLCipher PRAGMA key 消費）
disk-v1（LUKS，#231）刻意不寫入 env file — 開機碟解鎖在 initramfs 階段，
不該落在 app 的 env file 裡。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from keymgmt import keystore  # noqa: E402
from keymgmt.backends import KeyBackendError, RealFido2Backend  # noqa: E402
from keymgmt.derive_child import MASTER_LEN, derive_child, fernet_key, hex_key  # noqa: E402

ENV_FALLBACK_VAR = "ICS_MASTER_KEY"

# label → (env var 名, 編碼函式)
ENV_MAP = {
    "backup-v1": ("BACKUP_KEY", fernet_key),
    "db-v1": ("DB_KEY", hex_key),
}
DEFAULT_LABELS = ("backup-v1", "db-v1")


def master_from_env() -> bytes | None:
    """ICS_MASTER_KEY fallback（dev / no-FIDO2）。hex 64 字元。"""
    raw = os.getenv(ENV_FALLBACK_VAR)
    if not raw:
        return None
    try:
        master = bytes.fromhex(raw.strip())
    except ValueError:
        raise SystemExit(f"{ENV_FALLBACK_VAR} 必須為 hex（64 字元）") from None
    if len(master) != MASTER_LEN:
        raise SystemExit(f"{ENV_FALLBACK_VAR} 解出 {len(master)} bytes，應為 {MASTER_LEN}")
    return master


def render_env_file(master: bytes, labels: tuple[str, ...] = DEFAULT_LABELS) -> str:
    """master → env file 內容（純函式，測試收口）。"""
    lines = ["# 由 keymgmt/unlock_key.py 產生 — 含金鑰材料，勿入版控、勿外流"]
    for label in labels:
        if label not in ENV_MAP:
            raise ValueError(f"label 無對應 env var：{label}（已知：{sorted(ENV_MAP)}）")
        var, encoder = ENV_MAP[label]
        lines.append(f"{var}={encoder(derive_child(master, label))}")
    return "\n".join(lines) + "\n"


def write_env_file(master: bytes, output: Path, labels: tuple[str, ...] = DEFAULT_LABELS) -> None:
    """atomic write + 0600（同 keystore.save_store 慣例）。"""
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + ".tmp")
    tmp.write_text(render_env_file(master, labels), encoding="utf-8")
    if sys.platform != "win32":
        os.chmod(tmp, 0o600)
    tmp.replace(output)


def main() -> int:
    ap = argparse.ArgumentParser(description="FIDO2 解鎖 → child keys → env file（P1-12a）")
    ap.add_argument("--store", type=Path, help="master-key.enc 路徑（FIDO2 模式必填）")
    ap.add_argument("--output", type=Path, required=True, help="env file 輸出路徑")
    ap.add_argument("--labels", default=",".join(DEFAULT_LABELS), help="要衍生的 label（逗號分隔）")
    ap.add_argument("--from-mnemonic", action="store_true", help="rescue：以助記詞解鎖（事後應 re-enroll）")
    args = ap.parse_args()
    labels = tuple(s.strip() for s in args.labels.split(",") if s.strip())

    master = master_from_env()
    if master is not None:
        print(
            f"⚠⚠ {ENV_FALLBACK_VAR} fallback 模式 — master 以明文 env 供應，"
            "僅限 dev / CI / 無 FIDO2 場景。production 必須走 FIDO2 enroll。",
            file=sys.stderr,
        )
    elif args.from_mnemonic:
        from keymgmt import mnemonic

        print("請輸入 24 字 rescue 助記詞（空白分隔，單行）：")
        master = mnemonic.decode(input("> ").split())
        print("⚠ rescue 解鎖成功 — 請儘速 enroll 新 token（enroll_fido2.py --from-mnemonic）", file=sys.stderr)
    else:
        if not args.store:
            raise SystemExit("FIDO2 模式需 --store（或設 ICS_MASTER_KEY / --from-mnemonic）")
        try:
            _, entries = keystore.load_store(args.store)
            backend = RealFido2Backend()
            import getpass

            pin = getpass.getpass("FIDO2 PIN（無則 Enter）：") or None
            input("請插入任一把已註冊 token 後按 Enter …")
            master, label = keystore.unlock(entries, backend, pin)
            print(f"✔ 以「{label}」解鎖成功")
        except (keystore.KeyStoreError, KeyBackendError) as e:
            print(f"✘ {e}", file=sys.stderr)
            return 1

    write_env_file(master, args.output, labels)
    print(f"✔ child keys（{', '.join(labels)}）已寫入 {args.output}（0600）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
