from pathlib import Path


def test_csp_report_endpoint_accepts_violation(client, tmp_db):
    payload = {
        "csp-report": {
            "document-uri": "http://testserver/static/commander_dashboard.html",
            "violated-directive": "script-src",
            "blocked-uri": "inline",
        }
    }

    response = client.post("/api/security/csp-report", json=payload)

    assert response.status_code == 204
    from core.database import get_conn

    with get_conn() as conn:
        row = conn.execute(
            "SELECT violated_directive, blocked_uri, document_uri, raw_report FROM csp_violations"
        ).fetchone()
    assert row["violated_directive"] == "script-src"
    assert row["blocked_uri"] == "inline"
    assert row["document_uri"].endswith("/static/commander_dashboard.html")
    assert "csp-report" in row["raw_report"]


def test_csp_header_includes_report_uri(client, monkeypatch):
    import core.security_headers as security_headers

    monkeypatch.setattr(security_headers, "CSP_MODE", "enforce")
    response = client.get("/static/commander_dashboard.html")

    assert response.status_code == 200
    assert "Content-Security-Policy" in response.headers
    assert "report-uri /api/security/csp-report" in response.headers["Content-Security-Policy"]


def test_csp_report_rate_limit(client):
    import routers.security as security_router

    security_router._csp_limiter.reset("testclient")
    payload = {"csp-report": {"violated-directive": "script-src"}}

    responses = [client.post("/api/security/csp-report", json=payload) for _ in range(61)]

    assert [r.status_code for r in responses[:60]] == [204] * 60
    assert responses[60].status_code == 429


def test_csp_header_no_unsafe_inline_on_commander(client, monkeypatch):
    import core.security_headers as security_headers

    monkeypatch.setattr(security_headers, "CSP_MODE", "enforce")
    response = client.get("/static/commander_dashboard.html")

    assert response.status_code == 200
    header = response.headers["Content-Security-Policy"]
    script_src = next(part.strip() for part in header.split(";") if part.strip().startswith("script-src"))
    assert script_src == "script-src 'self'"
    assert "'unsafe-inline'" not in script_src


def test_csp_report_only_on_admin_and_other_paths(client, monkeypatch):
    import core.security_headers as security_headers

    monkeypatch.setattr(security_headers, "CSP_MODE", "enforce")

    admin = client.get("/static/admin_backups.html")
    unknown = client.get("/api/health")

    assert "Content-Security-Policy-Report-Only" in admin.headers
    assert "'unsafe-inline'" in admin.headers["Content-Security-Policy-Report-Only"]
    assert "Content-Security-Policy-Report-Only" in unknown.headers


def test_no_inline_script_in_commander_html():
    html = Path("static/commander_dashboard.html").read_text(encoding="utf-8")

    assert "<script>" not in html
    assert "onclick=" not in html
    assert "onerror=" not in html
    assert "onload=" not in html
    assert '<script type="module" src="/static/js/main.js"></script>' in html


def test_no_template_variables_in_commander_html():
    html = Path("static/commander_dashboard.html").read_text(encoding="utf-8")

    assert "{{" not in html
    assert "{%" not in html


def test_cdn_scripts_have_integrity_hash():
    html = Path("static/commander_dashboard.html").read_text(encoding="utf-8")

    assert '<script src="https://' not in html
