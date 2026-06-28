# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
integration/test_account_cert_api.py — #275 wave 3：裝置憑證綁定管理 API

/api/admin/accounts/{username}/certs（GET/POST/DELETE，sysadmin only）。
涵蓋：綁定→列出→撤銷生命週期、重複 active CN 409、撤銷越權隔離、未授權 401、找不到帳號 404。
"""

import pytest

pytestmark = pytest.mark.integration


def _mk_account(client, auth, username="alice", role="操作員"):
    r = client.post("/api/admin/accounts", json={"username": username, "pin": "739104", "role": role}, headers=auth)
    assert r.status_code in (200, 201), r.text
    return username


class TestCertLifecycle:
    def test_bind_list_revoke(self, client, auth):
        _mk_account(client, auth, "alice")
        # 綁定
        r = client.post(
            "/api/admin/accounts/alice/certs", json={"cert_cn": "alice-phone", "label": "私人手機"}, headers=auth
        )
        assert r.status_code == 200, r.text
        cert_id = r.json()["id"]
        assert r.json()["status"] == "active"
        # 列出
        r = client.get("/api/admin/accounts/alice/certs", headers=auth)
        assert r.status_code == 200 and len(r.json()) == 1
        # 撤銷
        r = client.delete(f"/api/admin/accounts/alice/certs/{cert_id}", headers=auth)
        assert r.status_code == 200 and r.json()["status"] == "revoked"
        # 撤銷後仍在列表（稽核留痕），但 status=revoked
        r = client.get("/api/admin/accounts/alice/certs", headers=auth)
        assert r.json()[0]["status"] == "revoked"

    def test_multiple_devices(self, client, auth):
        _mk_account(client, auth, "bob")
        for dev in ("bob-phone", "bob-tablet"):
            r = client.post("/api/admin/accounts/bob/certs", json={"cert_cn": dev}, headers=auth)
            assert r.status_code == 200
        assert len(client.get("/api/admin/accounts/bob/certs", headers=auth).json()) == 2

    def test_duplicate_active_cn_conflict(self, client, auth):
        _mk_account(client, auth, "carol")
        client.post("/api/admin/accounts/carol/certs", json={"cert_cn": "dup"}, headers=auth)
        r = client.post("/api/admin/accounts/carol/certs", json={"cert_cn": "dup"}, headers=auth)
        assert r.status_code == 409

    def test_revoke_cross_account_isolated(self, client, auth):
        _mk_account(client, auth, "dan")
        _mk_account(client, auth, "erin")
        cid = client.post("/api/admin/accounts/dan/certs", json={"cert_cn": "dan-dev"}, headers=auth).json()["id"]
        # 以 erin 的路徑撤 dan 的 cert_id → 404（帳號隔離）
        r = client.delete(f"/api/admin/accounts/erin/certs/{cid}", headers=auth)
        assert r.status_code == 404

    def test_unknown_account_404(self, client, auth):
        r = client.get("/api/admin/accounts/ghost/certs", headers=auth)
        assert r.status_code == 404

    def test_bind_rejects_invalid_cn(self, client, auth):
        """逗號 CN 會破壞 nginx CN 抽取、前導 dash 會被 step 當旗標 → 422。"""
        _mk_account(client, auth, "vic")
        for bad in ("a,b", "-flag"):
            r = client.post("/api/admin/accounts/vic/certs", json={"cert_cn": bad}, headers=auth)
            assert r.status_code == 422, bad


class TestOnlineIssue:
    def test_issue_503_when_step_ca_not_configured(self, client, auth):
        """#275 wave B-2：未配置 step-ca → 線上發證回 503（改走離線簽 + 僅綁定）。"""
        _mk_account(client, auth, "frank")
        r = client.post("/api/admin/accounts/frank/certs/issue", json={"cert_cn": "frank-pc"}, headers=auth)
        assert r.status_code == 503

    def test_issue_success_returns_p12_and_binds(self, client, auth, monkeypatch):
        """配置齊備 + daemon 簽成功 → 回 p12（x-pkcs12）+ 自動綁定 CN。"""
        import core.config as config
        import services.cert_issuance as ci

        monkeypatch.setattr(config, "step_ca_configured", lambda: True)
        monkeypatch.setattr(ci, "issue_p12", lambda cn: (b"PKCS12-FAKE-BYTES", "rand-pw-xyz"))
        _mk_account(client, auth, "grace")
        r = client.post(
            "/api/admin/accounts/grace/certs/issue", json={"cert_cn": "grace-laptop", "label": "工作機"}, headers=auth
        )
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "application/x-pkcs12"
        assert "grace-laptop.p12" in r.headers.get("content-disposition", "")
        assert r.content == b"PKCS12-FAKE-BYTES"
        # #307：p12 密碼經 X-P12-Password header 回前端（不進 audit / body）
        assert r.headers.get("x-p12-password") == "rand-pw-xyz"
        # 已自動綁定
        certs = client.get("/api/admin/accounts/grace/certs", headers=auth).json()
        assert any(c["cert_cn"] == "grace-laptop" and c["status"] == "active" for c in certs)

    def test_issue_mobileconfig(self, client, auth, monkeypatch):
        """#312：fmt=mobileconfig → 回 .mobileconfig（描述檔），密碼內嵌不回 header，仍自動綁定。"""
        import core.config as config
        import services.cert_issuance as ci

        monkeypatch.setattr(config, "step_ca_configured", lambda: True)
        monkeypatch.setattr(ci, "issue_p12", lambda cn: (b"P12", "embedded-pw"))
        monkeypatch.setattr(ci, "fetch_root_ca_pem", lambda: "-----BEGIN CERTIFICATE-----X")
        monkeypatch.setattr(ci, "build_mobileconfig", lambda cn, p12, pw, root, url: b"<plist>MC</plist>")
        _mk_account(client, auth, "iris")
        r = client.post(
            "/api/admin/accounts/iris/certs/issue?fmt=mobileconfig", json={"cert_cn": "iris-ipad"}, headers=auth
        )
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "application/x-apple-aspen-config"
        assert "iris-ipad.mobileconfig" in r.headers.get("content-disposition", "")
        assert r.content == b"<plist>MC</plist>"
        assert "x-p12-password" not in {k.lower() for k in r.headers}  # 密碼內嵌、不另回
        certs = client.get("/api/admin/accounts/iris/certs", headers=auth).json()
        assert any(c["cert_cn"] == "iris-ipad" and c["status"] == "active" for c in certs)

    def test_issue_invalid_fmt_422(self, client, auth, monkeypatch):
        import core.config as config

        monkeypatch.setattr(config, "step_ca_configured", lambda: True)
        _mk_account(client, auth, "jack")
        r = client.post("/api/admin/accounts/jack/certs/issue?fmt=xml", json={"cert_cn": "jack-x"}, headers=auth)
        assert r.status_code == 422

    def test_issue_409_when_cn_already_active(self, client, auth, monkeypatch):
        import core.config as config
        import services.cert_issuance as ci

        monkeypatch.setattr(config, "step_ca_configured", lambda: True)
        monkeypatch.setattr(ci, "issue_p12", lambda cn: (b"X", "pw"))
        _mk_account(client, auth, "heidi")
        client.post("/api/admin/accounts/heidi/certs", json={"cert_cn": "dupe-cn"}, headers=auth)
        r = client.post("/api/admin/accounts/heidi/certs/issue", json={"cert_cn": "dupe-cn"}, headers=auth)
        assert r.status_code == 409

    def test_issue_502_on_issuance_error(self, client, auth, monkeypatch):
        import core.config as config
        import services.cert_issuance as ci

        monkeypatch.setattr(config, "step_ca_configured", lambda: True)

        def _boom(cn):
            raise ci.CertIssuanceError("daemon 不可達")

        monkeypatch.setattr(ci, "issue_p12", _boom)
        _mk_account(client, auth, "ivan")
        r = client.post("/api/admin/accounts/ivan/certs/issue", json={"cert_cn": "ivan-pc"}, headers=auth)
        assert r.status_code == 502


