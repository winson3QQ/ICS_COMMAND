"""routers/chat.py — GeoChat 通聯唯讀 API（#213 b1）。

P2-07（#129）入向 b-t-f 只做「存進 chats 表」沒做「送」→ 主 dashboard 看不到現場通聯
（只在 AAR 回放）。本端點補唯讀投影，餵右欄通聯單一流面板。

RBAC：中央 gate（role_enum.allowed_roles_for `/api/chat` → READ_ROLES，含 observer——
通聯屬當前場情境資料，observer 做 audit/AAR 觀察需看；跨場 PII 由 resolve_scope 守門，
observer/operator 鎖當前 active 場，歷史場 `?exercise_id` 限 COMMAND_ROLES）。
出向 compose（POST）留 #216。
"""

from fastapi import APIRouter, Query, Request

from repositories._helpers import iso_utc
from services.chat_service import build_chat_feed
from services.exercise_service import resolve_scope

router = APIRouter(tags=["通聯"])


def _range_bound(value: str | None, *, end: bool) -> str | None:
    """from/to 時間界正規化為 ISO 8601 UTC Z（與 chats.time 同格式才能字典序比較）。

    純日期（YYYY-MM-DD）→ 補當天起/訖；**再經 iso_utc 補 Z**——否則無-Z 界字典序落在
    `YYYY-MM-DDThh:mm:ssZ` 之前（'Z' 比缺字大），`to` 界會把當天最後一秒漏掉。
    與 exercises._range_bound 同邏輯（/tracks #123 review 同雷）；shared helper 待後續 dedup。
    """
    if not value:
        return None
    if "T" not in value and len(value) == 10:
        value += "T23:59:59" if end else "T00:00:00"
    return iso_utc(value)


@router.get("/api/chat")
def list_chat(
    request: Request,
    exercise_id: int | None = None,
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    limit: int = 200,
):
    """某場通聯單一流（#213 b1）—— 最新 limit 筆、chronological 升序。

    RBAC：READ_ROLES（中央 gate）。P1-14：預設只回當前 active 場；commander 顯式帶
    exercise_id 才看歷史（resolve_scope 守門，防 operator/observer by-id 跨場撈通聯 PII）。
    回 {meta: {count, truncated}, chats: [{id, sender_uid, callsign, message, group, lat, lon, t}]}；
    message 入庫已 escape（chat_service），前端純文字渲染、不反解。truncated → 縮 from/to 重查。
    """
    return build_chat_feed(
        resolve_scope(request.state.session, exercise_id),
        since=_range_bound(from_, end=False),
        until=_range_bound(to, end=True),
        limit=min(max(limit, 1), 1000),
    )
