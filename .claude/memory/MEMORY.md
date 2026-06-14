# ICS_Command 專案記憶索引

由 [ICS_DMAS](https://github.com/winson3QQ/ICS_DMAS) 於 2026-05-25 拆分而來的指揮部單體版本。
ICS_DMAS 的 memory（行為規則、架構決策、HTTPS 決策、Remote SoT、演練排程等）仍適用於本專案，請參照原 repo `.claude/memory/`。

## 本 repo 特有

- [precommit-ruff-config-cwd](precommit-ruff-config-cwd.md) — pre-commit cd 進 command-dashboard/，root-level 腳本須 line-length=120（ruff 0.11.7）否則 commit 中止（stash 衝突回滾）
- [P2 TAK 部署 / #101](p2-tak-deploy-issue101.md) — P2-01 官方 TAK Server 部署 merged；#101 已關（RSA 憑證 + fed-truststore 兩根因皆解）；本機 dev env（docker + ~/.ics/dev cert）保留可快速重起
- [Boris 路線工作流](process-workflow-boris-route.md) — Claude Code 內建 skill + 兩個自寫 quality gate；3 角色不形式化 handoff
- [事件符號 / 分類體系決策](event-symbology-classification.md) — NAPSG/CoT/台灣 四軸對照、視覺 affiliation-aware（敵我=2525框/類型=NAPSG象形/severity=halo）、type-first 建立流程、字典擴充模型；#66 編輯器(剩 C2) / #64 硬化(剩 #3→P1-10h) 狀態 + 環境 quirk
- [TAK Server = 官方 5.7 / Marti REST 走 cert 非 OAuth2](tak-server-marti-cert-not-oauth.md) — 部署官方 tak.gov 5.7-RELEASE-43；Marti :8443 認證 = client cert(mTLS) 非 OAuth2；ROADMAP P2-11 OAuth2 規格錯誤已改正(#138)；TAK REST 一律 aiohttp+cert 沿用 TAK_CLIENT_CERT/KEY
- [演習/實戰歸屬 = server-authoritative](exercise-scope-server-authoritative.md) — exercise_id 由指揮部 current_exercise_id() 決定,不信 client(ATAK opex)宣告；紅隊 TAK-C(讀 opex 分流)評估後不採(違反 doctrine)；TAK/指揮部獨立啟停,歸屬恆 server 決定
- [TAK streaming = 原生 stale+archive / 對 iTAK 刪不掉(client 限制)](tak-streaming-archive-stale-vs-mission.md) — iTAK 本機刪除不傳播(wire+server 雙證)；server repository 持久存一切、`<archive/>` 才是持久訊號、stale 是 client 顯示提示；#161 = ICS 顯示對齊原生 honor stale+archive(退 WIP time 窗口)；**[2026-06-11 dogfood] 推翻「Mission=可靠刪除」：mission REMOVE_CONTENT/t-x-d-d/刪整個 mission 都不清 iTAK 地圖 marker(收到 REMOVE 訊息但圖不動)→ server 任何刪除信號對 iTAK(iOS) 無效，只能裝置本機刪 = client 硬限制；P2-14(A) 可靠刪除不可解、(C) resync(/cot/sa) 仍可行(#194)**
- [P2-30 part 3 交接](p2-30-part3-handoff.md) — 手動感知/敵情標記 UI（WIP 在 `feat/issue-180-part3-contact`）；做了什麼/驗到哪/剩什麼/怎麼跑，接手即可續驗
- [TAK Marti 授權模型(實測)](tak-marti-authz-model.md) — **🔑 TAK 5.7 全 Marti 契約 OpenAPI spec = `docs/reference/takserver-5.7-openapispec.json`(301 paths)；動 TAK REST 先查它別猜**。讀=truststore 信任即通(註冊無關)/寫=mission `defaultRole` 決定(owner role 不附著 stateless REST,連 creator 都不行)；寬鬆 mission 任何受信 cert 可寫、READONLY mission 無人可寫；內容增刪(ADD/REMOVE_CONTENT)不需 admin→#173/#161 可達；**刪整個 mission 對 tool=public+creator 也 200(非 admin 限定，2026-06-10 精煉)**；**mission 成員≠repository 刪除**；UserAuthenticationFile 非 Marti gate；docker 不 hot-reload 須 restart；**`/cot/sa` resync 契約敲定=`?start=&end=` 用 `.000Z` + 小時間窗(大窗 400 是 gotcha 非格式)**；推翻 #176 cert-role 數前提
- [COP marker/event 解耦 founding-why](cop-marker-event-decoupling.md) — 演習第一波協同攻擊被當 N 個孤立事件、指揮部窮於應付、只有 AAR 才見全貌 → 催生本系統；marker=原子(觀察類型)/event=工作流外殼(事件分類)/junction=唯一關聯(可 re-parent)，兩 type 軸分離、triage 三態、降級定義；**N:1 即時聚合=脊椎非 polish**（把 AAR 上帝視角前移到 live）；P2-28 不得自動生孤立事件；指導 P2-27/28/29/30/33
- [COP=共享真實、協作可編輯(撤 #146)](cop-collaborative-edit-shared-truth.md) — **使用者 2026-06-14 拍板**：地圖資料以「當前真實」為準、全體協作維護；任何掌握可信變動者(原標圖者/隊友/指揮部)皆可編輯/移動/刪除任一 sensor 物件並即時 propagate，不因『誰建立』silo；對齊 TAK last-write-wins、**撤 #146/threat_model §8.2「實戰鎖死外部來源」防呆**；威脅改由 **audit 問責**+P2-10 白名單+`_PUT_FORBIDDEN_FIELDS`+RBAC 緩解；刪除仍受 archived/propagate 限制(例外，view-hide 退路 #217)；落地 Issue α(自建物件編輯 UI，無 doctrine)+ Issue β(放開 #146+threat_model 改寫+audit+security-review)
