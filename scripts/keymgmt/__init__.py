# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
keymgmt — P1-12a 統一 key management 基礎建設（issue #227，umbrella #226）

架構（docs/roadmap/p1-key-management.md）：

    FIDO2 token (CTAP2 hmac-secret)
         │ unlock：PIN + touch（任一已註冊 token）
         ▼
    master key (32 bytes，僅 process memory)
         │ HKDF-SHA256 label-based derive（derive_child.py）
         ▼
         ├── child[0] = "backup-v1" → Fernet key（backup 加密）
         ├── child[1] = "db-v1"     → SQLCipher key（live DB，P1-12c）
         ├── child[2] = "audit-v1"  → 預留（audit log 簽章）
         └── child[3] = "disk-v1"   → LUKS unlock（threat_model §8.4，#231）

模組分工：
- derive_child.py  HKDF 衍生（單一收口）
- mnemonic.py      BIP-39 助記詞（rescue 紙本，wordlist vendored 自 bitcoin/bips）
- keystore.py      master key 的 per-token AES-GCM wrap 檔（master-key.enc）
- backends.py      FIDO2 backend 介面 + 真實裝置實作（lazy import fido2）
- enroll_fido2.py  CLI：註冊 N 把 token + rescue 助記詞輸出
- unlock_key.py    CLI：解鎖 → 衍生 child keys → 寫 env file 供 systemd

安全邊界（threat_model §3.4）：本套件只防 at-rest 洩漏；
runtime 已解密（master 在 process memory），不防主機特權存取。
"""
