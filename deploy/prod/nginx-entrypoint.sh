#!/bin/sh
# deploy/prod/nginx-entrypoint.sh — #232軌1-S3/S4：nginx 啟動包裝。
#
# 背景跑 CRL 自刷新迴圈，前景交還**官方** nginx entrypoint（保留其 envsubst template 處理，
# 本 deploy 的 default.conf 走 template + NGINX_ENVSUBST_FILTER，不可繞過）。
#
# fail-safe（S4 核心）：nginx.conf `include /crl-data/ssl_crl.conf`——本檔啟動前**必保證存在**：
#   初次抓到 CRL → crl-refresh 寫入啟用行（`ssl_crl ...;`）；抓不到 → 補**空檔**（nginx 照起、
#   退回 App 層擋，**絕不因缺 CRL 讓 nginx 起不來或鎖死全站**）；之後背景刷新成功會自動啟用 + reload。
set -u
CRL_REFRESH_INTERVAL="${CRL_REFRESH_INTERVAL:-14400}" # 預設 4h（<< step-ca cacheDuration，大 margin）
SSL_CRL_CONF="${SSL_CRL_CONF:-/crl-data/ssl_crl.conf}"

# 開機初次刷新（非致命：失敗僅記 log，不擋啟動）
sh /crl-refresh.sh || echo "[nginx-entrypoint] 初次 CRL 抓取失敗（保留既有/無檔，不擋啟動）"

# S4 fail-safe：確保 include 目標存在（初次無 CRL → 空檔，nginx 起得來但不掛 ssl_crl）
if [ ! -f "$SSL_CRL_CONF" ]; then
    mkdir -p "$(dirname "$SSL_CRL_CONF")"
    : >"$SSL_CRL_CONF"
    echo "[nginx-entrypoint] CRITICAL 初次無 CRL → ssl_crl 暫停用（退 App 層），待背景刷新啟用"
fi

# 背景週期刷新迴圈（reload 由 crl-refresh.sh 內部在有變動時觸發）
(
    while true; do
        sleep "$CRL_REFRESH_INTERVAL"
        sh /crl-refresh.sh || true
    done
) &

# 前景：官方 entrypoint（處理 templates → 啟 nginx）
exec /docker-entrypoint.sh nginx -g 'daemon off;'
