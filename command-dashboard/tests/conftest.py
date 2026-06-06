"""
tests/conftest.py — 共用 fixtures

TI-01 補充：
  hmac_client fixture 在 test DB 插入測試 trusted_key，
  並提供 sign(method, path, body_dict) → headers dict 供需要 HMAC 的測試使用。

DB 隔離策略：
  每個測試使用 tmp_path 建立獨立 SQLite 檔案，
  並 monkeypatch core.database.DB_PATH，確保測試之間不互汙染。

Session 隔離：
  sessions 存在 SQLite（sessions 表），隨 tmp_db 的獨立 DB 自動隔離。
  _clear_sessions autouse fixture 在測試前後刪除 sessions 表資料，
  防止跨測試 session 洩漏（包含無 tmp_db 的測試情境）。
"""

import hashlib
import hmac as _hmac
import json
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qsl, urlencode

# 讓測試能 import src/ 下的模組
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

# ── TI-01：共用 HMAC 測試憑證 ─────────────────────────────────────────────────
_CONFTEST_HMAC_KEY_ID = "conftest-hmac-key-001"
_CONFTEST_HMAC_SECRET = "f" * 64  # 64 hex chars（test-only）


def _make_hmac_sign_fn(key_id: str, secret: str):
    """回傳 sign(method, path, body_dict, query="") → (body_bytes, headers dict)。

    用法（確保 HMAC bytes 與送出 body 一致）：
        c, sign = hmac_client
        body, hdrs = sign("POST", "/api/snapshots", {"v": 1, ...})
        r = c.post("/api/snapshots", content=body, headers=hdrs)
    """

    def sign(method: str, path: str, body_dict: dict, query: str = "") -> tuple[bytes, dict]:
        # 以 json.dumps 序列化（與 TestClient json= 相同格式，字典順序 Python 3.7+ 穩定）
        body_bytes = json.dumps(body_dict).encode()
        ts = str(int(time.time() * 1000))
        nc = str(uuid.uuid4())
        query_canonical = urlencode(sorted(parse_qsl(query, keep_blank_values=True))) if query else ""
        body_hash = hashlib.sha256(body_bytes).hexdigest()
        canonical = "\n".join([method.upper(), path, query_canonical, ts, nc, body_hash])
        sig = _hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
        headers = {
            "X-ICS-Key-Id": key_id,
            "X-ICS-Timestamp": ts,
            "X-ICS-Nonce": nc,
            "X-ICS-Signature": sig,
            "Content-Type": "application/json",
        }
        return body_bytes, headers

    return sign


# ── Session 隔離（autouse，所有測試自動套用）────────────────────────────────


def _delete_all_sessions():
    """刪除當前 DB 的所有 sessions（test DB 或 real DB 均適用）"""
    try:
        from core.database import get_conn

        conn = get_conn()
        conn.execute("DELETE FROM sessions")
        conn.commit()
        conn.close()
    except Exception:
        pass  # sessions 表不存在（init_db 尚未執行）時忽略


@pytest.fixture(autouse=True)
def _clear_sessions():
    _delete_all_sessions()
    yield
    _delete_all_sessions()


# ── C1-A：Rate limit bucket 隔離（autouse）─────────────────────────────────
# 多測試共用 TestClient → 同一 IP → 沒重置會在第 11 次 login 後撞 429
@pytest.fixture(autouse=True)
def _reset_rate_limit():
    try:
        from auth.rate_limit import reset_for_tests

        reset_for_tests()
    except Exception:
        pass
    yield


# ── P1-13：map_config runtime 檔隔離（autouse）─────────────────────────────
# 任何測試（特別是 XSS hardening 那些 POST /api/map_config 案例）若沒隔離會把
# 真 disk 的 data/map_config.json 覆寫成測試 payload，dogfood 就看不到節點了
# （issue #27 開發過程實際撞過）。
#
# 策略：每個測試獨立 tmp_path 取代 MAP_CONFIG_PATH + MAP_CONFIG_SEED；seed 內容
# 從真 seed 複製過去，保留 fallback 行為一致性。monkeypatch 要套兩個位置：
#   (1) core.config 的常數（讓 router 內 `from core.config import MAP_CONFIG_PATH` 重 import 拿到）
#   (2) services.map_config_store 的模組級綁定（import 時抓走的 reference）
@pytest.fixture(autouse=True)
def _isolate_map_config(tmp_path, monkeypatch):
    tmp_runtime = tmp_path / "map_config.json"
    tmp_seed = tmp_path / "map_config.seed.json"

    real_seed = Path(__file__).parent.parent / "static" / "map_config.seed.json"
    if real_seed.exists():
        tmp_seed.write_text(real_seed.read_text(encoding="utf-8"), encoding="utf-8")

    import core.config
    from services import map_config_store

    monkeypatch.setattr(core.config, "MAP_CONFIG_PATH", tmp_runtime)
    monkeypatch.setattr(core.config, "MAP_CONFIG_SEED", tmp_seed)
    monkeypatch.setattr(map_config_store, "MAP_CONFIG_PATH", tmp_runtime)
    monkeypatch.setattr(map_config_store, "MAP_CONFIG_SEED", tmp_seed)
    yield


