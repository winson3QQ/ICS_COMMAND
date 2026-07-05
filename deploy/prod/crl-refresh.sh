#!/bin/sh
# deploy/prod/crl-refresh.sh — #232軌1-S3：nginx 側 CRL 自刷新（fail-safe 優先）。
#
# 從 step-ca 抓 CRL(DER) → 驗證 → 轉 PEM → **僅成功且內容有變才覆蓋 + reload nginx**。
# 鎖死防護（核心設計目標）：抓取/驗證任一步失敗，一律**保留 last-good、不覆蓋、不 reload**，
#   並記 log 供監控。寧可 CRL 稍舊，也不因一次抓取失敗把過期/壞檔推給 nginx（→ OpenSSL 拒所有
#   client 證 = 全站 mTLS 鎖死）。step-ca 端另設 renewPeriod + 長 cacheDuration 讓 nginx 檔恆有
#   數日效期餘裕（見 deploy/prod docker-compose / ca.json；step-ca 須連續掛掉數日才可能逼近過期）。
#
# 不用 openssl（nginx:alpine 無）：PEM CRL 即 base64(DER) 包 BEGIN/END X509 CRL 標頭。
set -u
CRL_URL="${CRL_URL:-https://step-ca:9000/crl}"
CRL_OUT="${CRL_OUT:-/crl-data/crl.pem}"
SSL_CRL_CONF="${SSL_CRL_CONF:-/crl-data/ssl_crl.conf}" # #232軌1-S4：nginx include 的 ssl_crl 開關檔

log() { echo "[crl-refresh $(date -u +%FT%TZ)] $*"; }

D=$(mktemp) || exit 1
P=$(mktemp) || { rm -f "$D"; exit 1; }
trap 'rm -f "$D" "$P"' EXIT

# 1. fetch DER（step-ca 內網容器名；CRL 為公開撤銷清單、無敏感 → --no-check-certificate 可接受，
#    信任由「只取公開 CRL + 後續 DER/PEM 結構驗證」保證，非機密通道）
if ! wget -q -T 15 --no-check-certificate -O "$D" "$CRL_URL"; then
    log "FETCH-FAIL 抓 CRL 失敗 → 保留 last-good（不覆蓋、不 reload）"
    exit 1
fi

# 2. 驗證：非空 + DER SEQUENCE(0x30) 起頭（擋空回應 / 錯誤頁 / 半截檔）
if [ ! -s "$D" ] || [ "$(head -c1 "$D" | od -An -tx1 | tr -d ' ')" != "30" ]; then
    log "VALIDATE-FAIL CRL 非法（空/非 DER）→ 保留 last-good"
    exit 1
fi

# 3. DER→PEM（base64 包標頭，免 openssl）
if ! { echo "-----BEGIN X509 CRL-----"; base64 "$D"; echo "-----END X509 CRL-----"; } >"$P"; then
    log "CONVERT-FAIL DER→PEM 轉檔失敗 → 保留 last-good"
    exit 1
fi

CHANGED=0

# 4. 僅內容有變才覆蓋 crl.pem（step-ca 24h 內給同一份 → 無變動不重寫，省無謂 reload）
mkdir -p "$(dirname "$CRL_OUT")"
if [ ! -f "$CRL_OUT" ] || ! cmp -s "$P" "$CRL_OUT"; then
    if ! cp "$P" "$CRL_OUT"; then
        log "WRITE-FAIL 寫入 $CRL_OUT 失敗 → 保留 last-good"
        exit 1
    fi
    log "UPDATED CRL 已更新（$(wc -c <"$CRL_OUT")B）"
    CHANGED=1
fi

# 5. S4：確保 ssl_crl.conf 啟用（crl.pem 已驗證有效存在）——idempotent，缺/不符才寫。
#    fail-safe：本步只在「已成功取得有效 CRL」的路徑執行；fetch/驗證失敗會在前面提早 exit，
#    故**不會**在缺 CRL 時把 ssl_crl 打開（避免指向不存在檔 → nginx reload 失敗 / 起不來）。
DESIRED="ssl_crl $CRL_OUT;"
if [ ! -f "$SSL_CRL_CONF" ] || [ "$(cat "$SSL_CRL_CONF" 2>/dev/null)" != "$DESIRED" ]; then
    if printf '%s\n' "$DESIRED" >"$SSL_CRL_CONF"; then
        log "ssl_crl 啟用（include → $CRL_OUT）"
        CHANGED=1
    fi
fi

# 6. 有變動才 reload（crl.pem 或 ssl_crl.conf 任一改動）
if [ "$CHANGED" = "0" ]; then
    log "OK CRL 無變動"
elif nginx -s reload 2>/dev/null; then
    log "RELOADED nginx"
else
    log "reload skipped（nginx 未起——啟動時由 entrypoint 負責首次載入）"
fi
