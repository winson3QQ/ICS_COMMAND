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


def _submit_op(callsign: str, fingerprint: str, group: str, op: str, timeout_s: float) -> tuple[str, str]:
    """寫請求檔（4 行 callsign/fingerprint/group/op，atomic）→ 輪詢結果。所有 op 共用此通道。

    回 (outcome, body)，outcome ∈ {"ok","err","timeout","write-failed"}；body = registrar 結果原文。
    best-effort：**絕不 raise**（caller 各自包成回傳形狀）。同步阻塞最多 timeout_s 等 registrar。
    """
    qdir = config.TAK_ENROLL_QUEUE_DIR
    req_dir = os.path.join(qdir, "requests")
    res_dir = os.path.join(qdir, "results")
    req_id = uuid.uuid4().hex
    req_path = os.path.join(req_dir, f"{req_id}.req")
    tmp_path = req_path + ".tmp"
    res_path = os.path.join(res_dir, f"{req_id}.res")
    # atomic：先寫 .tmp 再 os.replace（同卷 rename），避免 registrar 讀到半寫。ValueError 涵蓋
    # UnicodeEncodeError（callsign 已過 isascii，但 best-effort 契約「絕不 raise」，戒慎兜底）。
    try:
        os.makedirs(req_dir, exist_ok=True)
        os.makedirs(res_dir, exist_ok=True)
        with open(tmp_path, "w", encoding="ascii") as f:
            f.write(f"{callsign}\n{fingerprint}\n{group}\n{op}\n")
        os.replace(tmp_path, req_path)
    except (OSError, ValueError) as exc:
        log.warning("[tak-enroll] 寫請求檔失敗（best-effort 跳過）：%s", exc)
        _safe_unlink(tmp_path)
        return ("write-failed", str(exc))

    deadline = time.monotonic() + max(1.0, timeout_s)
    while time.monotonic() < deadline:
        if os.path.exists(res_path):
            try:
                with open(res_path, encoding="utf-8", errors="replace") as f:
                    body = f.read()  # 不 strip：reconcile 多行（首行 OK + 資料列），保留換行供 caller 解析
            except OSError:
                body = ""
            _safe_unlink(res_path)
            return ("ok" if body.lstrip().startswith("OK") else "err", body)
        time.sleep(0.3)

    # 逾時：清掉未被處理的請求（registrar 沒跑 / 卡住）。已知小限制：registrar「慢」會留沒人讀的 .res
    # （偽陰性）；usermod ~1-2s ≪ timeout，race 罕見、重試冪等，暫不過度設計。
    _safe_unlink(req_path)
    log.warning("[tak-enroll] op=%s 等 registrar 逾時 %.1fs（best-effort；registrar 未啟動?）", op, timeout_s)
    return ("timeout", "")


def enroll_device(callsign: str, fingerprint: str, group: str | None = None) -> dict:
    """把裝置證 fingerprint 註冊成 TAK managed user + 初始群（經 registrar 跑 usermod -f -g）。

    回 {enrolled: bool, reason: str, group?: str}。best-effort：任何失敗回 enrolled=False、不 raise。
    """
    if not is_configured():
        return {"enrolled": False, "reason": "enroll-not-configured"}
    # TAK username = cert CN = callsign。非 ASCII CN 在 TAK 會 mojibake（memory tak-faction-group-identifier），
    # 且 registrar CN_RE 只收 ASCII → 非 ASCII callsign 一律不 enroll（裝置落匿名、可手動 roster）。
    if not (callsign or "").isascii():
        return {"enrolled": False, "reason": "non-ascii-callsign"}
    grp = (group or config.TAK_ENROLL_DEFAULT_GROUP or "neutral").strip()
    outcome, body = _submit_op(callsign, fingerprint, grp, "register", config.TAK_ENROLL_TIMEOUT_S)
    if outcome == "ok":
        log.info("[tak-enroll] %s → TAK managed user，初始群=%s", callsign, grp)
        return {"enrolled": True, "reason": "ok", "group": grp}
    if outcome == "write-failed":
        return {"enrolled": False, "reason": f"queue-write-failed:{body}"}
    if outcome == "timeout":
        return {"enrolled": False, "reason": "timeout"}
    log.warning("[tak-enroll] %s registrar 回報失敗：%s", callsign, body[:200])
    return {"enrolled": False, "reason": f"registrar-error:{body.strip()[:120]}"}


def deregister_device(callsign: str) -> dict:
    """#398 A：從 TAK 移除 managed user（usermod -D，經 registrar）——讓 ICS 的撤銷成為**真 deregister**，
    不再只是帳面 flag。回 {ok: bool, reason: str}。best-effort 不 raise。

    非 ASCII callsign 本就沒 enroll（無 user 可刪）→ 直接回（不浪費 registrar 一趟）。
    """
    if not is_configured():
        return {"ok": False, "reason": "enroll-not-configured"}
    if not (callsign or "").isascii():
        return {"ok": False, "reason": "non-ascii-callsign"}
    outcome, body = _submit_op(callsign, "", "neutral", "deregister", config.TAK_ENROLL_TIMEOUT_S)
    if outcome == "ok":
        log.info("[tak-enroll] deregistered %s", callsign)
        return {"ok": True, "reason": "deregistered"}
    if outcome in ("timeout", "write-failed"):
        return {"ok": False, "reason": outcome}
    return {"ok": False, "reason": f"registrar-error:{body.strip()[:120]}"}


def reconcile_tak_users() -> dict:
    """#398 B：讀 TAK UserAuthenticationFile 取 cert-user → fingerprint 真相（registrar reconcile op）。

    回 {ok: bool, users: list[{callsign, fingerprint}], reason: str}。best-effort 不 raise。
    供 dashboard 對帳 ICS 紀錄 vs TAK 實際（殭屍 / fingerprint 不符 / 未同步）。
    """
    if not is_configured():
        return {"ok": False, "users": [], "reason": "enroll-not-configured"}
    # reconcile 要等 registrar 讀檔 + 逐列 sed，給比 enroll 寬的窗（至少 5s）。
    outcome, body = _submit_op("", "", "neutral", "reconcile", max(config.TAK_ENROLL_TIMEOUT_S, 5.0))
    if outcome != "ok":
        reason = outcome if outcome in ("timeout", "write-failed") else f"registrar-error:{body.strip()[:120]}"
        return {"ok": False, "users": [], "reason": reason}
    users: list[dict] = []
    # 首行 'OK reconcile <count>'，其後每行 callsign<TAB>fingerprint。
    for line in body.splitlines()[1:]:
        if "\t" not in line:
            continue
        cs, fp = line.split("\t", 1)
        cs, fp = cs.strip(), fp.strip()
        if cs:
            users.append({"callsign": cs, "fingerprint": fp})
    return {"ok": True, "users": users, "reason": "ok"}
