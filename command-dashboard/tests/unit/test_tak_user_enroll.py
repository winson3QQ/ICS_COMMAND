# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""#344 services/tak_user_enroll — 發證即註冊 TAK managed user（共享卷檔佇列）的單元測試。

不碰真 takserver / registrar；用 tmp 佇列 + 背景執行緒模擬 registrar 寫結果檔，驗：
未配置跳過、請求檔三行協定、成功/失敗/逾時 best-effort、初始群 passthrough。
"""

import glob
import os
import threading
import time

import pytest

import core.config as config
from services import tak_user_enroll


def _fake_registrar(qdir: str, result: str, capture: list, stop: threading.Event) -> None:
    """模擬 registrar：等 *.req 出現 → 記內容 → 刪請求 → 寫對應 .res。"""
    req_dir = os.path.join(qdir, "requests")
    res_dir = os.path.join(qdir, "results")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not stop.is_set():
        for f in glob.glob(os.path.join(req_dir, "*.req")):
            rid = os.path.basename(f)[:-4]
            with open(f, encoding="utf-8") as fh:
                capture.append(fh.read())
            os.remove(f)
            os.makedirs(res_dir, exist_ok=True)
            with open(os.path.join(res_dir, f"{rid}.res"), "w", encoding="utf-8") as fh:
                fh.write(result)
            return
        time.sleep(0.02)


@pytest.fixture
def queue(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TAK_ENROLL_QUEUE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "TAK_ENROLL_DEFAULT_GROUP", "neutral")
    monkeypatch.setattr(config, "TAK_ENROLL_TIMEOUT_S", 3.0)
    return str(tmp_path)


def test_not_configured_skips(monkeypatch):
    monkeypatch.setattr(config, "TAK_ENROLL_QUEUE_DIR", "")
    out = tak_user_enroll.enroll_device("dev-01", "AA:BB")
    assert out == {"enrolled": False, "reason": "enroll-not-configured"}


def test_non_ascii_callsign_skips_without_raising(queue):
    """#324 回歸：中文 callsign 不可讓 ascii 寫檔 raise → 發證端 500/幽靈列。改 best-effort 跳過。"""
    out = tak_user_enroll.enroll_device("主教", "AA:BB:CC")
    assert out == {"enrolled": False, "reason": "non-ascii-callsign"}
    # 不得寫出任何請求檔（直接擋在前）
    assert glob.glob(os.path.join(queue, "requests", "*.req")) == []


def test_success_and_request_protocol(queue):
    cap: list = []
    stop = threading.Event()
    t = threading.Thread(target=_fake_registrar, args=(queue, "OK neutral", cap, stop))
    t.start()
    try:
        out = tak_user_enroll.enroll_device("dev-01", "AA:BB:CC", group="neutral")
    finally:
        stop.set()
        t.join()
    assert out["enrolled"] is True
    assert out["group"] == "neutral"
    # 請求協定 = 四行：callsign / fingerprint / group / op（#398：op 第 4 行，enroll=register）
    assert cap and cap[0].splitlines() == ["dev-01", "AA:BB:CC", "neutral", "register"]


def test_default_group_when_none(queue):
    cap: list = []
    stop = threading.Event()
    t = threading.Thread(target=_fake_registrar, args=(queue, "OK neutral", cap, stop))
    t.start()
    try:
        out = tak_user_enroll.enroll_device("dev-02", "FP", group=None)
    finally:
        stop.set()
        t.join()
    assert out["enrolled"] is True and out["group"] == "neutral"
    assert cap[0].splitlines()[2] == "neutral"  # group 由預設帶入


def test_registrar_error_best_effort(queue):
    cap: list = []
    stop = threading.Event()
    t = threading.Thread(target=_fake_registrar, args=(queue, "ERR rc=1 usermod boom", cap, stop))
    t.start()
    try:
        out = tak_user_enroll.enroll_device("dev-03", "FP")
    finally:
        stop.set()
        t.join()
    assert out["enrolled"] is False
    assert out["reason"].startswith("registrar-error:")


