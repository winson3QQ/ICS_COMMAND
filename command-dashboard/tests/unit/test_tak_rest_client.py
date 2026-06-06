"""
tests/unit/test_tak_rest_client.py — P2-11（#138）：Marti REST client 抽象層

鎖住的不變式：
- build_marti_ssl_context：無 cafile 且未顯式 insecure → raise（不默默關閉 server 驗證）
- get_json：2xx→JSON；4xx→不重試立即 TakRestError；5xx/連線錯→指數退避重試；耗盡→TakRestError
- rate-limit：兩次請求間隔 ≥ min_interval_s
- poll：stop_event set 後終止；單次失敗不中斷迴圈；callback 收到 JSON
- factory build_tak_rest_client：真 cert 端到端建構不報錯
"""

import asyncio
import ssl
from datetime import datetime

import aiohttp
import pytest

from services.tak_rest_client import (
    TakRestClient,
    TakRestError,
    _join_url,
    build_marti_ssl_context,
    build_tak_rest_client,
)


def _run(coro):
    return asyncio.run(coro)


def _client(monkeypatch, fetch_impl, *, min_interval_s=0.0, max_retries=3):
    """建注入 fake ssl（不需 cert，_fetch 被 mock 不真連）+ mock _fetch 的 client。"""
    c = TakRestClient(
        "https://tak.test:8443",
        ssl.create_default_context(),
        min_interval_s=min_interval_s,
        max_retries=max_retries,
    )
    monkeypatch.setattr(c, "_fetch", fetch_impl)
    return c


@pytest.fixture
def no_sleep(monkeypatch):
    """退避 sleep no-op（測試不真等指數退避）。"""

    async def _f(*a, **k):
        return None

    monkeypatch.setattr(asyncio, "sleep", _f)


def _gen_self_signed(tmp_path):
    """生成自簽 EC cert + key（對齊 step-ca 預設 ECDSA P-256）；cafile 用自身。"""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ics-test")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime(2020, 1, 1))
        .not_valid_after(datetime(2035, 1, 1))
        .sign(key, hashes.SHA256())
    )
    cert_p = tmp_path / "cert.pem"
    key_p = tmp_path / "key.pem"
    cert_p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_p.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return str(cert_p), str(key_p), str(cert_p)


# ── build_marti_ssl_context 安全分支 ──────────────────────────────────────


def test_ssl_context_requires_ca_or_explicit_insecure(monkeypatch):
    """無 cafile 且未顯式 insecure → raise（不默默裸奔關閉 server 驗證）。"""
    monkeypatch.setattr(ssl.SSLContext, "load_cert_chain", lambda *a, **k: None)
    with pytest.raises(ValueError, match="cafile"):
        build_marti_ssl_context(client_cert="c", client_key="k")


def test_ssl_context_insecure_disables_verify(monkeypatch):
    """顯式 allow_insecure_tls → verify 關閉（dev only）。"""
    monkeypatch.setattr(ssl.SSLContext, "load_cert_chain", lambda *a, **k: None)
    ctx = build_marti_ssl_context(client_cert="c", client_key="k", allow_insecure_tls=True)
    assert ctx.verify_mode == ssl.CERT_NONE
    assert ctx.check_hostname is False


def test_join_url_rejects_absolute_url_host_override():
    """path 一律接在 base_url 下；絕對 URL 不覆寫 host（杜絕 SSRF footgun，#138）。"""
    base = "https://tak.test:8443"
    assert _join_url(base, "/Marti/api/x") == "https://tak.test:8443/Marti/api/x"
    assert _join_url(base, "Marti/api/x") == "https://tak.test:8443/Marti/api/x"  # 無前導 / 補上
    # 絕對 URL 當 path → 接在 base 後（不被導去 evil host）
    assert _join_url(base, "http://evil.com/x") == "https://tak.test:8443/http://evil.com/x"


def test_factory_with_real_certs_builds_client(tmp_path):
    """factory 用真自簽 cert + CA → 建出 TakRestClient（cert 路徑端到端不報錯）。"""
    cert, key, ca = _gen_self_signed(tmp_path)
    client = build_tak_rest_client(base_url="https://tak.test:8443", client_cert=cert, client_key=key, cafile=ca)
    assert isinstance(client, TakRestClient)


# ── get_json retry / 狀態碼 ───────────────────────────────────────────────


def test_get_json_success(monkeypatch):
    async def _f(path, params):
        return 200, '{"ok": true}'

    assert _run(_client(monkeypatch, _f).get_json("/x")) == {"ok": True}


