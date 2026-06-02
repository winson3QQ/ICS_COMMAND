"""event_taxonomy_validate — schema + 參照完整性驗證（#66 PR-A）。"""

from __future__ import annotations

import pytest

from services.event_taxonomy_validate import validate_taxonomy


def _ok():
    return {
        "version": 1,
        "groups": [
            {"key": "security", "label": "安全威脅", "order": 1},
            {"key": "ops", "label": "行動管理", "order": 2},
        ],
        "events": [
            {"key": "explosive", "label": "疑似爆裂物", "group": "security",
             "abbr": "爆", "severity": "critical", "cot_type": "a-h-G"},
            {"key": "other", "label": "其他", "group": "ops",
             "abbr": "他", "severity": "info", "cot_type": "a-u-G"},
        ],
    }


def test_valid_passes():
    validate_taxonomy(_ok())                 # 無 previous
    validate_taxonomy(_ok(), previous=_ok())  # previous = 自身（superset 成立）


def test_source_optional_and_enum():
    b = _ok()
    validate_taxonomy(b)                       # 無 source（可選，向後相容）
    b["events"][0]["source"] = "napsg"
    b["events"][1]["source"] = "ics"
    validate_taxonomy(b)                       # napsg / ics 皆合法


@pytest.mark.parametrize("mutate, frag", [
    (lambda b: b.pop("events"), "groups[] 與 events[]"),
    (lambda b: b["events"][0].__setitem__("severity", "bogus"), "severity"),
    (lambda b: b["events"][0].__setitem__("cot_type", ""), "cot_type"),
    (lambda b: b["events"][0].__setitem__("group", "nope"), "group 不存在"),
    (lambda b: b["events"][0].__setitem__("key", "Bad Key"), "key 格式"),
    (lambda b: b["events"][0].__setitem__("key", "__proto__"), "保留字"),
    (lambda b: b["groups"][0].__setitem__("key", "constructor"), "保留字"),
    (lambda b: b["events"].append(dict(b["events"][0])), "event key 重複"),
    (lambda b: b["groups"][0].__setitem__("label", "  "), "缺 label"),
    (lambda b: b["events"][0].__setitem__("deleted", "yes"), "deleted 需為 bool"),
    (lambda b: b["events"][0].__setitem__("source", "bogus"), "source 非法"),
])
def test_schema_violations_raise(mutate, frag):
    body = _ok()
    mutate(body)
    with pytest.raises(ValueError, match=frag):
        validate_taxonomy(body)


def test_superset_blocks_key_removal_and_rename():
    prev = _ok()
    # 移除既有 event key
    dropped = {**_ok(), "events": _ok()["events"][:1]}
    with pytest.raises(ValueError, match="不可移除或改名既有 event key"):
        validate_taxonomy(dropped, previous=prev)
    # 改名既有 group key（= 移除舊 + 新增新）
    renamed = _ok()
    renamed["groups"][0]["key"] = "security2"
    renamed["events"][0]["group"] = "security2"
    with pytest.raises(ValueError, match="不可移除或改名既有 group key"):
        validate_taxonomy(renamed, previous=prev)


def test_soft_delete_retains_key_and_passes():
    """soft-delete = 標 deleted=true 但保留 key → superset 成立、驗證通過。"""
    body = _ok()
    body["events"][0]["deleted"] = True
    validate_taxonomy(body, previous=_ok())


def test_cannot_delete_nonempty_group():
    body = _ok()
    body["groups"][0]["deleted"] = True   # 刪 security，但 explosive 還在且未刪
    with pytest.raises(ValueError, match="已標記刪除"):
        validate_taxonomy(body, previous=_ok())


def test_can_delete_group_when_all_its_events_deleted():
    body = _ok()
    body["groups"][0]["deleted"] = True
    body["events"][0]["deleted"] = True   # explosive 一併刪 → 允許
    validate_taxonomy(body, previous=_ok())
