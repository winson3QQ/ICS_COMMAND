"""
test_keymgmt.py — P1-12a key management 基建測試（#227）

全 mock，不需實體 FIDO2 金鑰（真 token 整合驗收 → #230）。
MockBackend 模擬 CTAP2 hmac-secret 語意：同 (credential, salt) 恆同輸出、
非本 token 的 credential 拒絕 — 協定層（wrap/unwrap、多 token 冗餘、
竄改偵測）因此可完整覆蓋。
"""

import hashlib
import hmac as hmac_mod
import secrets
import sys
from pathlib import Path

import pytest

# scripts/keymgmt 在 repo root 下，不在 command-dashboard/src
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from keymgmt import keystore, mnemonic  # noqa: E402
from keymgmt.backends import KeyBackendError  # noqa: E402
from keymgmt.derive_child import (  # noqa: E402
    INFO_PREFIX,
    MASTER_LEN,
    derive_child,
    fernet_key,
    hex_key,
    hkdf_derive,
)
from keymgmt.enroll_fido2 import enroll_tokens  # noqa: E402
from keymgmt.unlock_key import master_from_env, render_env_file, write_env_file  # noqa: E402

# ─── Mock FIDO2 backend ──────────────────────────────────────────────────────


class MockToken:
    """一把虛擬實體 token：device secret + 其上建立的 credentials。"""

    def __init__(self):
        self.device_secret = secrets.token_bytes(32)
        self.credentials: set[bytes] = set()


class MockBackend:
    """模擬「當前插著 token X」。hmac-secret 語意對齊 CTAP2：
    deterministic per (credential, salt)；外來 credential 拒絕。"""

    def __init__(self, token: MockToken):
        self.token = token

    def register(self, label, pin):
        cred = secrets.token_bytes(16)
        self.token.credentials.add(cred)
        return cred

    def hmac_secret(self, credential_id, salt, pin):
        if credential_id not in self.token.credentials:
            raise KeyBackendError("credential 非本 token 所有")
        return hmac_mod.new(self.token.device_secret, salt, hashlib.sha256).digest()


# ─── HKDF（RFC 5869 KAT）─────────────────────────────────────────────────────


def test_hkdf_rfc5869_case1():
    okm = hkdf_derive(
        bytes.fromhex("0b" * 22),
        salt=bytes.fromhex("000102030405060708090a0b0c"),
        info=bytes.fromhex("f0f1f2f3f4f5f6f7f8f9"),
        length=42,
    )
    assert okm == bytes.fromhex("3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf34007208d5b887185865")


def test_hkdf_rfc5869_case3_empty_salt_info():
    okm = hkdf_derive(bytes.fromhex("0b" * 22), salt=b"", info=b"", length=42)
    assert okm == bytes.fromhex("8da4e775a563c18f715f802a063c5a31b8a11f5c5ee1879ec3454e5f3c738d2d9d201395faa4b61a96c8")


def test_derive_child_deterministic_and_label_separated():
    master = secrets.token_bytes(MASTER_LEN)
    keys = {label: derive_child(master, label) for label in ("backup-v1", "db-v1", "audit-v1", "disk-v1")}
    # 同 label 恆同；不同 label 互不相同；皆 32 bytes
    assert all(derive_child(master, lb) == k for lb, k in keys.items())
    assert len(set(keys.values())) == 4
    assert all(len(k) == 32 for k in keys.values())


def test_derive_child_rejects_bad_master_and_label():
    with pytest.raises(ValueError):
        derive_child(b"short", "backup-v1")
    with pytest.raises(ValueError):
        derive_child(secrets.token_bytes(32), "")
    with pytest.raises(ValueError):
        derive_child(secrets.token_bytes(32), "中文label")


def test_fernet_key_is_fernet_compatible():
    from cryptography.fernet import Fernet

    child = derive_child(secrets.token_bytes(32), "backup-v1")
    f = Fernet(fernet_key(child).encode())
    assert f.decrypt(f.encrypt(b"payload")) == b"payload"


