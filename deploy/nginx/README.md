# nginx 反向代理（C1-B）

ICS_Command Command 的 HTTPS 終結層。FastAPI 跑在 127.0.0.1:8000（HTTP loopback），nginx 對外 :443（HTTPS）。

## 為何需要 nginx 而非 uvicorn 直接 TLS

業界標準（Mozilla Server Side TLS、NIST SP 800-52 Rev. 2）：

| 面向 | uvicorn 直接 TLS | nginx 反代 |
|---|---|---|
| TLS 套件選擇 | uvicorn 受 Python ssl module 限制 | nginx + OpenSSL，業界主流 cipher 選擇彈性大 |
| 憑證 reload | 需重啟 app（中斷現有連線）| `nginx -s reload`，零中斷 |
| HTTP/2、HTTP/3 | 需額外設定 | 一行 `listen 443 ssl http2;` |
| Rate limit / IP allow-list | 需 app 層實作 | nginx 內建 module |
| Access log / OWASP CRS | 需自寫 middleware | nginx + ModSecurity（C2 階段加） |
| Tier 3 mTLS | uvicorn 不支援 | `ssl_verify_client on;` 一行切換 |

## 檔案結構

```
deploy/nginx/
├── README.md              ← 本檔
├── nginx.conf             ← 主設定（dev 用，prod 由 systemd 啟動的 /etc/nginx/）
├── conf.d/
│   ├── ssl-common.conf    ← TLS 1.2+1.3 cipher、protocol（業界基線，多個 server block 共用）
│   ├── security-headers.conf  ← HSTS / X-Frame / Referrer-Policy 等
│   ├── command.conf       ← Command FastAPI 反代（基礎，所有層級啟用）
│   └── tier3-mtls.conf.disabled  ← Phase 7 / Tier 3 mTLS 雙向憑證（C4 授權開啟）
└── start-dev.sh           ← Mac 開發機啟動（使用 deploy/step-ca/certs/command.ics.local/）
```

## 開發機啟動（Mac）

前置：
1. step-ca 已 init（`deploy/step-ca/init-mac-dev.sh`）
2. Command 憑證已簽（`deploy/step-ca/issue-cert.sh command.ics.local 127.0.0.1 localhost`）
3. nginx 已安裝：`brew install nginx`
4. FastAPI 已啟動於 :8000

啟動：
```bash
deploy/nginx/start-dev.sh
```

驗證：
```bash
curl --cacert ~/.step/certs/root_ca.crt https://command.ics.local/api/health
# 或瀏覽器開：https://command.ics.local/
```

## TLS 設定依據

- **Mozilla Intermediate** profile（2024-09 更新）作為基線
- TLS 1.2 為相容下限（規格 §C1-B 要求 1.2+），TLS 1.3 為偏好
- Cipher 選擇：移除所有 NULL/EXPORT/DES/3DES/RC4/MD5
- `ssl_prefer_server_ciphers off`：TLS 1.3 後由 client 選，server 強制無意義

## HSTS 策略

`max-age=31536000; includeSubDomains`（1 年 + 包含子域名）

**刻意不加 `preload`**：HSTS preload list 是瀏覽器內建清單，加入後不可逆，內網 dev 域名加進去會永久鎖死。商用部署有真實公網域名才考慮加。

## 客戶部署（C3-B 一鍵腳本會處理）

C1-B 階段只做 dev。C3-B 會把這份配置打包進 `install.sh`：
- 寫入 `/etc/nginx/conf.d/`
- systemd `nginx.service` 開機啟動
- ACME renew hook 在 cert 更新後自動 `nginx -s reload`
