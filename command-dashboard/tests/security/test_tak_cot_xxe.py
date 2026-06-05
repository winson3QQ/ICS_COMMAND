"""P2-02（#102）— CoT XML 解析的 XXE / 實體展開防護（ROADMAP P2-08 接點）。

CoT 來自外部不可信。parse_cot_xml 走 defusedxml（forbid_dtd / forbid_entities /
forbid_external），任何 DTD / 外部實體 / 實體炸彈都必須被擋下並收斂成 CoTParseError，
**絕不**解析出外部檔案內容或撐爆記憶體。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services.tak_service import CoTParseError, parse_cot_xml

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cot"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_xxe_external_file_entity_blocked():
    # 外部實體 file:///etc/passwd 必須被擋，不得解析出檔案內容
    with pytest.raises(CoTParseError):
        parse_cot_xml(_load("xxe_external_file.xml"))


def test_xxe_does_not_leak_local_file_content():
    # 即使被擋，也再確認任何路徑都不會回傳含 passwd 標記的內容
    try:
        e = parse_cot_xml(_load("xxe_external_file.xml"))
    except CoTParseError:
        return  # 擋下 = 正確
    assert "root:" not in (e.remarks or "")
    assert "root:" not in str(e.detail)


def test_billion_laughs_entity_expansion_blocked():
    # 實體展開炸彈：forbid_dtd 連 DTD 都拒 → 解析前就擋，不會展開撐爆記憶體
    with pytest.raises(CoTParseError):
        parse_cot_xml(_load("billion_laughs.xml"))


def test_inline_doctype_rejected():
    # 任何 DOCTYPE/DTD 一律拒（CoT 串流不該帶 DTD）
    payload = (
        '<?xml version="1.0"?>\n'
        "<!DOCTYPE event []>\n"
        '<event version="2.0" uid="D-1" type="a-f-G" time="2026-06-05T04:00:00Z" '
        'start="2026-06-05T04:00:00Z" stale="2026-06-05T04:10:00Z" how="m-g">'
        '<point lat="24.1" lon="120.6"/></event>'
    )
    with pytest.raises(CoTParseError):
        parse_cot_xml(payload)
