"""#286 H1：upload_map_image path traversal / 任意檔案寫入 回歸測試。

原漏洞：client 給的 file.filename 直接拼進 STATIC_DIR → 可用 `../` / 絕對路徑
逃出目錄、覆寫既有 JS·HTML（→ 儲存型 XSS·全站接管），且允許 svg（內含 <script>）。
修法：副檔名白名單（無 svg）+ server 端 uuid 命名 + resolve()/is_relative_to 縱深防禦。
"""

import pytest


@pytest.fixture
def static_tmp(monkeypatch, tmp_path):
    """把 routers.map 綁定的 STATIC_DIR 換成 tmp，避免測試污染真實 static/。"""
    import routers.map

    d = tmp_path / "static"
    d.mkdir()
    monkeypatch.setattr(routers.map, "STATIC_DIR", d)
    return d


def _upload(client, auth, filename, content=b"\x89PNG\r\n\x1a\n", ctype="image/png"):
    return client.post(
        "/api/map/upload-image",
        files={"file": (filename, content, ctype)},
        headers=auth,
    )


def test_valid_png_gets_uuid_name(client, auth, static_tmp):
    r = _upload(client, auth, "battlefield.png")
    assert r.status_code == 200, r.text
    name = r.json()["filename"]
    # server 生成 uuid 名，非 client 原名
    assert name != "battlefield.png"
    assert name.startswith("map_upload_") and name.endswith(".png")
    assert (static_tmp / name).exists()


def test_relative_traversal_cannot_escape(client, auth, static_tmp):
    """`../../` 檔名不得逃出 static；只該以 uuid 名落在 static 內。"""
    r = _upload(client, auth, "../../evil.png")
    assert r.status_code == 200, r.text
    name = r.json()["filename"]
    assert "/" not in name and "\\" not in name and ".." not in name
    # 逃逸目標不存在
    assert not (static_tmp.parent / "evil.png").exists()
    # 真正落點在 static 內
    assert (static_tmp / name).exists()


def test_absolute_path_cannot_escape(client, auth, static_tmp):
    r = _upload(client, auth, "C:/Windows/Temp/evil.png")
    assert r.status_code == 200, r.text
    name = r.json()["filename"]
    assert name.startswith("map_upload_")
    assert (static_tmp / name).exists()


def test_cannot_overwrite_existing_frontend_js(client, auth, static_tmp):
    """即使把副檔名偽裝，也不能覆寫既有 main.js（.js 非白名單）。"""
    victim = static_tmp / "main.js"
    victim.write_text("// original", encoding="utf-8")
    r = _upload(client, auth, "../main.js", content=b"alert(1)", ctype="application/javascript")
    assert r.status_code == 400  # .js 副檔名被拒
    assert victim.read_text(encoding="utf-8") == "// original"


def test_svg_rejected(client, auth, static_tmp):
    """svg 可內含 <script> → 儲存型 XSS，必須拒絕。"""
    r = _upload(client, auth, "x.svg", content=b"<svg><script>alert(1)</script></svg>", ctype="image/svg+xml")
    assert r.status_code == 400


def test_no_extension_rejected(client, auth, static_tmp):
    r = _upload(client, auth, "noext")
    assert r.status_code == 400
