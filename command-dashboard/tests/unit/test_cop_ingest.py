"""
tests/unit/test_cop_ingest.py — P2-04（#105）：ingest_cot_event 接縫

鎖住的不變式（共用消費者，subscribe / REST router 都靠它）：
- 新 uid → insert，回 DB row，廣播 op=create
- 既有 uid、event 較新 → version_clock +1，廣播 op=update
- 既有 uid、event 較舊 / 重送 → None，DB 不動，**不廣播**（防 out-of-order 倒退位置）
- exercise_id 由 server 端 current_exercise_id() 綁定（不信任外部）
- 廣播訊息格式對齊 routers/cop.py（op/uid/version_clock/entity）
"""

import asyncio

import pytest

from repositories.cop_entity_repo import get_cop_entity
from schemas.tak import CoTEventIn
from services import cop_service


@pytest.fixture(autouse=True)
def _db(tmp_db):
    """ingest 走真 DB（cop_entities）+ normalize 查 exercises，需 init 過的 tmp DB。"""
    yield


@pytest.fixture
def captured_broadcasts(monkeypatch):
    """攔截 cop_hub.broadcast（async）→ 收集 (message, exercise_id)，不真的開 WS。"""
    calls: list[tuple[dict, int | None]] = []

    async def _fake(message, exercise_id=None):
        calls.append((message, exercise_id))

    monkeypatch.setattr(cop_service.cop_hub, "broadcast", _fake)
    return calls


def _event(uid: str = "TAK-INGEST-1", *, time: str, **overrides) -> CoTEventIn:
    base = {
        "uid": uid,
        "type": "a-f-G-U-C",
        "time": time,
        "start": time,
        "stale": "2099-01-01T00:00:00Z",  # 遠未來 → 不被當 stale 過濾
        "how": "m-g",
        "lat": 24.137,
        "lon": 120.687,
    }
    base.update(overrides)
    return CoTEventIn(**base)


def _ingest(event: CoTEventIn):
    return asyncio.run(cop_service.ingest_cot_event(event))


# ── 1. 新 uid → insert + 廣播 create ─────────────────────────────────────────


def test_new_uid_inserts_and_broadcasts_create(captured_broadcasts):
    row = _ingest(_event(time="2026-06-05T04:00:00Z", callsign="ALPHA-1"))
    assert row is not None
    assert row["uid"] == "TAK-INGEST-1"
    assert row["source"] == "tak"
    assert row["version_clock"] == 1
    assert row["callsign"] == "ALPHA-1"
    # DB 真的有
    assert get_cop_entity("TAK-INGEST-1") is not None
    # 廣播一次 create，格式對齊 routers/cop.py
    assert len(captured_broadcasts) == 1
    msg, _ = captured_broadcasts[0]
    assert msg["op"] == "create"
    assert msg["uid"] == "TAK-INGEST-1"
    assert msg["version_clock"] == 1
    assert msg["entity"]["uid"] == "TAK-INGEST-1"


# ── 2. 既有 uid、較新 event → update（version_clock +1）+ 廣播 update ─────────


def test_newer_event_updates_and_bumps_version(captured_broadcasts):
    _ingest(_event(time="2026-06-05T04:00:00Z", lat=24.0, lon=120.0))
    row = _ingest(_event(time="2026-06-05T04:01:00Z", lat=25.0, lon=121.0))
    assert row is not None
    assert row["version_clock"] == 2
    assert row["lat"] == 25.0
    assert row["lon"] == 121.0
    assert row["time"] == "2026-06-05T04:01:00Z"
    # 第二次廣播是 update
    assert captured_broadcasts[-1][0]["op"] == "update"
    assert captured_broadcasts[-1][0]["version_clock"] == 2


# ── 3. 既有 uid、較舊 / 重送 → 丟棄（不動 DB、不廣播）─────────────────────────


