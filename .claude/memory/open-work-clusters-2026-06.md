---
name: open-work-clusters-2026-06
description: 2026-06-23 open issue + roadmap 分類快照 +
metadata:
  node_type: memory
  type: project
  originSessionId: 29390dcb-7476-4dcb-9e65-2f4fa398dfc3
---

#344（faction 現場層 enrollment + live 重分類橋）已完成（PR #364 merged、#344 closed、ROADMAP P2-37 已記）。見 [[tak-faction-group-identifier]]。
此後 open issue 綜整 8 群（A 安全硬化 / B 加密 / C COP 脊椎 / D TAK 忠實度 / E 產品交付 / F 合規 / G AAR / H 技術債），供決定下一單。

## 🔁 重歸類快照（2026-06-24，43 open，覆蓋 06-23）
群成員（號碼會增減，動工前 `gh issue list --state open` 重抓）：
- **A 安全/演練前硬化（13，主線·演習先決）**：統籌 #348(白箱傘狀)/#342(Tier0 sprint)；XSS #293(傘狀:httpOnly+CSP 收尾,root-fix 已 #373)/#136(TAK ingest **後端** escape,與 #373 前端互補仍開)；可下手 #295(鎖定-DoS)/#285(成功動作異常)/#280(perimeter)/#323(CA 私鑰同機)/#369(TOCTOU,low)/#372(稽核鏈閉環)；需實機 #301(黑箱驗)；大項 #232(撤銷傘狀)；**#306(mTLS bootstrap — code 似已落地 deploy,疑可關待 RC)**
- **B 加密傘狀(3,卡硬體)**：#226/#231(LUKS)/#230(FIDO2)＝#348 F1
- **C COP 脊椎(6)**：#240(N:1 即時聚合,脊椎)/#267/#256/#217/#219/#66
- **D TAK 忠實度/下行(10)**：#214/#216/#260/#211/#176/#248/#172/#168/#98/#125
- **E 產品交付/TAK admin(5)**：#357(管理級 cert,演習先決)/#318(真撤銷 CRL)/#278/#255/#302
- **F 合規/授權(2)**：#349/#351
- **G AAR/指標(2)**：#204/#201
- **H 技術債/UX(2)**：#142/#250

**建議序**：①收 #373 合併+上 prod ②A 群續攻 #295 或 #372 ③#306 RC 疑可關 ④功能線則 #240。

