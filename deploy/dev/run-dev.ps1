# deploy/dev/run-dev.ps1 — ICS 儀表板 dev 迴圈（容器、熱重載、接同一台活 TAK）
#
# 設計：複用 prod build 的 ics-command:dev image（不用 host 裝 python），掛 live 原始碼
#   → uvicorn --reload 改檔即生效；接 deploy/prod 那台活著的容器化 takserver（同 icsnet）；
#   獨立 dev 資料卷（不碰 prod ics-data）；mTLS 關、直連 :8000 方便。
#
# 安全：只綁 127.0.0.1（這是公網 prod 主機，dev 無 mTLS，絕不可對外）。
# 前置：deploy/prod 棧已起（step-ca/takserver 在 icsnet）。憑證沿用 deploy/prod/tak-certs（dedicated CA）。
#
# 用法：  pwsh deploy/dev/run-dev.ps1        （Ctrl-C 停；改 command-dashboard/src 下任何檔即熱重載）
#   開瀏覽器：http://127.0.0.1:8000/static/commander_dashboard.html
#   API docs（dev 開）：http://127.0.0.1:8000/docs

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path "$PSScriptRoot\..\..").Path
$src  = Join-Path $repo 'command-dashboard\src'
$tc   = Join-Path $repo 'deploy\prod\tak-certs'

if (-not (docker network ls --format '{{.Name}}' | Select-String -SimpleMatch 'ics-prod_icsnet')) {
  Write-Error "找不到 ics-prod_icsnet —— 先起 deploy/prod 棧（docker compose -f deploy/prod/docker-compose.yml --profile tak up -d）"
}

Write-Host "啟動 ics-dev（熱重載，接活 TAK，只綁 127.0.0.1:8000）..." -ForegroundColor Cyan
docker run --rm -it --name ics-dev `
  --network ics-prod_icsnet `
  -p 127.0.0.1:8000:8000 `
  -v "${src}:/app/src" `
  -v "${tc}:/tak-certs:ro" `
  -v "ics-dev-data:/app/data" `
  -e ICS_ENV=dev `
  -e ICS_MTLS_REQUIRED=false `
  -e TAK_ENABLED=true `
  -e TAK_COT_URL=tls://takserver:8089 `
  -e TAK_CLIENT_CERT=/tak-certs/ics-cot.fullchain.pem `
  -e TAK_CLIENT_KEY=/tak-certs/ics-cot.key `
  -e TAK_CAFILE=/tak-certs/tak-ca.pem `
  -e TAK_MARTI_URL=https://takserver:8443 `
  -e TAK_MARTI_READ_CERT=/tak-certs/ics-marti-read.fullchain.pem `
  -e TAK_MARTI_READ_KEY=/tak-certs/ics-marti-read.key `
  -e TAK_MARTI_WRITE_CERT=/tak-certs/ics-marti-write.fullchain.pem `
  -e TAK_MARTI_WRITE_KEY=/tak-certs/ics-marti-write.key `
  --entrypoint uvicorn `
  ics-command:dev `
  main:app --host 0.0.0.0 --port 8000 --reload --reload-dir /app/src
