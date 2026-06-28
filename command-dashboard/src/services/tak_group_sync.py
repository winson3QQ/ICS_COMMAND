# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""services/tak_group_sync.py — #344 ICS faction 分類 → TAK Server group 同步（現場層隔離第二支槓桿）。

#343 的 admin 紅藍分類只控 ICS 視圖；本模組把同一個分類動作**推到 TAK group**，讓現場端
（ATAK/iTAK）也按陣營切廣播域（紅軍 ATAK 收不到藍軍 CoT）。一個分類動作、兩層同時隔離（設計 §8.2）。

reality-check 定案（2026-06-23，活 takserver 實證，見 GitHub #344）：
- 橋 = `GET /Marti/api/subscriptions/all` 回 `{clientUid, username}` → 用 ICS 的 client_key（CoT uid）
  join 出 TAK username。**不用 `/clientEndPoints`**（殘留殭屍 row、同 uid 多筆陳舊 username 會 join
  到錯的對象；見 `_username_for_client_key` docstring 實測）。
- 寫 = `PUT /user-management/api/update-groups`（**補齊 groupList/IN/OUT 三欄否則 NPE→500**）→ 把該
  username 的群設為單一 faction 群（replace=脫離其他群，含 __ANON__ → 達成隔離）。
- 需 **管理級 cert**（ROLE_ADMIN，certmod -A）—— `TAK_MARTI_ADMIN_CERT/KEY`；read/write cert 打
  user-management API 回 403。
- **前提：該 device 須已是 managed user（certmod 註冊過）**；REST 建不了 cert-user（new-user 只 password）
  → enrollment 仍走 certmod（演習前 roster 批次）。本模組只「改已存在 user 的群」。