# ─── 助記詞（rescue）────────────────────────────────────────────────────────


def test_wordlist_integrity_pinned():
    raw = mnemonic.WORDLIST_PATH.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == mnemonic.WORDLIST_SHA256
    words = raw.decode("ascii").split()
    assert len(words) == 2048
    assert words[0] == "abandon" and words[-1] == "zoo"


def test_mnemonic_roundtrip_24_words():
    master = secrets.token_bytes(32)
    words = mnemonic.encode(master)
    assert len(words) == 24
    assert mnemonic.decode(words) == master


def test_mnemonic_checksum_detects_transcription_error():
    words = mnemonic.encode(secrets.token_bytes(32))
    wrong = list(words)
    wrong[5] = "zoo" if words[5] != "zoo" else "abandon"
    with pytest.raises(ValueError, match="checksum"):
        mnemonic.decode(wrong)


def test_mnemonic_rejects_unknown_word_and_bad_count():
    words = mnemonic.encode(secrets.token_bytes(32))
    with pytest.raises(ValueError, match="wordlist"):
        mnemonic.decode(words[:-1] + ["notaword"])
    with pytest.raises(ValueError, match="字數"):
        mnemonic.decode(words[:23])


# ─── keystore：多 token 冗餘 + 竄改偵測 ─────────────────────────────────────


def _enroll_three(master):
    tokens = [MockToken() for _ in range(3)]
    entries = []
    for i, tok in enumerate(tokens):
        entries += enroll_tokens(master, MockBackend(tok), [f"token-{i}"], pin=None)
    return tokens, entries


def test_any_enrolled_token_can_unlock(tmp_path):
    master = secrets.token_bytes(MASTER_LEN)
    tokens, entries = _enroll_three(master)
    store_path = tmp_path / "master-key.enc"
    keystore.save_store(store_path, keystore.build_store(master, entries, "ics-command.local"))
    _, loaded = keystore.load_store(store_path)
    for i, tok in enumerate(tokens):
        got, label = keystore.unlock(loaded, MockBackend(tok), pin=None)
        assert got == master
        assert label == f"token-{i}"


def test_unenrolled_token_cannot_unlock():
    master = secrets.token_bytes(MASTER_LEN)
    _, entries = _enroll_three(master)
    stranger = MockToken()
    with pytest.raises(keystore.KeyStoreError, match="解鎖失敗"):
        keystore.unlock(entries, MockBackend(stranger), pin=None)


def test_tampered_store_detected(tmp_path):
    master = secrets.token_bytes(MASTER_LEN)
    tokens, entries = _enroll_three(master)
    # 竄改第一筆 wrapped ciphertext 一個 byte → AESGCM InvalidTag
    e0 = entries[0]
    entries[0] = keystore.TokenEntry(
        label=e0.label,
        credential_id=e0.credential_id,
        salt=e0.salt,
        nonce=e0.nonce,
        wrapped=bytes([e0.wrapped[0] ^ 0xFF]) + e0.wrapped[1:],
    )
    # 用 token-0 解：自己那筆已壞 → 整體失敗（其他 entry 的 credential 不在這把上）
    with pytest.raises(keystore.KeyStoreError):
        keystore.unlock(entries, MockBackend(tokens[0]), pin=None)
    # 用 token-1 解：不受影響
    got, _ = keystore.unlock(entries, MockBackend(tokens[1]), pin=None)
    assert got == master


def test_corrupted_nonce_field_raises_keystoreerror_not_valueerror():
    """nonce 長度損毀時 AESGCM 拋 ValueError（非 InvalidTag）—
    必須包成 KeyStoreError，否則穿透 unlock() 逐 entry 迴圈、壞一筆毀全部。"""
    master = secrets.token_bytes(MASTER_LEN)
    tok = MockToken()
    [entry] = enroll_tokens(master, MockBackend(tok), ["t"], pin=None)
    bad = keystore.TokenEntry(
        label=entry.label,
        credential_id=entry.credential_id,
        salt=entry.salt,
        nonce=b"x",  # 非法長度
        wrapped=entry.wrapped,
    )
    with pytest.raises(keystore.KeyStoreError):
        keystore.unlock([bad], MockBackend(tok), pin=None)


