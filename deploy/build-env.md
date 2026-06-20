# 本機 build 環境問題 — OneDrive 殘留 cloud reparse point

> 影響:**在 `C:\Users\yello\Desktop\ICS_COMMAND` 這份 working tree 直接 `docker build`(ICS 或 TAK image)會失敗**。
> 範圍:**只卡 `docker build`**;dev server(uvicorn)、git、pytest、vitest 全正常。
> 與程式碼/`.dockerignore` 無關——是檔案系統層的環境問題。

## 症狀
```
ERROR: failed to solve: invalid file request requirements.txt
（在 "[internal] load build context" 階段）
```

## 根因(已查證 2026-06-21)
repo 的檔案是**孤兒 cloud-files placeholder**:Windows Cloud Files API reparse point
(tag `IO_REPARSE_TAG_CLOUD_2` / `0x9000201a`,Tag value `Microsoft`),是**以前 repo 放在
OneDrive 同步時留下的殘渣**。

- 現在 `Desktop` 已**不**被 OneDrive 同步(`$env:OneDrive=C:\Users\yello\OneDrive`、
  `User Shell Folders\Desktop=C:\Users\yello\Desktop`、`SyncRootManager` 無此路徑註冊)
  → **OneDrive 確實已切離**,但 ~2424 個舊檔仍帶這個 stale reparse metadata。
- 一般程式讀檔會「用到即下載(hydrate)」→ 正常;但 **buildkit 掃 build context 用低階列舉,
  遇 reparse point 直接拒絕** → 報錯。
- 已排除是 `.dockerignore`/code:**整個移除 `.dockerignore` 也重現**。

## 怎麼驗(乾淨與否)
```powershell
# 單一檔(乾淨後不該含 ReparsePoint)
(Get-Item "command-dashboard\requirements.txt" -Force).Attributes
fsutil reparsepoint query "command-dashboard\requirements.txt"   # 乾淨: "...not a reparse point"
# 全 repo 還有幾個 reparse point 檔(乾淨後應為 0,或僅剩 static/tiles junction 等刻意項)
(Get-ChildItem . -Recurse -Force -File -EA SilentlyContinue | ? { $_.Attributes -match 'ReparsePoint' }).Count
```

## 怎麼清(把 placeholder 變回純檔)

### 方案 A(建議):重新 clone 到非同步路徑
git clone 寫出的全是純檔,且順便讓 repo 離開 Desktop。
```powershell
git clone https://github.com/winson3QQ/ICS_COMMAND.git C:\dev\ICS_COMMAND
cd C:\dev\ICS_COMMAND
git config core.hooksPath .githooks          # memory sync hook(見 CLAUDE.md)
# 重設 runtime(底圖/DB):照 README §換機器 / 新接手者(provision_basemap.sh + first-run)
docker build -t ics-command:dev command-dashboard   # 這次應成功
```

### 方案 B:保留現有 runtime,複製內容出去(較費工)
robocopy 會讀內容→寫純檔。但 `.git` 不要用 robocopy(會壞)→ 仍建議用 clone(A)。
若硬要原地保留 data/:clone 到 C:\dev 後,把舊 `command-dashboard/data/` 複製過去即可。

### ⚠ 不要用 `fsutil reparsepoint delete`
對 cloud placeholder 直接刪 reparse point **會掉檔案內容**。一律走「複製成新純檔」。

## 完成判準
在 `C:\dev\ICS_COMMAND`(非同步路徑)`docker build` ICS + TAK image 皆成功,
`(Get-Item requirements.txt -Force).Attributes` 不含 `ReparsePoint`。

> 相關:此問題擋住「prod image 本機 build 驗證」(#294 的 docker 實證、`deploy/prod/` 合併棧驗證都受影響)。
