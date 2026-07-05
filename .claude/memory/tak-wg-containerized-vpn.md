---
name: tak-wg-containerized-vpn
description: "ICS 統管 TAK client「連線那半」= 容器化 WireGuard（#434）；容器 WG 是 prod 公網 VPN @51822，Windows-native 已退役（cutover 2026-06-28）"
metadata:
  node_type: memory
  type: project
  originSessionId: 72b22dc7-5884-409b-9af4-5bc2fe7c9a5a
---

「ICS 完整管理 TAK client」= **cert（enrollment，[[tak-enrollment-working]]）+ 連線（WG，本檔 #434）** 兩半，2026-06-28 兩半皆 live。

**架構決策（方案 B 容器化，非 Windows-native）**：Windows-native WG 跨 OS 邊界、容器內 ICS 驅動不了（`wg.exe` 要 host admin、無 netns 捷徑，reality-check 實證）。改跑 **Linux 容器 `ics-wg`**（kernel WG，WSL2 6.6 原生）後，ICS 比照 tak-registrar 經**共享卷檔案佇列**（`/wg-queue`，4 行純文字 pubkey/allowed_ip/op/label）加/刪 peer，**不引 docker.sock**。ICS 端純 Python：X25519 keygen（`cryptography`，與 `wg` 相容實證）+ 配 IP（`wg_peers`/m034）+ QR（**segno**，德國 BSD-3，非中國）。發 TAK 證連帶配 peer + 夾 `.conf`+QR 外層 bundle；撤證連動撤 peer（雙層撤）。

**部署拓樸（prod = Windows Docker 機，見 [[deployment-topology-windows-docker]]）**：
- 容器 WG = **唯一 prod 公網 VPN**，`1.34.230.218:51822`（host publish `51822→容器內 51820`）；server pubkey 持久在 `/wg-data/server.key`。
- **Windows-native WG（`WireGuardTunnel$ICS-WG`，舊 :51820）已 cutover 退役**：`Stop-Service` + `StartupType Disabled`（**可逆**，設定保留未 uninstall；回退＝`Set-Service … -StartupType Automatic; Start-Service`）。其 legacy peer ICS 看不到（非 admin shell `wg show` 拒；不在名冊）——cutover 前確認「僅業主自用舊裝置可棄」才停。
- 殘留（業主手動）：router 移除 :51820→Windows 的死 forward。

**踩過的坑（hard-won）**：
- **持久化路徑**（#439）：`wg0.conf` 原寫 `/etc/wireguard`（容器 ephemeral）→ `docker restart` 留著但 **`--force-recreate`（每次重部署）wipe 全 peer**。必須在 `/wg-data` 持久卷（與 server.key 同卷）。entrypoint/wg-peer-registrar/wg-persist 三 script 的 `CONF` default。
- **umask 洩漏**：entrypoint `umask 077`（server key 用）會洩到 watcher → 佇列結果檔 600、ICS（uid 10001）讀不到 → 誤判失敗。修＝watcher `umask 022`（結果檔須 ICS 可讀）+ entrypoint 子殼 scope。反向：`wg-persist.sh` 自己 `umask 077`（持久 conf 含 server 私鑰須 0600，#439 security-review 收）。
- **Bash 坑**：`${VAR:-…{1,3}…}` 預設值內大括號會提前截斷 parameter expansion → SUBNET_RE 分兩步。
- **在線判斷**（#438）：registrar `status` op 回 `wg show latest-handshakes`（`<pubkey>\t<unix>`，0=從未）→ ICS `peer_handshakes()` → 帳本 🟢/⚪。
- 容器經 **Docker Desktop UDP NAT** 到達裝置（source SNAT 172.19.0.1/192.168.65.x；漫遊 ~60–90s 恢復）；WG 按 pubkey 認證不靠 IP，NAT 不影響准入但弱化 per-IP 溯源。
- **ATAK「Unable to validate Truststore / Open network settings」偶發＝良性、既有、與 VPN 無關**（2026-06-28 查證：使用者證實**沒 VPN 時就有**此症狀）。非真憑證問題（連得上＝證有效），是 ATAK 對「TLS 重連撞上短暫網路中斷」的**籠統誤導訊息**（cellular/wifi 切換、TAK 瞬間忙皆觸發）。**排除 MTU**：DF 探測無乾淨斷點、outage 時連小封包也 100% 丟（MTU 只打大封包）。處置＝**按 Cancel 自動重連、不改 code**（別按 OK，那是 red herring 開網路設定）。診斷法留痕：WG 容器內 `wg show wg0 latest-handshakes` 距今秒數＝隧道死活的乾淨訊號（>120s≈斷窗口），對 error 時刻可判「掉線 vs TLS 層」。

