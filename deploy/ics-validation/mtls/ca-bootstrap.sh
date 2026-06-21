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
PROV_PASS="${STEP_CA_PASSWORD:?STEP_CA_PASSWORD 必須設定（CA provisioner 密碼，勿用預設）}"  # #290 H6

# 1. ICS 後端線上發證憑據（provisioner 密碼 + root 指紋）
printf '%s' "$PROV_PASS" > /share/prov.pass
step certificate fingerprint /ca/certs/root_ca.crt > /share/fingerprint
FP="$(cat /share/fingerprint)"

# 2. nginx server 憑證：向 daemon provisioner 簽（本腳本不持 CA 鑰）
#    外網驗證：ICS_SERVER_SANS 帶你的公網 IP/網域（空白分隔），寫進 SAN → 手機連
#    https://<公網IP>/ 不跳憑證名稱不符。預設只 localhost/127.0.0.1（本機驗）。
#    #321 L9：server cert SAN 任何 TLS client 握手即可讀 → 預設「過濾 RFC1918 內網 IP」，
#    避免對外 cert 洩內網拓樸。內網 by-IP 存取走 split-DNS / 公網 hairpin；純 LAN/dev 確需把
#    內網 IP 寫進 cert，設 ICS_SERVER_SANS_ALLOW_PRIVATE=true 明確開（會印安全警告）。
step ca root /tmp/root.crt --ca-url "$CA" --fingerprint "$FP" -f >/dev/null

_is_rfc1918() {  # $1=SAN token；私有/link-local IPv4 回 0
  case "$1" in
    10.*|192.168.*|169.254.*) return 0 ;;
    172.1[6-9].*|172.2[0-9].*|172.3[01].*) return 0 ;;
    *) return 1 ;;
  esac
}
_allow_priv="${ICS_SERVER_SANS_ALLOW_PRIVATE:-false}"
SAN_ARGS="--san localhost --san 127.0.0.1"
_dropped=""
CN_SUBJECT="localhost"
for s in ${ICS_SERVER_SANS:-}; do
  if [ "$_allow_priv" != "true" ] && _is_rfc1918 "$s"; then
    _dropped="$_dropped $s"
    continue
  fi
  SAN_ARGS="$SAN_ARGS --san $s"
  [ "$CN_SUBJECT" = "localhost" ] && CN_SUBJECT="$s"   # CN = 第一個保留的對外 SAN
done
if [ -n "$_dropped" ]; then
  echo "[ca-bootstrap] ⚠ #321 L9：已從公網 server cert SAN 過濾內網 IP（防拓樸洩漏）:$_dropped" >&2
  echo "[ca-bootstrap]   → 內網 by-IP 連線將憑證名不符；確需請設 ICS_SERVER_SANS_ALLOW_PRIVATE=true" >&2
elif [ "$_allow_priv" = "true" ]; then
  for s in ${ICS_SERVER_SANS:-}; do
    _is_rfc1918 "$s" && { echo "[ca-bootstrap] ⚠ 安全：ALLOW_PRIVATE=true → 公網 cert 含內網 IP（$s），對外洩內網拓樸（#321 L9，僅限純 LAN/dev）" >&2; break; }
  done
fi
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
