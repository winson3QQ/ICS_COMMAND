"""
mnemonic.py — BIP-39 助記詞編解碼（rescue 紙本 SOP 用，P1-12a #227）

master key（32 bytes entropy）↔ 24 字英文助記詞。失去所有 FIDO2 token 時，
operator 以紙本助記詞 re-enroll（enroll_fido2.py --from-mnemonic）。

wordlist vendored 自 bitcoin/bips（bip-0039/english.txt，public domain），
sha256 完整性由 tests/unit/test_keymgmt.py 釘住。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

WORDLIST_PATH = Path(__file__).parent / "wordlist_english.txt"
WORDLIST_SHA256 = "2f5eed53a4727b4bf8880d8f3f199efc90e58503646d9ff8eff3a2ed3b24dbda"

_VALID_ENTROPY_LENS = (16, 20, 24, 28, 32)  # BIP-39 ENT 128~256 bits

_words_cache: list[str] | None = None
_index_cache: dict[str, int] | None = None


def _load_wordlist() -> list[str]:
    global _words_cache, _index_cache
    if _words_cache is None:
        raw = WORDLIST_PATH.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != WORDLIST_SHA256:
            raise RuntimeError(
                f"wordlist 完整性檢查失敗（期望 {WORDLIST_SHA256[:16]}…，"
                f"實際 {digest[:16]}…）— 檔案可能被竄改"
            )
        _words_cache = raw.decode("ascii").split()
        if len(_words_cache) != 2048:
            raise RuntimeError(f"wordlist 應為 2048 字，實際 {len(_words_cache)}")
        _index_cache = {w: i for i, w in enumerate(_words_cache)}
    return _words_cache


def encode(entropy: bytes) -> list[str]:
    """entropy → BIP-39 助記詞（32 bytes → 24 字）。"""
    if len(entropy) not in _VALID_ENTROPY_LENS:
        raise ValueError(f"entropy 長度須為 {_VALID_ENTROPY_LENS} 之一，收到 {len(entropy)}")
    words = _load_wordlist()
    ent_bits = len(entropy) * 8
    cs_bits = ent_bits // 32
    checksum = hashlib.sha256(entropy).digest()
    bits = bin(int.from_bytes(entropy, "big"))[2:].zfill(ent_bits)
    bits += bin(int.from_bytes(checksum, "big"))[2:].zfill(256)[:cs_bits]
    return [words[int(bits[i : i + 11], 2)] for i in range(0, len(bits), 11)]


def decode(words_in: list[str]) -> bytes:
    """BIP-39 助記詞 → entropy。checksum 不符或字不在表內 → ValueError。"""
    _load_wordlist()
    assert _index_cache is not None
    n = len(words_in)
    if n % 3 != 0 or not (12 <= n <= 24):
        raise ValueError(f"助記詞字數須為 12/15/18/21/24，收到 {n}")
    try:
        indices = [_index_cache[w.strip().lower()] for w in words_in]
    except KeyError as e:
        raise ValueError(f"字不在 BIP-39 wordlist 內：{e.args[0]!r}") from None
    bits = "".join(bin(i)[2:].zfill(11) for i in indices)
    cs_bits = len(bits) // 33
    ent_bits = len(bits) - cs_bits
    entropy = int(bits[:ent_bits], 2).to_bytes(ent_bits // 8, "big")
    expected = bin(int.from_bytes(hashlib.sha256(entropy).digest(), "big"))[2:].zfill(256)[:cs_bits]
    if bits[ent_bits:] != expected:
        raise ValueError("助記詞 checksum 不符 — 抄寫可能有誤，請逐字核對紙本")
    return entropy