def test_older_event_is_dropped_no_change_no_broadcast(captured_broadcasts):
    _ingest(_event(time="2026-06-05T04:05:00Z", lat=25.0, lon=121.0))
    n_after_create = len(captured_broadcasts)
    # 較舊（out-of-order）
    out = _ingest(_event(time="2026-06-05T04:00:00Z", lat=0.0, lon=0.0))
    assert out is None
    cur = get_cop_entity("TAK-INGEST-1")
    assert cur["version_clock"] == 1  # 沒被 bump
    assert (cur["lat"], cur["lon"]) == (25.0, 121.0)  # 位置沒倒退
    assert len(captured_broadcasts) == n_after_create  # 沒有新廣播


def test_resend_same_time_is_dropped(captured_broadcasts):
    _ingest(_event(time="2026-06-05T04:00:00Z"))
    n = len(captured_broadcasts)
    out = _ingest(_event(time="2026-06-05T04:00:00Z"))  # 完全重送
    assert out is None
    assert len(captured_broadcasts) == n


# ── 4. exercise_id 由 server 綁定（test DB 無 active → None）──────────────────


def test_exercise_id_bound_by_server(captured_broadcasts):
    row = _ingest(_event(time="2026-06-05T04:00:00Z"))
    # test DB 無 active exercise → current_exercise_id() = None（實戰/未分場池）
    assert row["exercise_id"] is None


# ── P2-06c（#126）：小隊欄位隨 update 刷新 ──────────────────────────────────


def test_squad_battery_refreshed_on_update(captured_broadcasts):
    """battery 在 _TAK_UPDATE_FIELDS → update 路徑刷新（位置更新時電量也更新）。"""
    _ingest(_event(time="2026-06-05T04:00:00Z", detail={"status": {"battery": "78"}}))
    row = _ingest(_event(time="2026-06-05T04:01:00Z", detail={"status": {"battery": "50"}}))
    assert row["battery"] == 50  # update 刷新，非保留舊值 78


def test_squad_coalesce_none_does_not_overwrite(captured_broadcasts):
    """無 <__group>/<status> 的較新位置幀不把已存 team_color/role/battery 打成 NULL
    （coalesce，#126-2）—— 否則 P2-06d GROUP BY 會在空窗期少算該 entity。"""
    _ingest(_event(time="2026-06-05T04:00:00Z",
                   detail={"__group": {"name": "Cyan", "role": "Lead"}, "status": {"battery": "78"}}))
    row = _ingest(_event(time="2026-06-05T04:01:00Z"))  # 無 detail → squad 全 None
    assert row["team_color"] == "Cyan"  # 保留，非倒退成 NULL
    assert row["role"] == "Lead"
    assert row["battery"] == 78  # 保留最後已知電量


def test_m015_backfill_from_attributes():
    """既有 TAK entity 從 attributes JSON 回填 squad 欄位（#126-3 migration backfill）。"""
    import json as _json

    from core.database import _m015_cop_entities_squad_cols, get_conn

    with get_conn() as conn:
        conn.execute(
            "INSERT INTO cop_entities (uid,type,time,start,stale,how,lat,lon,source,attributes) "
            "VALUES ('BACKFILL-1','a-f-G','t','t','t','m-g',24.0,120.0,'tak',?)",
            (_json.dumps({"__group": {"name": "Teal", "role": "Medic"}, "status": {"battery": "63"}}),),
        )
        _m015_cop_entities_squad_cols(conn)  # idempotent：column 已存，重跑只 backfill
        row = conn.execute(
            "SELECT team_color, role, battery FROM cop_entities WHERE uid='BACKFILL-1'"
        ).fetchone()
    assert (row["team_color"], row["role"], row["battery"]) == ("Teal", "Medic", 63)


# ── P2-09（review #135-3）：severity 單調升級（只升不降）──────────────────────


def test_severity_escalates_to_critical_on_medevac_update(captured_broadcasts):
    """同 uid 先普通幀(info)、後送 MEDEVAC 幀 → severity 單調升 critical（漏升=地圖不醒目）。"""
    created = _ingest(_event(time="2026-06-05T04:00:00Z"))  # 普通 create
    assert created["severity"] == "info"
    row = _ingest(_event(time="2026-06-05T04:01:00Z", detail={"_medevac_": {"urgent": "1"}}))
    assert row["severity"] == "critical"  # update 路徑升上去


