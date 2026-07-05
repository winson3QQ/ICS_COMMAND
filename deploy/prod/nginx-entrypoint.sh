#!/bin/sh
# deploy/prod/nginx-entrypoint.sh — #232軌1-S3：nginx 啟動包裝。
#
# 背景跑 CRL 自刷新迴圈，前景交還**官方** nginx entrypoint（保留其 envsubst template 處理，
# 本 deploy 的 default.conf 走 template + NGINX_ENVSUBST_FILTER，不可繞過）。
#
# fail-safe：開機先抓一次 CRL；抓不到**不擋 nginx 啟動**（S3 階段 nginx 尚未 ssl_crl，無檔無妨；
#   S4 掛 ssl_crl 前會確保有 bootstrap 檔，見 S4）。之後每 CRL_REFRESH_INTERVAL 秒刷新一次。
set -u
CRL_REFRESH_INTERVAL="${CRL_REFRESH_INTERVAL:-14400}" # 預設 4h（<< step-ca cacheDuration，大 margin）

# 開機初次刷新（非致命：失敗僅記 log，不擋啟動）
sh /crl-refresh.sh || echo "[nginx-entrypoint] 初次 CRL 抓取失敗（保留既有/無檔，不擋啟動）"

# 背景週期刷新迴圈（reload 由 crl-refresh.sh 內部在有變動時觸發）
(
    while true; do
        sleep "$CRL_REFRESH_INTERVAL"
        sh /crl-refresh.sh || true
    done
) &

# 前景：官方 entrypoint（處理 templates → 啟 nginx）
exec /docker-entrypoint.sh nginx -g 'daemon off;'
