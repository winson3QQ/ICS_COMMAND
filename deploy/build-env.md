# 本機 build 環境問題 — OneDrive 殘留 cloud reparse point（✅ 已解 / 歷史記錄）

> ✅ **狀態:已解（#300）。** 2026-06-22 實測 `C:\Users\yello\Desktop\ICS_COMMAND`
> 主 repo 與各 worktree 的 reparse-point 檔數**皆為 0**(`requirements.txt` 等抽樣屬性
> 僅 `Archive`、無 `ReparsePoint`)→ 檔案系統層的 OneDrive 殘渣已清除,`docker build`
> 不再受此阻擋。`deploy/prod/README.md` 亦記 #300 已解。
>
> **本文件保留為歷史記錄 + 復發排錯參考。** 下方〈症狀〉〈根因〉〈怎麼驗〉〈怎麼清〉
> 描述的是**當時(2026-06-21)的故障狀態**;若日後 repo 再被 OneDrive 同步污染可循此處理。

---

## 當時影響(歷史)

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

> 關鍵事實:**Desktop 現已不被 OneDrive 同步**(`$env:OneDrive` 在別處、無 SyncRootManager)
> → **在此 Desktop 路徑寫的「新檔」就是純檔**(實證:工具新建的檔皆無 reparse)。
> 所以**不必把 repo 搬到別處**——原地重 clone(同路徑)即可,並行 session/worktree 照樣找得到。

### 方案 A(建議):原地重 clone(同一 Desktop 路徑,位置不變)
⚠ 破壞性 + 並行依賴,做之前務必:
1. **所有並行 session/worktree 的未提交工作先 commit/push**——repo 內有別的 worktree
   (`.claude/worktrees/*`),刪 repo 會連它們未提交變更一起沒。
2. **備份 gitignored runtime**(clone 不含):`command-dashboard/data/`(DB/使用者資料)、
   `command-dashboard/static/tiles/`(底圖)。
3. **沒有任何 session(含 Claude)跑在此 repo 時**才做(不能刪自己站的地)。
```powershell
# (先備份 data/ 與 static/tiles/ 到別處)
Remove-Item -Recurse -Force C:\Users\yello\Desktop\ICS_COMMAND
git clone https://github.com/winson3QQ/ICS_COMMAND.git C:\Users\yello\Desktop\ICS_COMMAND
cd C:\Users\yello\Desktop\ICS_COMMAND
git config core.hooksPath .githooks
# 還原 data/ 與 static/tiles/;worktree 視需要 git worktree add 重建
docker build -t ics-command:dev command-dashboard   # 這次應成功
```

### 方案 B(零風險替代):臨時 clone 到別處「只用來 build」,Desktop 不動
build 只在 build 時需要乾淨檔;image 進 Docker 共用 store、不綁路徑。
```powershell
git clone https://github.com/winson3QQ/ICS_COMMAND.git C:\Temp\ics-build
docker build -t ics-command:dev C:\Temp\ics-build\command-dashboard
# (TAK image 同理在乾淨 clone build)→ 回 Desktop `docker compose up`(用建好的 image,勿 --build)
Remove-Item -Recurse -Force C:\Temp\ics-build   # 用完即丟
```
working repo + 並行 session **完全不動**。代價:runtime bind-mount(如 TAK `../tak-server/release`)
仍讀 Desktop 的 reparse 檔——一般讀檔會 hydrate,通常 OK,但這是唯一未證實的點。

### ⚠ 不要用 `fsutil reparsepoint delete`(cloud placeholder 會掉內容)。

## 完成判準（✅ 已達成）
`docker build` ICS + TAK image 成功;`(Get-Item requirements.txt -Force).Attributes` 不含 `ReparsePoint`。
→ 2026-06-22 全 repo reparse-point 檔數實測為 0,判準滿足。

> 並行 session 顧慮:**用方案 A(同路徑)或方案 B(不動 Desktop)**,都不會讓其他 session 找不到 repo。
> 唯有「永久搬到 `C:\dev`」才會改變路徑——那需同步更新所有 session 的 repo 根,非必要不做。
> 相關:此問題擋住「prod image 本機 build 驗證」(#294 docker 實證、`deploy/prod/` 合併棧驗證)。
