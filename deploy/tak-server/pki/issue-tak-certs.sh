#!/usr/bin/env bash
# issue-tak-certs.sh — 用內網 step-ca 簽 TAK Server 憑證並產生 Java keystore（JKS）
#
# 取代官方 setup 的自簽 CA：TAK Server 改用 ICS_Command 既有 step-ca PKI（deploy/step-ca/），
# 與 dashboard / 未來 federation 同一條信任鏈。對應 reality check #98 drift 2。
#
# 產出（放進 CoreConfig.xml 指定路徑 /opt/tak/certs/files/）：
#   - takserver.jks         server 憑證 + 私鑰（step-ca 簽）
#   - truststore-root.jks   step-ca root（讓 TAK 信任同 CA 簽的 client / peer）
#   - fed-truststore.jks    federation 信任庫（裝 step-ca root，同官方 makeCert.sh 的 fed-truststore）
#       ★ 即使不開 federation 也必須有：messaging JVM 無條件部署 distributed-federation-manager
#         Ignite service，其 SSLConfig 載此檔；缺檔 → SSLContext 未初始化 → service 部署失敗
#         → messaging（Ignite server node）整個掛 → config/api 等 client 全斷線 → :8443 不綁（#101 根因#2）。
#
# 前置：
#   1. step-ca daemon 已啟動（deploy/step-ca/start-ca.sh）
#   2. 已 cp .env.example .env 並設好 TAK_HOSTNAME / TAK_KEYSTORE_PASS
#   3. 官方 release 已解壓到 ../release/（tak/certs/files/ 目錄存在或本腳本自建）
#   4. 本機有 openssl 與 keytool（JDK；無 JDK 可改用容器內 keytool，見 README）
#
# 用法：deploy/tak-server/pki/issue-tak-certs.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TAK_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"          # deploy/tak-server/
REPO_ROOT="$(cd "$TAK_DIR/../.." && pwd)"        # repo 根
ENV_FILE="$TAK_DIR/.env"
STEP_CA_DIR="$REPO_ROOT/deploy/step-ca"
ROOT_CA="${STEP_ROOT_CA:-$HOME/.step/certs/root_ca.crt}"
OUT_DIR="$TAK_DIR/release/tak/certs/files"

# ── 0. 前置檢查 ──────────────────────────────────────────────────────────
command -v openssl >/dev/null || { echo "✗ 缺 openssl" >&2; exit 1; }
command -v keytool >/dev/null || { echo "✗ 缺 keytool（需 JDK）；無 JDK 請改用容器內 keytool（README §憑證）" >&2; exit 1; }
[[ -f "$ENV_FILE" ]] || { echo "✗ 缺 .env（先 cp .env.example .env）" >&2; exit 1; }
[[ -f "$ROOT_CA" ]] || { echo "✗ 找不到 step-ca root：$ROOT_CA（先跑 deploy/step-ca/init-mac-dev.sh）" >&2; exit 1; }
[[ -x "$STEP_CA_DIR/issue-cert.sh" ]] || { echo "✗ 缺 $STEP_CA_DIR/issue-cert.sh" >&2; exit 1; }

# shellcheck disable=SC1090
set -a; . "$ENV_FILE"; set +a
: "${TAK_HOSTNAME:?.env 缺 TAK_HOSTNAME}"
: "${TAK_KEYSTORE_PASS:?.env 缺 TAK_KEYSTORE_PASS}"

echo "▸ 簽 TAK Server 憑證：CN=$TAK_HOSTNAME（經 step-ca）"

# ── 1. 用 step-ca 簽 server 憑證（RSA！含 SAN：hostname + localhost + 容器名 takserver）──
# ★ 必須 RSA：TAK api 的 jwkSource bean 用 server cert 的 key 建 JWT 簽章源，寫死轉型 RSAPublicKey。
#   若給 EC 憑證（step-ca 預設 ECDSA P-256）→ ClassCastException → api context 死 → 8443 不綁（#101）。
#   故此處直呼 step ca certificate 帶 --kty RSA（不走 issue-cert.sh 的 EC 預設）。
OUT_CERT_DIR="$STEP_CA_DIR/certs/$TAK_HOSTNAME"
mkdir -p "$OUT_CERT_DIR"
CERT_PEM="$OUT_CERT_DIR/cert.pem"
KEY_PEM="$OUT_CERT_DIR/key.pem"
step ca certificate "$TAK_HOSTNAME" "$CERT_PEM" "$KEY_PEM" \
  --provisioner="admin@ics.local" --password-file="$HOME/.step/secrets/password" \
  --kty RSA --size 2048 \
  --san "$TAK_HOSTNAME" --san localhost --san takserver --san 127.0.0.1 --force
