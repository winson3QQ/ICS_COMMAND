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
    # 請求協定 = 三行：callsign / fingerprint / group
    assert cap and cap[0].splitlines() == ["dev-01", "AA:BB:CC", "neutral"]


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
