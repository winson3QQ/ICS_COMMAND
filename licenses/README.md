# licenses/ — 第三方授權完整條文

多數寬鬆授權（BSD / MIT / Apache / OFL / CC BY / ODbL）要求**散布時隨附授權副本**。
本目錄收納各第三方元件之**授權全文**，供 NOTICE 引用、隨產品交付。

## 待補全文檔（骨架）

把下列各授權之官方全文存成對應檔名（純文字），交付時隨產品打包：

| 檔名 | 授權 | 對應元件 | 官方全文 |
|---|---|---|---|
| `ODbL-1.0.txt` | Open Database License 1.0 | OpenStreetMap 底圖資料 | https://opendatacommons.org/licenses/odbl/1-0/ |
| `CC-BY-4.0.txt` | Creative Commons Attribution 4.0 | NAPSG 象形符號 | https://creativecommons.org/licenses/by/4.0/legalcode.txt |
| `OFL-1.1.txt` | SIL Open Font License 1.1 | JetBrains Mono（亦見 static/fonts/LICENSE.txt） | https://openfontlicense.org/ |
| `MIT.txt` | MIT License | milsymbol、FastAPI、Pydantic、sqlcipher3 | https://opensource.org/license/mit |
| `BSD-3-Clause.txt` | BSD 3-Clause | MapLibre GL JS、PMTiles、Uvicorn、Starlette | https://opensource.org/license/bsd-3-clause |
| `BSD-2-Clause.txt` | BSD 2-Clause | fido2 | https://opensource.org/license/bsd-2-clause |
| `Apache-2.0.txt` | Apache License 2.0 | jsQR、aiohttp、pytak、takproto、cryptography | https://www.apache.org/licenses/LICENSE-2.0.txt |
| `PSF.txt` | Python Software Foundation License | defusedxml | https://docs.python.org/3/license.html |
| `SQLCipher-BSD.txt` | SQLCipher（Zetetic）BSD-style | SQLCipher Community Edition | https://github.com/sqlcipher/sqlcipher/blob/master/LICENSE |

> **TAK Server（GPLv3）不列於此**：其不隨本產品散布（見 NOTICE「互通對接」段），
> 由使用者自行向 tak.gov 取得，授權全文隨該 release 提供（`deploy/tak-server/release/tak/LICENSE.txt`）。

## 來源
見根目錄 `NOTICE`、`THIRD_PARTY_LICENSES.md`，與 issue #351。