- **best-effort**：device 離線（無 clientEndPoint）/ 未註冊 / TAK 錯 → log 不 raise，**不拖垮 #343 ICS 分類**。
"""

from __future__ import annotations

import logging
import ssl

from core import config
from services.tak_rest_client import TakRestError, build_tak_rest_client

log = logging.getLogger(__name__)

# faction → TAK group 名（與演習 certmod -g 用的群名一致）。neutral 也獨立成群（與紅藍互不可見）。
_FACTION_GROUP: dict[str, str] = {"blue": "blue", "red": "red", "neutral": "neutral"}


def is_configured() -> bool:
    """是否已配置管理級 cert（未配置 → faction 分類純 ICS 視圖層、不同步 TAK group）。"""
    return bool(config.TAK_MARTI_URL and config.TAK_MARTI_ADMIN_CERT and config.TAK_MARTI_ADMIN_KEY)


def _build_admin_client():
    return build_tak_rest_client(
        base_url=config.TAK_MARTI_URL,
        client_cert=config.TAK_MARTI_ADMIN_CERT,
        client_key=config.TAK_MARTI_ADMIN_KEY,
        cafile=config.TAK_CAFILE,
        allow_insecure_tls=config.TAK_ALLOW_INSECURE_TLS,
        min_interval_s=config.TAK_MARTI_MIN_INTERVAL_S,
        max_retries=config.TAK_MARTI_MAX_RETRIES,
    )


async def list_online_subscriptions() -> list[dict]:
    """查 TAK 在線訂閱（`/Marti/api/subscriptions/all`，admin 證）→ [{client_uid, username, groups}]。

    #404：面板顯示「誰**連著**」（含匿名 `__ANON__`），補 reconcile（只讀名冊 UserAuthenticationFile）
    的盲區——被刪帳號/未授權但 CA 信任的裝置會**匿名連入、不在名冊**，唯有在線視圖看得到。
    best-effort：未配置 admin cert / cert 壞 / TAK 錯 → 回 []（不拖垮 reconcile 面板）。
    """
    if not is_configured():
        return []
    client = None
    try:
        client = _build_admin_client()
        data = await client.get_json("/Marti/api/subscriptions/all")
        rows = (data or {}).get("data", []) if isinstance(data, dict) else (data or [])
        out: list[dict] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            grps = r.get("groups") or []
            groups = sorted({g.get("name") for g in grps if isinstance(g, dict) and g.get("name")})
            out.append({"client_uid": r.get("clientUid") or "", "username": r.get("username") or "", "groups": groups})
        return out
    except (TakRestError, OSError, ssl.SSLError, ValueError) as exc:
        log.warning("[tak-group-sync] list_online_subscriptions 失敗（best-effort）：%s", exc)
        return []
    finally:
        if client is not None:
            await client.close()


async def _username_for_client_key(client, client_key: str) -> str | None:
    """查在線訂閱找 client_key（CoT uid）對應的 TAK username。離線/查無 → None。

    用 `/Marti/api/subscriptions/all`（可靠在線視圖）**而非** `/clientEndPoints`——後者保留歷史
    殭屍 row（同一 uid 多筆陳舊 username、且裝置關機後 lastStatus 仍標 Connected），取第一筆會
    join 到**錯的** username（2026-06-23 實測：`AC4B…` → 殭屍 `blue-01`〔別台裝置〕、`ANDROID…` →
    匿名 `3QQ`），導致 update-groups 改到別台/不存在的 user、真身留在原群。subscriptions/all 只列
    當前實際訂閱中的 client（memory tak-faction-group-identifier；對齊欄位 clientUid/username）。
    """
    data = await client.get_json("/Marti/api/subscriptions/all")
    rows = (data or {}).get("data", []) if isinstance(data, dict) else (data or [])
    for r in rows:
        if isinstance(r, dict) and r.get("clientUid") == client_key and r.get("username"):
            return r["username"]
    return None


async def online_uid_to_username() -> dict[str, str]:
    """#344：在線訂閱的 {uid: username(=cert CN)} 對照，供 client_identity 快取批次寫入。

    best-effort：未配置 / TAK 錯 → 回 {}（不拖垮面板）。後寫覆蓋（同 uid 多筆取最後一筆即可，
    list_online_subscriptions 已是當前在線視圖，無歷史殭屍）。
    """
    out: dict[str, str] = {}
    for s in await list_online_subscriptions():
        u, n = s.get("client_uid"), s.get("username")
        if u and n:
            out[u] = n
    return out


async def sync_client_faction(client_key: str, faction: str | None, *, username: str | None = None) -> dict:
    """把裝置的 TAK group 設成 faction 群。best-effort，回 {synced, reason, username}。

    #344：username 已知（client_key 即 cert CN）時直接用，免再經 subscriptions 解 uid→username。
    未給 username → 退回用 client_key（CoT uid）查在線訂閱解析（舊 #343 行為）。

    skip（synced=False）情形：未配置 admin cert / faction 無對應群 / 裝置離線（subscriptions/all 查無 clientUid）/
    TAK 寫入錯（log warning）。caller（faction_service.classify）不因本函式失敗而 raise。
    """
    if not is_configured():
        return {"synced": False, "reason": "tak-admin-not-configured"}
    group = _FACTION_GROUP.get(faction or "")
    if group is None:
        return {"synced": False, "reason": f"no-group-for-faction:{faction}"}
    client = None
    try:
        # build 放 try 內：cert 檔缺/壞（env 設了但 issue-tak-certs 沒跑 / volume 掛錯）會拋
        # OSError/ssl.SSLError/ValueError，須一併吞掉走 best-effort，否則衝進 classify → 500（破壞契約）。
        client = _build_admin_client()
        username = username or await _username_for_client_key(client, client_key)
        if not username:
            log.info(
                "[tak-group-sync] client_key %s 無在線 TAK 連線 → 跳過群同步（device 連上後重分類即生效）", client_key
            )
            return {"synced": False, "reason": "device-offline-or-unknown"}
        # replace 群 → 只在此 faction 群（脫離 __ANON__/其他 → 隔離）；補齊三欄避免 server NPE。
        await client.put_json(
            "/user-management/api/update-groups",
            {"username": username, "groupList": [group], "groupListIN": [], "groupListOUT": []},
        )
        log.info("[tak-group-sync] %s（uid %s）→ TAK group=%s", username, client_key, group)
        return {"synced": True, "reason": "ok", "username": username, "group": group}
    except (TakRestError, OSError, ssl.SSLError, ValueError) as exc:
        log.warning("[tak-group-sync] client_key %s → group %s 失敗（best-effort 不中斷）：%s", client_key, group, exc)
        return {"synced": False, "reason": f"sync-error:{exc}"}
    finally:
        if client is not None:
            await client.close()
