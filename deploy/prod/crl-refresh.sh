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

# 4. 僅內容有變才覆蓋 + reload（step-ca 24h 內給同一份 → 無變動即跳過，省無謂 reload）
if [ -f "$CRL_OUT" ] && cmp -s "$P" "$CRL_OUT"; then
    log "OK CRL 無變動"
    exit 0
fi

mkdir -p "$(dirname "$CRL_OUT")"
if ! cp "$P" "$CRL_OUT"; then
    log "WRITE-FAIL 寫入 $CRL_OUT 失敗 → 保留 last-good"
    exit 1
fi
log "UPDATED CRL 已更新（$(wc -c <"$CRL_OUT")B）"

# 5. reload nginx（S4 掛上 ssl_crl 後才實際生效；nginx 未起 / 未設 ssl_crl 時 reload 失敗無害）
if nginx -s reload 2>/dev/null; then
    log "RELOADED nginx"
else
    log "reload skipped（nginx 未起或尚未設 ssl_crl）"
fi
