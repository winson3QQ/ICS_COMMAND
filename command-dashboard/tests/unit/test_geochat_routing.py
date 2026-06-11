"""
tests/unit/test_geochat_routing.py — P2-07（#129）：GeoChat b-t-f 分流到 chats 表

鎖住的不變式：
- CoT type b-t-f **不進** cop_entities（作戰圖主表，負向）
- b-t-f **進** chats 表（message / callsign / group / exercise_id）
- message 存前 html.escape（XSS 後端防線）
- exercise scoping：綁 current_exercise_id（無 active → NULL）
- 非 b-t-f 不被誤擋，照常進 cop_entities
"""

import asyncio
from pathlib import Path

import pytest

from core.database import get_conn
from schemas.tak import CoTEventIn
from services import cop_service
from services.tak_service import parse_cot_xml

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cot"


@pytest.fixture(autouse=True)
def _db(tmp_db):
    yield


@pytest.fixture(autouse=True)
def _silence_broadcast(monkeypatch):
    """非 b-t-f 路徑會 broadcast；攔截避免真開 WS。"""

    async def _fake(message, exercise_id=None):
        return None

    monkeypatch.setattr(cop_service.cop_hub, "broadcast", _fake)


def _event(**ov) -> CoTEventIn:
    base = {
        "uid": "CHAT-1", "type": "b-t-f",
        "time": "2026-06-05T04:00:00Z", "start": "2026-06-05T04:00:00Z",
        "stale": "2099-01-01T00:00:00Z", "how": "h-g-i-g-o", "lat": 24.1, "lon": 120.6,
    }
    base.update(ov)
    return CoTEventIn(**base)


def _ingest(ev):
    return asyncio.run(cop_service.ingest_cot_event(ev))


def _chats() -> list[dict]:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM chats").fetchall()]


def _cop_count(uid: str) -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM cop_entities WHERE uid=?", (uid,)).fetchone()[0]


# ── 1. b-t-f 不進 cop_entities（負向）────────────────────────────────────────


def test_btf_not_in_cop_entities():
    out = _ingest(_event(remarks="hello"))
    assert out is None                    # 分流路徑回 None（非 cop row）
    assert _cop_count("CHAT-1") == 0      # 主表零筆


# ── 2. b-t-f 進 chats 表 ─────────────────────────────────────────────────────


def test_btf_goes_to_chats():
    _ingest(_event(remarks="集結點 A", callsign="ALPHA-1",
                   detail={"__chat": {"chatroom": "All Chat Rooms", "senderCallsign": "ALPHA-1"}}))
    chats = _chats()
    assert len(chats) == 1
    c = chats[0]
    assert c["sender_uid"] == "CHAT-1"
    assert c["message"] == "集結點 A"
    assert c["callsign"] == "ALPHA-1"
    assert c["group"] == "All Chat Rooms"


# ── 3. message 存前 html.escape（XSS 後端防線）──────────────────────────────


def test_message_escaped():
    _ingest(_event(remarks="敵情 <script>alert(1)</script>"))
    msg = _chats()[0]["message"]
    assert "&lt;script&gt;" in msg
    assert "<script>" not in msg          # 原樣不得存


# ── 4. exercise scoping（無 active → NULL）──────────────────────────────────


def test_exercise_scoping_null_when_no_active():
    _ingest(_event(remarks="x"))
    assert _chats()[0]["exercise_id"] is None


# ── 5. 真 fixture 全鏈（parse → 分流 → escape，且不進主表）──────────────────


def test_pipeline_from_fixture():
    event = parse_cot_xml((FIXTURES / "geochat_btf.xml").read_text(encoding="utf-8"))
    _ingest(event)
    chats = _chats()
    assert len(chats) == 1
    assert "&lt;script&gt;" in chats[0]["message"]   # fixture XSS payload 已 escape
    assert chats[0]["group"] == "All Chat Rooms"
    assert _cop_count(event.uid) == 0                # b-t-f 不進主表


# ── 6. 非 b-t-f 不被誤擋，照常進 cop_entities ───────────────────────────────


def test_non_btf_still_in_cop_entities():
    out = _ingest(_event(uid="UNIT-1", type="a-f-G-U-C", remarks="x"))
    assert out is not None
    assert _cop_count("UNIT-1") == 1
    assert _chats() == []                 # 一般 entity 不進 chats


# ── 7. review #131：缺 chatroom 時 group=None（不把 groupOwner 布林旗標當群組名）──


def test_group_none_when_no_chatroom():
    _ingest(_event(remarks="x", detail={"__chat": {"groupOwner": "false", "senderCallsign": "B"}}))
    assert _chats()[0]["group"] is None   # 非 "false"


# ── 8. b2（#213）：b-t-f 寫入後即時 WS 廣播 op=chat（feed 形狀，帶 exercise scope）──


def test_btf_broadcasts_chat_op(monkeypatch):
    """ingest 後 cop_hub.broadcast({op:chat, chat:<feed 形狀>}, exercise_id=...) 被呼叫一次。
    payload 與 GET /api/chat 同形狀（含 t、不含 raw time）→ 前端同渲染路徑可吃。"""
    calls = []

    async def _capture(message, exercise_id=None):
        calls.append((message, exercise_id))

    monkeypatch.setattr(cop_service.cop_hub, "broadcast", _capture)
    _ingest(_event(remarks="集結點 A", callsign="ALPHA-1",
                   detail={"__chat": {"chatroom": "All Chat Rooms"}}))
    assert len(calls) == 1
    msg, _ex = calls[0]
    assert msg["op"] == "chat"
    c = msg["chat"]
    assert c["message"] == "集結點 A"
    assert c["callsign"] == "ALPHA-1"
    assert c["group"] == "All Chat Rooms"
    assert "t" in c and "time" not in c          # feed 形狀（t = time or received_at），非 raw row
    assert {"id", "sender_uid", "lat", "lon"} <= set(c)


def test_non_btf_does_not_broadcast_chat(monkeypatch):
    """一般 entity（非 b-t-f）不走 chat 廣播（不誤發 op=chat）。"""
    chat_calls = []

    async def _capture(message, exercise_id=None):
        if isinstance(message, dict) and message.get("op") == "chat":
            chat_calls.append(message)

    monkeypatch.setattr(cop_service.cop_hub, "broadcast", _capture)
    _ingest(_event(uid="UNIT-9", type="a-f-G-U-C", remarks="x"))
    assert chat_calls == []
