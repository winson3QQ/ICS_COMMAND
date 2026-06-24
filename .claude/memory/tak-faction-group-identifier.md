---
name: tak-faction-group-identifier
metadata:
  node_type: memory
  type: project
  originSessionId: 4f02ee3c-044a-4295-9c88-009a1aca6204
---

#344（faction→TAK group 現場層隔離）對活 prod takserver 只讀探針（2026-06-23，`/Marti/api/groups/all`、`/clientEndPoints`、`/user-management/api/*`，ICS marti-read cert）的地基結論：

- **識別軸 = cert CN（=TAK username），callsign/team 僅顯示不可當識別。** clientEndPoints 實測同一 uid 出 3 個 callsign（3QQ-aTAK/3QQ-aTak/CAP-AT-MK）、同 uid 宣告 Blue/Red/Orange → 操作員可隨手改。穩定識別只有 uid（ICS 側 [[event-symbology-classification]] 歸屬鏈 client_key）+ username=cert CN（TAK group 綁定鍵）。
- **橋 = `GET /Marti/api/clientEndPoints`** 回 `{uid, username, callsign, team, lastStatus}`，ICS 用 uid join 出 username，把 #343(uid 鍵) 接 #344(username 鍵)。
- **🔴 阻礙 1：現行裝置證 CN 全坍縮成 `3QQ`**（多台不同 uid 的 username 都是 3QQ）。group 是 per-username → 同 username 無法分群。前置必須**每台唯一 CN 發證 + ICS 記 CN**（`tak_device_certs` 無 CN 欄，需補；接 #315 面板）。沒唯一 CN = 隔離不成立。
- **🔴 阻礙 2：`/user-management/api/*` 用 marti-read cert → 403**（管 group 是管理級）。ICS 需配管理級 TAK 帳號/cert。對齊 [[tak-marti-authz-model]]：寫內容≠管帳號。
- **預設群洩漏實錘：現存 group 只有 `__ANON__`** → 目前零隔離全員共享。#344 須建 blue/red 群**並讓裝置移出 __ANON__**，否則照樣互看。
- **gotcha 2（group 變更對已串流連線是否即時生效）未解**——只讀測不到，建群後實測。
- 非 ASCII CN（`主教`）REST mojibake（連動 #324）→ CN 限 ASCII。
- 探針時 takserver+tak-database Up（prod TAK_ENABLED=true，對外 1.34.230.218）。

完整版：`docs/design/red-blue-faction-isolation.md` §8.4 + GitHub #344 留言。

