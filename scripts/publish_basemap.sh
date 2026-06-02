#!/bin/bash
# publish_basemap.sh — 發佈者用：把（重 build 後的）底圖上傳 GitHub + Codeberg release，
# 並更新 basemap.manifest.json 供你 git PR。只在有網 + 有認證的主力機跑。
#
# 流程：
#   1. 算 SHA256 + size
#   2. GitHub release（gh）：建 tag + 上傳資產 taiwan-<version>.pmtiles
#   3. Codeberg release（Forgejo API + CODEBERG_TOKEN）：同上
#   4. 改寫 command-dashboard/basemap.manifest.json（version/sha/size/tag/asset）
#   5. 你 review 後 git add manifest → PR（merge 才算「現行版本」生效，序列化多機）
#
# 用法：
#   CODEBERG_TOKEN=xxxx ./scripts/publish_basemap.sh --version 20260601 \
#       [--file command-dashboard/static/tiles/taiwan.pmtiles]
#
# 注意：底圖本體（235MB）不進 git；本腳本只把它放到 release，manifest 才進 git。

set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_basemap_lib.sh"

VERSION=""; FILE="$REPO_ROOT/command-dashboard/static/tiles/taiwan.pmtiles"
while [ $# -gt 0 ]; do
  case "$1" in
    --version) VERSION="$2"; shift 2 ;;
    --file) FILE="$2"; shift 2 ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) _die "未知參數：$1" ;;
  esac
done

[ -n "$VERSION" ] || _die "需 --version <YYYYMMDD>（對齊 planet build 日期，見 POLICY.md provenance）"
[ -f "$FILE" ] || _die "底圖檔不存在：$FILE（請先用 POLICY.md recipe build）"

bm_load
TAG="basemap-$VERSION"
# 版本放在 release tag（basemap-$VERSION），asset 名沿用檔案 basename。
# gh release 的 asset name = 上傳檔 basename（#label 只改顯示、不改 name / 不影響 --pattern），
# 故 asset 必須叫 taiwan.pmtiles，否則 provision 的 --pattern 對不上（實測踩到）。
ASSET="taiwan.pmtiles"
SHA="$(lc "$(sha256_of "$FILE")")"
SIZE="$(wc -c < "$FILE" | tr -d ' ')"
echo "[資訊] $ASSET  sha256=$SHA  size=$SIZE"

# ── GitHub release（gh）──
if command -v gh >/dev/null 2>&1; then
  echo "[GitHub] release $TAG @ $BM_GH_REPO"
  if gh release view "$TAG" --repo "$BM_GH_REPO" >/dev/null 2>&1; then
    gh release upload "$TAG" "$FILE#$ASSET" --repo "$BM_GH_REPO" --clobber
  else
    gh release create "$TAG" "$FILE#$ASSET" --repo "$BM_GH_REPO" \
      --title "Basemap $VERSION" \
      --notes "taiwan.pmtiles ($VERSION). sha256=$SHA. © OpenStreetMap contributors (ODbL). build recipe 見 docs/design/POLICY.md。"
  fi
  echo "[GitHub] ✓"
else
  echo "[GitHub] ⚠ 無 gh，跳過 GitHub release"
fi

# ── Codeberg release（Forgejo API）──
if [ -n "${CODEBERG_TOKEN:-}" ]; then
  echo "[Codeberg] release $TAG @ $BM_CB_REPO"
  api="$BM_CB_BASE/api/v1/repos/$BM_CB_REPO"
  # token 走 --config + process substitution（printf 為 bash builtin）→ 不進 argv、不落磁碟，
  # 避免本機他人由 /proc/<pid>/cmdline 讀到 write-scoped token（review #62 security MED）。
  _cb_hdr() { printf 'header = "Authorization: token %s"\n' "$CODEBERG_TOKEN"; }
  rid="$(curl -fsS --config <(_cb_hdr) "$api/releases/tags/$TAG" 2>/dev/null \
        | "$PY" -c 'import json,sys;print(json.load(sys.stdin).get("id",""))' 2>/dev/null || true)"
  if [ -z "$rid" ]; then
    rid="$(curl -fsS -X POST --config <(_cb_hdr) -H "Content-Type: application/json" \
          -d "{\"tag_name\":\"$TAG\",\"name\":\"Basemap $VERSION\",\"body\":\"taiwan.pmtiles ($VERSION) sha256=$SHA. ODbL © OpenStreetMap contributors.\"}" \
          "$api/releases" | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["id"])')"
  fi
  [ -n "$rid" ] || _die "Codeberg release id 取得失敗"
  curl -fsS -X POST --config <(_cb_hdr) \
    -F "attachment=@$FILE;filename=$ASSET" \
    "$api/releases/$rid/assets?name=$ASSET" >/dev/null
  echo "[Codeberg] ✓"
else
  echo "[Codeberg] ⚠ 無 CODEBERG_TOKEN，跳過 Codeberg release（記得補上，否則只有單邊備援）"
fi

# ── 更新 manifest（供 PR）──
"$PY" - "$MANIFEST" "$VERSION" "$SHA" "$SIZE" "$TAG" "$ASSET" <<'PYEOF'
import json, sys
p = sys.argv[1]
m = json.load(open(p, encoding="utf-8"))
m["version"], m["sha256"], m["size_bytes"] = sys.argv[2], sys.argv[3], int(sys.argv[4])
m["release_tag"], m["release_asset"] = sys.argv[5], sys.argv[6]
json.dump(m, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
open(p, "a", encoding="utf-8").write("\n")
PYEOF

echo ""
echo "✓ 發佈完成。manifest 已更新為 v$VERSION。"
echo "  下一步（序列化「現行版本」）：git add command-dashboard/basemap.manifest.json && 開 PR"
echo "  同時更新 docs/design/POLICY.md 的 Provenance（build 日期/SHA）。"
