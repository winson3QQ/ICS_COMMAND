# SBOM — 軟體物料清單（Software Bill of Materials）

#351 商用化前置：供投標 / 資安盡職調查 / 漏洞·授權衝突偵測。格式為 **CycloneDX 1.6 (JSON)**。

## 檔案
| 檔 | 範圍 | 來源 |
|---|---|---|
| `python-runtime.cdx.json` | 後端**執行期** Python 相依（11） | `command-dashboard/requirements.txt` |
| `python-test.cdx.json` | 測試 / 開發 Python 相依（4） | `command-dashboard/requirements-test.txt` |

## 產生方式（可重現）
```bash
python -m cyclonedx_py requirements command-dashboard/requirements.txt \
  --of JSON --output-reproducible -o sbom/python-runtime.cdx.json
```
工具：OWASP **cyclonedx-bom 7.3.0**（CLI `cyclonedx_py`，US/OWASP，非中國）。
Windows 需 `PYTHONUTF8=1`（requirements.txt 含 UTF-8 中文註解，否則 cp950 解碼失敗）。

## 涵蓋範圍與限制（誠實標示）
- **僅涵蓋宣告的直接相依**（requirements 模式不解析 transitive）；完整 transitive 需於部署環境用
  `cyclonedx_py environment` 重產。
- **3 個未鎖版**（requirements.txt 用 `>=`）：`cryptography`、`aiohttp`、`structlog` →
  SBOM 顯示無 version。建議公測前鎖定確切版本以利漏洞比對。
- **授權欄未自動填**（requirements 模式不抓 metadata）；各元件授權以根目錄 `NOTICE` /
  `THIRD_PARTY_LICENSES.md` 為權威來源。
- **Node `server/`**：無 `package.json`，使用 Node 內建模組，無第三方 npm 相依 → 無 npm SBOM。
- **非套件管理的元件**（不在本 SBOM，已於 `NOTICE` 列管）：
  OpenStreetMap 底圖資料（ODbL）、JetBrains Mono（OFL）、MapLibre GL JS / PMTiles（BSD）、
  milsymbol（MIT）、jsQR（Apache-2.0）、NAPSG 象形（CC BY 4.0）；
  TAK Server 為獨立第三方（GPLv3，不隨產品散布）。

## 後續（#351）
- [ ] 公測前用 `environment` 模式產含 transitive + 確切版本的完整 SBOM
- [ ] 鎖定 3 個 `>=` 相依版本
- [ ] 將 SBOM 納入 release pipeline（每版自動產 + 簽章）