def test_severity_not_downgraded_by_normal_frame(captured_broadcasts):
    """MEDEVAC(critical) 後普通位置幀**不**打回 info（只升不降）。"""
    created = _ingest(_event(time="2026-06-05T04:00:00Z", detail={"_medevac_": {"urgent": "1"}}))
    assert created["severity"] == "critical"
    row = _ingest(_event(time="2026-06-05T04:01:00Z"))  # 無 _medevac_ 的普通位置幀
    assert row["severity"] == "critical"  # 保留，非倒退成 info


# ── TAK-B（紅隊）：來源所有權守門 — TAK 事件不得覆寫本地非 tak entity ──────────


def test_tak_event_does_not_overwrite_non_tak_entity(captured_broadcasts):
    """紅隊 TAK-B：偽造/碰撞 uid 撞上本地 source='manual' entity 時，TAK ingest 一律
    拒絕（不覆寫 type/lat/lon/callsign），回 None、不廣播。CoT uid 不改寫（保留供 P2-13
    下行對位），改以來源所有權隔離。"""
    from core.database import get_conn

    # 本地 manual entity（指揮部自畫，權威）；uid 故意取成 TAK 可能碰撞 / 被偽造的值
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO cop_entities (uid,type,time,start,stale,how,lat,lon,source,callsign) "
            "VALUES ('manual:victim','b-m-p','2026-06-05T04:00:00Z','2026-06-05T04:00:00Z',"
            "'2099-01-01T00:00:00Z','h-g',24.5,120.5,'manual','LOCAL-PIN')"
        )

    n = len(captured_broadcasts)
    # TAK CoT 帶同 uid + 較新時間 → 一般邏輯會 CAS update 覆寫；來源守門須擋下
    out = _ingest(
        _event(uid="manual:victim", time="2026-06-05T05:00:00Z", lat=0.0, lon=0.0, callsign="SPOOF")
    )
    assert out is None  # 被守門丟棄

    cur = get_cop_entity("manual:victim")
    assert cur["source"] == "manual"  # 來源沒被改
    assert (cur["lat"], cur["lon"]) == (24.5, 120.5)  # 位置沒被覆寫
    assert cur["callsign"] == "LOCAL-PIN"  # callsign 沒被覆寫
    assert len(captured_broadcasts) == n  # 沒有新廣播


def test_tak_event_updates_own_tak_entity(captured_broadcasts):
    """正向對照：source='tak' 的既有 entity 仍可被後續 TAK 事件正常 update（守門不誤殺）。"""
    _ingest(_event(uid="TAK-OWN-1", time="2026-06-05T04:00:00Z", lat=24.0, lon=120.0))
    row = _ingest(_event(uid="TAK-OWN-1", time="2026-06-05T04:01:00Z", lat=25.0, lon=121.0))
    assert row is not None
    assert row["version_clock"] == 2
    assert (row["lat"], row["lon"]) == (25.0, 121.0)


# ── TAK soft-stale 移除窗口（#160/#161）：外部來源 grace、manual 即時 ──────────


