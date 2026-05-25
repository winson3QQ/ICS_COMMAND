def test_csp_violations_table_schema(tmp_db):
    from core.database import get_conn

    with get_conn() as conn:
        columns = {
            row["name"]: row["type"]
            for row in conn.execute("PRAGMA table_info(csp_violations)").fetchall()
        }

    assert columns["id"].upper().startswith("INTEGER")
    assert columns["reported_at"].upper().startswith("TEXT")
    assert columns["source_ip"].upper().startswith("TEXT")
    assert columns["violated_directive"].upper().startswith("TEXT")
    assert columns["blocked_uri"].upper().startswith("TEXT")
    assert columns["document_uri"].upper().startswith("TEXT")
    assert columns["raw_report"].upper().startswith("TEXT")


def test_unknown_path_falls_to_report_only(monkeypatch):
    import core.security_headers as security_headers

    monkeypatch.setattr(security_headers, "CSP_MODE", "enforce")
    header_name, header_value = security_headers._get_csp_header("/static/unclassified.html")

    assert header_name == "Content-Security-Policy-Report-Only"
    assert "'unsafe-inline'" in header_value


def test_enforce_path_list_documented():
    import core.security_headers as security_headers

    assert "/static/commander_dashboard.html" in security_headers.ENFORCE_PATHS
    assert "/static/admin_backups.html" in security_headers.REPORT_ONLY_PATHS
    assert "/static/scenario_designer.html" in security_headers.REPORT_ONLY_PATHS
    assert "/static/qr_scanner.html" in security_headers.REPORT_ONLY_PATHS
    assert "MUST update" in (security_headers.__doc__ or "")