### ✅ 2026-06-24 後續 session 進度（A 群再清一批）
**PR #373（merge `2772cb1`，main）= 演練前硬化批次三合一**，已 merged（GitHub；Codeberg 仍暫停）：
- **#293 root-fix**（DOM-XSS escaping）：88 sink 稽核 → 修 decisions/events/帳號管理(auth)/Pi-data 未跳脫 innerHTML；TAK 面本就已 escape。**#293 留開**為 XSS 收尾傘狀（剩 httpOnly cookie + 4 report-only 頁 CSP enforce）。
- **#295 closed**：高權帳號(COMMAND_ROLES)**在 mTLS 強制下不硬鎖**（防戰時 C2 鎖定-DoS）；非 mTLS 維持原鎖（review 加固）。殘留 per-source 節流→#280。
- **#372 closed**（Scope A）：sysadmin `GET /api/admin/audit-chain/verify` + 開機驗證 log + 修 architecture-readiness-review overclaim。Scope B（keyed HMAC off-box）併 #226/#348-F1。
- **#306 reality-checked（未關，留使用者決定）**：方案 b bootstrap 窗口 code-complete + 單元測試 + README 文件化；唯缺自落地後一次乾淨佈署真機 onboarding dogfood。
- review 另記 pre-existing：`failed_login_count` TOCTOU(low)、boot-verify O(n)、後端 enum 強制(decision_type/assigned_unit/username) 縱深 → 後續。
- **已上 prod**：2026-06-24 隨 #306 真機測試一併佈署 → live prod = **backend-v2.15.3 + frontend-v1.11.2**（含 #293/#295/#372）。tags 已推 GitHub。**#306 closed**（真機 dogfood PASS：fresh ics-data + mTLS=true 全程不翻 env，iPhone 4G 完成 onboarding 綁 `iphone-01` 證、bootstrap 單向閂落下）。prod DB 已 fresh（只 admin + iphone-01；舊資料未還原，備份留 `deploy/prod/_dbbackup/ics-data.prebootstrap.tgz`）。
- **wipe ics-data 測 bootstrap 的招**：只砍 DB 卷 `ics-data`（external，須 `docker volume rm` 後 `create` 重建空卷）、**保留 `ics-prod_ca-data`** → step-ca 不變、現有 client cert 仍 CA-valid、不用重發。`/tak-certs`(bind mount) + `ics-tiles` 不受影響。TAK 裝置證本體/TAK server managed user 不在 ics-data → 不受影響，只清掉 dashboard `tak_device_certs` 台帳列。
- Git 操作流程坑：pre-commit ruff/ruff-format ping-pong → 先手動 `pre-commit run ruff && ruff-format --files ...` 跑到 fixpoint 再 commit，避免 stash 衝突回滾（見 [[precommit-ruff-config-cwd]]）。
- **#369 / #285 / #136（2026-06-24 後續）**：
  - **#369 closed**（PR #374，`0f064f1`）：最後 sysadmin 守門 TOCTOU → admin.py module `_SYSADMIN_GUARD_LOCK` 序列化三出口 check+mutate。1-agent review 重現 race 確認修復。**衍生 #375**（suspend-all 殘留 TOCTOU：自排除保發起者非最後 sysadmin，並發 demote 發起者仍可歸零；連 suspend-all 納鎖也不夠，需事後 re-assert ≥1 sysadmin；LOW、演習後）。
  - **prod 已上 `backend-v2.15.4`**（`cd74f56`，2026-06-24）：含 #369+#285；recreate 保留 bootstrap 狀態（iphone-01）。前端仍 v1.11.2。
  - **#285 closed**（PR #376，`a5db484`）：成功動作異常偵測 → `_helpers.audit()` 落地後中央 hook `security_monitor.screen_audit_event`（best-effort 絕不擋）。敏感單筆即告警（cert_bind/revoke、db_reset、exercise_reset、all_accounts_suspended、pi_node_deleted）+ 批次量滑窗達閾值(5/300s)告警（account_created/status/role_updated、cop_entity/exercise_deleted）。主體=operator。時段/來源規則待 #280 真實 IP。review 確認 action_type 全對得上。
  - **#348-F10 done**（PR #377，`fcc307a`，#348 umbrella 仍 open）：reality check 重框——sessions.ip 已由 session 刪除解決、events 已 cascade（殘 AAR 張力，去識別化另議）、**chats 是真洞**（不在 cascade 又無 TTL → message/callsign/lat-lon 永久累積）。修：`cleanup_expired_chats`（CHATS_TTL_DAYS=90、軸 received_at、共用 ttl_enabled、週期 task）+ chats 補進 `_EXERCISE_SCOPED_TABLES`。**#348 finding 進度（8 項新發現全評估完畢）**：✅ F3(#372)/F4(#370)/**F5 doc(#380)+P1 後端策略(#381 core/pin_policy:min6/blocklist/長密語/不溯及)**/F7(#295)/**F9(#380 doc DR runbook)**/F10(#377 chats)/**F11(#378 ISMS 誠實化)**/F15(#379)；F13 主體(#285)；F2/F12 併 #323/#231/#232/#279；F1 併 #226。⏳ **真修待刀**（非傘狀範圍、已記錄）：**F5-P2**(新帳號隨機臨時 PIN+首登強制改全帳號——⚠️地雷：is_first_run_required=任一 sysadmin is_default_pin=1→全系統 423 鎖死；須 per-account must_change_pin 與系統 first-run 解耦+不破 #306 bootstrap，單獨 PR+重測)/**F5-P3**(前端輸入欄+show-password)、F9 DR 實演練(operational)+HA(infra)、F10-events 去識別化、F1 加密啟用(實機)。**umbrella 可考慮收斂**（純後端/可文件項已清完）。F5/F9 純文件批 = 評估後記錄接受風險+界定限制（不主張達標），同 F11 性質。
**F15 殘留**：no_user dummy 600k 對 legacy-100k 帳號 over-correct（微弱 reverse timing oracle；fresh 佈署無、登入即 rehash 收斂；完全閉合需 verify_pin 補固定 600k＝過度未做）。
**prod 版本軌**：`backend-v2.15.5`（=F10）為當前 prod；**#369/#285 已在 2.15.4、F10 在 2.15.5**。**F11(docs,免部署)+F15 在 main(`672c400`) 未上 prod → 下批 release（F15）帶上**。
  - **#136 deferred（勿單獨做 ingest-escape）**：reality check 判定 TAK ingest HTML-escape 是**錯層反模式**（與 #373 sink-escaping 雙重跳脫；store-escaped 在 P2-12 textContent 顯字面 &lt;；reject 會丟 CoT 掉點）。**正解=sink-side（P2-12 panel textContent）**，維持 open 為 **P2-12 security 前置**，動工 P2-12 時一起做。
