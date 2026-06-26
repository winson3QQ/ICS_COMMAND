# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
derive_child.py — HKDF-SHA256 label-based child key 衍生（P1-12a，#227）

所有 child key 衍生的單一收口：master（32 bytes）→ HKDF → child（32 bytes）。
label 版本化（backup-v1 / db-v1 / ...）→ 未來 rotate 時換新 label 對齊
migration window，不動 master。

info 帶固定前綴，確保與其他系統的 HKDF 用途隔離（domain separation）。
"""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

KEY_LEN = 32
MASTER_LEN = 32
INFO_PREFIX = b"ics-keymgmt/v1/"

# 已定義的 label（docs/roadmap/p1-key-management.md key 階層）
# disk-v1：threat_model §8.4 決議，LUKS 統一 unlock 預留位（#231 消費）
KNOWN_LABELS = ("backup-v1", "db-v1", "audit-v1", "disk-v1")


def hkdf_derive(ikm: bytes, *, salt: bytes | None, info: bytes, length: int = KEY_LEN) -> bytes:
    """RFC 5869 HKDF-SHA256（低階介面，KAT 測試用）。

    salt=None 等同 RFC 規定的 zero-filled salt（hash length 個零）。
    """
    return HKDF(
        algorithm=hashes.SHA256(),
        length=length,
        salt=salt,
        info=info,
    ).derive(ikm)


def derive_child(master: bytes, label: str) -> bytes:
    """從 master 衍生指定 label 的 child key（32 bytes）。

    label 不限於 KNOWN_LABELS（未來 rotate 加新 label 不需改此處），
    但必須是非空 ASCII，避免編碼歧義造成跨平台 key 不一致。
    """
    if len(master) != MASTER_LEN:
        raise ValueError(f"master key 必須為 {MASTER_LEN} bytes，收到 {len(master)}")
    if not label or not label.isascii():
        raise ValueError(f"label 必須為非空 ASCII：{label!r}")
    return hkdf_derive(master, salt=None, info=INFO_PREFIX + label.encode("ascii"))


def fernet_key(child: bytes) -> str:
    """child key → Fernet 相容格式（urlsafe base64，backup-v1 用）。"""
    if len(child) != KEY_LEN:
        raise ValueError(f"child key 必須為 {KEY_LEN} bytes")
    return base64.urlsafe_b64encode(child).decode("ascii")


def hex_key(child: bytes) -> str:
    """child key → hex 字串（db-v1 / disk-v1 用，SQLCipher PRAGMA key x'..'）。"""
    if len(child) != KEY_LEN:
        raise ValueError(f"child key 必須為 {KEY_LEN} bytes")
    return child.hex()