def test_timeout_when_no_registrar_and_cleans_request(queue, monkeypatch):
    monkeypatch.setattr(config, "TAK_ENROLL_TIMEOUT_S", 0.5)  # 無 registrar → 快速逾時
    out = tak_user_enroll.enroll_device("dev-04", "FP")
    assert out == {"enrolled": False, "reason": "timeout"}
    # 逾時後請求檔應被清掉（不殘留）
    assert glob.glob(os.path.join(queue, "requests", "*.req")) == []


# ── #507：enroll_infra_groups（ICS 自身 infra 證多群註冊）──────────────────────


def test_enroll_infra_groups_multi_group_sorted(queue):
    """多群 → 排序去空白逗號 join（registrar 逐群展開 -g）；請求協定 op=register。"""
    cap: list = []
    stop = threading.Event()
    t = threading.Thread(target=_fake_registrar, args=(queue, "OK blue,neutral,red", cap, stop))
    t.start()
    try:
        out = tak_user_enroll.enroll_infra_groups("ics-marti-read", "FC:BB", {"neutral", "blue", "red"})
    finally:
        stop.set()
        t.join()
    assert out["enrolled"] is True
    assert out["groups"] == "blue,neutral,red"  # sorted、無空白
    lines = cap[0].splitlines()
    assert lines[0] == "ics-marti-read" and lines[1] == "FC:BB"
    assert lines[2] == "blue,neutral,red" and lines[3] == "register"


def test_enroll_infra_groups_strips_whitespace_and_dedup(queue):
    """群集清洗：strip 空白 + 去重 → registrar 才不會對 ' red' fail-closed。"""
    cap: list = []
    stop = threading.Event()
    t = threading.Thread(target=_fake_registrar, args=(queue, "OK blue,red", cap, stop))
    t.start()
    try:
        out = tak_user_enroll.enroll_infra_groups("ics-marti-write", "AA:BB", {"blue ", " blue", "red"})
    finally:
        stop.set()
        t.join()
    assert out["enrolled"] is True
    assert cap[0].splitlines()[2] == "blue,red"  # dedup + strip


def test_enroll_infra_groups_empty_no_request(queue):
    """空群集 → no-groups，不寫請求（不送空 register）。"""
    out = tak_user_enroll.enroll_infra_groups("ics-marti-read", "FC:BB", set())
    assert out == {"enrolled": False, "reason": "no-groups"}
    assert glob.glob(os.path.join(queue, "requests", "*.req")) == []


def test_enroll_infra_groups_not_configured(monkeypatch):
    monkeypatch.setattr(config, "TAK_ENROLL_QUEUE_DIR", "")
    out = tak_user_enroll.enroll_infra_groups("ics-marti-read", "FC:BB", {"blue"})
    assert out == {"enrolled": False, "reason": "enroll-not-configured"}


# ── #398 Slice 2：deregister + reconcile ──────────────────────────────────────


def test_deregister_writes_op_and_succeeds(queue):
    cap: list = []
    stop = threading.Event()
    t = threading.Thread(target=_fake_registrar, args=(queue, "OK deregistered", cap, stop))
    t.start()
    try:
        out = tak_user_enroll.deregister_device("dev-09")
    finally:
        stop.set()
        t.join()
    assert out == {"ok": True, "reason": "deregistered"}
    # 請求第 4 行 op = deregister（callsign 在、fp 空）
    lines = cap[0].splitlines()
    assert lines[0] == "dev-09" and lines[3] == "deregister"


def test_deregister_non_ascii_skips_without_request(queue):
    # 中文 callsign 本就沒 enroll → 不發 registrar 請求（無 user 可刪）
    out = tak_user_enroll.deregister_device("主教")
    assert out == {"ok": False, "reason": "non-ascii-callsign"}
    assert glob.glob(os.path.join(queue, "requests", "*.req")) == []


def test_deregister_not_configured(monkeypatch):
    monkeypatch.setattr(config, "TAK_ENROLL_QUEUE_DIR", "")
    assert tak_user_enroll.deregister_device("x") == {"ok": False, "reason": "enroll-not-configured"}