**儀表板也走 VPN（#280，2026-06-28，backend-v2.25.0/frontend-v1.18.0，merged PR #440）**：#434 把 TAK :8089 收進 VPN 後，儀表板 :443 仍公網直曝＝不對稱 → 把儀表板也納入同一 WG 周界。**SAN 規避是關鍵**：瀏覽器連 WG 私網 IP（10.13.13.1）會 TLS 主機名失敗（nginx server cert SAN 實測只含公網 IP `1.34.230.218`+localhost，無私網 IP；對齊 #321「SAN 不含 RFC1918」）→ 解法＝device `.conf` 的 `AllowedIPs` 加**公網 IP/32**（config `WG_EXTRA_ALLOWED_IPS`，prod .env 已設 `1.34.230.218/32`）→ 瀏覽器**照用原網址** `https://公網IP`、封包改走 tunnel → 既有 DNAT(wg0 :443→nginx，規則 match 任意 dest :443) → SAN 已含公網 IP。**WG 自身 transport 封包（往 Endpoint:port）由 fwmark 排除在隧道外，不成迴圈**（與 `0.0.0.0/0` full-tunnel 同機制）——**真機 dogfood 證實假設成立**。**一條 tunnel 通吃**：`AllowedIPs = 10.13.13.0/24, 公網IP/32` → 子網涵蓋 10.13.13.1:8089（iTAK 連 TAK）+ 公網 IP（儀表板）→ **每台裝置一條 tunnel 同載 TAK + 儀表板**（iOS 只允許一條 active VPN，這正好夠）。發放：純儀表板使用者（無 TAK 裝置）走帳號管理「📶 發 VPN」(`POST /wg/issue?label=username`)；TAK 裝置使用者的 WG 隨發證自動配（現也含公網 IP 路由）。撤 WG-only＝`POST /wg/peers/revoke`。威脅 threat_model **§8.9.1**（v1.2）。**剩（部署層、可逆、分階段）**：router 移除公網 :443 forward → 儀表板僅 VPN 可達（公測可暫留 mTLS-公網，§8.8 可接受）。

**WG DNAT 轉發埠（entrypoint.sh `dnat`）**：wg0 進來 → `:8089`+`:8446`→takserver、`:443`→nginx。**[2026-07-05 #503/#506 加 `:8443`→takserver]**：8443=Marti REST API（mission/Data Sync/Enterprise Sync 附件上傳/頻道），**原刻意不轉發**（compose 誤註「8443 admin 不對外減攻擊面」）→ 害現場 ATAK 一切需 Marti API 的功能全滅（附件永遠上不了 server、建 Data Sync feed「Searching for server channels…」卡死）。**改轉發**：8443 是 TAK 標準 client 面埠、非純 admin；安全＝三重閘（WG-only `-i wg0` 非公網 + 強制 mTLS〔實測拒無 cert `TLSV13_ALERT_CERTIFICATE_REQUIRED`〕 + Marti group/role 授權），同已開放的 8089。**8443 不對 host 公網發佈**（compose `ports:` 不加），只走 WG 隧道給已發證裝置。⚠ 現況：runtime `iptables` 規則已加、**源碼（entrypoint.sh + compose 註解）已改但待 rebuild `ics-wg` 持久化 + commit**（rebuild 會短暫斷裝置 VPN）。詳見 [[tak-mission-datasync-plane]]、[[tak-filestore-image-uplink]]。

**SoT**：架構/進度 ROADMAP P2-26 #434 條目 + 儀表板 VPN-gate 條目（#280）；威脅 threat_model **§8.9 + §8.9.1**；腳本 `deploy/perimeter/wireguard/container/`；ICS 端 `services/wg_provision.py`（`WG_EXTRA_ALLOWED_IPS`）+`repositories/wg_peer_repo.py`+`routers/admin.py`（wg/issue、wg/peers/revoke）。**剩**：階段3 cutover ✅、威脅模型 ✅、儀表板 VPN-gate 程式碼 ✅；唯一待辦＝業主移除公網 :443 forward（收口）。