- **retro review（2026-06-24）**：早批 #354/#345/#367/#370（PR #365/#366/#368/#371）當初無 posted GitHub review，補一次 2-agent 對抗式 retro → **clean，無新 HIGH/MED**。確認：#354 守門無漏網繞道、唯一殘留 = **#369 TOCTOU（已 open，≥2 sysadmin 並發降級歸零）**；#370 零路由落 deny 兜底；#345 稽核分流正確 + 活躍指揮官靠 5s poll 不誤登出；#367 快取**非**遮蔽 prod bug（3 常數無 runtime 變動路徑）。前瞻 caveat：若未來 idle/session timeout 改 DB config runtime 可調，auth.service import-time 快取會默默忽略（今日非 bug）。

---
## 🔄 A 群 burn-down 進度（2026-06-24 session）
本 session 把 A 群（安全/演練前硬化）做掉一批，全 merged 進 main：
- **#354** 最後 active sysadmin 防自鎖：後端守門（role/status/delete 三出口回 409，`_require_not_last_sysadmin`）+ 前端 UI 鎖（admin 列降權控制禁用）。**公網真機 dogfood 驗 PASS**（2 sysadmin 不鎖、剩 1 鎖角色）。
- **#345** session 生命週期：reality check 發現 #93(b) 已修 code 層 cutoff，故核心僅剩 **prod `ICS_IDLE_TIMEOUT_SECONDS` 86400→3600**（一行 env，殺半夜 SESSION_EXPIRED 串）+ 批次清理稽核分流 **`SESSION_REAPED`**（與 per-request SESSION_EXPIRED 區隔）。RC1 滑動絕對逾時/RC4 並發治理/cookie(#293) 評估後延後。
- **#367** 測試脆弱性 ×2（dogfood 副發現，非原 audit_log 假設）：(1) `auth.service` import 時快取 SESSION/IDLE/WARNING 常數，test 先 monkeypatch core.config 再首次 import → 永久洩漏污染後續測試（順序相依）→ conftest autouse fixture 每測後從 core.config 重同步；(2) restore 422 test 比對原始 DB bytes flaky（被拒 restore 合法寫 audit_log + WAL checkpoint 非決定性）→ 改 sentinel 存活斷言。
- **#348-F4** RBAC 兜底改 **default-deny**（#370）：`allowed_roles_for` 未登記路徑原 default-allow（GET→READ/else→WRITE，#287 成因）→ 改回空 `frozenset()`（middleware `is_role_allowed(role, frozenset())` 恆 False→403）。**前置**：先把所有靠兜底的現役路由明確登記（cop/events/decisions/snapshots/manual_records/security/sync-GET/ingress/pi-push/pi-data + dashboard/staff/audit_log/facilities/health/status/version）照凍結分類 → golden 零改＝行為等價。新增 `test_no_route_falls_through_to_deny_fallback` 完整性守門。

**Release**：`backend-v2.15.1` / `frontend-v1.11.1`，後續 **`backend-v2.15.2`（#370 default-deny 上 prod，commit `6ad3e2a`，2026-06-24）**。**prod 已跑 2.15.2**（容器 recreate 驗 `/api/version` server_version=2.15.2 + role_enum `return frozenset()` 在運行 image=fail-closed 生效）。main = `6ad3e2a`。⚠️ Codeberg 暫停同步（使用者 2026-06-24：Codeberg 端有 issue，push 只推 GitHub；origin remote 僅 github push URL，dual-push 未配）。
**衍生**：**#369**（#354 守門 TOCTOU 並發窗口——2 sysadmin 並發各降一個仍可達零 admin；low，演習後修）。
**#348 umbrella 剩**：F1（DB 加密 `ICS_DB_ENCRYPTED` 出廠仍 false，啟用糾纏 #226/P1-12 key 管理）、F3（`verify_audit_chain` 全 codebase 從未被呼叫＝裝飾、需 HMAC off-box 錨）、其餘 F5/F9/F10/F11/F15 待分刀。
**運維**：CI Actions quota 仍爆（merge 一律 `gh pr merge --admin` 繞、2s fail 是 runner 起不來非 code）。
---

