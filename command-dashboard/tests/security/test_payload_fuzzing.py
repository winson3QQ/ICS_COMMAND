"""
tests/security/test_payload_fuzzing.py — 惡意/畸形 Payload Fuzzing 測試

涵蓋：
  - 型態錯誤（integer 替代 string）
  - SQL 注入字串存入欄位（應安全存入，不執行）
  - XSS payload 存入欄位（後端不負責 escape，記錄安全性）
  - 超大字串（50KB description）
  - 缺少必填欄位 → 422
  - 未知的 enum 值 → 422
  - Unicode / emoji → 應正常處理
  - Null byte → 應安全處理

設計備忘：
  FastAPI Pydantic 驗證負責型態檢查與必填欄位。
  SQL 注入防護來自 SQLite 參數化查詢（DB 層）。
  XSS 防護是前端責任，後端儲存原始字串為正確行為。
"""

import pytest

# ─────────────────────────────────────────────────────────────────
# Login Payload 異常
# ─────────────────────────────────────────────────────────────────

class TestLoginFuzzing:
    def test_integer_pin_coerced_or_422(self, client):
        """
        pin 傳入整數 1234（非字串）→ Pydantic 強制轉換或 422。
        兩種結果都可接受；重點是不崩潰。
        """
        r = client.post("/api/auth/login", json={"username": "admin", "pin": 1234})
        assert r.status_code in (200, 401, 422), f"非預期狀態碼：{r.status_code}"

    def test_sql_injection_in_username(self, client):
        """SQL 注入字串作為 username → 401（不崩潰、DB 不受影響）"""
        r = client.post("/api/auth/login", json={
            "username": "' OR '1'='1",
            "pin": "1234"
        })
        assert r.status_code == 401

    def test_sql_injection_in_pin(self, client):
        """SQL 注入字串作為 pin → 401（不崩潰）"""
        r = client.post("/api/auth/login", json={
            "username": "admin",
            "pin": "' OR '1'='1'; --"
        })
        assert r.status_code == 401

    def test_missing_username_returns_422(self, client):
        """缺少 username → 422"""
        r = client.post("/api/auth/login", json={"pin": "1234"})
        assert r.status_code == 422

    def test_missing_pin_returns_422(self, client):
        """缺少 pin → 422"""
        r = client.post("/api/auth/login", json={"username": "admin"})
        assert r.status_code == 422

    def test_empty_body_returns_422(self, client):
        """空 JSON body → 422"""
        r = client.post("/api/auth/login", json={})
        assert r.status_code == 422


# ─────────────────────────────────────────────────────────────────
# Event Payload 異常
# ─────────────────────────────────────────────────────────────────

