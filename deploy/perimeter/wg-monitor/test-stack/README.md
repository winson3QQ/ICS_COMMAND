# wg-monitor 自帶整合測試棧（#447）

不需 prod、不需真機，就能在**真 WireGuard** 上驗證監測的核心管線與主菜偵測器（盜鑰 = endpoint 擺盪）。

## 它做什麼

起 4 個小容器（完全獨立、不碰 prod/ICS/TAK）：

| 容器 | 角色 |
|---|---|
| `keygen` | 執行期產 server 金鑰 + **一把被複製的 client 金鑰**（不入 git）|
| `wgtest-server` | 真 WireGuard server（wg0），單一 peer = 那把 cloned key |
| `client-a` / `client-b` | **同一把 key** 的兩台「裝置」，各從自己的容器 IP 對 server 握手 |
| `wg-monitor` | 監測本體，`network_mode: container:wgtest-server` 共享 server netns 讀 `wg show` |

`run.sh` 用 `docker compose pause/unpause` 驅動「持有者」在 A→B→A 間切換（＝同一把 key 從兩處輪流使用＝真實盜鑰情境）。server 的 `wg show` endpoint 隨之擺盪，監測在**真 wg show** 上偵測到 `endpoint_oscillation/critical`。

## 跑

```bash
cd deploy/perimeter/wg-monitor/test-stack
bash run.sh
# 清理：
docker compose down -v
```

## 預期（PASS）

```
監測記錄到的來源 IP（應有 2 個＝兩台 cloned 裝置）: ['172.18.0.3', '172.18.0.4']
events: [('endpoint_change', 1), ('endpoint_oscillation', N)]
alerts: [('endpoint_oscillation', 'critical'), ...]
PASS：真 WireGuard 上偵測到 cloned-key endpoint 擺盪 → ('endpoint_oscillation', 'critical')
```

## 前置 / 限制

- 需 Docker 的核心支援 **kernel WireGuard 模組**（WSL2 6.6 / mainline 原生有；已實測通過）。若 `ip link add wg0 type wireguard` 失敗，代表核心無 WG 模組。
- 這驗的是**監測管線 + 偵測邏輯對真 WireGuard 的正確性**（parser、netns 共享、振盪偵測、告警落帳）。
- 它**不**取代 prod dogfood：你 prod 的 `ics-wg` 容器接法、真機 iOS/Android ntfy 推播，仍建議在 prod 用 [`../verify.sh`](../verify.sh) + 真機收一次。