class TestRootCaDownload:
    """#327：桌機信任 server 用的 root CA 下載（GET /api/admin/ca/root）。"""

    def test_download_root_ca(self, client, auth, monkeypatch):
        import services.cert_issuance as ci

        monkeypatch.setattr(
            ci, "fetch_root_ca_pem", lambda: "-----BEGIN CERTIFICATE-----\nROOTCA\n-----END CERTIFICATE-----\n"
        )
        r = client.get("/api/admin/ca/root", headers=auth)
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("application/x-pem-file")
        assert "ics-root-ca.pem" in r.headers.get("content-disposition", "")
        assert "BEGIN CERTIFICATE" in r.text

    def test_root_ca_503_when_not_configured(self, client, auth, monkeypatch):
        import services.cert_issuance as ci

        def _boom():
            raise ci.CertIssuanceError("step-ca 線上發證未配置")

        monkeypatch.setattr(ci, "fetch_root_ca_pem", _boom)
        assert client.get("/api/admin/ca/root", headers=auth).status_code == 503

    def test_root_ca_requires_auth(self, client):
        assert client.get("/api/admin/ca/root").status_code == 401


class TestPurgeRevoked:
    """#307 缺口 2：清除已撤銷列（DELETE /certs/revoked）。"""

    def test_purge_removes_revoked_keeps_active(self, client, auth):
        _mk_account(client, auth, "pat")
        keep = client.post("/api/admin/accounts/pat/certs", json={"cert_cn": "pat-active"}, headers=auth).json()["id"]
        gone = client.post("/api/admin/accounts/pat/certs", json={"cert_cn": "pat-revoked"}, headers=auth).json()["id"]
        client.delete(f"/api/admin/accounts/pat/certs/{gone}", headers=auth)
        # 清除已撤銷
        r = client.delete("/api/admin/accounts/pat/certs/revoked", headers=auth)
        assert r.status_code == 200, r.text
        assert r.json()["purged"] == 1
        certs = client.get("/api/admin/accounts/pat/certs", headers=auth).json()
        assert len(certs) == 1 and certs[0]["id"] == keep

    def test_purge_route_not_shadowed_by_int_param(self, client, auth):
        """/certs/revoked 須贏過 /certs/{cert_id:int}（literal path 優先），不得 422。"""
        _mk_account(client, auth, "quinn")
        r = client.delete("/api/admin/accounts/quinn/certs/revoked", headers=auth)
        assert r.status_code == 200 and r.json()["purged"] == 0

    def test_purge_scoped_to_account(self, client, auth):
        _mk_account(client, auth, "rita")
        _mk_account(client, auth, "sam")
        cid = client.post("/api/admin/accounts/sam/certs", json={"cert_cn": "sam-dev"}, headers=auth).json()["id"]
        client.delete(f"/api/admin/accounts/sam/certs/{cid}", headers=auth)
        # 清 rita 的不動 sam 的 revoked 列
        client.delete("/api/admin/accounts/rita/certs/revoked", headers=auth)
        assert len(client.get("/api/admin/accounts/sam/certs", headers=auth).json()) == 1

    def test_purge_unknown_account_404(self, client, auth):
        r = client.delete("/api/admin/accounts/ghost/certs/revoked", headers=auth)
        assert r.status_code == 404