def test_reconcile_parses_groups(queue):
    """#404：reconcile 第 3 欄 = 逗號分隔群清單，解析成 list；空群 → []。"""
    cap: list = []
    stop = threading.Event()
    rows = "ics-cot\tA2:10:7F\tred,blue,neutral\nics-tak-admin\t61:F8:E3\t__ANON__\nselfclosed\tDD:EE\t"
    result = "OK reconcile 3\n" + rows + "\n"
    t = threading.Thread(target=_fake_registrar, args=(queue, result, cap, stop))
    t.start()
    try:
        out = tak_user_enroll.reconcile_tak_users()
    finally:
        stop.set()
        t.join()
    assert out["ok"] is True
    assert out["users"] == [
        {"callsign": "ics-cot", "fingerprint": "A2:10:7F", "groups": ["red", "blue", "neutral"]},
        {"callsign": "ics-tak-admin", "fingerprint": "61:F8:E3", "groups": ["__ANON__"]},
        {"callsign": "selfclosed", "fingerprint": "DD:EE", "groups": []},  # 無顯式群 → runtime __ANON__
    ]
    assert cap[0].splitlines()[3] == "reconcile"  # op


def test_reconcile_backward_compat_two_field(queue):
    """#404 向後相容：舊式 registrar 只回兩欄 → groups=None（未知，不誤判隔離破口）。"""
    cap: list = []
    stop = threading.Event()
    result = "OK reconcile 1\nred-01\t42:26:5E\n"
    t = threading.Thread(target=_fake_registrar, args=(queue, result, cap, stop))
    t.start()
    try:
        out = tak_user_enroll.reconcile_tak_users()
    finally:
        stop.set()
        t.join()
    assert out["users"] == [{"callsign": "red-01", "fingerprint": "42:26:5E", "groups": None}]


def test_reconcile_timeout(queue, monkeypatch):
    monkeypatch.setattr(config, "TAK_ENROLL_TIMEOUT_S", 0.5)
    out = tak_user_enroll.reconcile_tak_users()
    assert out["ok"] is False and out["reason"] == "timeout" and out["users"] == []


# ── #404：strip-anon（移出 __ANON__ 隔離破口修復）──────────────────────────────


def test_strip_anon_writes_op_and_succeeds(queue):
    cap: list = []
    stop = threading.Event()
    t = threading.Thread(target=_fake_registrar, args=(queue, "OK stripped", cap, stop))
    t.start()
    try:
        out = tak_user_enroll.strip_anon_group("ics-cot", "A2:10:7F")
    finally:
        stop.set()
        t.join()
    assert out == {"ok": True, "reason": "stripped"}
    # 請求協定：callsign / fingerprint / group(placeholder) / op=strip-anon
    lines = cap[0].splitlines()
    assert lines[0] == "ics-cot" and lines[1] == "A2:10:7F" and lines[3] == "strip-anon"


def test_strip_anon_non_ascii_skips_without_request(queue):
    out = tak_user_enroll.strip_anon_group("主教", "A2:10:7F")
    assert out == {"ok": False, "reason": "non-ascii-callsign"}
    assert glob.glob(os.path.join(queue, "requests", "*.req")) == []


def test_strip_anon_not_configured(monkeypatch):
    monkeypatch.setattr(config, "TAK_ENROLL_QUEUE_DIR", "")
    assert tak_user_enroll.strip_anon_group("x", "FP") == {"ok": False, "reason": "enroll-not-configured"}


def test_strip_anon_registrar_error_best_effort(queue):
    cap: list = []
    stop = threading.Event()
    t = threading.Thread(target=_fake_registrar, args=(queue, "ERR rc=1 usermod boom", cap, stop))
    t.start()
    try:
        out = tak_user_enroll.strip_anon_group("ics-cot", "A2:10:7F")
    finally:
        stop.set()
        t.join()
    assert out["ok"] is False and out["reason"].startswith("registrar-error:")
