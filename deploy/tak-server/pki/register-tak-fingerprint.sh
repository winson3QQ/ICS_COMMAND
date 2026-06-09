#!/usr/bin/env bash
# register-tak-fingerprint.sh — 把 client cert 的 SHA-256 fingerprint 註冊進 TAK Server
# 的 UserAuthenticationFile.xml（File backend，hot-reload 免 restart）。
#
# 背景（#177 L1 / cert-role 定案 #176）：
#   ICS 對 TAK 的 role 指派原本靠**手動編** UserAuthenticationFile.xml（P2-14 实测被安全
#   分類器擋＝高危 RBAC 寫入）。本腳本把它變成**受控、冪等、可重跑**的操作。
#   server role 一律 ROLE_USER（ICS 不需 ROLE_ADMIN，#176 实测定案）；mission 級角色
#   （readonly-subscriber / owner）由 mission 訂閱時帶，不在本檔。
#
# 用法：
#   register-tak-fingerprint.sh <cert.pem> <identifier> [group1 group2 ...]
#   register-tak-fingerprint.sh --apply <cert.pem> <identifier> [groups...]   # 真寫入
#
#   預設 **dry-run**（只印將寫入的 <User> 元素 + 算出的 fingerprint，不動檔）。
#   確認格式無誤後加 --apply 才落地。RBAC 寫入不容默默出錯，故預設不寫。
#
# 範例（兩張 mission 服務 cert）：
#   register-tak-fingerprint.sh ../../step-ca/certs/ics-mission-read/client.crt  ics-mission-read  __ANON__
#   register-tak-fingerprint.sh ../../step-ca/certs/ics-mission-write/client.crt ics-mission-write __ANON__
#
# 目標檔：$TAK_AUTH_FILE（預設 deploy/tak-server/release/tak/UserAuthenticationFile.xml；
#   release/ 是 bind-mount 進容器 /opt/tak 的同一份，改 host 檔即改容器檔，TAK 監看 hot-reload）。
#
# ⚠ 待你以 live TAK 的既有 working entry 確認的 3 點（#177 L1 open question）：
#   (a) fingerprint 正規化：colons? 大小寫?（FP_FORMAT 變數，預設 colon-uppercase = openssl 原樣）
#   (b) <User> 是否需 identifier 屬性、其值規則
#   (c) groupList 預設內容（__ANON__ 或專用 group）
#   存在既有 entry 時本腳本會印出供你比對；格式不符**先別 --apply**。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TAK_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"          # deploy/tak-server/
TAK_AUTH_FILE="${TAK_AUTH_FILE:-$TAK_DIR/release/tak/UserAuthenticationFile.xml}"

# fingerprint 正規化策略（(a) open question）。預設 colon-uppercase = openssl 原始輸出。
#   raw      → AB:CD:...:EF（openssl -fingerprint -sha256 原樣，去掉 "sha256 Fingerprint=" 前綴）
#   nocolon  → ABCD...EF（去冒號、保留大寫）
#   lower    → ab:cd:...:ef（小寫保冒號）
FP_FORMAT="${FP_FORMAT:-raw}"

command -v openssl >/dev/null || { echo "✗ 缺 openssl" >&2; exit 1; }

APPLY=0
if [[ "${1:-}" == "--apply" ]]; then APPLY=1; shift; fi
[[ $# -ge 2 ]] || { echo "用法：$0 [--apply] <cert.pem> <identifier> [groups...]" >&2; exit 1; }

CERT="$1"; IDENTIFIER="$2"; shift 2
# ★ 勿用 GROUPS：那是 bash 內建唯讀陣列（當前使用者的 OS group id），賦值不生效。
GROUP_LIST=("$@"); [[ ${#GROUP_LIST[@]} -gt 0 ]] || GROUP_LIST=(__ANON__)
[[ -f "$CERT" ]] || { echo "✗ 找不到 cert：$CERT" >&2; exit 1; }

# ── 算 SHA-256 fingerprint ──────────────────────────────────────────────────
RAW="$(openssl x509 -in "$CERT" -noout -fingerprint -sha256 | sed 's/^.*Fingerprint=//')"
case "$FP_FORMAT" in
  raw)     FP="$RAW" ;;
  nocolon) FP="${RAW//:/}" ;;
  lower)   FP="$(printf '%s' "$RAW" | tr 'A-Z' 'a-z')" ;;
  *)       echo "✗ 未知 FP_FORMAT=$FP_FORMAT（raw|nocolon|lower）" >&2; exit 1 ;;
esac

# ── 組 <User> 元素 ──────────────────────────────────────────────────────────
GROUP_XML=""
for g in "${GROUP_LIST[@]}"; do GROUP_XML+="<groupList>${g}</groupList>"; done
USER_XML="<User identifier=\"${IDENTIFIER}\" role=\"ROLE_USER\" fingerprint=\"${FP}\">${GROUP_XML}</User>"

echo "▸ cert        : $CERT"
echo "▸ identifier  : $IDENTIFIER"
echo "▸ fingerprint : $FP   (FP_FORMAT=$FP_FORMAT)"
echo "▸ groups      : ${GROUP_LIST[*]}"
echo "▸ 將寫入元素  : $USER_XML"
echo "▸ 目標檔      : $TAK_AUTH_FILE"
echo ""

# ── 既有檔比對（給你對格式）──────────────────────────────────────────────────
if [[ -f "$TAK_AUTH_FILE" ]]; then
  echo "── 既有 <User …fingerprint…> entry（供格式比對）──"
  grep -o '<User[^>]*fingerprint="[^"]*"[^>]*>' "$TAK_AUTH_FILE" | head -5 || echo "  （無既有 fingerprint entry）"
  echo ""
  if grep -q "fingerprint=\"${FP}\"" "$TAK_AUTH_FILE"; then
    echo "✓ 此 fingerprint 已存在於檔內，無需重複註冊（冪等）。"; exit 0
  fi
else
  echo "⚠ 目標檔不存在：$TAK_AUTH_FILE"
  echo "  （本機未 provision TAK release 屬正常；在跑 TAK 的機器上執行本腳本。）"
fi

if [[ $APPLY -eq 0 ]]; then
  echo ""
  echo "— DRY-RUN（未寫入）。格式確認無誤後加 --apply 落地。RBAC 寫入預設不自動執行。"
  exit 0
fi

# ── --apply：冪等 upsert（先備份，再在 </UserAuthenticationFile> 前插入）──────────
[[ -f "$TAK_AUTH_FILE" ]] || { echo "✗ 目標檔不存在，無法 --apply：$TAK_AUTH_FILE" >&2; exit 1; }
BAK="$TAK_AUTH_FILE.bak.$(openssl rand -hex 4)"
cp "$TAK_AUTH_FILE" "$BAK"
# 在閉合 tag 前插入新 <User>（保留縮排）。TAK File backend 監看此檔，存檔即 hot-reload。
if grep -q '</UserAuthenticationFile>' "$TAK_AUTH_FILE"; then
  awk -v ins="  $USER_XML" '/<\/UserAuthenticationFile>/{print ins} {print}' "$BAK" > "$TAK_AUTH_FILE"
else
  echo "✗ 目標檔無 </UserAuthenticationFile> 閉合 tag，結構非預期，中止（已備份 $BAK）" >&2
  exit 1
fi
echo "✓ 已註冊（備份：$BAK）。TAK File backend 應於數秒內 hot-reload。"
echo "  驗證：grep '$IDENTIFIER' '$TAK_AUTH_FILE'"
