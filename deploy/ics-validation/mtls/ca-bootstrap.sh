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
step ca root /tmp/root.crt --ca-url "$CA" --fingerprint "$FP" -f >/dev/null
step ca certificate localhost /pki/server.crt /pki/server.key \
  --provisioner "$PROV" --provisioner-password-file /share/prov.pass \
  --ca-url "$CA" --root /tmp/root.crt \
  --san localhost --san 127.0.0.1 --not-after 23h -f >/dev/null

# 3. nginx client-cert truststore = daemon root + intermediate（公開憑證，供 ssl_verify_depth 2）
cat /ca/certs/root_ca.crt /ca/certs/intermediate_ca.crt > /pki/ca-bundle.crt

chmod 644 /pki/server.crt /pki/ca-bundle.crt /share/prov.pass /share/fingerprint
chmod 640 /pki/server.key
echo "[ca-bootstrap] 單一 CA 收斂完成（nginx 與線上發證共用 daemon CA，fp=${FP%${FP#????????}}…）"
