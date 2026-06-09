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
# step-ca 是雙層 PKI（root→intermediate→leaf）。intermediate 路徑頂部統一定義：
# truststore（section 3/4）與 client 簽發（section 5）都要用，故上提避免重複/漂移。
STEP_INT_CA="${STEP_INTERMEDIATE_CA:-$HOME/.step/certs/intermediate_ca.crt}"
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

# SAN 清單：base = hostname + 容器內慣用名。
# ★ 裝置（iTAK/ATAK）連線時做**嚴格 hostname/IP 驗證**：連的位址必須在 server cert SAN 內，
#   否則 `IP address mismatch / not valid for '<addr>'` → disconnected（#170 缺口 2 實證）。
# ★ doctrine：
#     prod  → TAK_HOSTNAME 設**真實 FQDN**（cert 綁名、DNS 管 IP；公網 IP 浮動/failover 免重簽）。
#     dev/LAN（無 DNS）→ 用 TAK_EXTRA_SANS 補裝置可達的 LAN IP / 額外名稱（逗號分隔）。
#   IP-SAN 僅限 dev；勿在 prod 把浮動 IP 寫進 cert。
SAN_ARGS=(--san "$TAK_HOSTNAME" --san localhost --san takserver --san 127.0.0.1)
if [[ -n "${TAK_EXTRA_SANS:-}" ]]; then
  IFS=',' read -ra _EXTRA_SANS <<< "$TAK_EXTRA_SANS"
  for _san in "${_EXTRA_SANS[@]}"; do
    _san="${_san#"${_san%%[![:space:]]*}"}"   # 去前導空白
    _san="${_san%"${_san##*[![:space:]]}"}"   # 去尾隨空白
    [[ -n "$_san" ]] && SAN_ARGS+=(--san "$_san")
  done
  echo "  額外 SAN（TAK_EXTRA_SANS）：$TAK_EXTRA_SANS"
fi
step ca certificate "$TAK_HOSTNAME" "$CERT_PEM" "$KEY_PEM" \
  --provisioner="admin@ics.local" --password-file="$HOME/.step/secrets/password" \
  --kty RSA --size 2048 \
  "${SAN_ARGS[@]}" --force
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

# ── 3. truststore-root.jks（信任 step-ca → 同 CA 簽的 client/peer 都被信任）──
# ★ 必須同時匯 root **+ intermediate**（#170 缺口 1）：step-ca 雙層，client（iTAK 等）通常只送
#   **leaf**（不附 intermediate）。truststore 若只有 root → server 建不出 leaf→intermediate→root 鏈
#   → `SSLHandshakeException: peer not verified` → client disconnected（dogfood 實證）。
rm -f "$OUT_DIR/truststore-root.jks"
keytool -importcert -noprompt -alias step-ca-root \
  -file "$ROOT_CA" \
  -keystore "$OUT_DIR/truststore-root.jks" -storetype JKS -storepass "$TAK_KEYSTORE_PASS"
[[ -f "$STEP_INT_CA" ]] || { echo "✗ 找不到 step-ca intermediate：$STEP_INT_CA（缺它 client leaf-only 驗證會失敗）" >&2; exit 1; }
keytool -importcert -noprompt -alias step-ca-intermediate \
  -file "$STEP_INT_CA" \
  -keystore "$OUT_DIR/truststore-root.jks" -storetype JKS -storepass "$TAK_KEYSTORE_PASS"

# ── 4. fed-truststore.jks（CoreConfig <federation-server> 引用，messaging 無條件部署需要）──
# ★ 缺此檔 = #101 根因#2：messaging 部署 distributed-federation-manager Ignite service 時
#   SSLConfig 載 fed-truststore.jks 失敗 → SSLContext 未初始化 → service 部署失敗 → messaging 掛
#   → Ignite server node 死 → config/api client 全 disconnect → api 卡 federation bean → :8443 不綁。
# 內容 = step-ca root + intermediate（同 section 3 理由：對端 peer 送 leaf-only 時要靠
#   intermediate 補鏈才驗得過；單 CA PoC 下與 truststore-root 同。對端若用不同 CA，P2-07 再加匯入）。
rm -f "$OUT_DIR/fed-truststore.jks"
keytool -importcert -noprompt -alias step-ca-root \
  -file "$ROOT_CA" \
  -keystore "$OUT_DIR/fed-truststore.jks" -storetype JKS -storepass "$TAK_KEYSTORE_PASS"
keytool -importcert -noprompt -alias step-ca-intermediate \
  -file "$STEP_INT_CA" \
  -keystore "$OUT_DIR/fed-truststore.jks" -storetype JKS -storepass "$TAK_KEYSTORE_PASS"

# ── 5. client 服務憑證（離線簽 fullchain）──────────────────────────────────────
# ★ :8089/:8443 mTLS 需 client 憑證。改用**離線**簽（step certificate create，不靠 daemon）——
#   step-ca daemon 預設聽 :8443 會跟 TAK web tier 撞，離線簽 daemon 不在也能跑（#106 實測）。
# ★ 輸出 **fullchain（leaf+intermediate）**：TAK truststore 只有 root，client 只送 leaf →
#   `peer not verified`（#106 log 實證）；須 leaf+intermediate 補齊鏈才握手過。
# ★ client cert 可 EC（不像 server 的 jwkSource 寫死 RSA）。streaming 只需 CA-trusted
#   fullchain，**不需** UserManager enroll（enroll 是 :8443 web UI admin 才要）。
# ★ 簽三張（#177 L1 / cert-role 定案 #176）：
#     cop-subscriber    :8089 CoT streaming 被動收（P2-03 #107，無 mission 角色）
#     ics-mission-read  :8443 Marti 讀（mission readonly-subscriber → P2-14 resync）
#     ics-mission-write :8443 Marti 寫（mission owner → P2-13 權威增刪）
#   三張 server role 皆 ROLE_USER，差在 mission 級角色——由 register-tak-fingerprint.sh
#   把 fingerprint 註冊進 UserAuthenticationFile.xml（本腳本只簽，不碰 auth 檔）。
# STEP_INT_CA 已在頂部定義（section 3/4 truststore 也用）。
STEP_INT_KEY="${STEP_INTERMEDIATE_KEY:-$HOME/.step/secrets/intermediate_ca_key}"
STEP_PASS_FILE="${STEP_CA_PASSWORD_FILE:-$HOME/.step/secrets/password}"

# 簽一張 client fullchain cert：sign_client_cert <cn> → 產 client.crt/key + client-fullchain.crt
sign_client_cert() {
  local cn="$1"
  local dir="$STEP_CA_DIR/certs/$cn"
  mkdir -p "$dir"
  step certificate create "$cn" "$dir/client.crt" "$dir/client.key" \
    --ca "$STEP_INT_CA" --ca-key "$STEP_INT_KEY" --ca-password-file "$STEP_PASS_FILE" \
    --not-after="${TAK_CLIENT_CERT_DURATION:-2160h}" --no-password --insecure --force
  chmod 600 "$dir/client.key"
  # fullchain = leaf + intermediate（補齊 TAK 端信任鏈：truststore 只有 root）
  cat "$dir/client.crt" "$STEP_INT_CA" > "$dir/client-fullchain.crt"
}

CLIENT_CNS=(cop-subscriber ics-mission-read ics-mission-write)
if [[ -f "$STEP_INT_CA" && -f "$STEP_INT_KEY" && -f "$STEP_PASS_FILE" ]]; then
  for cn in "${CLIENT_CNS[@]}"; do
    sign_client_cert "$cn"
  done
  CLIENT_NOTE="    cop-subscriber → TAK_CLIENT_CERT/KEY（:8089 streaming）
    ics-mission-read  → TAK_MARTI_READ_CERT/KEY（mission readonly-subscriber）
    ics-mission-write → TAK_MARTI_WRITE_CERT/KEY（mission owner）
    （各目錄 client-fullchain.crt + client.key，路徑 $STEP_CA_DIR/certs/<cn>/）"
else
  CLIENT_NOTE="    ⚠ client 憑證跳過：找不到 intermediate CA/key/password（$STEP_INT_CA）"
fi

echo "✓ 已產出："
echo "    $OUT_DIR/takserver.jks"
echo "    $OUT_DIR/truststore-root.jks"
echo "    $OUT_DIR/fed-truststore.jks"
echo "$CLIENT_NOTE"
echo ""
echo "下一步："
echo "  0. dashboard 訂閱 :8089：設 env TAK_ENABLED=true、TAK_COT_URL=tls://<host>:8089、"
echo "     TAK_CLIENT_CERT=<cop-subscriber/client-fullchain.crt>、TAK_CLIENT_KEY=<cop-subscriber/client.key>、"
echo "     TAK_CAFILE=$ROOT_CA（驗 server 憑證；不設則須 TAK_ALLOW_INSECURE_TLS=true，有 MITM 風險）。"
echo "  0b. Marti REST 讀寫（#177 L1）：先用 register-tak-fingerprint.sh 把 mission cert 註冊進"
echo "      UserAuthenticationFile.xml（hot-reload），再設 TAK_MARTI_READ_CERT/KEY、TAK_MARTI_WRITE_CERT/KEY。"
echo "  1. 確認 release/tak/CoreConfig.xml 的 <tls> keystoreFile 指向 certs/files/takserver.jks，"
echo "     keystorePass/truststorePass = $TAK_KEYSTORE_PASS（官方 default 'atakatak'）。"
echo "  2. federation（P2-07）與外部 TAK 對端互通時 → 把對端 CA / peer cert 也匯入 fed-truststore.jks。"
echo ""
echo "⚠ 效期警告（reality check #98 drift 2）：step-ca 預設簽 24h，TAK 長跑服務不可行。"
echo "  簽前先把 ~/.step/config/ca.json 的 provisioner.claims 加："
echo '      "maxTLSCertDuration": "2160h", "defaultTLSCertDuration": "2160h"   （90 天）'
echo "  並重啟 step-ca。否則每天要 renew。"
