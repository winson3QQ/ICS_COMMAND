# ICS_Command

**指揮部儀表板（Command Dashboard）** — 事件管理體系（ICS）通用作戰圖（COP）與決策支援系統。

從 [ICS_DMAS](https://github.com/winson3QQ/ICS_DMAS) 多組件平台拆分出來的單體交付版本，聚焦指揮部後端與儀表板。

## 元件

| 路徑 | 說明 |
|---|---|
| `command-dashboard/` | FastAPI + SQLite 後端、指揮官 / 幕僚儀表板（HTML + JS） |
| `server/` | Node.js WebSocket relay（保留作前端 / security 整合介接點） |
| `docs/compliance/` | NIST SSDF / ASVS / ISO 25010 對照（policies + threat model） |
| `deploy/`（Stage 2 補） | nginx + step-ca 內網 PKI |

## 快速啟動（Mac）

```bash
chmod +x start_mac.sh
./start_mac.sh
```

啟動後開 <http://127.0.0.1:8000/static/commander_dashboard.html>。

## 測試

```bash
cd command-dashboard
.venv/bin/pytest
```

## 版號規則

- `command-vX.Y.Z` — 指揮部後端與儀表板（SemVer，git tag）
- `server-vX.Y.Z` — Node.js relay（若獨立演進才打）

## 安全聲明

見 [SECURITY.md](SECURITY.md)。Compliance 主張見 [docs/compliance/](docs/compliance/)。

## 授權

待定（拆分前 ICS_DMAS 為私有 repo；對外交付前需明定 license）。