- **A. 安全 / 演練前硬化（最大且最新，多標今天 2026-06-23）**：umbrella **#348**（全棧白箱檢視，8 新發現+串既有單）、**#342**（Tier0 公網硬化 sprint 排序，統籌 #279/#280/#293/#295/#136/#301）；具體可下手：**#345**（session idle 逾時 86400→14h、半夜 SESSION_EXPIRED 噪音）、**#354**（最後一個 active sysadmin 不得自鎖 brick）；其餘 #293 token→httpOnly cookie、#295 鎖定-DoS、#285 成功動作異常偵測、#280 perimeter、#323 CA 私鑰同機、#306 mTLS bootstrap、#301 Windows 黑箱驗、#136 TAK ingest XSS、#232 憑證撤銷 umbrella。
- **B. P1-12 加密（umbrella #226，⏳ 唯一未完 P1）**：#231 LUKS 整碟（主控）、#230 FIDO2 硬體驗收（待實體 token）、12c SQLCipher code-complete 卡 #231 政策。
- **C. COP 解耦脊椎 / 感測 doctrine**：**#240**（P2-33c event↔marker N:1 即時聚合，承接已做完的 P2-31/32）、#267 感測 doctrine（Group+編組兩層）、#256 移動尾跡 breadcrumbs、#217 現場↔指揮部生命週期一致、#219 P2-29 filter IA、#66 taxonomy 編輯器。
- **D. TAK 忠實度 / 下行 / DataSync**：#214 出向 CoT 補欄位（team/color/icon/link）、#216 出向 GeoChat、#260 Route 忠實度、#211 ATAK Mission/DataSync dogfood、#176 P2-14 reality check、#248 GeoChat 入向落差、#172 繪圖 fade、#168 map filter、#98 TAK drift、#125 跨場軌跡歸錯場。
- **E. 產品交付 / TAK admin console**：**#357**（管理級 TAK cert 管 group——標「#344 前置」；~~大概可直接關~~ **更正 2026-06-24 reality check：不可關**——ICS 現有 `marti-read` cert 打 `/user-management/api/*` 得 403，需**管理級 TAK cert** + 實測 group 指派粒度（spec `PUT /Marti/api/groups/active` per-uid 若可行則取代「每台唯一 CN」阻礙 1）。`tak_group_sync.py` 僅部分。**演習先決真工作**）、#318 真撤銷 CRL（大項）、#278 enrollment 產品化、#255 連線設定 DB 化、#302 前端 IP 保護。
- **F. 合規 / 授權**：#349 應變管理互通（IPAWS/EDXL/NIMS/CAP-TWP/無障礙）、#351 商用化授權盤點（LICENSE/SBOM/GPLv3/ODbL）。
- **G. 演習 AAR / 指標**：#204 P2-21 指標（⏳ 子集）、#201 P2-20B AAR 回放前端頁。
- **H. 技術債 / UX**：#142 DB_PATH 動態讀、#250 通聯面板 UX（chat 延遲 + chip 重複）。

**下一步建議**：A 群（演練前安全硬化）最連貫——同 #343/#344「演習先決」線、多標今天、且有 #348/#342 兩個統籌單可先拆 sprint；順手關 #357。要走功能/COP 則 #240（脊椎，承接 P2-31/32）。

**運維尾巴（非 issue）**：① GitHub Actions `issue-snapshot` + `mirror-to-codeberg` 兩 workflow 已 `disabled_manually`（GitHub Free private repo 2,000 min/月帳號額度用爆、起不來；月底重置或處理 billing 後 `gh workflow enable` 開回）；Test 留著。② Codeberg ICS_COMMAND 161 MiB 警告 = snapshot JSON churn（365 commit 灌 docs/backups，本機壓 15MB、Codeberg 沒 gc）→ 待砍掉重 push（不改寫歷史即可清，方案已議）。③ TAK 殘留休眠 entry `3QQ-atak`/`red-01`/`blue-01` 可選清（usermod -D，安全閘擋過、需手動跑）。

⚠️ **issue 編號為 2026-06-23 快照、會增減**：動工前先 `gh issue list --state open` 重抓現況再對照本分類。

---
## ✅ 2026-06-25 session：#348-F5 PIN 硬化全段 + #348 收斂 + #389 faction 面板（全上 prod、公網實機驗）

