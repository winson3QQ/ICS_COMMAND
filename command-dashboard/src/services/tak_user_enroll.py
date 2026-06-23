"""services/tak_user_enroll.py — #344 發證即註冊 TAK managed user（enrollment）。

機制（2026-06-23 PoC 實證，見 GitHub #344 / memory tak-faction-group-identifier）：
TAK 的 usermod/certmod 是 **server-coupled**——連 takserver 本機 IPC 把變更熱套用，**不是檔案編輯器**。
所以 ICS（獨立 netns、無 Java）不能自己跑（會 timeout）；裸寫 UserAuthenticationFile.xml 跑著的
server 不認、且會被下次 usermod re-marshal 清掉。解法＝與 takserver **共享 network namespace** 的
registrar sidecar（`deploy/tak-server/registrar/registrar.sh`），經**共享卷檔佇列**收請求後本機跑 usermod。

本模組（ICS 端）：寫請求檔（atomic：先 .tmp 再 rename）→ 輪詢結果檔 → 回 {enrolled, reason}。
**best-effort**：queue 未配置 / registrar 沒跑 / 逾時 / 失敗 → 回 enrolled=False，**不 raise**
（不拖垮 #315 發證；裝置仍拿到證，只是落匿名 __ANON__，待 roster 補或重發/重連後分類補同步）。

分工：本模組＝enrollment（發證一次，建 cert-user + 初始群）；live 重分類＝REST update-groups
（services/tak_group_sync，PR #363，裝置已是 managed user 即生效）。
"""

from __future__ import annotations

import logging
import os
import time
import uuid

from core import config

log = logging.getLogger(__name__)


def is_configured() -> bool:
    """是否已配置 enrollment queue（未配置 → 發證不註冊、裝置落匿名、純視圖層 #343 仍運作）。"""
    return bool(config.TAK_ENROLL_QUEUE_DIR)


def _safe_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def enroll_device(callsign: str, fingerprint: str, group: str | None = None) -> dict:
    """把裝置證 fingerprint 註冊成 TAK managed user + 初始群（經 registrar 跑 usermod -f -g）。

    回 {enrolled: bool, reason: str, group?: str}。best-effort：任何失敗回 enrolled=False、不 raise。
    同步阻塞最多 TAK_ENROLL_TIMEOUT_S 等 registrar 結果（典型 1~2s；registrar 沒跑則逾時跳過）。
    """
    if not is_configured():
        return {"enrolled": False, "reason": "enroll-not-configured"}
    # TAK username = cert CN = callsign。非 ASCII CN 在 TAK 會 mojibake（memory tak-faction-group-identifier），
    # 且 registrar CN_RE 只收 ASCII → 非 ASCII callsign 一律不 enroll（裝置落匿名、可手動 roster）。
    # 關鍵：**不得**讓寫請求檔（ascii 編碼）對中文 callsign 拋 UnicodeEncodeError → 否則衝出 best-effort
    # 邊界、發證端 500 且證已記 = 幽靈列（#324 修過的坑，勿在 enroll 路徑重蹈）。
    if not (callsign or "").isascii():
        return {"enrolled": False, "reason": "non-ascii-callsign"}
    grp = (group or config.TAK_ENROLL_DEFAULT_GROUP or "neutral").strip()
    qdir = config.TAK_ENROLL_QUEUE_DIR
    req_dir = os.path.join(qdir, "requests")
    res_dir = os.path.join(qdir, "results")
    req_id = uuid.uuid4().hex
    req_path = os.path.join(req_dir, f"{req_id}.req")
    tmp_path = req_path + ".tmp"
    res_path = os.path.join(res_dir, f"{req_id}.res")

    # 請求 = 三行純文字：callsign / fingerprint / group（registrar bash 解析，無 JSON 依賴）。
    # atomic：先寫 .tmp 再 os.replace（同卷 rename），避免 registrar 讀到半寫。
    try:
        os.makedirs(req_dir, exist_ok=True)
        os.makedirs(res_dir, exist_ok=True)
        with open(tmp_path, "w", encoding="ascii") as f:
            f.write(f"{callsign}\n{fingerprint}\n{grp}\n")
        os.replace(tmp_path, req_path)
    except (OSError, ValueError) as exc:
        # ValueError 涵蓋 UnicodeEncodeError（防禦：callsign 已過 isascii，但 best-effort 契約「絕不 raise」）。
        log.warning("[tak-enroll] 寫請求檔失敗（best-effort 跳過）：%s", exc)
        _safe_unlink(tmp_path)
        return {"enrolled": False, "reason": f"queue-write-failed:{exc}"}

    # 輪詢結果檔（registrar 跑完 usermod 後寫 .res，首 token OK|ERR）。
    deadline = time.monotonic() + max(1.0, config.TAK_ENROLL_TIMEOUT_S)
    while time.monotonic() < deadline:
        if os.path.exists(res_path):
            try:
                with open(res_path, encoding="utf-8", errors="replace") as f:
                    body = f.read().strip()
            except OSError:
                body = ""
            _safe_unlink(res_path)
            if body.startswith("OK"):
                log.info("[tak-enroll] %s → TAK managed user，初始群=%s", callsign, grp)
                return {"enrolled": True, "reason": "ok", "group": grp}
            log.warning("[tak-enroll] %s registrar 回報失敗：%s", callsign, body[:200])
            return {"enrolled": False, "reason": f"registrar-error:{body[:120]}"}
        time.sleep(0.3)

    # 逾時：清掉未被處理的請求（registrar 沒跑 / 卡住），避免殘留佇列。
    # 已知小限制：若 registrar 只是「慢」（> timeout 才處理），它仍會 usermod 成功並寫一個沒人讀的 .res
    # → 該 .res 殘留 + 本次回報 timeout（偽陰性）。registrar 為本機 sidecar、usermod ~1-2s ≪ timeout，
    # 此 race 罕見；admin 重發冪等（usermod -f replace）。量大再加 results/ 清掃即可，暫不過度設計。
    _safe_unlink(req_path)
    log.warning(
        "[tak-enroll] %s 等 registrar 結果逾時 %.1fs（best-effort 跳過；registrar 未啟動?）",
        callsign,
        config.TAK_ENROLL_TIMEOUT_S,
    )
    return {"enrolled": False, "reason": "timeout"}
