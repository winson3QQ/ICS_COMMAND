#!/usr/bin/env bash
# gen-device-dp.sh — 為 TAK 裝置生成 data package（step-ca offline 簽 client cert）
#
# 背景：TAK Server 的 truststore 已換成 step-ca PKI；TAK 內建 CA（:8446 enrollment）
# 或舊 ICS_DMAS Dev CA 簽的 client cert server 不認 → PEER_DID_NOT_RETURN_A_CERTIFICATE。
# 新裝置接入一律用此腳本，不走 :8446 enrollment。（2026-06-16 dogfood 定案）
#
# 用法：
#   ./pki/gen-device-dp.sh <callsign> [atak|aware] [output-dir]
#
#   atak  = ATAK Android（預設）— 嵌套 zip，cert 路徑用 Android 絕對路徑
#   aware = TAK Aware iOS      — flat zip，cert 路徑相對
#
# 範例：
#   ./pki/gen-device-dp.sh atak-phone-01
#   ./pki/gen-device-dp.sh 3QQ-AWARE aware
#   ./pki/gen-device-dp.sh atak-phone-01 atak ~/Desktop
#
# 前置：openssl、keytool（JDK）、step CLI、python3
# step-ca daemon 不需要跑（offline 簽）。
#
# 產出：<output-dir>/<callsign>-dp.zip

set -euo pipefail

# ── 參數 ──────────────────────────────────────────────────────────────────────
CALLSIGN="${1:?用法：$0 <callsign> [atak|aware] [output-dir]}"
MODE="${2:-atak}"          # atak | aware
OUT_DIR="${3:-$HOME/Desktop}"

[[ "$MODE" == "atak" || "$MODE" == "aware" ]] || {
  echo "✗ mode 必須是 atak 或 aware（輸入：$MODE）" >&2; exit 1
}

# ── step-ca 路徑 ──────────────────────────────────────────────────────────────
INT_CA="${STEP_INTERMEDIATE_CA:-$HOME/.step/certs/intermediate_ca.crt}"
ROOT_CA="${STEP_ROOT_CA:-$HOME/.step/certs/root_ca.crt}"
INT_KEY="${STEP_INTERMEDIATE_KEY:-$HOME/.step/secrets/intermediate_ca_key}"
PASS_FILE="${STEP_CA_PASSWORD_FILE:-$HOME/.step/secrets/password}"
P12_PASS="atakatak"

for f in "$INT_CA" "$ROOT_CA" "$INT_KEY" "$PASS_FILE"; do
  [[ -f "$f" ]] || { echo "✗ 找不到：$f" >&2; exit 1; }
done

# ── 取 server host（來自 .env，fallback 172.20.10.2）──────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/../.env"
TAK_HOST="172.20.10.2"
if [[ -f "$ENV_FILE" ]]; then
  _H="$(grep -E '^TAK_HOSTNAME=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d ' ')"
  [[ -n "$_H" ]] && TAK_HOST="$_H"
fi
TAK_PORT="8089"

echo "▸ callsign=$CALLSIGN  mode=$MODE  server=$TAK_HOST:$TAK_PORT"

# ── 1. 簽 client cert（offline）──────────────────────────────────────────────
CERT_DIR="$(mktemp -d)"
trap 'rm -rf "$CERT_DIR"' EXIT

step certificate create "$CALLSIGN" \
  "$CERT_DIR/client.crt" "$CERT_DIR/client.key" \
  --ca "$INT_CA" --ca-key "$INT_KEY" \
  --ca-password-file "$PASS_FILE" \
  --not-after="2160h" --no-password --insecure --force

# fullchain（leaf + intermediate）— TAK truststore 只有 root，client 需補鏈
cat "$CERT_DIR/client.crt" "$INT_CA" > "$CERT_DIR/client-fullchain.crt"

# ── 2. client cert → PKCS12 ──────────────────────────────────────────────────
openssl pkcs12 -export \
  -in "$CERT_DIR/client-fullchain.crt" \
  -inkey "$CERT_DIR/client.key" \
  -name "$CALLSIGN" \
  -out "$CERT_DIR/${CALLSIGN}.p12" \
  -passout "pass:$P12_PASS"

# ── 3. truststore PKCS12（step-ca root + intermediate）───────────────────────
keytool -importcert -noprompt -alias step-root \
  -file "$ROOT_CA" \
  -keystore "$CERT_DIR/truststore-root.p12" -storetype PKCS12 -storepass "$P12_PASS"
keytool -importcert -noprompt -alias step-intermediate \
  -file "$INT_CA" \
  -keystore "$CERT_DIR/truststore-root.p12" -storetype PKCS12 -storepass "$P12_PASS"

# ── 4. 組 data package ────────────────────────────────────────────────────────
WORK="$(mktemp -d)"
ZIP_OUT="$OUT_DIR/${CALLSIGN}-dp.zip"

_uuid() { python3 -c 'import uuid; print(uuid.uuid4())'; }
_uuidhex() { python3 -c 'import uuid; print(uuid.uuid4().hex)'; }

if [[ "$MODE" == "atak" ]]; then
  # ── ATAK Android：嵌套 zip（outer = Mission Package wrapper，inner = data package）
  INNER_SLOT="$(_uuidhex)"
  INNER_NAME="ICS_TAK_${CALLSIGN}.zip"

  # inner data package
  mkdir -p "$WORK/inner/MANIFEST" "$WORK/inner/$INNER_SLOT"
  cp "$CERT_DIR/${CALLSIGN}.p12"      "$WORK/inner/$INNER_SLOT/"
  cp "$CERT_DIR/truststore-root.p12"  "$WORK/inner/$INNER_SLOT/"

  cat > "$WORK/inner/$INNER_SLOT/preference.pref" << PREF