**prod 現況**：`backend-v2.16.2` / `frontend-v1.12.2`（單機 docker `ics-prod`，home router forward 1.34.230.218:443→nginx mTLS）。

**#348-F5 PIN/密碼硬化四段全落地 + 公網 iPhone E2E PASS**（admin/3QQ/3QQ-test 帳號）：
- **P1**（#381，先前）`core/pin_policy`：min6 + blocklist + 開放長密語、不溯及。
- **P2a**（PR #383）admin 建帳號首登強制改：`is_default_pin` + `auth_middleware` per-account 閘（非白名單→423 PIN_CHANGE_REQUIRED）+ **cop `/ws/updates` 補閘**（HTTP middleware 不跑 WS scope）+ `reset_pin` 限 first-run bootstrap（防被盜 session 免舊 PIN 自解閘）。`is_first_run_required` 收斂 **bootstrap-only**（accounts==1 sysadmin default，解「建第 2 帳號鎖死全系統」地雷）。
- **P3**（PR #385）前端 3 個「同一長久秘密」入口（登入/首登 overlay/PinLock 解鎖）放寬接受密語 6→128 + show-password 眼睛（opt-in、登出/鎖定復原遮蔽 `_maskPwField`）。
- **P2b**（PR #386）create/reset 改 `pin_policy.generate_temp_pin` 系統產隨機臨時 PIN（admin 不自設、一次性回 `temp_pin`、不落 plaintext/不入 audit）；`reset_pin` 語意變＝admin reset→系統臨時+強制改（不收 body）；first-run admin 自改改走 **change-initial-pin**（非 reset_pin）；FE 建帳號拔 PIN 欄 + 「重設為臨時 PIN」鈕 + 一次性 `_showTempPin` 模態。
  - **hotfix**（frontend-v1.12.1）：`_showTempPin` 用共用 openModal（#overlay z210）被「帳號管理」面板（#admin-panel z300）蓋住看不到 → 改自帶 z10000 overlay。
- **衍生**：**#384**（移除死 Admin PIN 空殼——`verify_admin_pin` 零呼叫者、後端不讀 X-Admin-PIN，RBAC 取代後遺留；ROADMAP RT-M2 已更正）、**#382**（第一個 admin 免-CLI 量產 onboarding，QR 出廠密碼陷阱已議）。

**#348 umbrella CLOSED**：8 findings 全處置（程式碼可動全清）；殘留分流 **F1→#226**（加密卡硬體）、**F9 DR 實演練+HA→#387**、**F10-events 去識別化→#388**。

**#389 紅藍分類面板 UX**（PR #390/#391，backend-v2.16.1/2、frontend-v1.12.2，雙實機 ATAK+iTAK 驗 PASS）：① 「實戰池」誤名→「待命池（未開場：非演習非實戰）」（**NULL scope ≠ 實戰**；實戰=type=real active 場、有 id；TAK 連入綁 `current_exercise_id()`，無 active→NULL）；② 文案「連上的」→「本場觀測到的（含已離線）」；③ 🟢/⚪ 在線指示。**關鍵修正**：online/last_seen 必用 **`updated_at`（最後活動）非 `received_at`（首見、再廣播不更新）**——dogfood：live 裝置 received_at 停昨天→誤判離線。**faction 分類 = per-scope**（`client_faction` 鍵 (COALESCE(exercise_id,-1), client_key)；同裝置跨場可不同陣營）。**未決 doctrine**：per-scope 重複分類（裝置陣營若大多固定→是否「全域預設+各場覆寫」，待操作流程定）。

**本 session 工程坑（記取）**：
- **編輯 worktree 不是 main checkout**：feature 改要編 `.claude/worktrees/<wt>/...`，誤編 `C:\Users\yello\Desktop\ICS_COMMAND\...`（main checkout）會讓 worktree 測到舊碼。修法＝main checkout `git stash` → worktree `git stash pop`。
- **esbuild minify 把中文/emoji 轉 `\uXXXX`**（#302 前端 IP 保護）→ 驗 minified 檔別 grep 字面中文，改驗 codepoint（待=5f85、🟢=1f7e2）。jsmin stage 有 layer cache，純前端改若疑沒進 → `docker build --no-cache-filter jsmin`。
- **received_at = 首見、updated_at = 最後活動、stale>now = 原生在線**（cop_entities）。
- 版號 release commit 撞 ruff E501（中文註解過長）會擋 commit、tag 恐指錯 commit → 先驗 ruff 再 tag。