def test_get_json_retries_then_succeeds(monkeypatch, no_sleep):
    calls = {"n": 0}

    async def _f(path, params):
        calls["n"] += 1
        if calls["n"] == 1:
            raise aiohttp.ClientError("boom")  # 第一次連線錯
        return 200, "[]"

    assert _run(_client(monkeypatch, _f).get_json("/x")) == []
    assert calls["n"] == 2  # 重試後成功


def test_get_json_4xx_no_retry(monkeypatch, no_sleep):
    calls = {"n": 0}

    async def _f(path, params):
        calls["n"] += 1
        return 404, "not found"

    with pytest.raises(TakRestError, match="404"):
        _run(_client(monkeypatch, _f, max_retries=3).get_json("/x"))
    assert calls["n"] == 1  # 用戶端錯立即拋，不重試


def test_get_json_5xx_retries_then_raises(monkeypatch, no_sleep):
    calls = {"n": 0}

    async def _f(path, params):
        calls["n"] += 1
        return 503, "down"

    with pytest.raises(TakRestError, match="耗盡"):
        _run(_client(monkeypatch, _f, max_retries=3).get_json("/x"))
    assert calls["n"] == 3  # 5xx 重試到耗盡


def test_get_json_invalid_json_raises(monkeypatch):
    async def _f(path, params):
        return 200, "not json"

    with pytest.raises(TakRestError, match="非 JSON"):
        _run(_client(monkeypatch, _f).get_json("/x"))


def test_get_json_204_empty_body_returns_none(monkeypatch):
    """204 No Content / 空 body 的 2xx → None（非當成 JSON 解析失敗，review #138-4）。"""
    async def _f(path, params):
        return 204, ""

    assert _run(_client(monkeypatch, _f).get_json("/x")) is None


# ── 並發保護（鎖）────────────────────────────────────────────────────────────


def test_concurrent_ensure_session_single_instance():
    """並發首次 _ensure_session 只建一個 session（鎖防 race 建多個洩漏，review #138-3）。"""
    c = TakRestClient("https://t:8443", ssl.create_default_context())

    async def _scenario():
        s1, s2 = await asyncio.gather(c._ensure_session(), c._ensure_session())
        same = s1 is s2
        await c.close()
        return same

    assert _run(_scenario()) is True


# ── rate-limit ────────────────────────────────────────────────────────────


def test_rate_limit_enforces_min_interval(monkeypatch):
    """兩次請求間隔 ≥ min_interval_s（self-throttle）。"""

    async def _f(path, params):
        return 200, "1"

    c = _client(monkeypatch, _f, min_interval_s=0.2)

    async def _two():
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        await c.get_json("/a")
        await c.get_json("/b")
        return loop.time() - t0

    assert _run(_two()) >= 0.2  # 第二次被 throttle


# ── poll ──────────────────────────────────────────────────────────────────


def test_poll_invokes_callback_and_stops(monkeypatch):
    received = []

    async def _f(path, params):
        return 200, '{"n": 1}'

    async def _scenario():
        c = _client(monkeypatch, _f, min_interval_s=0.0)
        stop = asyncio.Event()

        def cb(data):
            received.append(data)
            if len(received) >= 2:
                stop.set()

        await c.poll("/x", cb, interval_s=0.01, stop_event=stop)

    _run(_scenario())
    assert len(received) >= 2
    assert received[0] == {"n": 1}


def test_poll_awaits_async_callback(monkeypatch):
    """async callback 被 await（inspect.isawaitable，review #138-5）。"""
    seen = []

    async def _f(path, params):
        return 200, "1"

    async def _scenario():
        c = _client(monkeypatch, _f, min_interval_s=0.0)
        stop = asyncio.Event()

        async def cb(data):
            seen.append(data)
            stop.set()

        await c.poll("/x", cb, interval_s=0.01, stop_event=stop)

    _run(_scenario())
    assert seen == [1]


def test_poll_survives_single_failure(monkeypatch, no_sleep):
    received = []
    calls = {"n": 0}

    async def _f(path, params):
        calls["n"] += 1
        if calls["n"] == 1:
            return 500, "err"  # 第一輪失敗（5xx 重試耗盡 → get_json raise）
        return 200, "true"

    async def _scenario():
        c = _client(monkeypatch, _f, min_interval_s=0.0, max_retries=1)
        stop = asyncio.Event()

        def cb(data):
            received.append(data)
            stop.set()

        await c.poll("/x", cb, interval_s=0.01, stop_event=stop)

    _run(_scenario())
    assert received == [True]  # 第一輪失敗不中斷，第二輪成功
