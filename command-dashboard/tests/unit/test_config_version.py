# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""兩軌版號常數 sanity（守 config.py 版號 bump 的手滑）。

背景：2026-07-03 一次 CMD_VERSION bump 把舊版字串留在 `os.getenv("CMD_VERSION", "v1.24.2",
"v1.24.1", …)` 成第三個位置參數 → `TypeError: getenv() takes from 1 to 2 positional
arguments but 3 were given`，import config 即炸、prod 容器 crash-loop。純前端 slice 略過
pytest 沒抓到。本測試把「版號常數必為格式正確字串」釘進 CI——任何 getenv 誤用或格式漂移
（import 期 TypeError 或格式不符）都會在此紅燈，不必等 runtime。
"""

import re


def test_app_version_is_semver_string():
    from core.config import APP_VERSION

    assert isinstance(APP_VERSION, str), f"APP_VERSION 非字串：{APP_VERSION!r}（疑 os.getenv 位置參數手滑）"
    assert re.fullmatch(r"\d+\.\d+\.\d+", APP_VERSION), f"APP_VERSION 非 SemVer：{APP_VERSION!r}"


def test_cmd_version_is_vprefixed_semver_string():
    from core.config import CMD_VERSION

    assert isinstance(CMD_VERSION, str), f"CMD_VERSION 非字串：{CMD_VERSION!r}（疑 os.getenv 位置參數手滑）"
    assert re.fullmatch(r"v\d+\.\d+\.\d+", CMD_VERSION), f"CMD_VERSION 非 vX.Y.Z：{CMD_VERSION!r}"
