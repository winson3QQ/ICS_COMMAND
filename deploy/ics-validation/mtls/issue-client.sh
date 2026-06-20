#!/bin/sh
# issue-client.sh — #275：CLI 發 per-device client 憑證（走 step-ca daemon provisioner）
#
# 與面板「發憑證」同源（同一個 daemon CA）→ 簽出的證 nginx 認得。CA 鑰不離 daemon。
#
# 用法（host）：
#   docker compose -f docker-compose.mtls.yml run --rm -e CERT_CN=commander-phone-01 issue-client
# 產出 host 的 ./out/<CN>/<CN>.p12（匯入密碼預設 icsclient）+ root_ca.crt（信任用）。
# 匯入裝置後到 admin 面板「僅綁定」CN ↔ 帳號（或面板直接「發憑證」省掉本步）。
set -e

CA="${STEP_CA_URL:-https://step-ca:9000}"
PROV="${STEP_CA_PROVISIONER:-ics}"
CN="${CERT_CN:?需設 CERT_CN（裝置憑證 CN，例 commander-phone-01）}"
P12_PASS="${ICS_CLIENT_P12_PASS:-icsclient}"
OUT="/out/$CN"

[ -f /share/fingerprint ] || { echo "✗ ca-share 未就緒，先 docker compose up ca-bootstrap" >&2; exit 1; }
mkdir -p "$OUT"
FP="$(cat /share/fingerprint)"

step ca root "$OUT/root_ca.crt" --ca-url "$CA" --fingerprint "$FP" -f >/dev/null
step ca certificate "$CN" "$OUT/client.crt" "$OUT/client.key" \
  --provisioner "$PROV" --provisioner-password-file /share/prov.pass \
  --ca-url "$CA" --root "$OUT/root_ca.crt" --not-after "${ICS_CLIENT_CERT_DURATION:-23h}" -f >/dev/null
step certificate p12 "$OUT/$CN.p12" "$OUT/client.crt" "$OUT/client.key" \
  --password-file <(printf '%s' "$P12_PASS") >/dev/null
chmod 644 "$OUT/$CN.p12" "$OUT/root_ca.crt"

echo "✓ client 憑證已簽（daemon CA）：$OUT/$CN.p12（匯入密碼：$P12_PASS）"
echo "  → 匯入裝置/瀏覽器後，到 admin 面板「僅綁定」CN=$CN ↔ 帳號"
