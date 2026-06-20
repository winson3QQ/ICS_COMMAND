#!/usr/bin/env bash
# issue-client-cert.sh — #275 wave 3：簽一張 ICS dashboard mTLS 裝置 client 憑證
#
# 用途：per-device 裝置憑證（mTLS 第二因子）。CN 即「裝置身份」，須與後端帳號的
#       憑證綁定一致（admin console：POST /api/admin/accounts/<user>/certs {cert_cn}）。
#
# 與 server 憑證（issue-cert.sh）不同處：
#   - 這是 *client* cert（extendedKeyUsage = clientAuth），瀏覽器/裝置出示用
#   - 額外打包 PKCS#12（.p12）供瀏覽器 / 行動裝置匯入（含私鑰）
#   - 複用 TAK pki/gen-device-dp.sh 的 step-ca offline 簽發 pattern（step-ca daemon 不需跑）
#
# 用法：
#   ./issue-client-cert.sh <cert-cn> [<output-dir>]
#
# 範例：
#   ./issue-client-cert.sh commander-phone-01
#   ./issue-client-cert.sh alice-laptop ~/Desktop
#
# 產出（<output-dir>/<cert-cn>/）：
#   client.crt  client.key  <cert-cn>.p12  root_ca.crt
#
# 前置：step CLI、openssl；step-ca 已 init（init-mac-dev.sh）。

set -euo pipefail

CERT_CN="${1:?用法：$0 <cert-cn> [<output-dir>]}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="${2:-$SCRIPT_DIR/certs/clients}/$CERT_CN"

# step-ca 路徑（與 gen-device-dp.sh 同慣例，可用 env 覆寫）
INT_CA="${STEP_INTERMEDIATE_CA:-$HOME/.step/certs/intermediate_ca.crt}"
ROOT_CA="${STEP_ROOT_CA:-$HOME/.step/certs/root_ca.crt}"
INT_KEY="${STEP_INTERMEDIATE_KEY:-$HOME/.step/secrets/intermediate_ca_key}"
PASS_FILE="${STEP_CA_PASSWORD_FILE:-$HOME/.step/secrets/password}"
P12_PASS="${ICS_CLIENT_P12_PASS:-icsclient}"

command -v step >/dev/null 2>&1 || { echo "✗ step CLI 未安裝：brew install step" >&2; exit 1; }
for f in "$INT_CA" "$ROOT_CA" "$INT_KEY" "$PASS_FILE"; do
  [[ -f "$f" ]] || { echo "✗ 找不到 step-ca 檔案：$f（先跑 init-mac-dev.sh）" >&2; exit 1; }
done

mkdir -p "$OUT_DIR"

# ── 1. 簽 client cert（offline，clientAuth；效期 90 天）─────────────────────
# --profile leaf 的預設 EKU 含 serverAuth+clientAuth；明確只要 clientAuth 用 --not-after + 預設即可。
step certificate create "$CERT_CN" \
  "$OUT_DIR/client.crt" "$OUT_DIR/client.key" \
  --ca "$INT_CA" --ca-key "$INT_KEY" \
  --ca-password-file "$PASS_FILE" \
  --not-after="2160h" --no-password --insecure --force

# fullchain（leaf + intermediate）— 部份 client 需補鏈
cat "$OUT_DIR/client.crt" "$INT_CA" > "$OUT_DIR/client-fullchain.crt"

# ── 2. 打包 PKCS#12（瀏覽器 / 行動裝置匯入用，含私鑰）──────────────────────
openssl pkcs12 -export \
  -in "$OUT_DIR/client-fullchain.crt" \
  -inkey "$OUT_DIR/client.key" \
  -name "$CERT_CN" \
  -out "$OUT_DIR/$CERT_CN.p12" \
  -passout "pass:$P12_PASS"

# root CA 一併輸出（nginx ssl_client_certificate truststore / 裝置信任用）
cp "$ROOT_CA" "$OUT_DIR/root_ca.crt"

chmod 600 "$OUT_DIR/client.key" "$OUT_DIR/$CERT_CN.p12"
chmod 644 "$OUT_DIR/client.crt" "$OUT_DIR/root_ca.crt"

echo ""
echo "✓ ICS client 憑證已產生：$OUT_DIR"
echo "  CN          : $CERT_CN"
echo "  p12         : $OUT_DIR/$CERT_CN.p12（匯入密碼：$P12_PASS）"
echo "  root CA     : $OUT_DIR/root_ca.crt（nginx ssl_client_certificate 用）"
echo "  效期        : 90 天（step-ca offline 簽）"
echo ""
echo "下一步："
echo "  1. 後端綁定（admin / sysadmin）："
echo "     POST /api/admin/accounts/<username>/certs  {\"cert_cn\":\"$CERT_CN\",\"label\":\"...\"}"
echo "  2. 把 $CERT_CN.p12 交給該裝置匯入（瀏覽器：設定→憑證→匯入）。"
echo "  3. 撤銷（裝置遺失）：DELETE /api/admin/accounts/<username>/certs/<cert_id>（即時失效）。"