def test_env_map_labels_registered_in_known_labels():
    """ENV_MAP 是 KNOWN_LABELS 的子集 — label 註冊表單一來源（review 收口）。"""
    from keymgmt.derive_child import KNOWN_LABELS
    from keymgmt.unlock_key import ENV_MAP

    assert set(ENV_MAP) <= set(KNOWN_LABELS)


def test_store_load_validates(tmp_path):
    p = tmp_path / "master-key.enc"
    with pytest.raises(keystore.KeyStoreError, match="不存在"):
        keystore.load_store(p)
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(keystore.KeyStoreError, match="格式錯誤"):
        keystore.load_store(p)
    p.write_text('{"version": 99, "tokens": []}', encoding="utf-8")
    with pytest.raises(keystore.KeyStoreError, match="version"):
        keystore.load_store(p)


def test_reenroll_from_mnemonic_recovers_same_children():
    """失 token 演練的協定層驗證：助記詞 → 新 token re-enroll → child keys 不變。"""
    master = secrets.token_bytes(MASTER_LEN)
    recovered = mnemonic.decode(mnemonic.encode(master))
    new_token = MockToken()
    entries = enroll_tokens(recovered, MockBackend(new_token), ["災後新鑰"], pin=None)
    got, _ = keystore.unlock(entries, MockBackend(new_token), pin=None)
    assert derive_child(got, "backup-v1") == derive_child(master, "backup-v1")
    assert derive_child(got, "db-v1") == derive_child(master, "db-v1")


# ─── unlock_key：env file + fallback ────────────────────────────────────────


def test_render_env_file_format_and_keys():
    master = secrets.token_bytes(MASTER_LEN)
    content = render_env_file(master)
    lines = [ln for ln in content.splitlines() if ln and not ln.startswith("#")]
    kv = dict(ln.split("=", 1) for ln in lines)
    assert set(kv) == {"BACKUP_KEY", "DB_KEY"}
    assert kv["BACKUP_KEY"] == fernet_key(derive_child(master, "backup-v1"))
    assert kv["DB_KEY"] == hex_key(derive_child(master, "db-v1"))
    assert len(bytes.fromhex(kv["DB_KEY"])) == 32


def test_render_env_file_rejects_unknown_label():
    with pytest.raises(ValueError, match="無對應 env var"):
        render_env_file(secrets.token_bytes(MASTER_LEN), labels=("disk-v1",))


def test_write_env_file(tmp_path):
    out = tmp_path / "keys.env"
    write_env_file(secrets.token_bytes(MASTER_LEN), out)
    assert out.exists()
    assert "BACKUP_KEY=" in out.read_text(encoding="utf-8")
    assert not out.with_suffix(out.suffix + ".tmp").exists()  # atomic：tmp 已 rename


def test_master_from_env_fallback(monkeypatch):
    master = secrets.token_bytes(MASTER_LEN)
    monkeypatch.setenv("ICS_MASTER_KEY", master.hex())
    assert master_from_env() == master
    monkeypatch.setenv("ICS_MASTER_KEY", "abcd")  # 長度錯
    with pytest.raises(SystemExit):
        master_from_env()
    monkeypatch.setenv("ICS_MASTER_KEY", "zz" * 32)  # 非 hex
    with pytest.raises(SystemExit):
        master_from_env()
    monkeypatch.delenv("ICS_MASTER_KEY")
    assert master_from_env() is None


def test_info_prefix_domain_separation():
    """同 master、同字串，經 derive_child 與裸 HKDF（無前綴）必須不同 — 防跨系統撞 key。"""
    master = secrets.token_bytes(MASTER_LEN)
    assert derive_child(master, "backup-v1") != hkdf_derive(master, salt=None, info=b"backup-v1")
    assert INFO_PREFIX.startswith(b"ics-keymgmt/")