# ── DB 隔離 ────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """
    每個測試建立獨立 SQLite DB，並在結束後自動刪除。
    monkeypatch 確保所有 get_conn() 呼叫都指向測試 DB。
    """
    db_file = tmp_path / "test_ics.db"
    import core.config
    import core.database

    monkeypatch.setattr(core.config, "DB_PATH", db_file)
    monkeypatch.setattr(core.database, "DB_PATH", db_file)
    from core.database import init_db

    init_db()
    return db_file


# ── FastAPI TestClient ────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_db, monkeypatch):
    """
    FastAPI TestClient，綁定測試 DB。
    startup event 會自動執行 init_db + ensure_default_admin。

    C1-A 註：production 改用 ensure_initial_admin_token（隨機 PIN），
    但測試需要可預測 admin/1234，所以 monkeypatch 為舊版 fallback；
    並清 is_default_pin 標記，避免 first_run_gate middleware 擋下所有測試。
    """
    from core.database import get_conn
    from repositories import account_repo

    def _setup_test_admin():
        account_repo.ensure_default_admin("1234")
        # 清 is_default_pin 讓 first_run_gate 放行
        with get_conn() as conn:
            conn.execute("UPDATE accounts SET is_default_pin=0")
            conn.commit()

    monkeypatch.setattr("main.ensure_initial_admin_token", lambda *args, **kwargs: _setup_test_admin())
    # 測試環境：Admin PIN 不自動產生（由各測試自行 set_admin_pin）
    monkeypatch.setattr("main.ensure_default_admin_pin", lambda *args, **kwargs: None)
    # routers.dashboard 在 import 時 `from core.config import DB_PATH`（綁定快照），health
    # endpoint 讀此模組級 DB_PATH。tmp_db 只 patch core.config / core.database 的 DB_PATH，
    # **漏了 dashboard 模組綁定值** → health 會讀原始持久 DB（測試隔離破口：unit 測試先跑
    # 後，integration 的 health schema_version 對不上 max migration）。一併 patch 成測試 DB。
    import routers.dashboard

    monkeypatch.setattr(routers.dashboard, "DB_PATH", tmp_db)
    from fastapi.testclient import TestClient

    from main import app

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture
def session_token(client):
    """已登入的 session_id（預設 admin/1234）"""
    r = client.post("/api/auth/login", json={"username": "admin", "pin": "1234"})
    assert r.status_code == 200, f"登入失敗：{r.text}"
    return r.json()["session_id"]


@pytest.fixture
def auth(session_token):
    """帶認證的 header dict，直接傳給 client.get(..., headers=auth)"""
    return {"X-Session-Token": session_token}


@pytest.fixture
def hmac_client(client):
    """在 test DB 插入 HMAC 測試金鑰，回傳 (client, sign) 。

    sign(method, path, body_dict, query="") → headers dict（含 X-ICS-* + Content-Type）
    用法：
        c, sign = hmac_client
        r = c.post("/api/snapshots",
                   content=json.dumps(body).encode(),
                   headers=sign("POST", "/api/snapshots", body))
    """
    from core.database import get_conn

    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO trusted_keys (key_id, secret, status) VALUES (?, ?, 'active')",
        (_CONFTEST_HMAC_KEY_ID, _CONFTEST_HMAC_SECRET),
    )
    conn.commit()
    conn.close()
    return client, _make_hmac_sign_fn(_CONFTEST_HMAC_KEY_ID, _CONFTEST_HMAC_SECRET)


@pytest.fixture
def active_exercise(client, auth):
    """建立並啟動一個 TTX 演練，回傳 exercise dict"""
    r = client.post("/api/exercises", json={"name": "fixture-exercise", "type": "ttx"}, headers=auth)
    assert r.status_code == 200
    ex = r.json()
    r2 = client.post(f"/api/exercises/{ex['id']}/activate", json={}, headers=auth)
    assert r2.status_code == 200
    return r2.json()
