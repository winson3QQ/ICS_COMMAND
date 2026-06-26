# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
tests/security/test_session_config.py — Session 設定邊界測試

補充 test_session_edge.py（已覆蓋 timeout 邊界 / heartbeat / logout）。
本檔聚焦：
  - SESSION_TIMEOUT env 覆寫行為
  - cleanup_expired_sessions() 批量清除
  - 極短 timeout 場景
  - session 並發安全：同一 token 同時送多個請求
"""

import threading
import time
from datetime import UTC

# ─────────────────────────────────────────────────────────────────
# SESSION_TIMEOUT 環境變數覆寫
# ─────────────────────────────────────────────────────────────────


class TestSessionTimeoutConfig:
    def test_custom_short_timeout_expires_session(self, tmp_db, monkeypatch):
        """SESSION_TIMEOUT=2 秒：2 秒後 session 過期"""
        import auth.service as svc

        # service 讀自己的模組級快取常數 svc.SESSION_TIMEOUT（非 core.config 直讀），故只 patch svc。
        # #367：先前另 patch core.config.SESSION_TIMEOUT 是冗餘且為洩漏觸發點（patch cfg 後才首次
        # import auth.service → 模組以 patched 值綁定常數 → monkeypatch 還原成 patched 值），已移除。
        monkeypatch.setattr(svc, "SESSION_TIMEOUT", 2)

        token = svc.create_session({"username": "u1", "role": "op", "display_name": "U1"})
        assert svc.check_and_touch(token) is not None  # 建立後立刻有效

        # 直接改 DB last_active 為 3 秒前（超過 timeout=2）
        from datetime import datetime, timedelta

        from core.database import get_conn

        old = (datetime.now(UTC) - timedelta(seconds=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with get_conn() as conn:
            conn.execute("UPDATE sessions SET last_active=? WHERE token=?", (old, token))
            conn.commit()

        result = svc.check_and_touch(token)
        assert result is None, "SESSION_TIMEOUT=2s，3 秒後應過期"

    def test_zero_timeout_immediately_expires(self, tmp_db, monkeypatch):
        """SESSION_TIMEOUT=0：任何 session 立即過期（邊界值）"""
        import auth.service as svc

        monkeypatch.setattr(svc, "SESSION_TIMEOUT", 0)

        token = svc.create_session({"username": "u2", "role": "op", "display_name": "U2"})
        # 讓 DB 的 last_active 稍舊（1 秒前）
        from datetime import datetime, timedelta

        from core.database import get_conn

        old = (datetime.now(UTC) - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with get_conn() as conn:
            conn.execute("UPDATE sessions SET last_active=? WHERE token=?", (old, token))
            conn.commit()

        result = svc.check_and_touch(token)
        assert result is None, "timeout=0 時 session 應立即過期"


# ─────────────────────────────────────────────────────────────────
# cleanup_expired_sessions()
# ─────────────────────────────────────────────────────────────────


class TestCleanupExpiredSessions:
    def test_cleanup_removes_expired_sessions(self, tmp_db):
        """cleanup_expired_sessions() 移除所有過期 session"""
        from datetime import datetime, timedelta

        from auth.service import cleanup_expired_sessions, create_session
        from core.config import SESSION_TIMEOUT
        from core.database import get_conn

        # 建立 3 個 session
        tokens = [create_session({"username": f"u{i}", "role": "op", "display_name": f"U{i}"}) for i in range(3)]

        # 把前 2 個設為過期
        old = (datetime.now(UTC) - timedelta(seconds=SESSION_TIMEOUT + 60)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with get_conn() as conn:
            conn.execute("UPDATE sessions SET last_active=? WHERE token IN (?, ?)", (old, tokens[0], tokens[1]))
            conn.commit()

        deleted = cleanup_expired_sessions()
        assert deleted == 2

        # 第 3 個仍存在
        with get_conn() as conn:
            remaining = conn.execute("SELECT COUNT(*) as c FROM sessions").fetchone()["c"]
        assert remaining == 1

    def test_cleanup_returns_zero_when_nothing_expired(self, tmp_db):
        """沒有過期 session 時 cleanup 回傳 0"""
        from auth.service import cleanup_expired_sessions, create_session

        create_session({"username": "fresh", "role": "op", "display_name": "Fresh"})
        deleted = cleanup_expired_sessions()
        assert deleted == 0

    def test_cleanup_writes_audit_for_each_expired(self, tmp_db):
        """#93(b)：批次清理被丟棄（abandoned）的逾時 session → 每筆留痕（AAR 可查）。
        #345：留痕 action_type 改為 SESSION_REAPED（例行維護），與 per-request SESSION_EXPIRED 分流。"""
        from datetime import datetime, timedelta

        from auth.service import cleanup_expired_sessions, create_session
        from core.config import SESSION_TIMEOUT
        from core.database import get_conn

        tokens = {
            f"abandoned{i}": create_session({"username": f"abandoned{i}", "role": "op", "display_name": f"A{i}"})
            for i in range(2)
        }
        # 兩個都設為逾時（超過 SESSION_TIMEOUT，確定落入清理範圍）
        old = (datetime.now(UTC) - timedelta(seconds=SESSION_TIMEOUT + 60)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with get_conn() as conn:
            conn.execute("UPDATE sessions SET last_active=?", (old,))
            conn.commit()

        deleted = cleanup_expired_sessions()
        assert deleted == 2

        # 每個被清的 username 都應有一筆 SESSION_REAPED audit
        with get_conn() as conn:
            rows = conn.execute("SELECT operator FROM audit_log WHERE action_type='SESSION_REAPED'").fetchall()
        operators = {r["operator"] for r in rows}
        assert operators == set(tokens.keys()), f"audit 留痕不齊：{operators}"

    def test_cleanup_audit_does_not_emit_session_expired(self, tmp_db):
        """#345 分流回歸守門：批次清理只記 SESSION_REAPED，不得記 SESSION_EXPIRED
        （否則例行 reap 又混入 per-request 安全事件、稀釋訊噪比）。"""
        from datetime import datetime, timedelta

        from auth.service import cleanup_expired_sessions, create_session
        from core.config import SESSION_TIMEOUT
        from core.database import get_conn

        create_session({"username": "abandoned", "role": "op", "display_name": "A"})
        old = (datetime.now(UTC) - timedelta(seconds=SESSION_TIMEOUT + 60)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with get_conn() as conn:
            conn.execute("UPDATE sessions SET last_active=?", (old,))
            conn.commit()

        assert cleanup_expired_sessions() == 1
        with get_conn() as conn:
            expired = conn.execute("SELECT COUNT(*) c FROM audit_log WHERE action_type='SESSION_EXPIRED'").fetchone()[
                "c"
            ]
            reaped = conn.execute("SELECT COUNT(*) c FROM audit_log WHERE action_type='SESSION_REAPED'").fetchone()["c"]
        assert expired == 0, "批次清理不該記 SESSION_EXPIRED（應分流到 SESSION_REAPED）"
        assert reaped == 1

    def test_cleanup_idle_cutoff_uses_idle_timeout_not_session_timeout(self, tmp_db):
        """#93(b) 回歸守門：idle cutoff 用 min(IDLE_TIMEOUT, SESSION_TIMEOUT)（預設 15 分），
        非 SESSION_TIMEOUT（14h）。閒置超過 IDLE_TIMEOUT 但 expires_at 尚未到的 abandoned
        session 也應被清（修正前卡 14h 才清）。"""
        from datetime import datetime, timedelta

        from auth.service import cleanup_expired_sessions, create_session
        from core.config import IDLE_TIMEOUT, SESSION_TIMEOUT
        from core.database import get_conn

        # 前提：IDLE_TIMEOUT < SESSION_TIMEOUT，否則本回歸場景不成立（預設 900 < 50400）
        assert IDLE_TIMEOUT < SESSION_TIMEOUT

        create_session({"username": "idle_only", "role": "op", "display_name": "Idle"})
        # last_active 設為「超過 idle 上限但遠未達 absolute 上限」：修正前不清、修正後清
        idle_old = (datetime.now(UTC) - timedelta(seconds=IDLE_TIMEOUT + 60)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with get_conn() as conn:
            # 僅改 last_active；expires_at 維持 create_session 設的未來時間（+SESSION_TIMEOUT）
            conn.execute("UPDATE sessions SET last_active=?", (idle_old,))
            conn.commit()

        deleted = cleanup_expired_sessions()
        assert deleted == 1, "閒置逾 IDLE_TIMEOUT 的 session 應被清（idle cutoff 修正）"


# ─────────────────────────────────────────────────────────────────
# 並發 Session 安全
# ─────────────────────────────────────────────────────────────────


class TestConcurrentSession:
    def test_concurrent_requests_with_same_token(self, client):
        """
        同一 token 同時發出多個請求（模擬前端 tab 並發）。
        所有請求應都通過（check_and_touch 不應競爭損壞 session）。
        """
        r = client.post("/api/auth/login", json={"username": "admin", "pin": "1234"})
        token = r.json()["session_id"]
        headers = {"X-Session-Token": token}

        results = []

        def call_me():
            resp = client.get("/api/auth/me", headers=headers)
            results.append(resp.status_code)

        threads = [threading.Thread(target=call_me) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 全部應回 200（不應因並發 UPDATE 而損壞 session）
        assert all(s == 200 for s in results), f"部分請求失敗：{results}"

    def test_session_touch_updates_last_active(self, client, tmp_db):
        """每次 check_and_touch 都更新 last_active（確認非 read-only）"""
        from auth.service import check_and_touch, create_session
        from core.database import get_conn

        token = create_session({"username": "u", "role": "op", "display_name": "U"})

        with get_conn() as conn:
            before = conn.execute("SELECT last_active FROM sessions WHERE token=?", (token,)).fetchone()["last_active"]

        time.sleep(1.1)
        check_and_touch(token)

        with get_conn() as conn:
            after = conn.execute("SELECT last_active FROM sessions WHERE token=?", (token,)).fetchone()["last_active"]

        assert after > before
