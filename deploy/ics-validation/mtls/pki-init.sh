#!/bin/sh
# pki-init.sh — #275 wave A：在 step-cli 容器內離線建 CA + 簽 nginx server 憑證
#
# 由 docker-compose.mtls.yml 的 pki-init one-shot service 跑（image: smallstep/step-cli）。
# 寫進 named volume /pki（CA 私鑰不進 ICS 後端，符 (ii) 離線簽決策）。idempotent：CA 已存在就跳過。
set -e

export STEPPATH=/pki
P=/pki
PASS="$P/secrets/password"
SERVER_DNS="${ICS_SERVER_DNS:-localhost}"

if [ ! -f "$P/certs/root_ca.crt" ]; then
  mkdir -p "$P/secrets"
  [ -f "$PASS" ] || (head -c 24 /dev/urandom | base64 | tr -d '\n' > "$PASS")
  step ca init --name "ICS mTLS Verify CA" --dns "$SERVER_DNS" --address :9000 \
    --provisioner admin --password-file "$PASS" --provisioner-password-file "$PASS" >/dev/null
  echo "[pki-init] CA 建立完成"
else
  echo "[pki-init] CA 已存在，跳過 init"
fi

# nginx 的 client-cert truststore：root + intermediate（配 ssl_verify_depth 2）
cat "$P/certs/root_ca.crt" "$P/certs/intermediate_ca.crt" > "$P/ca-bundle.crt"

# nginx server 憑證（每次起都重簽，確保未過期；offline 簽）
step certificate create "$SERVER_DNS" "$P/server.crt" "$P/server.key" \
  --ca "$P/certs/intermediate_ca.crt" --ca-key "$P/secrets/intermediate_ca_key" \
  --ca-password-file "$PASS" --san "$SERVER_DNS" --san localhost --san 127.0.0.1 \
  --not-after 2160h --no-password --insecure --force >/dev/null

# nginx 容器（root master）讀得到即可；key 不開全域讀
chmod 644 "$P/server.crt" "$P/ca-bundle.crt" "$P/certs/root_ca.crt"
chmod 640 "$P/server.key"
echo "[pki-init] server 憑證 + ca-bundle 就緒（CN/SAN=$SERVER_DNS）"