<?xml version='1.0' standalone='yes'?>
<preferences>
    <preference version="1" name="cot_streams">
        <entry key="count" class="class java.lang.Integer">1</entry>
        <entry key="description0" class="class java.lang.String">ICS_TAK_${CALLSIGN}</entry>
        <entry key="enabled0" class="class java.lang.Boolean">true</entry>
        <entry key="connectString0" class="class java.lang.String">${TAK_HOST}:${TAK_PORT}:ssl</entry>
    </preference>
    <preference version="1" name="com.atakmap.app_preferences">
        <entry key="displayServerConnectionWidget" class="class java.lang.Boolean">true</entry>
        <entry key="caLocation" class="class java.lang.String">/storage/emulated/0/atak/cert/truststore-root.p12</entry>
        <entry key="caPassword" class="class java.lang.String">atakatak</entry>
        <entry key="clientPassword" class="class java.lang.String">atakatak</entry>
        <entry key="certificateLocation" class="class java.lang.String">/storage/emulated/0/atak/cert/${CALLSIGN}.p12</entry>
    </preference>
</preferences>
PREF

  cat > "$WORK/inner/MANIFEST/manifest.xml" << MANIFEST
<MissionPackageManifest version="2"><Configuration><Parameter name="uid" value="$(_uuid)"/><Parameter name="name" value="ICS_TAK_${CALLSIGN}"/><Parameter name="onReceiveDelete" value="true"/></Configuration><Contents><Content ignore="false" zipEntry="${INNER_SLOT}/preference.pref"/><Content ignore="false" zipEntry="${INNER_SLOT}/truststore-root.p12"/><Content ignore="false" zipEntry="${INNER_SLOT}/${CALLSIGN}.p12"/></Contents></MissionPackageManifest>
MANIFEST

  # inner zip
  mkdir -p "$WORK/outer/MANIFEST" "$WORK/outer/$INNER_SLOT"
  (cd "$WORK/inner" && zip -q -r "$WORK/outer/$INNER_SLOT/$INNER_NAME" .)

  # outer wrapper
  cat > "$WORK/outer/MANIFEST/manifest.xml" << OUTERM
<MissionPackageManifest version="2"><Configuration><Parameter name="uid" value="$(_uuid)"/><Parameter name="name" value="ICS_TAK_${CALLSIGN}_CONFIG"/></Configuration><Contents><Content ignore="false" zipEntry="${INNER_SLOT}/${INNER_NAME}"/></Contents></MissionPackageManifest>
OUTERM

  rm -f "$ZIP_OUT"
  (cd "$WORK/outer" && zip -q -r "$ZIP_OUT" .)

else
  # ── TAK Aware iOS：flat zip（cert/pref/ 目錄，相對路徑）
  mkdir -p "$WORK/aware/MANIFEST" "$WORK/aware/cert" "$WORK/aware/pref"
  cp "$CERT_DIR/${CALLSIGN}.p12"      "$WORK/aware/cert/"
  cp "$CERT_DIR/truststore-root.p12"  "$WORK/aware/cert/"

  cat > "$WORK/aware/pref/config.pref" << PREF
<?xml version="1.0" standalone="yes"?>
<preferences>
    <preference version="1" name="com.atakmap.app_preferences">
        <entry key="displayServerConnectionWidget" class="class java.lang.Boolean">true</entry>
        <entry key="caPassword" class="class java.lang.String">atakatak</entry>
        <entry key="caLocation" class="class java.lang.String">cert/truststore-root.p12</entry>
        <entry key="certificatePassword" class="class java.lang.String">atakatak</entry>
        <entry key="certificateLocation" class="class java.lang.String">cert/${CALLSIGN}.p12</entry>
        <entry key="connectString0" class="class java.lang.String">${TAK_HOST}:${TAK_PORT}:ssl</entry>
        <entry key="count" class="class java.lang.Integer">1</entry>
    </preference>
</preferences>
PREF

  cat > "$WORK/aware/MANIFEST/manifest.xml" << MANIFEST
<MissionPackageManifest version="2">
   <Configuration>
      <Parameter name="uid" value="$(_uuid)"/>
      <Parameter name="name" value="TAK Aware - ${CALLSIGN}"/>
      <Parameter name="onReceiveDelete" value="false"/>
   </Configuration>
   <Contents>
      <Content ignore="false" zipEntry="cert/${CALLSIGN}.p12">
         <Parameter name="name" value="${CALLSIGN}.p12"/>
      </Content>
      <Content ignore="false" zipEntry="cert/truststore-root.p12">
         <Parameter name="name" value="truststore-root.p12"/>
      </Content>
      <Content ignore="false" zipEntry="pref/config.pref">
         <Parameter name="name" value="config.pref"/>
      </Content>
   </Contents>
</MissionPackageManifest>
MANIFEST

  rm -f "$ZIP_OUT"
  (cd "$WORK/aware" && zip -q -r "$ZIP_OUT" .)
fi

rm -rf "$WORK"

echo "✅ $ZIP_OUT"
echo "   callsign  : $CALLSIGN"
echo "   mode      : $MODE"
echo "   server    : $TAK_HOST:$TAK_PORT:ssl"
echo "   p12 密碼  : $P12_PASS"
echo "   client cert 效期 : 90 天（step-ca offline 簽）"
