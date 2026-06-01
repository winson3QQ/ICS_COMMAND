#!/bin/bash
# _basemap_lib.sh — 底圖整備腳本共用函式（provision / preflight / publish 皆 source 本檔）
#
# 設計：底圖 taiwan.pmtiles 是 gitignored deploy artifact；版控的單一真相是
# command-dashboard/basemap.manifest.json（version + sha256 + release）。
# 本 lib 提供：repo 定位、manifest 讀取、跨平台 SHA256。
# 見 docs/design/POLICY.md「底圖資料來源與 build recipe」。

# REPO_ROOT = 含 scripts/ 的 repo 根（本檔在 scripts/ 下）
BASEMAP_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$BASEMAP_LIB_DIR/.." && pwd)"
MANIFEST="$REPO_ROOT/command-dashboard/basemap.manifest.json"

# 跨平台 python（Linux/Mac 多為 python3；Windows git-bash 多為 python）
PY="$(command -v python3 || command -v python || true)"

_die() { echo "✗ $*" >&2; exit 1; }

[ -n "$PY" ] || _die "找不到 python3 / python（manifest 解析需要）"
[ -f "$MANIFEST" ] || _die "manifest 不存在：$MANIFEST"

# mf_get <dotted.key> — 讀 manifest 欄位（支援巢狀，如 sources.github_repo）
mf_get() {
  "$PY" - "$MANIFEST" "$1" <<'PYEOF'
import json, sys
try:
    m = json.load(open(sys.argv[1], encoding="utf-8"))
    cur = m
    for part in sys.argv[2].split("."):
        cur = cur[part]
except (KeyError, TypeError):
    sys.stderr.write("manifest 缺欄位：%s\n" % sys.argv[2]); sys.exit(1)
except Exception as e:  # JSON 壞 / 檔讀不到
    sys.stderr.write("manifest 解析失敗：%s\n" % e); sys.exit(1)
print(cur)
PYEOF
}

# sha256_of <file> — 跨平台 SHA256（小寫 hex）
sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    "$PY" - "$1" <<'PYEOF'
import hashlib, sys
h = hashlib.sha256()
with open(sys.argv[1], "rb") as f:
    for b in iter(lambda: f.read(1 << 20), b""):
        h.update(b)
print(h.hexdigest())
PYEOF
  fi
}

# 小寫正規化（SHA 比對不分大小寫）
lc() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }

# 解析常用欄位為環境變數
bm_load() {
  BM_TARGET_REL="$(mf_get target)"
  BM_TARGET="$REPO_ROOT/$BM_TARGET_REL"
  BM_VERSION="$(mf_get version)"
  BM_SHA="$(lc "$(mf_get sha256)")"
  BM_TAG="$(mf_get release_tag)"
  BM_ASSET="$(mf_get release_asset)"
  BM_GH_REPO="$(mf_get sources.github_repo)"
  BM_CB_REPO="$(mf_get sources.codeberg_repo)"
  BM_CB_BASE="$(mf_get sources.codeberg_base)"
}
