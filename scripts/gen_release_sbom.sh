#!/usr/bin/env bash
# gen_release_sbom.sh — release 時對「build 出的映像」產 CycloneDX SBOM（#351）
#
# 接 docs/PROCESS.md 步驟 9.5：乾淨 build 後、release commit 前執行。
# 對「映像本身」產（非 requirements.txt）→ 含 transitive + 實際安裝版本 + OS 套件，
# 對得上實際出貨。SBOM 綁「後端軌」（相依屬後端；純前端版升不產）。
# 工具 syft（Anchore）或 trivy（Aqua），皆非中國；安裝見 deploy/build-env.md。
#
# 用法：./scripts/gen_release_sbom.sh [image]      # image 預設 ics-command:dev
# 產物：sbom/releases/backend-v<APP_VERSION>.cdx.json（隨 release commit 進 main，由步驟 9 的 tag 釘版）

set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="${1:-ics-command:dev}"

# 後端版號 = SBOM 綁定軌（SoT：core/config.py APP_VERSION，見 CLAUDE.md 版號規則）
VER="$(grep -E '^APP_VERSION' "$REPO/command-dashboard/src/core/config.py" | head -1 | sed -E 's/.*"([0-9.]+)".*/\1/')"
[ -n "$VER" ] || { echo "錯誤：抓不到 APP_VERSION（core/config.py）" >&2; exit 1; }

OUT_DIR="$REPO/sbom/releases"
OUT="$OUT_DIR/backend-v${VER}.cdx.json"
mkdir -p "$OUT_DIR"

if command -v syft >/dev/null 2>&1; then
  syft "$IMAGE" -o "cyclonedx-json=$OUT"
elif command -v trivy >/dev/null 2>&1; then
  trivy image --quiet --format cyclonedx --output "$OUT" "$IMAGE"
else
  echo "錯誤：需要 syft 或 trivy（Anchore/Aqua，非中國）。安裝見 deploy/build-env.md。" >&2
  exit 1
fi

# 正規化 LF + 結尾換行（避免 pre-commit end-of-file / CRLF 衝突回滾，見 memory precommit-ruff-config-cwd 同類）
python3 - "$OUT" <<'PY'
import sys
f = sys.argv[1]
raw = open(f, "rb").read()
new = raw.replace(b"\r\n", b"\n")
if new and not new.endswith(b"\n"):
    new += b"\n"
open(f, "wb").write(new)
PY

# #419：另落 docker build context 內固定路徑，供 Dockerfile 烤入 → GET /api/sbom 服務。
# （gitignored build artifact；committed 的版本化紀錄為 repo 根 sbom/releases/。）
CTX_SBOM="$REPO/command-dashboard/sbom/current.cdx.json"
mkdir -p "$(dirname "$CTX_SBOM")"
cp "$OUT" "$CTX_SBOM"

echo "✅ SBOM → ${OUT#"$REPO"/}"
echo "   烤入用副本 → ${CTX_SBOM#"$REPO"/}（Dockerfile COPY → /app/sbom/ → GET /api/sbom）"
echo "   下一步：隨 release commit 一起 git add（步驟 9 打 backend-v${VER} tag 即釘版）。"
