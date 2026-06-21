"""
keystore.py — master key 的 per-token wrap 檔（master-key.enc，P1-12a #227）

格式（JSON，欄位 base64）：

    {
      "version": 1,
      "rp_id": "ics-command.local",
      "tokens": [
        {"label": "主鑰", "credential_id": "...", "salt": "...",
         "nonce": "...", "wrapped": "..."},
        ...
      ]
    }

每把 token 一筆 entry：wrap_key = token 的 hmac-secret(credential_id, salt)，
master 以 AES-256-GCM(wrap_key, nonce, aad=credential_id) 包裹。
任一把 token 可獨立解出 master（多 token 冗餘）；wrap 檔被竄改時
GCM 認證失敗（InvalidTag）→ 明確報錯，不會解出垃圾 key。

檔案本身不含任何可單獨推出 master 的材料 — 沒有 token（或 rescue 助記詞）
拿到 master-key.enc 也無用。
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import sys
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

STORE_VERSION = 1
SALT_LEN = 32
NONCE_LEN = 12
MASTER_LEN = 32


class KeyStoreError(Exception):
    """keystore 載入 / 解鎖失敗。"""


@dataclass
class TokenEntry:
    label: str
    credential_id: bytes
    salt: bytes
    nonce: bytes
    wrapped: bytes


def _b64e(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def wrap_master(master: bytes, wrap_key: bytes, credential_id: bytes) -> tuple[bytes, bytes]:
    """master → (nonce, ciphertext)。aad 綁 credential_id，防 entry 間張冠李戴。"""
    if len(master) != MASTER_LEN:
        raise ValueError(f"master 必須為 {MASTER_LEN} bytes")
    nonce = secrets.token_bytes(NONCE_LEN)
    ct = AESGCM(wrap_key).encrypt(nonce, master, credential_id)
    return nonce, ct


def unwrap_master(entry: TokenEntry, wrap_key: bytes) -> bytes:
    """以 wrap_key 解出 master。竄改 / key 不對 / 欄位損毀 → KeyStoreError。

    ValueError 也要接：nonce/key 長度損毀時 AESGCM 拋 ValueError 而非
    InvalidTag — 不接的話會穿透 unlock() 的逐 entry 迴圈，壞一筆毀全部。
    """
    try:
        return AESGCM(wrap_key).decrypt(entry.nonce, entry.wrapped, entry.credential_id)
    except (InvalidTag, ValueError) as e:
        raise KeyStoreError(
            f"entry「{entry.label}」解鎖失敗 — wrap key 不符、欄位損毀或檔案被竄改"
        ) from e


def build_store(master: bytes, entries: list[TokenEntry], rp_id: str) -> dict:
    if not entries:
        raise ValueError("至少需要一筆 token entry")
    return {
        "version": STORE_VERSION,
        "rp_id": rp_id,
        "tokens": [
            {
                "label": e.label,
                "credential_id": _b64e(e.credential_id),
                "salt": _b64e(e.salt),
                "nonce": _b64e(e.nonce),
                "wrapped": _b64e(e.wrapped),
            }
            for e in entries
        ],
    }


def atomic_write_private(path: Path, text: str) -> None:
    """atomic write + 0600 的單一收口（keystore 與 unlock_key env file 共用）。

    Windows 無 POSIX chmod，跳過（與 core/database.py 同慣例）— 機敏檔在
    Windows dev 無權限保護，呼叫端負責警示。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    if sys.platform != "win32":
        os.chmod(tmp, 0o600)
    tmp.replace(path)


def save_store(path: Path, store: dict) -> None:
    atomic_write_private(path, json.dumps(store, ensure_ascii=False, indent=2))


def load_store(path: Path) -> tuple[dict, list[TokenEntry]]:
    if not path.exists():
        raise KeyStoreError(f"keystore 不存在：{path} — 請先跑 enroll_fido2.py")
    try:
        store = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise KeyStoreError(f"keystore 格式錯誤：{e}") from e
    if store.get("version") != STORE_VERSION:
        raise KeyStoreError(f"keystore version 不符（期望 {STORE_VERSION}，實際 {store.get('version')}）")
    entries = [
        TokenEntry(
            label=t["label"],
            credential_id=_b64d(t["credential_id"]),
            salt=_b64d(t["salt"]),
            nonce=_b64d(t["nonce"]),
            wrapped=_b64d(t["wrapped"]),
        )
        for t in store.get("tokens", [])
    ]
    if not entries:
        raise KeyStoreError("keystore 內無任何 token entry")
    return store, entries


def unlock(entries: list[TokenEntry], backend, pin: str | None) -> tuple[bytes, str]:
    """逐 entry 嘗試以當前插入的 token 解鎖；回傳 (master, 成功的 entry label)。

    全部失敗 → KeyStoreError（彙整各 entry 失敗原因，方便判斷是插錯把
    還是檔案壞掉）。
    """
    from .backends import KeyBackendError

    failures: list[str] = []
    for entry in entries:
        try:
            wrap_key = backend.hmac_secret(entry.credential_id, entry.salt, pin)
            master = unwrap_master(entry, wrap_key)
            if len(master) != MASTER_LEN:
                raise KeyStoreError(f"entry「{entry.label}」解出長度異常（{len(master)} bytes）")
            return master, entry.label
        except (KeyBackendError, KeyStoreError) as e:
            failures.append(f"「{entry.label}」：{e}")
    raise KeyStoreError("所有 entry 解鎖失敗（token 未註冊或檔案損毀）：\n  " + "\n  ".join(failures))
