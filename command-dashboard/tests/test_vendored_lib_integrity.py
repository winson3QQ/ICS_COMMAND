"""vendored 前端 lib 供應鏈完整性（#64-4）。

POLICY.md 以 SHA256 釘住 vendored `static/lib/pmtiles.js`，但原本只有文件記錄、無 runtime/CI
驗證 on-disk 檔未被替換成 backdoor 版。本測試讓 CI 把關：on-disk 內容 SHA 必須等於釘值。

line-ending 正規化（CRLF→LF）再 hash：跨平台（Windows 工作目錄可能 CRLF、CI/Linux 為 LF），
仍能抓任何內容竄改（line-ending 以外的位元組變動都會翻 hash）。釘值＝git blob（LF）SHA＝POLICY。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

# 與 docs/design/POLICY.md 同步（升 pmtiles.js 版時兩處一起改）。
_PMTILES_JS_SHA256 = "36bcbe1ba97cc07b3fc90cee9cba11729b04e25ec8790cf65a0787d5b38e091b"
_CMD_ROOT = Path(__file__).resolve().parents[1]  # command-dashboard/


def _normalized_sha256(p: Path) -> str:
    data = p.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def test_pmtiles_js_matches_pinned_sha():
    p = _CMD_ROOT / "static" / "lib" / "pmtiles.js"
    assert p.exists(), f"vendored pmtiles.js 不存在：{p}"
    got = _normalized_sha256(p)
    assert got == _PMTILES_JS_SHA256, (
        f"pmtiles.js SHA 不符（疑似被替換）：{got} != {_PMTILES_JS_SHA256}"
    )


def test_policy_doc_pins_same_sha():
    # POLICY.md 與測試釘的 SHA 必須一致（避免文件/測試各說各話）。
    policy = _CMD_ROOT / "docs" / "design" / "POLICY.md"
    assert policy.exists(), f"POLICY.md 不存在：{policy}"
    assert _PMTILES_JS_SHA256 in policy.read_text(encoding="utf-8"), (
        "POLICY.md 未釘與測試相同的 pmtiles.js SHA（升版需同步兩處）"
    )