class TestTakDeviceCert:
    """#315 P2-26 L2：dashboard 發 TAK 裝置證 data package。"""

    def test_issue_returns_zip_and_audits(self, client, auth, monkeypatch, tmp_path):
        import core.config as config
        import services.tak_device_cert as tdc

        (tmp_path / "tak-ca.key").write_text("KEY", encoding="ascii")
        (tmp_path / "tak-ca.pem").write_text("PEM", encoding="ascii")
        monkeypatch.setattr(config, "TAK_DEVICE_CONNECT_HOST", "1.2.3.4")
        monkeypatch.setattr(config, "TAK_DEVICE_CA_DIR", str(tmp_path))
        monkeypatch.setattr(tdc, "build_device_package", lambda *a, **k: (b"ZIP-DP-BYTES", "S3R1AL", "FP:00"))
        r = client.post("/api/admin/tak/device-cert?callsign=atak-01&mode=atak", headers=auth)
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "application/zip"
        assert "atak-01-dp.zip" in r.headers.get("content-disposition", "")
        assert r.content == b"ZIP-DP-BYTES"
        # #317：發證後進盤點表（serial 記下）
        listing = client.get("/api/admin/tak/device-certs", headers=auth).json()
        assert any(x["callsign"] == "atak-01" and x["serial"] == "S3R1AL" and x["status"] == "active" for x in listing)

    def test_issue_enrollment_mode_binds_fingerprint(self, client, auth, monkeypatch, tmp_path):
        """#431：enrollment 模式（#429 signClient）發證後**仍須**呼叫 enroll_device 把 fingerprint
        綁進 TAK 名冊——signClient 只發證、不寫回 fingerprint，漏綁則帳號停密碼認證、reconcile
        未同步、證不可撤（#318 需 hash）。迴歸：use_enroll=True 不得跳過 enroll_device、不得硬寫 'ok'。"""
        import core.config as config
        import services.tak_device_cert as tdc
        import services.tak_enrollment as te
        import services.tak_user_enroll as tue

        (tmp_path / "tak-ca.key").write_text("KEY", encoding="ascii")
        (tmp_path / "tak-ca.pem").write_text("PEM", encoding="ascii")
        monkeypatch.setattr(config, "TAK_DEVICE_CONNECT_HOST", "1.2.3.4")
        monkeypatch.setattr(config, "TAK_DEVICE_CA_DIR", str(tmp_path))
        monkeypatch.setattr(tdc, "build_device_package", lambda *a, **k: (b"DP", "SER-ENR", "FP:EN:RO:LL"))
        monkeypatch.setattr(te, "is_configured", lambda: True)  # → use_enroll=True（enrollment 模式）
        calls = []
        monkeypatch.setattr(
            tue,
            "enroll_device",
            lambda cn, fp, grp=None: (calls.append((cn, fp, grp)) or {"enrolled": True, "reason": "ok", "group": grp}),
        )
        r = client.post("/api/admin/tak/device-cert?callsign=enr-01&mode=aware", headers=auth)
        assert r.status_code == 200, r.text
        # 核心斷言：enrollment 模式也以「發證 fingerprint」呼叫 enroll_device 綁定（初始群 = 預設群）
        assert calls == [("enr-01", "FP:EN:RO:LL", config.TAK_ENROLL_DEFAULT_GROUP)]
        assert r.headers["X-TAK-Enroll-Status"] == "ok"

    def test_issue_bundles_wireguard_when_configured(self, client, auth, monkeypatch, tmp_path):
        """#434：WG 配置時，發證回傳外層 bundle（TAK 包 + wireguard.conf + QR + 說明），X-WG-Status=ok。"""
        import io
        import zipfile

        import core.config as config
        import services.tak_device_cert as tdc
        import services.wg_provision as wgp

        (tmp_path / "tak-ca.key").write_text("KEY", encoding="ascii")
        (tmp_path / "tak-ca.pem").write_text("PEM", encoding="ascii")
        monkeypatch.setattr(config, "TAK_DEVICE_CONNECT_HOST", "1.2.3.4")
        monkeypatch.setattr(config, "TAK_DEVICE_CA_DIR", str(tmp_path))
        monkeypatch.setattr(tdc, "build_device_package", lambda *a, **k: (b"TAKZIP", "SER-WG", "FP:00"))
        monkeypatch.setattr(wgp, "is_configured", lambda: True)
        monkeypatch.setattr(
            wgp,
            "provision_device",
            lambda cs, op: {"ok": True, "reason": "ok", "conf": "[Interface]\nPrivateKey = x\n", "qr": b"PNGX"},
        )
        r = client.post("/api/admin/tak/device-cert?callsign=wg-01&mode=aware", headers=auth)
        assert r.status_code == 200, r.text
        assert r.headers["X-WG-Status"] == "ok"
        names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
        assert "wireguard.conf" in names and "wireguard-qr.png" in names and "安裝說明.txt" in names
        assert "wg-01-TAK.zip" in names

    def test_issue_wg_failure_falls_back_to_plain_pkg(self, client, auth, monkeypatch, tmp_path):
        """#434：WG provisioning 失敗 → 仍交付純 TAK 包（不擋發證），X-WG-Status 帶 reason。"""
        import core.config as config
        import services.tak_device_cert as tdc
        import services.wg_provision as wgp

        (tmp_path / "tak-ca.key").write_text("KEY", encoding="ascii")
        (tmp_path / "tak-ca.pem").write_text("PEM", encoding="ascii")
        monkeypatch.setattr(config, "TAK_DEVICE_CONNECT_HOST", "1.2.3.4")
        monkeypatch.setattr(config, "TAK_DEVICE_CA_DIR", str(tmp_path))
        monkeypatch.setattr(tdc, "build_device_package", lambda *a, **k: (b"TAKZIP-ONLY", "SER", "FP:00"))
        monkeypatch.setattr(wgp, "is_configured", lambda: True)
        monkeypatch.setattr(wgp, "provision_device", lambda cs, op: {"ok": False, "reason": "pool-exhausted"})
        r = client.post("/api/admin/tak/device-cert?callsign=wg-02&mode=aware", headers=auth)
        assert r.status_code == 200
        assert r.headers["X-WG-Status"] == "pool-exhausted"
        assert r.content == b"TAKZIP-ONLY"  # WG 失敗仍交付純 TAK 包

    def test_issue_wg_exception_backstop(self, client, auth, monkeypatch, tmp_path):
        """#434 review blocker：provision_device 拋例外（如並發 IntegrityError）不得擋發證
        （證已發/已 audit）→ 仍 200 + 純 TAK 包 + X-WG-Status=error。"""
        import core.config as config
        import services.tak_device_cert as tdc
        import services.wg_provision as wgp

        (tmp_path / "tak-ca.key").write_text("KEY", encoding="ascii")
        (tmp_path / "tak-ca.pem").write_text("PEM", encoding="ascii")
        monkeypatch.setattr(config, "TAK_DEVICE_CONNECT_HOST", "1.2.3.4")
        monkeypatch.setattr(config, "TAK_DEVICE_CA_DIR", str(tmp_path))
        monkeypatch.setattr(tdc, "build_device_package", lambda *a, **k: (b"TAK-ONLY", "SER", "FP:00"))
        monkeypatch.setattr(wgp, "is_configured", lambda: True)

        def _boom(cs, op):
            raise RuntimeError("boom")

        monkeypatch.setattr(wgp, "provision_device", _boom)
        r = client.post("/api/admin/tak/device-cert?callsign=wg-03&mode=aware", headers=auth)
        assert r.status_code == 200
        assert r.headers["X-WG-Status"] == "error"
        assert r.content == b"TAK-ONLY"

    def test_list_and_revoke_flag(self, client, auth, monkeypatch, tmp_path):
        """#317：列管 + 撤銷-flag（帳面，不 enforce）。"""
        import core.config as config
        import services.tak_device_cert as tdc

        (tmp_path / "tak-ca.key").write_text("KEY", encoding="ascii")
        (tmp_path / "tak-ca.pem").write_text("PEM", encoding="ascii")
        monkeypatch.setattr(config, "TAK_DEVICE_CONNECT_HOST", "1.2.3.4")
        monkeypatch.setattr(config, "TAK_DEVICE_CA_DIR", str(tmp_path))
        monkeypatch.setattr(tdc, "build_device_package", lambda *a, **k: (b"Z", "SER-X", "FP:00"))
        client.post("/api/admin/tak/device-cert?callsign=itak-rev&mode=aware", headers=auth)
        rec = next(
            x for x in client.get("/api/admin/tak/device-certs", headers=auth).json() if x["callsign"] == "itak-rev"
        )
        # 撤銷標記
        r = client.post(f"/api/admin/tak/device-certs/{rec['id']}/revoke", headers=auth)
        assert r.status_code == 200 and r.json()["status"] == "revoked"
        # 已撤再撤 → 404
        assert client.post(f"/api/admin/tak/device-certs/{rec['id']}/revoke", headers=auth).status_code == 404

    def test_issue_chinese_callsign_no_500(self, client, auth, monkeypatch, tmp_path):
        """#324：中文 callsign 不可因 Content-Disposition 非 latin-1 檔名而 500。

        舊 bug：`isalnum()` 對中文回 True → 中文進 header → uvicorn UnicodeEncodeError → 500。
        修後：ASCII fallback `filename=` + RFC5987 `filename*` 保留中文，header latin-1 安全。"""
        import core.config as config
        import services.tak_device_cert as tdc

        (tmp_path / "tak-ca.key").write_text("KEY", encoding="ascii")
        (tmp_path / "tak-ca.pem").write_text("PEM", encoding="ascii")
        monkeypatch.setattr(config, "TAK_DEVICE_CONNECT_HOST", "1.2.3.4")
        monkeypatch.setattr(config, "TAK_DEVICE_CA_DIR", str(tmp_path))
        monkeypatch.setattr(tdc, "build_device_package", lambda *a, **k: (b"ZIP", "SER-CJK", "FP:00"))
        r = client.post("/api/admin/tak/device-cert", params={"callsign": "主教", "mode": "aware"}, headers=auth)
        assert r.status_code == 200, r.text  # 關鍵：不是 500
        cd = r.headers.get("content-disposition", "")
        cd.encode("latin-1")  # header 須 latin-1 可編碼（否則 ASGI 送不出）
        assert 'filename="device-dp.zip"' in cd  # 全中文 → ASCII fallback
        assert "filename*=UTF-8''%E4%B8%BB%E6%95%99-dp.zip" in cd  # RFC5987 保留中文
        # 證確實簽出 + 進盤點（非幽靈）
        assert any(
            x["callsign"] == "主教" and x["status"] == "active"
            for x in client.get("/api/admin/tak/device-certs", headers=auth).json()
        )

    def test_delete_revoked_record(self, client, auth, monkeypatch, tmp_path):
        """#325：刪已撤銷盤點紀錄；active 不可刪（404）。"""
        import core.config as config
        import services.tak_device_cert as tdc

        (tmp_path / "tak-ca.key").write_text("KEY", encoding="ascii")
        (tmp_path / "tak-ca.pem").write_text("PEM", encoding="ascii")
        monkeypatch.setattr(config, "TAK_DEVICE_CONNECT_HOST", "1.2.3.4")
        monkeypatch.setattr(config, "TAK_DEVICE_CA_DIR", str(tmp_path))
        monkeypatch.setattr(tdc, "build_device_package", lambda *a, **k: (b"Z", "SER-D", "FP:00"))
        client.post("/api/admin/tak/device-cert?callsign=itak-del&mode=aware", headers=auth)
        rec = next(
            x for x in client.get("/api/admin/tak/device-certs", headers=auth).json() if x["callsign"] == "itak-del"
        )
        # active 不可刪 → 404
        assert client.delete(f"/api/admin/tak/device-certs/{rec['id']}", headers=auth).status_code == 404
        # 撤銷後可刪
        client.post(f"/api/admin/tak/device-certs/{rec['id']}/revoke", headers=auth)
        assert client.delete(f"/api/admin/tak/device-certs/{rec['id']}", headers=auth).status_code == 200
        assert all(x["id"] != rec["id"] for x in client.get("/api/admin/tak/device-certs", headers=auth).json())

    def test_delete_requires_auth(self, client):
        assert client.delete("/api/admin/tak/device-certs/1").status_code == 401

    def test_list_requires_auth(self, client):
        assert client.get("/api/admin/tak/device-certs").status_code == 401

    def test_503_when_no_ca_dir(self, client, auth, monkeypatch):
        import core.config as config

        monkeypatch.setattr(config, "TAK_DEVICE_CONNECT_HOST", "1.2.3.4")
        monkeypatch.setattr(config, "TAK_DEVICE_CA_DIR", "")
        r = client.post("/api/admin/tak/device-cert?callsign=x&mode=atak", headers=auth)
        assert r.status_code == 503

    def test_invalid_mode_422(self, client, auth):
        r = client.post("/api/admin/tak/device-cert?callsign=x&mode=bogus", headers=auth)
        assert r.status_code == 422

    def test_invalid_callsign_422(self, client, auth, monkeypatch):
        import core.config as config

        monkeypatch.setattr(config, "TAK_DEVICE_CONNECT_HOST", "1.2.3.4")
        r = client.post("/api/admin/tak/device-cert?callsign=a,b&mode=atak", headers=auth)
        assert r.status_code == 422

    def test_503_when_no_connect_host(self, client, auth, monkeypatch):
        import core.config as config

        monkeypatch.setattr(config, "TAK_DEVICE_CONNECT_HOST", "")
        r = client.post("/api/admin/tak/device-cert?callsign=x&mode=atak", headers=auth)
        assert r.status_code == 503

    def test_requires_auth(self, client):
        assert client.post("/api/admin/tak/device-cert?callsign=x&mode=atak").status_code == 401


class TestAuthz:
    def test_requires_auth(self, client):
        assert client.get("/api/admin/accounts/alice/certs").status_code == 401
        assert client.post("/api/admin/accounts/alice/certs", json={"cert_cn": "x"}).status_code == 401
        assert client.post("/api/admin/accounts/alice/certs/issue", json={"cert_cn": "x"}).status_code == 401
        assert client.delete("/api/admin/accounts/alice/certs/revoked").status_code == 401
