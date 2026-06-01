#!/bin/bash
# provision_basemap.sh — 整備時把底圖側載到本機（在有網/有 USB 的基地跑，非場域）
#
# 取得 manifest 指定版本的 taiwan.pmtiles → 驗 SHA256 → atomic 放到 static/tiles/。
# 已是現行版本則跳過。場域離線零信任：場域機請改用 --from <USB>，勿在場域連網。
#
# 取得來源優先序：
#   --from <路徑>   本地檔 / USB（完全 air-gap，最高優先）
#   --url  <url>    指定 URL
#   （預設）        GitHub release → 失敗則 Codeberg release（對齊 C 方案雙 remote）
#
# 用法：
#   ./scripts/provision_basemap.sh
#   ./scripts/provision_basemap.sh --from /mnt/usb/taiwan-20260601.pmtiles
#   ./scripts/provision_basemap.sh --url http://nas.local/taiwan.pmtiles
#   ./scripts/provision_basemap.sh --force        # 即使已就緒也重抓
#
# private repo 認證：GitHub 走已登入的 gh；Codeberg 私有需 CODEBERG_TOKEN 環境變數。

set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_basemap_lib.sh"

FROM=""; URL=""; FORCE=false
while [ $# -gt 0 ]; do
  case "$1" in
    --from) FROM="$2"; shift 2 ;;
    --url)  URL="$2";  shift 2 ;;
    --force) FORCE=true; shift ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) _die "未知參數：$1" ;;
  esac
done

bm_load
mkdir -p "$(dirname "$BM_TARGET")"

# 已是現行版本 → 跳過（除非 --force）
if [ "$FORCE" != true ] && [ -f "$BM_TARGET" ]; then
  if [ "$(lc "$(sha256_of "$BM_TARGET")")" = "$BM_SHA" ]; then
    echo "✓ 已是現行版本 taiwan v$BM_VERSION（SHA 符），跳過。"
    exit 0
  fi
  echo "[偵測] 既有底圖 SHA 不符 manifest（v$BM_VERSION），重新取得…"
fi

TMP="$(mktemp "${TMPDIR:-/tmp}/taiwan.pmtiles.XXXXXX")"
cleanup() { rm -f "$TMP" 2>/dev/null || true; }
trap cleanup EXIT

acquire() {
  if [ -n "$FROM" ]; then
    echo "[取得] 從本地/USB：$FROM"
    [ -f "$FROM" ] || _die "--from 檔不存在：$FROM"
    cp "$FROM" "$TMP"
    return 0
  fi
  if [ -n "$URL" ]; then
    echo "[取得] 從 URL：$URL"
    curl -fL --retry 3 -o "$TMP" "$URL" || _die "下載失敗：$URL"
    return 0
  fi
  # 預設：GitHub release（gh）→ Codeberg release（curl）
  if command -v gh >/dev/null 2>&1; then
    echo "[取得] GitHub release $BM_TAG（$BM_GH_REPO）資產 $BM_ASSET …"
    if gh release download "$BM_TAG" --repo "$BM_GH_REPO" --pattern "$BM_ASSET" --output "$TMP" --clobber 2>/dev/null; then
      return 0
    fi
    echo "[退回] GitHub 取得失敗，改試 Codeberg…"
  else
    echo "[資訊] 無 gh，直接試 Codeberg…"
  fi
  local cb_url="$BM_CB_BASE/$BM_CB_REPO/releases/download/$BM_TAG/$BM_ASSET"
  echo "[取得] Codeberg release：$cb_url"
  if [ -n "${CODEBERG_TOKEN:-}" ]; then
    curl -fL --retry 3 -H "Authorization: token $CODEBERG_TOKEN" -o "$TMP" "$cb_url" \
      || _die "Codeberg 下載失敗（private 需正確 CODEBERG_TOKEN）"
  else
    curl -fL --retry 3 -o "$TMP" "$cb_url" \
      || _die "Codeberg 下載失敗（private repo 請設 CODEBERG_TOKEN，或改用 --from USB）"
  fi
}

acquire

# 驗 SHA — 不符一律中止，不安裝（防半截/竄改/拿錯版）
actual="$(lc "$(sha256_of "$TMP")")"
[ "$actual" = "$BM_SHA" ] || _die "SHA 不符，拒絕安裝。期望 $BM_SHA，實際 $actual"

# atomic 就位
mv -f "$TMP" "$BM_TARGET"
trap - EXIT
echo "✓ 底圖整備完成：$BM_TARGET_REL（taiwan v$BM_VERSION，SHA 符）"
