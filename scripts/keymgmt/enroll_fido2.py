"""
enroll_fido2.py — FIDO2 token 註冊 CLI（P1-12a #227）

    # 全新 enroll（產生新 master，註冊 2 把 token，印 rescue 助記詞）
    python scripts/keymgmt/enroll_fido2.py --store /etc/ics/master-key.enc --tokens 2 --show-rescue

    # 失去所有 token 後以紙本助記詞 re-enroll（master 不變，歷史 backup/DB 可解）
    python scripts/keymgmt/enroll_fido2.py --store /etc/ics/master-key.enc --tokens 2 --from-mnemonic

流程：每把 token 依序「插入 → PIN → touch」；全部註冊完成才寫檔（atomic）。
⚠ rescue 助記詞 = master 本體，僅該印一次、紙本離線保存（保險箱），
  印完即從畫面清除。詳見 docs/security/key-management.md。
⚠ 真實 token 路徑尚待硬體驗收（#230）。
"""

from __future__ import annotations

import argparse
import getpass
import secrets
import sys
from pathlib import Path

# 允許直接以 script 執行（python scripts/keymgmt/enroll_fido2.py）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from keymgmt import keystore, mnemonic  # noqa: E402
from keymgmt.backends import RP_ID, KeyBackendError, RealFido2Backend  # noqa: E402
from keymgmt.derive_child import MASTER_LEN  # noqa: E402


def enroll_tokens(master: bytes, backend, labels: list[str], pin: str | None) -> list[keystore.TokenEntry]:
    """逐把註冊並產生 wrap entry（與 CLI 解耦，mock backend 可測）。"""
    entries: list[keystore.TokenEntry] = []
    for label in labels:
        credential_id = backend.register(label, pin)
        salt = secrets.token_bytes(keystore.SALT_LEN)
        wrap_key = backend.hmac_secret(credential_id, salt, pin)
        nonce, wrapped = keystore.wrap_master(master, wrap_key, credential_id)
        entries.append(
            keystore.TokenEntry(
                label=label, credential_id=credential_id, salt=salt, nonce=nonce, wrapped=wrapped
            )
        )
    return entries


def read_mnemonic_interactive() -> bytes:
    print("請輸入 24 字 rescue 助記詞（空白分隔，單行）：")
    words = input("> ").split()
    master = mnemonic.decode(words)
    if len(master) != MASTER_LEN:
        raise SystemExit(f"助記詞解出 {len(master)} bytes，非 master key（應為 {MASTER_LEN}）")
    return master


def main() -> int:
    if sys.platform == "win32":
        # console codepage 缺字以 ? 取代 — 輸出不准炸掉已完成的操作
        sys.stdout.reconfigure(errors="replace")
        sys.stderr.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="FIDO2 token enroll（P1-12a）")
    ap.add_argument("--store", type=Path, required=True, help="master-key.enc 輸出路徑")
    ap.add_argument("--tokens", type=int, default=2, help="註冊把數（建議 ≥2：主 + 備援）")
    ap.add_argument("--from-mnemonic", action="store_true", help="以 rescue 助記詞還原 master（re-enroll）")
    ap.add_argument("--show-rescue", action="store_true", help="印出 rescue 助記詞（紙本保存後清螢幕）")
    args = ap.parse_args()

    if args.store.exists():
        print(f"[WARN] {args.store} 已存在 — enroll 會整檔覆蓋（舊 token 全部失效）。")
        if input("確定繼續？輸入 yes：").strip() != "yes":
            return 1

    if args.from_mnemonic:
        master = read_mnemonic_interactive()
        print("[OK] 助記詞 checksum 通過，master 已還原。")
    else:
        master = secrets.token_bytes(MASTER_LEN)

    try:
        backend = RealFido2Backend()
    except KeyBackendError as e:
        print(f"[FAIL] {e}", file=sys.stderr)
        return 1

    labels: list[str] = []
    for i in range(args.tokens):
        labels.append(input(f"token {i + 1}/{args.tokens} 名稱（如「主鑰」「備援」）：").strip() or f"token-{i + 1}")

    pin = getpass.getpass("FIDO2 PIN（無則 Enter）：") or None
    entries: list[keystore.TokenEntry] = []
    for label in labels:
        input(f"請插入「{label}」後按 Enter …")
        try:
            entries.extend(enroll_tokens(master, backend, [label], pin))
        except KeyBackendError as e:
            print(f"[FAIL] 「{label}」註冊失敗：{e}", file=sys.stderr)
            return 1
        print(f"[OK] 「{label}」註冊完成")

    keystore.save_store(args.store, keystore.build_store(master, entries, RP_ID))
    print(f"[OK] keystore 已寫入 {args.store}（{len(entries)} 把 token，任一把可解鎖）")

    if args.show_rescue:
        words = mnemonic.encode(master)
        print("\n=== RESCUE 助記詞（24 字）— 抄到紙本，存離線保險箱 ===")
        for row in range(0, 24, 6):
            print("  " + "  ".join(f"{i + 1:2d}.{w}" for i, w in enumerate(words[row : row + 6], start=row)))
        print("=== 抄完按 Enter 清除畫面 ===")
        input()
        print("\033[2J\033[H", end="")  # 清螢幕（終端支援 ANSI 時）
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
