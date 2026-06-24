---
name: build-env-gotchas-windows
description: "本機(Windows, Desktop\\ICS_COMMAND)環境地雷——docker build 因孤兒 cloud reparse point 失敗；test_tak_events 因 env TAK_ENABLED=true 失敗；Git Bash `python` 常解析成沒裝 deps 的 3.12（要用 `py -3.13`）。皆非 code bug"
metadata:
  node_type: memory
  type: project
  originSessionId: 256d8b90-93cb-49e3-b78d-e85a825bd46b
---

主開發/部署機(Windows, repo 在 `C:\Users\yello\Desktop\ICS_COMMAND`)兩個會反覆踩的地雷(2026-06-21 查死)：

## 1. 本機 `docker build` 失敗：`invalid file request requirements.txt`
- **根因**：repo 裡 **2424 個檔帶孤兒 cloud-files reparse point**(`IO_REPARSE_TAG_CLOUD_2`, tag `0x9000201a`)——是**以前 repo 在 OneDrive 時留下的殘渣**(現在 Desktop 已**不**被 OneDrive 同步：`$env:OneDrive=C:\Users\yello\OneDrive`、`User Shell Folders\Desktop=C:\Users\yello\Desktop`、無 SyncRootManager 註冊)。
- buildkit 掃 build context 讀不了 reparse-point 檔 → 報錯。**移除整個 .dockerignore 也重現** → 確定是檔案系統層,非 code/.dockerignore。
- **不影響**：uvicorn dev server、git、pytest、vitest 全正常(一般讀檔會即時 hydrate)。**只卡 `docker build`**。
- **解法**：把內容複製成全新純檔再 build——`git clone` 或 robocopy 到非同步路徑(如 `C:\dev\ICS_COMMAND`,有 worktree 要 `git worktree repair`),在那 build；或在 CI/乾淨 checkout build。`fsutil reparsepoint delete` 不要用(雲端檔會掉內容)。
- 驗證乾淨：`(Get-Item requirements.txt -Force).Attributes` 不再含 `ReparsePoint`。

## 2. `test_tak_events::test_status_reflects_disabled_default` 在本機 fail
- 本機 `env TAK_ENABLED=true` → 測試假設未設(預期 disabled)→ `assert body["enabled"] is False` 掛。
- **非 code bug**：stash 全部改動、clean main 上也 fail；CI(無此 env)會過。跑全套件看到「僅此 1 條 FAILED」= 正常,別誤判成自己的回歸。
- **同類**：`test_backup_restore_api.py::TestRestore::test_restore_bad_payload_422_current_untouched` 在本機 Windows **deterministic fail**（422 請求路徑附帶 DB 寫入 → 「current DB 位元組逐一比對未變」假設脆，一個 page byte 變）。stash 回 clean main 也 4/4 fail；**CI Linux passed**。亦非回歸（2026-06-21 P1-12c 收尾查死）。

## 3. Git Bash 的 `python` 常是 Python 3.12（沒裝專案 deps）
- 跑 `python -m pyflakes/pytest` 可能報 `No module named pyflakes` 或缺 fastapi 等——因為 Git Bash 的 `python` 解析到 `Python312`，專案 deps 裝在 **3.13**。
- **解法**：一律用 **`py -3.13 -m ...`**（pyflakes/pytest 都有）。或先確認 `py -3.13 -c "import sys;print(sys.version)"`。
- 另：rebase/checkout 後 `scripts/keymgmt/wordlist_english.txt` 可能被 autocrlf 轉 CRLF → mnemonic sha256 釘住假失敗；`rm` 後 `git checkout --` 重正規化（committed blob 永遠 LF，CI/Mac 不受影響）。

## 4. 跑全套 pytest 的乾淨指令（避開上面 1+3 雷）
`cd command-dashboard && TAK_ENABLED=false py -3.13 -m pytest -q -o addopts="" -W ignore`
→ 預期約 1185 passed / 2 skipped（2 skip = win32 `test_restore_roundtrip` 熱 DB 鎖 + `test_import_facilities` 缺 pyproj，皆非回歸）。

相關：[[worktree-stale-target-main]]、[[deployment-topology-windows-docker]]
