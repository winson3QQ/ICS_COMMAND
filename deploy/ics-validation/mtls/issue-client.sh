#!/bin/sh
# issue-client.sh — #275 wave A：在 step-cli 容器內離線簽一張 per-device client 憑證 + p12
#
# 用法（host 端，Windows Git Bash / PowerShell 皆可，經 docker compose run）：
#   docker compose -f docker-compose.mtls.yml run --rm \
#     -e CERT_CN=commander-phone-01 issue-client
#
# 產出寫進 named volume /pki/clients/<CN>/ 與綁定 host 的 ./out（compose 掛載）：
#   <CN>.p12（瀏覽器/裝置匯入，含私鑰）、root_ca.crt（信任用）。
# 匯入後到 admin 面板（或 API）綁定 CN ↔ 帳號才生效。見 README.md。
set -e

export STEPPATH=/pki
P=/pki
PASS="$P/secrets/password"
CN="${CERT_CN:?需設 CERT_CN（裝置憑證 CN，例 commander-phone-01）}"
P12_PASS="${ICS_CLIENT_P12_PASS:-icsclient}"
OUT="/out/$CN"

[ -f "$P/secrets/intermediate_ca_key" ] || { echo "✗ CA 未初始化，先 docker compose up pki-init" >&2; exit 1; }
mkdir -p "$OUT"

step certificate create "$CN" "$OUT/client.crt" "$OUT/client.key" \
  --ca "$P/certs/intermediate_ca.crt" --ca-key "$P/secrets/intermediate_ca_key" \
  --ca-password-file "$PASS" --not-after 2160h --no-password --insecure --force >/dev/null

# fullchain（leaf + intermediate）打進 p12，部份 client 需補鏈
cat "$OUT/client.crt" "$P/certs/intermediate_ca.crt" > "$OUT/client-fullchain.crt"
step certificate p12 "$OUT/$CN.p12" "$OUT/client-fullchain.crt" "$OUT/client.key" \
  --password-file <(echo "$P12_PASS") >/dev/null 2>&1 || \
  openssl pkcs12 -export -in "$OUT/client-fullchain.crt" -inkey "$OUT/client.key" \
    -name "$CN" -out "$OUT/$CN.p12" -passout "pass:$P12_PASS" >/dev/null 2>&1
cp "$P/certs/root_ca.crt" "$OUT/root_ca.crt"
chmod 644 "$OUT"/*.p12 "$OUT/root_ca.crt"

echo "✓ client 憑證已簽：$OUT/$CN.p12（匯入密碼：$P12_PASS）"
echo "  → 匯入裝置/瀏覽器後，到 admin 面板綁定：CN=$CN ↔ 帳號"