**Experiment A（2026-06-23，admin cert `ics-tak-admin`=ROLE_ADMIN，certmod -A 標的，到 2028-09）：**
- 阻礙 2 解決：admin cert 打 `/user-management/api/list-groupnames` → 200（read cert 是 403）。
- **`subscriptions/all` 才是可靠在線視圖**（實測只剩 ics-cot 自己）；`clientEndPoints` lastStatus 是殭屍（裝置關機 TCP 半開未回收，lastEventTime 停舊仍標 Connected）→ 建橋勿信它。
- **REST `update-group-users`/`get-groups-for-user` → 500**（cert-auth 無 LDAP，這條 web-UI API 不通）→ ICS 動態建群走不通。
- **grounded 機制（UserManager.jar usage 親證）**：cert user 指派群 = `certmod -g/-ig/-og`（-g in+out、-ig 寫、-og 讀，方向性 CLI 即有；-a append、-r remove；無群落 anonymous）。**撤回**先前「CoreConfig 須預定義群」之猜測（未證實）。
- 三路徑：REST update-group-users(500,死)/CLI certmod -g(per-username,命中阻礙1+跨容器呼叫待解)/REST `groups/active?clientUid`(200,per-uid,繞過阻礙1 但**效果未驗,需實機**)。
- **✅ 2026-06-23 雙實機驗證通過**：ATAK(red-01)+iTAK(blue-01) certmod -g 進不重疊 red/blue 群 → **SA/marker/GeoChat 全部互不可見**。**阻礙1(每台唯一 CN+certmod -g)=驗證有效正解**，per-uid 不需要。唯一 CN **不需改 code**(#315 發證本就 CN=callsign;坍縮 3QQ 只是重用同證的操作問題)。**封包格式**:ATAK=嵌套 zip+MANIFEST+Android 絕對路徑、iTAK=flat 無 MANIFEST+legacy p12;**用 dashboard #315 `services/tak_device_cert.py` 的 `assemble_package`/`_make_p12_materials`,別手刻**(`gen-device-pkg.sh` 格式錯、`gen-device-dp.sh` 用 step-ca 是半遷移舊狀態)。
- **仍待做**：①gotcha2(連線中即時改群)未驗,演習用預分類 roster 即足 ②ICS 白隊全見:ics-cot 在 __ANON__ 收不到 red/blue,需把 ICS marti read 身分 certmod 進 red+blue ③群指派自動化:certmod 是 takserver 容器內 CLI、ICS 跨容器呼叫待解(update-group-users REST=500),演習手動 roster 可行、產品化接 #343 faction 為後續。
- **🔑 [2026-06-23 產品化 reality-check 重大更正] REST `update-groups` 其實可行**：先前「REST 寫 500」是**我 body 漏 `groupListIN`/`groupListOUT` → `FileUserAccountManagementApi` 對 null list `Arrays.asList` NPE**（takserver-api.log 實證）。**補齊三欄（groupList+groupListIN+groupListOUT，可空[]）→ 200 且真改群**（red-01 [red]→[red,blue]→還原，讀回確認）。→ **faction 重分類 = ICS 經網路 REST 直呼，免 certmod/sidecar/跨容器**。
- **但建立 cert-user 仍只能 certmod**：REST `new-user`（`NewUserModel`={username,password,groupList...}）只建 password user、無 fingerprint 欄 → 建不了 cert-user；update-groups 只改**已存在** user（匿名未 certmod 者查/改皆失敗）。auth backend=`<File UserAuthenticationFile.xml>`，certmod 寫此檔、無 restart 即被新連線採用。
- **#344 產品化架構定案**：enrollment（發證+註冊 cert-user+初始群）= certmod（跨容器，需 takserver 側 helper 或一次性批次 script，演習用批次即可）；**live 重分類 = REST update-groups（全欄位）ICS 直呼，無橋**；ICS 全見 = ics-cot certmod 入所有群一次。gotcha2（連線中改群即時生效）仍待實機。
- **[2026-06-23 #344 live 重分類已 merged(#363)+上線]** classify → tak_group_sync.sync_client_faction → clientEndPoints(uid→username) + update-groups(三欄) 改群。`TAK_MARTI_ADMIN_CERT/KEY`=ics-tak-admin 已配置。**但只對 managed user 有效**——dogfood 實機用 #315 匿名證(username 3QQ/3QQ-atak、get-groups-for-user 500)→ sync 靜默跳過、裝置不反應(非 gotcha2，是裝置非 managed)。**#315 發證預設匿名=系統缺口；重發證不解，要發證時也 certmod 註冊。**
- **[#344 Option A sidecar reality-check=可行，PoC 待做]** /opt/tak 是 host bind mount→sidecar 掛同路共用 UserAuthenticationFile+jar；sidecar 重用 ics-takserver:5.7 image+icsnet。**唯一 PoC 未知**：外部 process(sidecar) 跑 certmod 對活 messaging 層是否等同 docker-exec(理論一樣=寫同檔、連線讀檔，但防 in-container IPC)。**下個 session：先 PoC(sidecar 跑一次 certmod→實機連上驗進對群)→通則建 sidecar HTTP service(内網+共享密鑰，高權限)→接 #315 發證自動註冊；分工 sidecar=註冊(一次)/ICS REST=改群(動態)。gotcha2 仍待 managed 裝置驗。** 完整交接見 GitHub #344 留言。
- **[2026-06-23 PoC 完成 — enrollment 註冊機制實證鎖定（活 prod takserver 實機）]** 「唯一 PoC 未知」已解，結論推翻先前 sidecar 樂觀假設：
  - **UserManager.jar（usermod/certmod）是 server-coupled，不是檔案編輯器**——它會連 takserver 的**本機 IPC** 把變更熱套用。實證三路：① 獨立 sidecar（自己 netns）跑 usermod → **timeout**「server running on this machine?」、且 timeout 前**不寫檔**；② `docker exec takserver` usermod → **成功、`get-groups-for-user` 200、免 restart**；③ sidecar **共用 takserver netns**（`docker run --network container:takserver` + 掛 /opt/tak）→ **也成功 200**（localhost IPC 可達）= **免 docker socket 的路**。
  - **直接編 `UserAuthenticationFile.xml` 雙重死亡**：跑著的 server **不重讀檔**（編入的 user `get-groups-for-user` 回 500，server 狀態存記憶體、boot 才載入），且**下一次 usermod 會從記憶體 re-marshal 整個 XML、把直接編的 entry 清掉**。→ **Mechanism A（dashboard 裸寫檔）徹底不可行。**
  - **enrollment 不需裝置在線**：`usermod -f <fingerprint> -g <group> <callsign>` 純記 fp→username→group 映射（假 fp 即可建出 entry，裝置之後帶證連上自動 match）→ 比 PR #363 的 clientEndPoints uid-join（需在線）更乾淨；fingerprint dashboard 用 openssl 自算（已是依賴）。
  - **判別器**：`GET /user-management/api/get-groups-for-user/{username}`（admin cert）managed→200+groupList、未註冊/未被 server 認→500。
  - **架構定案（使用者 2026-06-23 拍板）**：**netns sidecar + 共享卷檔佇列**跑 usermod（reuse ics-takserver:5.7 image 有 Java，`network_mode: service:takserver`，掛 /opt/tak + queue volume，bash watcher 輪詢）；dashboard 算 fp + 寫請求檔到 queue（best-effort，不拖垮 #315 發證）；**初始群 = neutral（fail-closed）**；live 重分類維持 PR #363 REST `update-groups`（裝置已是 managed user 即生效）；ics-cot 一次性加進 neutral 補白隊全見。**排除**：dashboard 裸寫檔（死）、dashboard 掛 docker socket（root 逃逸面，違 ICS 免 socket 姿態）、獨立 netns sidecar（timeout）。
- 注意：repo `.claude/memory/` 為 SoT、post-merge hook 會 sync 蓋掉讀取位置的手寫 memory（本檔可能被覆蓋；durable 記錄在 GitHub #344/#357 留言）。
