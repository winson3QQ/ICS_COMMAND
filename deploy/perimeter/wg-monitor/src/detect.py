# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""detect.py — WG 層連線異常偵測規則（#447；**純函式、無 IO，可單測**）。

doctrine（見 #447）：WG 已用密碼學擋掉未授權 peer，殘留的真實威脅 = **盜鑰（valid key, wrong hands）**。
WG 層唯一的破綻是 **endpoint 行為異常**。偵測器按精準度：

  1. endpoint 並存（cloned-key proxy）── **主菜、critical、零外部依賴**
     `wg show` 每個 peer 只顯「最後一次來源」，無法在單張 dump 看到兩個 endpoint；
     被複製的 key 會讓兩台裝置輪流握手 → endpoint 在短窗內**在 ≥2 個 IP 間擺盪**。
     故以「窗內 distinct IP 數 ≥2」推斷，而非單張快照。
  2. dormant_reactivation（沉睡 key 由新來源復活）── warning
  3. offline / online（liveness）── info
  4. endpoint_change（單純漫遊換 IP）── **info**（手機漫遊每天發生，刻意不升級，免淹沒）
  5. volume_spike（流量 vs 基線暴量）── warning（需學習期）

GeoIP/ASN 跳變偵測（#447 3b）暫不實作（GeoLite2 供應鏈未核准）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from wg_dump import PeerSample


@dataclass
class PriorState:
    """上一輪持久化的 peer 狀態（由 store 提供給純偵測用）。"""

    pubkey: str
    last_handshake: int
    last_endpoint_ip: str | None
    last_rx: int
    last_tx: int
    last_seen_ts: int  # 上次觀測的 wall-clock 秒
    online: bool
    vol_baseline: float  # 每 interval bytes 的 EWMA 基線
    vol_samples: int


@dataclass
class Detection:
    kind: str  # endpoint_oscillation | dormant_reactivation | offline | online | endpoint_change | volume_spike
    pubkey: str
    severity: str  # critical | warning | info
    summary: str
    detail: dict = field(default_factory=dict)


def is_online(last_handshake: int, now: int, offline_after_s: int) -> bool:
    """握手在門檻內 = 線上。0（從未握手）= 離線。"""
    return last_handshake > 0 and (now - last_handshake) <= offline_after_s


def detect_peer(
    cur: PeerSample,
    prior: PriorState | None,
    recent_ips: list[str],
    now: int,
    cfg,
) -> list[Detection]:
    """對單一 peer 跑所有規則。

    recent_ips = 振盪窗內的 endpoint **變更序列**（on-change 紀錄、依時間排序、含重訪），由 caller 取自 history。
    """
    out: list[Detection] = []
    online = is_online(cur.last_handshake, now, cfg.offline_after_s)

    # ── 1. endpoint 並存（cloned-key proxy）→ critical ──
    # 關鍵：單純漫遊 A→B 也會有 2 個 distinct IP，但**不會回頭**；被複製的 key 兩台裝置輪流握手 →
    # endpoint 來回擺盪、**某 IP 被重訪**（A→B→A）。on-change 序列中「重訪」⟺ len(序列) > len(distinct)。
    # 故判定 = distinct ≥2 **且** 序列長於 distinct（有重訪），把單次漫遊排除在外（降為 endpoint_change/info）。
    seq = [ip for ip in recent_ips if ip]
    distinct = sorted(set(seq))
    oscillation = len(distinct) >= 2 and len(seq) > len(distinct)
    if oscillation:
        out.append(
            Detection(
                kind="endpoint_oscillation",
                pubkey=cur.pubkey,
                severity="critical",
                summary=f"key 在 {cfg.oscillation_window_s}s 內於 {len(distinct)} 個來源間來回擺盪（疑似 key 被複製）",
                detail={"endpoints": distinct, "changes": len(seq)},
            )
        )

    # 首見 peer：不對 liveness/漫遊/流量報（冷啟動噪音）；並存仍會報（上面已處理）。
    if prior is None:
        return out

    # ── 2/3. liveness 轉換 + dormant 復活 ──
    if prior.online and not online:
        out.append(
            Detection("offline", cur.pubkey, "info", "裝置離線（握手停滯）", {"last_handshake": cur.last_handshake})
        )
    elif not prior.online and online:
        gap = cur.last_handshake - prior.last_handshake if prior.last_handshake > 0 else 0
        new_ep = bool(cur.endpoint_ip and cur.endpoint_ip != prior.last_endpoint_ip)
        if gap > cfg.dormant_after_s and new_ep:
            out.append(
                Detection(
                    "dormant_reactivation",
                    cur.pubkey,
                    "warning",
                    "沉睡 key 由新來源復活（疑似被盜用啟用）",
                    {"prev_endpoint": prior.last_endpoint_ip, "endpoint": cur.endpoint_ip, "silent_s": gap},
                )
            )
        else:
            out.append(Detection("online", cur.pubkey, "info", "裝置上線", {"endpoint": cur.endpoint_ip}))

    # ── 4. 單純 endpoint 變更（持續線上、IP 變、非並存）→ info（漫遊正常，不升級）──
    if (
        online
        and prior.online
        and cur.endpoint_ip
        and prior.last_endpoint_ip
        and cur.endpoint_ip != prior.last_endpoint_ip
        and not oscillation
    ):
        out.append(
            Detection(
                "endpoint_change",
                cur.pubkey,
                "info",
                "endpoint 變更（漫遊）",
                {"from": prior.last_endpoint_ip, "to": cur.endpoint_ip},
            )
        )

    # ── 5. 流量暴量（學習期後才報）──
    drx = max(0, cur.rx_bytes - prior.last_rx)  # max(0,..) 防 counter reset（重配 peer）
    dtx = max(0, cur.tx_bytes - prior.last_tx)
    delta = drx + dtx
    if prior.vol_samples >= cfg.volume_learning_samples and prior.vol_baseline > 0:
        threshold = max(cfg.volume_window_floor, prior.vol_baseline * cfg.volume_spike_factor)
        if delta > threshold:
            out.append(
                Detection(
                    "volume_spike",
                    cur.pubkey,
                    "warning",
                    f"流量暴量（本區間 {delta} bytes，基線約 {int(prior.vol_baseline)} bytes）",
                    {"delta": delta, "baseline": int(prior.vol_baseline)},
                )
            )
    return out


def next_baseline(prior: PriorState | None, cur: PeerSample, alpha: float = 0.2) -> tuple[float, int]:
    """回 (新 EWMA 基線, 新樣本數)。delta = 本輪相對上輪的 rx+tx 增量（防 reset）。"""
    if prior is None:
        return 0.0, 1
    delta = max(0, cur.rx_bytes - prior.last_rx) + max(0, cur.tx_bytes - prior.last_tx)
    baseline = delta if prior.vol_baseline <= 0 else (1 - alpha) * prior.vol_baseline + alpha * delta
    return baseline, prior.vol_samples + 1