def test_soft_stale_window_external_grace_manual_immediate():
    """stale 治理按 how：tak 活追蹤(how=m)窗口內保留/過窗口移除；tak 人工標記(how=h)持久；
    manual 明確刪除即時移除。"""
    from datetime import UTC, datetime, timedelta

    from core.database import get_conn
    from repositories.cop_entity_repo import list_cop_entities

    now = datetime.now(UTC)

    def _t(delta_s):
        return (now + timedelta(seconds=delta_s)).strftime("%Y-%m-%dT%H:%M:%SZ")

    with get_conn() as conn:
        base = "INSERT INTO cop_entities (uid,type,time,start,stale,how,lat,lon,source) VALUES (?,?,?,?,?,?,?,?,?)"
        # tak 活追蹤（how=m-*）: stale 2 分前（窗口 5min 內）→ 保留（變灰）
        conn.execute(base, ("tak:recent", "a-f-G-U-C", "t", "t", _t(-120), "m-g", 24.0, 120.0, "tak"))
        # tak 活追蹤: stale 6 分前（過窗口）→ 移除
        conn.execute(base, ("tak:old", "a-f-G-U-C", "t", "t", _t(-360), "m-g", 24.0, 120.0, "tak"))
        # tak 人工放置標記（how=h-*）: stale 6 分前也**持久**（靜態標註、無心跳，不該因 stale 消失）
        conn.execute(base, ("tak:placed", "a-u-G", "t", "t", _t(-360), "h-g-i-g-o", 24.0, 120.0, "tak"))
        # manual: stale 剛過（操作員明確 DELETE）→ 即時移除（無 grace）
        conn.execute(base, ("manual:del", "b-m-p", "t", "t", _t(-1), "h-e", 24.0, 120.0, "manual"))

    uids = {e["uid"] for e in list_cop_entities(exercise_id=None)}
    assert "tak:recent" in uids       # 活追蹤窗口內：保留
    assert "tak:old" not in uids      # 活追蹤過窗口：移除
    assert "tak:placed" in uids       # 人工標記：持久，不因 stale 消失
    assert "manual:del" not in uids   # manual 明確刪除：即時移除


# ── TAK t-x-d-d 刪除命令處理（#161 正解：iTAK 其實有送刪除信號）───────────────


def test_tak_delete_command_soft_deletes_target(captured_broadcasts):
    """t-x-d-d → 軟刪 <link uid> 指向的 tak entity（墓碑 deleted=1）、廣播 op=delete、本身不進主表。"""
    from repositories.cop_entity_repo import list_cop_entities

    created = _ingest(_event(uid="TAK-DEL-TGT", time="2026-06-05T04:00:00Z"))
    assert created is not None
    out = _ingest(_event(uid="del-cmd-1", type="t-x-d-d", time="2026-06-05T04:05:00Z",
                         detail={"link": {"uid": "TAK-DEL-TGT", "type": "a-f-G-U-C", "relation": "p-p"}}))
    assert out is not None and out["uid"] == "TAK-DEL-TGT"          # 回傳被刪 entity
    assert "TAK-DEL-TGT" not in {e["uid"] for e in list_cop_entities()}  # 預設 list 移除
    tgt = get_cop_entity("TAK-DEL-TGT")
    assert tgt is not None and tgt["deleted"] is True               # row 還在（墓碑，可 audit）
    assert get_cop_entity("del-cmd-1") is None                       # t-x-d-d 本身不存
    assert captured_broadcasts[-1][0]["op"] == "delete"             # 廣播 op=delete
    assert captured_broadcasts[-1][0]["uid"] == "TAK-DEL-TGT"


def test_tak_delete_ownership_guard_protects_non_tak(captured_broadcasts):
    """t-x-d-d 指向非 tak 來源（manual）→ 拒刪（防偽造刪本地標繪）。"""
    from core.database import get_conn
    from repositories.cop_entity_repo import list_cop_entities

    with get_conn() as conn:
        conn.execute(
            "INSERT INTO cop_entities (uid,type,time,start,stale,how,lat,lon,source) "
            "VALUES ('manual:keep','b-m-p','t','t','2099-01-01T00:00:00Z','h-e',24.0,120.0,'manual')"
        )
    out = _ingest(_event(uid="del-cmd-2", type="t-x-d-d", time="2026-06-05T04:00:00Z",
                         detail={"link": {"uid": "manual:keep"}}))
    assert out is None
    assert "manual:keep" in {e["uid"] for e in list_cop_entities()}  # 沒被刪


def test_tak_delete_no_link_ignored(captured_broadcasts):
    """t-x-d-d 無 <link uid> → 忽略（None），不炸、不廣播。"""
    n = len(captured_broadcasts)
    out = _ingest(_event(uid="del-cmd-3", type="t-x-d-d", time="2026-06-05T04:00:00Z"))
    assert out is None
    assert len(captured_broadcasts) == n
