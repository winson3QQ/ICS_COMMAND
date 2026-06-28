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

**SoT**：架構/進度 ROADMAP P2-26 #434 條目；威脅 threat_model **§8.9**（特權容器/佇列控制面/DNAT 信任邊界，v1.1）；腳本 `deploy/perimeter/wireguard/container/`；ICS 端 `services/wg_provision.py`+`repositories/wg_peer_repo.py`。**剩**：階段3 cutover ✅、威脅模型 ✅；無已知 ⏳。
