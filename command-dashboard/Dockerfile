# command-dashboard/Dockerfile — ICS 指揮部後端容器化（驗證 / 佈署用）
# 設計：static/（factory default）烤進 image；data/（runtime user data）走 volume。
# 啟動方式對齊 setup.sh：從 src/ 跑 uvicorn main:app（main.py 用 src 為根的扁平 import）。

# ── #302 layer-1：前端 IP 保護（prod 才砍註解 + minify；dev 源碼保持可讀）──────────
# 逐檔 esbuild --minify（**不 bundle** → 保留 import 路徑/export 名、不動 42 個 dynamic import、
# CSP `script-src 'self'` safe、無 eval/inline）。砍掉 ~1394 行繁中設計註解（最大 IP 洩漏）。
# 只在 image build 跑 → repo 的 static/js 維持可讀（dev 不受影響）。esbuild=美國 npm，pinned。
FROM node:20-alpine AS jsmin
WORKDIR /jsmin
COPY static/js/ ./js/
RUN npx --yes esbuild@0.24.0 ./js/*.js --minify --legal-comments=none --outdir=./min --log-level=warning

FROM python:3.12-slim

# #275 wave B-2：step CLI（線上發證用——後端呼叫 step-ca daemon 簽 client 憑證，
# CA 鑰不進後端）。多階段從官方 smallstep/step-cli 取單一 binary（smallstep = 美國，非中國）。
COPY --from=smallstep/step-cli:latest /usr/local/bin/step /usr/local/bin/step

# 非 root 執行（對外服務最小權限）。--create-home：first-run admin token 寫在 $HOME/.ics/
RUN groupadd --gid 10001 ics && useradd --uid 10001 --gid ics --create-home --shell /usr/sbin/nologin ics

WORKDIR /app

# 相依先裝（layer cache）。slim + manylinux wheels 足夠（cryptography/pytak/takproto/aiohttp 皆有 amd64 wheel）。
COPY requirements.txt .
RUN pip install --no-cache-dir -U pip && pip install --no-cache-dir -r requirements.txt

# 程式 + factory default 靜態資源（不含 data/、不含巨大的 static/tiles，見 .dockerignore）
COPY src/ ./src/
COPY static/ ./static/
# #302：用 minified JS 覆蓋 raw（同檔名，import 路徑/dynamic import 不變）。HTML/CSS/seed 不動。
COPY --from=jsmin /jsmin/min/ ./static/js/
# #419：SBOM 烤入 /app/sbom/（release build 由 gen_release_sbom.sh 產 current.cdx.json；
# dev build 僅 .gitkeep → GET /api/sbom 回 404）。非 static（不走 nginx，經 app RBAC）。
COPY sbom/ ./sbom/

# runtime 資料夾（DATA_DIR = BASE_DIR/data）。掛 named volume；先建好並交給 ics 使用者，
# 新 named volume 首次使用會繼承此目錄 owner，確保非 root 可寫。
RUN mkdir -p /app/data /var/log/ics && chown -R ics:ics /app/data /var/log/ics

USER ics
WORKDIR /app/src

# Build 戳記（build 時 --build-arg ICS_BUILD_ID 注入；放最後 → 不影響上方相依/COPY layer cache）
ARG ICS_BUILD_ID=dev
ENV ICS_BUILD_ID=$ICS_BUILD_ID

EXPOSE 8000

# 健康檢查：打 /api/version（公開端點，回兩軌版號）
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/version', timeout=3).status==200 else 1)" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
