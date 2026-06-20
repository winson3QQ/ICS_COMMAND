#!/bin/sh
# ca-bootstrap.sh — #275 wave B-2：單一 CA 收斂（= step-ca daemon）
#
# 取代舊 pki-init（它另造一個 CA，與 daemon 不同 → 線上發的證 nginx 不認）。本腳本讓
# **nginx 與線上發證共用 daemon 這一個 CA**：
#   - 向 daemon（provisioner）簽 nginx server 憑證 → /pki（本腳本不碰 CA 鑰）
#   - nginx truststore = daemon root + intermediate（公開憑證）→ /pki/ca-bundle.crt
#   - 寫 ICS 後端線上發證要的 prov.pass + fingerprint → /share
#
# 掛載：ca-data:/ca:ro（讀 daemon 公開 root/intermediate）、pki:/pki、ca-share:/share
set -e

CA="${STEP_CA_URL:-https://step-ca:9000}"
PROV="${STEP_CA_PROVISIONER:-ics}"
PROV_PASS="${STEP_CA_PASSWORD:-icsprov}"

# 1. ICS 後端線上發證憑據（provisioner 密碼 + root 指紋）
printf '%s' "$PROV_PASS" > /share/prov.pass
step certificate fingerprint /ca/certs/root_ca.crt > /share/fingerprint
FP="$(cat /share/fingerprint)"

# 2. nginx server 憑證：向 daemon provisioner 簽（本腳本不持 CA 鑰）
#    外網驗證：ICS_SERVER_SANS 帶你的公網 IP/網域（空白分隔），寫進 SAN → 手機連
#    https://<公網IP>/ 不跳憑證名稱不符。預設只 localhost/127.0.0.1（本機驗）。
step ca root /tmp/root.crt --ca-url "$CA" --fingerprint "$FP" -f >/dev/null
SAN_ARGS="--san localhost --san 127.0.0.1"
for s in ${ICS_SERVER_SANS:-}; do SAN_ARGS="$SAN_ARGS --san $s"; done
CN_SUBJECT="$(printf '%s' "${ICS_SERVER_SANS:-localhost}" | awk '{print $1}')"
# shellcheck disable=SC2086
step ca certificate "$CN_SUBJECT" /pki/server.crt /pki/server.key \
  --provisioner "$PROV" --provisioner-password-file /share/prov.pass \
  --ca-url "$CA" --root /tmp/root.crt \
  $SAN_ARGS --not-after "${ICS_SERVER_CERT_DURATION:-23h}" -f >/dev/null
# ↑ 驗證棧 CA 預設 maxTLSCertDuration=24h → default 23h。公測長放：先把 CA provisioner
#   調 2160h（step ca provisioner update ics --x509-max-dur=2160h）再設 ICS_SERVER_CERT_DURATION=2160h。

# 3. nginx client-cert truststore = daemon root + intermediate（公開憑證，供 ssl_verify_depth 2）
cat /ca/certs/root_ca.crt /ca/certs/intermediate_ca.crt > /pki/ca-bundle.crt

chmod 644 /pki/server.crt /pki/ca-bundle.crt /share/prov.pass /share/fingerprint
chmod 640 /pki/server.key
echo "[ca-bootstrap] 單一 CA 收斂完成（nginx 與線上發證共用 daemon CA，fp=${FP%${FP#????????}}…）"