chmod 600 "$KEY_PEM"; chmod 644 "$CERT_PEM"
[[ -f "$CERT_PEM" && -f "$KEY_PEM" ]] || { echo "✗ step-ca 未產出憑證" >&2; exit 1; }

# ── 2. PEM → PKCS12 → JKS（takserver.jks）──────────────────────────────────
mkdir -p "$OUT_DIR"
TMP_P12="$(mktemp -t takserver.XXXXXX.p12)"
trap 'rm -f "$TMP_P12"' EXIT

openssl pkcs12 -export \
  -in "$CERT_PEM" -inkey "$KEY_PEM" \
  -certfile "$ROOT_CA" \
  -name takserver \
  -out "$TMP_P12" -passout "pass:$TAK_KEYSTORE_PASS"

rm -f "$OUT_DIR/takserver.jks"
keytool -importkeystore -noprompt \
  -srckeystore "$TMP_P12" -srcstoretype PKCS12 -srcstorepass "$TAK_KEYSTORE_PASS" \
  -destkeystore "$OUT_DIR/takserver.jks" -deststoretype JKS -deststorepass "$TAK_KEYSTORE_PASS"

# ── 3. truststore-root.jks（信任 step-ca root → 同 CA 簽的 client/peer 都被信任）──
rm -f "$OUT_DIR/truststore-root.jks"
keytool -importcert -noprompt -alias step-ca-root \
  -file "$ROOT_CA" \
  -keystore "$OUT_DIR/truststore-root.jks" -storetype JKS -storepass "$TAK_KEYSTORE_PASS"

# ── 4. fed-truststore.jks（CoreConfig <federation-server> 引用，messaging 無條件部署需要）──
# ★ 缺此檔 = #101 根因#2：messaging 部署 distributed-federation-manager Ignite service 時
#   SSLConfig 載 fed-truststore.jks 失敗 → SSLContext 未初始化 → service 部署失敗 → messaging 掛
#   → Ignite server node 死 → config/api client 全 disconnect → api 卡 federation bean → :8443 不綁。
# 內容 = step-ca root（單 CA PoC 下與 truststore-root 同；federation 對端若用不同 CA，P2-07 再加匯入）。
rm -f "$OUT_DIR/fed-truststore.jks"
keytool -importcert -noprompt -alias step-ca-root \
  -file "$ROOT_CA" \
  -keystore "$OUT_DIR/fed-truststore.jks" -storetype JKS -storepass "$TAK_KEYSTORE_PASS"

echo "✓ 已產出："
echo "    $OUT_DIR/takserver.jks"
echo "    $OUT_DIR/truststore-root.jks"
echo "    $OUT_DIR/fed-truststore.jks"
echo ""
echo "下一步："
echo "  1. 確認 release/tak/CoreConfig.xml 的 <tls> keystoreFile 指向 certs/files/takserver.jks，"
echo "     keystorePass/truststorePass = $TAK_KEYSTORE_PASS（官方 default 'atakatak'）。"
echo "  2. federation（P2-07）與外部 TAK 對端互通時 → 把對端 CA / peer cert 也匯入 fed-truststore.jks。"
echo ""
echo "⚠ 效期警告（reality check #98 drift 2）：step-ca 預設簽 24h，TAK 長跑服務不可行。"
echo "  簽前先把 ~/.step/config/ca.json 的 provisioner.claims 加："
echo '      "maxTLSCertDuration": "2160h", "defaultTLSCertDuration": "2160h"   （90 天）'
echo "  並重啟 step-ca。否則每天要 renew。"