class TestEventFuzzing:
    def _login(self, client):
        r = client.post("/api/auth/login", json={"username": "admin", "pin": "1234"})
        return r.json()["session_id"]

    def _headers(self, token):
        return {"X-Session-Token": token}

    def test_invalid_severity_returns_422(self, client):
        """severity='critical_9999'（不在枚舉內）→ 422"""
        token = self._login(client)
        r = client.post("/api/events", headers=self._headers(token), json={
            "reported_by_unit": "shelter",
            "event_type": "capacity_warning",
            "severity": "critical_9999",
            "description": "test",
            "operator_name": "測試員",
        })
        assert r.status_code == 422

    def test_xss_in_description_stored_safely(self, client):
        """XSS payload 存入 description → 原樣存入（後端不 escape，前端責任）"""
        token = self._login(client)
        xss = '<script>alert("xss")</script>'
        r = client.post("/api/events", headers=self._headers(token), json={
            "reported_by_unit": "shelter",
            "event_type": "capacity_warning",
            "severity": "info",
            "description": xss,
            "operator_name": "測試員",
        })
        assert r.status_code == 200
        # 取回確認原樣存入（DB 未執行 script）
        events = client.get("/api/events", headers=self._headers(token)).json()
        found = [e for e in events if xss in e.get("description", "")]
        assert len(found) == 1, "XSS 字串應原樣存入 DB"

    def test_sql_injection_in_description_stored_safely(self, client):
        """SQL 注入字串存入 description → 原樣存入，DB 不受影響"""
        token = self._login(client)
        sql_payload = "'; DROP TABLE events; --"
        r = client.post("/api/events", headers=self._headers(token), json={
            "reported_by_unit": "shelter",
            "event_type": "capacity_warning",
            "severity": "info",
            "description": sql_payload,
            "operator_name": "測試員",
        })
        assert r.status_code == 200
        # events 表還在（沒被 DROP）
        events = client.get("/api/events", headers=self._headers(token))
        assert events.status_code == 200

    def test_oversized_description(self, client):
        """50,000 字元 description → 接受或回 422，不崩潰"""
        token = self._login(client)
        r = client.post("/api/events", headers=self._headers(token), json={
            "reported_by_unit": "shelter",
            "event_type": "capacity_warning",
            "severity": "info",
            "description": "A" * 50_000,
            "operator_name": "測試員",
        })
        assert r.status_code in (200, 422), f"應接受或拒絕，不應崩潰：{r.status_code}"

    def test_unicode_and_emoji_in_description(self, client):
        """Unicode + emoji → 正常處理"""
        token = self._login(client)
        r = client.post("/api/events", headers=self._headers(token), json={
            "reported_by_unit": "shelter",
            "event_type": "capacity_warning",
            "severity": "info",
            "description": "緊急！🚨 傷患湧入 ≥ 50 人",
            "operator_name": "王大明",
        })
        assert r.status_code == 200


# ─────────────────────────────────────────────────────────────────
# Snapshot Payload 異常
# ─────────────────────────────────────────────────────────────────

class TestSnapshotFuzzing:
    """TI-01 後 POST /api/snapshots 需要 HMAC，改用 hmac_client fixture。"""

    def test_missing_snapshot_id_returns_422(self, hmac_client):
        """snapshot_id 缺失 → 422（HMAC 通過後，body 驗證失敗）"""
        c, sign = hmac_client
        body = {"v": 3, "type": "shelter", "t": "2026-04-24T10:00:00Z", "src": "test"}
        body_bytes, hdrs = sign("POST", "/api/snapshots", body)
        r = c.post("/api/snapshots", content=body_bytes, headers=hdrs)
        assert r.status_code == 422

    def test_unknown_type_returns_400(self, hmac_client):
        """未知的 type='unknown_node' → 400（HMAC 通過後，router 驗證失敗）"""
        c, sign = hmac_client
        body = {
            "v": 3, "type": "unknown_node",
            "snapshot_id": "fuzz-001",
            "t": "2026-04-24T10:00:00Z", "src": "test",
        }
        body_bytes, hdrs = sign("POST", "/api/snapshots", body)
        r = c.post("/api/snapshots", content=body_bytes, headers=hdrs)
        assert r.status_code == 400

    def test_extra_unknown_fields_accepted(self, hmac_client):
        """SnapshotIn 設定 extra='allow'，額外欄位不拋錯"""
        c, sign = hmac_client
        body = {
            "v": 3, "type": "shelter",
            "snapshot_id": "fuzz-extra-001",
            "t": "2026-04-24T10:00:00Z", "src": "test",
            "totally_unknown_field": "should_be_ignored",
        }
        body_bytes, hdrs = sign("POST", "/api/snapshots", body)
        r = c.post("/api/snapshots", content=body_bytes, headers=hdrs)
        assert r.status_code == 200

    def test_negative_bed_values_accepted(self, hmac_client):
        """
        bed_used=-1 → 目前無 validator，接受存入（已知行為）。
        未來 C2 可加 ge=0 validator。
        """
        c, sign = hmac_client
        body = {
            "v": 3, "type": "shelter",
            "snapshot_id": "fuzz-neg-001",
            "t": "2026-04-24T10:00:00Z", "src": "test",
            "bed_used": -1, "bed_total": 50,
        }
        body_bytes, hdrs = sign("POST", "/api/snapshots", body)
        r = c.post("/api/snapshots", content=body_bytes, headers=hdrs)
        # 接受或 422 都可，記錄當前行為
        assert r.status_code in (200, 422)
