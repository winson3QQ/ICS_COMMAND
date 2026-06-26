"""
calc_engine.py — ICS 計算引擎
實作規格第六部分（6.2～6.5）

所有函式都是純函式（pure function），輸入快照列表，輸出計算結果。
不直接讀資料庫，由 main.py 傳入資料。
"""

from datetime import UTC, datetime

import structlog as _structlog

_log = _structlog.get_logger()

# ──────────────────────────────────────────
# 門檻預設值（規格 6.5）
# 可由外部 threshold_settings 覆蓋
# ──────────────────────────────────────────

DEFAULT_THRESHOLDS = {
    "medical": {
        "bed_usage_yellow": 0.70,  # 70%
        "bed_usage_red": 0.90,  # 90%
        "red_casualties_yellow": 1,
        "red_casualties_red": 2,
        "waiting_yellow": 3,
        "waiting_red": 6,
        "supply_yellow": 0.30,  # 剩餘 30%
        "supply_red": 0.10,  # 剩餘 10%
        # Phase 2：Data freshness = snapshot_time 的新舊
        # <5 fresh（ok）／5–15 stale（warn）／≥15 LKP
        "freshness_warn_min": 5,
        "freshness_crit_min": 15,  # 實務上 lkp 會先 hit
        "lkp_min": 15,
    },
    "shelter": {
        "bed_usage_yellow": 0.70,
        "bed_usage_red": 0.90,
        "pending_intake_yellow": 5,
        "pending_intake_red": 10,
        "srt_red_yellow": 3,
        "srt_red_red": 6,
        "freshness_warn_min": 5,
        "freshness_crit_min": 15,
        "lkp_min": 15,
    },
    "forward": {
        "red_per_team_yellow": 1,
        "red_per_team_red": 2,
        "freshness_warn_min": 5,
        "freshness_crit_min": 15,
        "lkp_min": 15,
    },
    "security": {
        "freshness_warn_min": 5,
        "freshness_crit_min": 15,
        "lkp_min": 15,
    },
}

# 最小快照跨度（分鐘），才能計算趨勢
MIN_SPAN_FOR_TREND = 10


# ──────────────────────────────────────────
# 時間工具
# ──────────────────────────────────────────


def _parse_dt(s: str) -> datetime:
    """解析 ISO 8601 UTC 字串"""
    s = s.replace("Z", "+00:00")
    return datetime.fromisoformat(s)


def _now_utc() -> datetime:
    return datetime.now(UTC)


def minutes_ago(dt_str: str) -> float:
    """距離現在幾分鐘（正數 = 過去）"""
    try:
        dt = _parse_dt(dt_str)
        delta = _now_utc() - dt
        return delta.total_seconds() / 60
    except Exception:
        # Q1 凍結：不改 except 結構，只補 logger.error（C1-D）
        _log.error("calc_engine_error", msg="calc_engine 計算異常", detail={"func": "minutes_ago", "input": dt_str})
        return 999.0


# ──────────────────────────────────────────
# 6.5 新鮮度（Freshness）
# ──────────────────────────────────────────


def freshness(snapshot_time: str, node_type: str = "medical", thresholds: dict = None) -> dict:
    """
    回傳 {level: 'ok'|'warn'|'crit'|'lkp', minutes: float, label: str}
    """
    th = (thresholds or DEFAULT_THRESHOLDS).get(node_type, DEFAULT_THRESHOLDS["medical"])
    mins = minutes_ago(snapshot_time)

    lkp_min = th.get("lkp_min", 10)
    crit_min = th.get("freshness_crit_min", 5)
    warn_min = th.get("freshness_warn_min", 3)

    if mins >= lkp_min:
        level = "lkp"
        label = f"LKP {int(mins)}分"
    elif mins >= crit_min:
        level = "crit"
        label = f"逾時 {int(mins)}分"
    elif mins >= warn_min:
        level = "warn"
        label = f"{int(mins)}分前"
    else:
        level = "ok"
        label = "即時"

    return {"level": level, "minutes": round(mins, 1), "label": label}


# ──────────────────────────────────────────
# 6.2 趨勢計算
# ──────────────────────────────────────────


def trend(snapshots: list[dict], field: str, thresholds: dict = None) -> dict:
    """
    趨勢速率 = (現值 - N分前值) ÷ N
    輸入：依時間倒序的快照列表（最新在前）
    回傳：
      {
        rate: float | None,        # 每分鐘變化量
        direction: 'up'|'down'|'flat'|None,
        current: float | None,
        confidence: 'high'|'medium'|'low'|'insufficient',
        span_min: float,           # 使用的快照跨度
        note: str
      }
    """
    if not snapshots:
        return _no_trend("無快照資料")

    # 篩掉 field 為 None 的快照
    valid = [s for s in snapshots if s.get(field) is not None]
    if len(valid) < 2:
        return _no_trend("快照數量不足（需 ≥ 2 筆有效值）")

    newest = valid[0]
    oldest = valid[-1]

    span_min = _span_minutes(oldest["snapshot_time"], newest["snapshot_time"])
    if span_min < MIN_SPAN_FOR_TREND:
        # 資料存在但跨度不足，顯示但標低信心度
        rate = (newest[field] - oldest[field]) / max(span_min, 0.1)
        return {
            "rate": round(rate, 2),
            "direction": _direction(rate),
            "current": newest[field],
            "confidence": "low",
            "span_min": round(span_min, 1),
            "note": f"快照跨度 {round(span_min, 1)} 分鐘（< {MIN_SPAN_FOR_TREND} 分），信心度低",
        }

    rate = (newest[field] - oldest[field]) / span_min
    conf = "high" if span_min >= 30 else "medium"
    return {
        "rate": round(rate, 2),
        "direction": _direction(rate),
        "current": newest[field],
        "confidence": conf,
        "span_min": round(span_min, 1),
        "note": f"依據 {len(valid)} 筆快照，跨度 {round(span_min, 1)} 分鐘",
    }


def _direction(rate: float) -> str:
    if rate > 0.05:
        return "up"
    elif rate < -0.05:
        return "down"
    return "flat"


def _no_trend(note: str) -> dict:
    return {"rate": None, "direction": None, "current": None, "confidence": "insufficient", "span_min": 0, "note": note}


def _span_minutes(older_dt_str: str, newer_dt_str: str) -> float:
    try:
        older = _parse_dt(older_dt_str)
        newer = _parse_dt(newer_dt_str)
        return (newer - older).total_seconds() / 60
    except Exception:
        # Q1 凍結：不改 except 結構，只補 logger.error（C1-D）
        _log.error(
            "calc_engine_error",
            msg="calc_engine 計算異常",
            detail={"func": "_span_minutes", "input": [older_dt_str, newer_dt_str]},
        )
        return 0.0


# ──────────────────────────────────────────
# 6.4 警戒倒數計算
# ──────────────────────────────────────────


def countdown(snapshots: list[dict], field: str, threshold_value: float, node_type: str = "medical") -> dict:
    """
    預計到達門檻時間 = (門檻值 - 現值) ÷ 趨勢速率
    回傳：
      {
        minutes_to_threshold: float | None,
        label: str,
        confidence: str,
        already_breached: bool
      }
    """
    t = trend(snapshots, field)

    if t["current"] is None:
        return {
            "minutes_to_threshold": None,
            "label": "資料不足",
            "confidence": "insufficient",
            "already_breached": False,
        }

    current = t["current"]
    if current >= threshold_value:
        return {"minutes_to_threshold": 0, "label": "已超門檻", "confidence": t["confidence"], "already_breached": True}

    if t["rate"] is None or t["rate"] <= 0:
        return {
            "minutes_to_threshold": None,
            "label": "趨勢穩定或下降，無倒數",
            "confidence": t["confidence"],
            "already_breached": False,
        }

    mins = (threshold_value - current) / t["rate"]
    mins = round(mins, 1)

    if t["confidence"] == "insufficient":
        label = "資料不足"
    elif t["confidence"] == "low":
        label = f"≈{int(mins)}分（信心度低）"
    else:
        label = f"{int(mins)}分鐘後達門檻"

    return {"minutes_to_threshold": mins, "label": label, "confidence": t["confidence"], "already_breached": False}


# ──────────────────────────────────────────
# 6.3 醫療壓力指數
# ──────────────────────────────────────────


def medical_pressure_index(
    medical_snap: dict | None,
    shelter_snap: dict | None,
    forward_snap: dict | None,
    security_snap: dict | None,
    coefficients: dict = None,
) -> dict:
    """
    MPI = (等待÷床位剩餘) + (前進傷患×0.8) + (收容升級×0.3) + (安全事件×0.1)
    規格 6.3
    """
    coef = coefficients or {
        "forward": 0.8,
        "shelter": 0.3,
        "security": 0.1,
    }

    components = {}

    # ① 等待壓力：等待÷床位剩餘
    if medical_snap:
        waiting = medical_snap.get("waiting_count") or 0
        bed_used = medical_snap.get("bed_used") or 0
        bed_total = medical_snap.get("bed_total") or 10
        remaining = max(bed_total - bed_used, 1)  # 避免除以零
        wait_pressure = waiting / remaining
        components["waiting"] = round(wait_pressure, 2)
    else:
        components["waiting"] = 0

    # ② 前進組傷患壓力
    if forward_snap:
        extra = forward_snap.get("extra") or {}
        units = extra.get("units", [])
        if units:
            # 多小隊：加總
            fwd_red = sum(u.get("casualties", {}).get("red", 0) for u in units)
        else:
            fwd_red = forward_snap.get("casualties_red") or 0
        components["forward"] = round(fwd_red * coef["forward"], 2)
    else:
        components["forward"] = 0

    # ③ 收容組 SRT 升級壓力
    if shelter_snap:
        extra = shelter_snap.get("extra") or {}
        srt = extra.get("srt", {})
        srt_red = srt.get("red", 0) if srt else 0
        components["shelter"] = round(srt_red * coef["shelter"], 2)
    else:
        components["shelter"] = 0

    # ④ 安全事件密度（以隔離人數近似）
    if security_snap:
        extra = security_snap.get("extra") or {}
        iso = extra.get("isolation_count", 0) or 0
        components["security"] = round(iso * coef["security"], 2)
    else:
        components["security"] = 0

    total = sum(components.values())

    # 判斷等級
    if total >= 3.0:
        level = "critical"
    elif total >= 1.5:
        level = "warning"
    else:
        level = "normal"

    return {
        "index": round(total, 2),
        "level": level,
        "components": components,
        "note": (
            f"等待壓力 {components['waiting']} + "
            f"前進 {components['forward']} + "
            f"收容 {components['shelter']} + "
            f"安全 {components['security']}"
        ),
    }


# ──────────────────────────────────────────
# 整合：產生儀表板所需的所有計算結果
# ──────────────────────────────────────────


def dashboard_calc(
    medical_snaps: list[dict],
    shelter_snaps: list[dict],
    forward_snaps: list[dict],
    security_snaps: list[dict],
    thresholds: dict = None,
    open_event_count: int = 0,
    event_trend_up: bool = False,
) -> dict:
    """
    一次計算所有儀表板需要的數字。
    main.py 呼叫這個函式，回傳給前端。
    """
    th = thresholds or DEFAULT_THRESHOLDS

    med = medical_snaps[0] if medical_snaps else None
    shel = shelter_snaps[0] if shelter_snaps else None
    fwd = forward_snaps[0] if forward_snaps else None
    sec = security_snaps[0] if security_snaps else None

    # 新鮮度
    freshness_medical = freshness(med["snapshot_time"], "medical", th) if med else _stale()
    freshness_shelter = freshness(shel["snapshot_time"], "shelter", th) if shel else _stale()
    freshness_forward = freshness(fwd["snapshot_time"], "forward", th) if fwd else _stale()
    freshness_security = freshness(sec["snapshot_time"], "security", th) if sec else _stale()

    # 醫療趨勢
    med_bed_trend = trend(medical_snaps, "bed_used")
    med_waiting_trend = trend(medical_snaps, "waiting_count")

    # 醫療警戒倒數（床位達 90%）
    med_bed_total = med["bed_total"] if med and med.get("bed_total") else 10
    med_red_threshold = int(med_bed_total * th["medical"]["bed_usage_red"])
    med_countdown = countdown(medical_snaps, "bed_used", med_red_threshold, "medical")

    # 收容趨勢
    shel_bed_trend = trend(shelter_snaps, "bed_used")

    # 醫療壓力指數
    mpi = medical_pressure_index(med, shel, fwd, sec)

    # 前進組資訊
    forward_units = _parse_forward_units(fwd)

    # 信心度低的預測數量
    low_conf_count = sum(
        1 for t in [med_bed_trend, med_waiting_trend, shel_bed_trend] if t["confidence"] in ("low", "insufficient")
    )

    # ── Wave 1.3 新增：三項智慧 ──

    # 物資消耗速率（醫療 + 收容各自的物資）
    burn_rates = {}
    # 醫療物資 keys
    med_supply_keys = set()
    for s in medical_snaps:
        extra = s.get("extra") or {}
        med_supply_keys.update((extra.get("supplies") or {}).keys())
    for key in med_supply_keys:
        burn_rates[f"medical_{key}"] = burn_rate(medical_snaps, key)
    # 收容物資 keys
    shel_supply_keys = set()
    for s in shelter_snaps:
        extra = s.get("extra") or {}
        shel_supply_keys.update((extra.get("supplies") or {}).keys())
    for key in shel_supply_keys:
        burn_rates[f"shelter_{key}"] = burn_rate(shelter_snaps, key)

    # 通訊健康度
    comm = {
        "medical": comm_health(medical_snaps, "medical", th),
        "shelter": comm_health(shelter_snaps, "shelter", th),
        "forward": comm_health(forward_snaps, "forward", th),
        "security": comm_health(security_snaps, "security", th),
    }

    # 產出監控
    output = output_monitor(medical_snaps, shelter_snaps)

    # ── Wave 2 新增：DCI（資料信心度指數）──
    freshness_all = {
        "medical": freshness_medical,
        "shelter": freshness_shelter,
        "forward": freshness_forward,
        "security": freshness_security,
    }
    trend_all = [med_bed_trend, med_waiting_trend, shel_bed_trend]
    dci = data_confidence_index(freshness_all, comm, trend_all)

    # ── 升降級檢查 ──
    esc = escalation_check(
        shelter_snaps,
        medical_snaps,
        forward_snaps,
        security_snaps,
        burn_rates,
        events_open_count=open_event_count,
        events_trend_up=event_trend_up,
    )

    return {
        "computed_at": _now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "medical": {
            "snapshot": med,
            "freshness": freshness_medical,
            "bed_trend": med_bed_trend,
            "waiting_trend": med_waiting_trend,
            "countdown_to_red": med_countdown,
            "source_breakdown": _extract_source_breakdown(med),
            "incident_pressure": _extract_ipi(med),
            "supplies": _extract_supplies(med),
            "ops_metrics": _extract_ops_metrics(med),
        },
        "shelter": {
            "snapshot": shel,
            "freshness": freshness_shelter,
            "bed_trend": shel_bed_trend,
            "incident_pressure": _extract_ipi(shel),
            "supplies": _extract_supplies(shel),
            "ops_metrics": _extract_ops_metrics(shel),
        },
        "forward": {
            "snapshot": fwd,
            "freshness": freshness_forward,
            "units": forward_units,
        },
        "security": {
            "snapshot": sec,
            "freshness": freshness_security,
        },
        "medical_pressure": mpi,
        "low_confidence_count": low_conf_count,
        "burn_rates": burn_rates,
        "comm_health": comm,
        "output_monitor": output,
        "data_confidence": dci,
        "escalation": esc,
    }


def _extract_source_breakdown(snap: dict | None) -> dict:
    """從快照 extra 欄位取得醫療入站來源分布"""
    if not snap:
        return {"a": 0, "b": 0, "c": 0}
    extra = snap.get("extra") or {}
    return {
        "a": extra.get("src_a", 0),
        "b": extra.get("src_b", 0),
        "c": extra.get("src_c", 0),
    }


def _extract_ipi(snap: dict | None) -> dict:
    """從快照 extra.incident_pressure 取事件壓力三維指標"""
    default = {
        "high": 0,
        "medium": 0,
        "low": 0,
        "ipi": 0,
        "recent_types": [],
        "open_total": 0,
        "resolved_30min": 0,
    }
    if not snap:
        return default
    extra = snap.get("extra") or {}
    ip = extra.get("incident_pressure")
    if not ip:
        return default
    return {
        "high": ip.get("high", 0),
        "medium": ip.get("medium", 0),
        "low": ip.get("low", 0),
        "ipi": ip.get("ipi", 0),
        "recent_types": ip.get("recent_types", []),
        "open_total": ip.get("open_total", 0),
        "resolved_30min": ip.get("resolved_30min", 0),
    }


def _extract_supplies(snap: dict | None) -> dict:
    """從快照 extra 取物資當前值與最大值"""
    default = {"current": {}, "max": {}}
    if not snap:
        return default
    extra = snap.get("extra") or {}
    return {
        "current": extra.get("supplies", {}),
        "max": extra.get("supplies_max", {}),
    }


def _extract_ops_metrics(snap: dict | None) -> dict:
    """從快照 extra 取人力與營運指標"""
    default = {
        "active_staff": None,
        "staff_ratio": None,
        "stuck_count": 0,
        "stuck_rate": 0,
    }
    if not snap:
        return default
    extra = snap.get("extra") or {}
    return {
        "active_staff": extra.get("active_staff"),
        "staff_ratio": extra.get("staff_ratio"),
        "stuck_count": extra.get("stuck_count", 0),
        "stuck_rate": extra.get("stuck_rate", 0),
    }


def _stale() -> dict:
    return {"level": "lkp", "minutes": 999, "label": "無資料"}


# ──────────────────────────────────────────
# 物資消耗速率（Resource Burn Rate）
# ──────────────────────────────────────────


def burn_rate(snapshots: list[dict], supply_key: str) -> dict:
    """
    從最近 N 筆快照計算物資消耗速率。
    supply_key: extra.supplies 中的 key（如 "iv_fluid", "bandage"）

    回傳：
      {
        rate_per_min: float | None,   # 每分鐘消耗量（正數=消耗中）
        current: int | None,          # 目前存量
        max_val: int | None,          # 最大存量
        pct_remaining: float | None,  # 剩餘百分比 0~1
        time_to_zero_min: float | None,  # 預估歸零時間（分鐘）
        level: str,                   # ok / warn / crit
        note: str
      }
    """
    no_data = {
        "rate_per_min": None,
        "current": None,
        "max_val": None,
        "pct_remaining": None,
        "time_to_zero_min": None,
        "level": "ok",
        "note": "無物資資料",
    }

    # 收集有效資料點：(snapshot_time, supply_value)
    points = []
    max_val = None
    for s in snapshots:
        extra = s.get("extra") or {}
        supplies = extra.get("supplies") or {}
        val = supplies.get(supply_key)
        if val is not None:
            points.append((s.get("snapshot_time", ""), val))
        # 取 max 值（取最大的那個）
        supplies_max = extra.get("supplies_max") or {}
        m = supplies_max.get(supply_key)
        if m is not None:
            max_val = m

    if not points:
        return no_data

    current = points[0][1]  # 最新值（snapshots 倒序）

    # 計算剩餘百分比
    pct = None
    if max_val and max_val > 0:
        pct = round(current / max_val, 3)

    if len(points) < 2:
        level = "ok"
        if pct is not None and pct < 0.10:
            level = "crit"
        elif pct is not None and pct < 0.30:
            level = "warn"
        return {
            "rate_per_min": None,
            "current": current,
            "max_val": max_val,
            "pct_remaining": pct,
            "time_to_zero_min": None,
            "level": level,
            "note": "僅一筆資料，無法計算消耗速率",
        }

    # 用最新和最舊有效點計算速率
    newest_t, newest_v = points[0]
    oldest_t, oldest_v = points[-1]
    span = _span_minutes(oldest_t, newest_t)

    if span < 1:
        return {
            "rate_per_min": None,
            "current": current,
            "max_val": max_val,
            "pct_remaining": pct,
            "time_to_zero_min": None,
            "level": "ok",
            "note": "時間跨度不足",
        }

    # rate > 0 表示在消耗（值在減少）
    rate = (oldest_v - newest_v) / span
    time_to_zero = None
    if rate > 0 and current > 0:
        time_to_zero = round(current / rate, 1)

    # 判斷等級
    if time_to_zero is not None and time_to_zero < 120:  # < 2h
        level = "crit"
    elif time_to_zero is not None and time_to_zero < 240:  # < 4h
        level = "warn"
    elif pct is not None and pct < 0.10:
        level = "crit"
    elif pct is not None and pct < 0.30:
        level = "warn"
    else:
        level = "ok"

    return {
        "rate_per_min": round(rate, 3),
        "current": current,
        "max_val": max_val,
        "pct_remaining": pct,
        "time_to_zero_min": time_to_zero,
        "level": level,
        "note": (
            f"消耗 {round(rate, 2)}/min，"
            f"剩餘 {current}"
            + (f"（{round(pct * 100)}%）" if pct is not None else "")
            + (f"，預估 {int(time_to_zero)} 分鐘歸零" if time_to_zero else "")
        ),
    }


# ──────────────────────────────────────────
# 通訊健康度（Communication Health）
# ──────────────────────────────────────────


def comm_health(snapshots: list[dict], node_type: str, thresholds: dict = None) -> dict:
    """
    綜合評估某節點的通訊健康：
    1. freshness — 最新快照新鮮度
    2. gap_detected — 快照間隔是否有異常大的空洞
    3. zero_anomaly — 關鍵欄位是否突然歸零

    回傳：
      {
        freshness: dict,       # freshness() 結果
        gap_detected: bool,    # 是否偵測到通訊空洞
        gap_max_min: float,    # 最大間隔（分鐘）
        zero_anomaly: bool,    # 關鍵欄位歸零異常
        zero_fields: list,     # 歸零的欄位名
        health_level: str,     # ok / warn / crit / lkp
        note: str
      }
    """
    th = (thresholds or DEFAULT_THRESHOLDS).get(node_type, DEFAULT_THRESHOLDS.get("medical", {}))

    if not snapshots:
        return {
            "freshness": _stale(),
            "gap_detected": False,
            "gap_max_min": 0,
            "zero_anomaly": False,
            "zero_fields": [],
            "health_level": "lkp",
            "note": "無快照",
        }

    # 1. 新鮮度
    fresh = freshness(snapshots[0].get("snapshot_time", ""), node_type, thresholds)

    # 2. 間隔空洞偵測
    gap_max = 0.0
    gap_detected = False
    expected_interval = th.get("freshness_crit_min", 5) * 2  # 超過 2 倍正常間隔視為空洞
    if len(snapshots) >= 2:
        for i in range(len(snapshots) - 1):
            t1 = snapshots[i].get("snapshot_time", "")
            t2 = snapshots[i + 1].get("snapshot_time", "")
            gap = _span_minutes(t2, t1)
            if gap > gap_max:
                gap_max = gap
        if gap_max > expected_interval:
            gap_detected = True

    # 3. 欄位歸零偵測（前一筆 > 0，本筆突然 = 0）
    zero_fields = []
    check_fields = {
        "medical": ["bed_used", "waiting_count"],
        "shelter": ["bed_used"],
        "forward": [],
        "security": [],
    }.get(node_type, [])

    if len(snapshots) >= 2:
        curr = snapshots[0]
        prev = snapshots[1]
        for f in check_fields:
            curr_val = curr.get(f)
            prev_val = prev.get(f)
            if prev_val and prev_val > 0 and (curr_val == 0 or curr_val is None):
                zero_fields.append(f)

    zero_anomaly = len(zero_fields) > 0

    # 綜合等級
    if fresh["level"] == "lkp":
        health_level = "lkp"
    elif fresh["level"] == "crit" or (gap_detected and gap_max > expected_interval * 2):
        health_level = "crit"
    elif fresh["level"] == "warn" or gap_detected or zero_anomaly:
        health_level = "warn"
    else:
        health_level = "ok"

    notes = []
    if gap_detected:
        notes.append(f"偵測到通訊空洞（最大間隔 {int(gap_max)} 分鐘）")
    if zero_anomaly:
        notes.append(f"欄位歸零異常：{', '.join(zero_fields)}")

    return {
        "freshness": fresh,
        "gap_detected": gap_detected,
        "gap_max_min": round(gap_max, 1),
        "zero_anomaly": zero_anomaly,
        "zero_fields": zero_fields,
        "health_level": health_level,
        "note": "；".join(notes) if notes else "通訊正常",
    }


# ──────────────────────────────────────────
# 產出監控（Output Monitor）
# ──────────────────────────────────────────


def output_monitor(medical_snaps: list[dict], shelter_snaps: list[dict]) -> dict:
    """
    監控系統的「產出」端：後送效率、離站效率、積壓狀況。

    回傳：
      {
        evac_backlog: int | None,       # 待後送積壓人數
        evac_trend: str | None,         # up / down / flat
        discharge_rate: float | None,   # 離站速率（人/分鐘）
        shelter_exit_rate: float | None,  # 收容離站速率
        level: str,                     # ok / warn / crit
        note: str
      }
    """
    result = {
        "evac_backlog": None,
        "evac_trend": None,
        "discharge_rate": None,
        "shelter_exit_rate": None,
        "level": "ok",
        "note": "產出正常",
    }

    notes = []

    # 醫療後送積壓
    if medical_snaps:
        med = medical_snaps[0]
        pending = med.get("pending_evac")
        result["evac_backlog"] = pending

        # 趨勢
        evac_t = trend(medical_snaps, "pending_evac")
        result["evac_trend"] = evac_t.get("direction")

        # discharge rate（evacuated_total 的增長速率）
        if len(medical_snaps) >= 2:
            valid = [s for s in medical_snaps if s.get("evacuated_total") is not None]
            if len(valid) >= 2:
                span = _span_minutes(valid[-1].get("snapshot_time", ""), valid[0].get("snapshot_time", ""))
                if span > 0:
                    diff = valid[0].get("evacuated_total", 0) - valid[-1].get("evacuated_total", 0)
                    result["discharge_rate"] = round(diff / span, 3)

        # 判斷：pending_evac 持續上升 = 積壓
        if pending is not None and pending >= 5:
            notes.append(f"後送積壓 {pending} 人")
            result["level"] = "crit"
        elif pending is not None and pending >= 3:
            notes.append(f"後送待撤 {pending} 人")
            result["level"] = "warn"

    # 收容離站速率
    if shelter_snaps and len(shelter_snaps) >= 2:
        extra_newest = shelter_snaps[0].get("extra") or {}
        extra_oldest = shelter_snaps[-1].get("extra") or {}
        exit_newest = extra_newest.get("exited_total", 0) or 0
        exit_oldest = extra_oldest.get("exited_total", 0) or 0
        span = _span_minutes(shelter_snaps[-1].get("snapshot_time", ""), shelter_snaps[0].get("snapshot_time", ""))
        if span > 0 and exit_newest >= exit_oldest:
            result["shelter_exit_rate"] = round((exit_newest - exit_oldest) / span, 3)

    if notes:
        result["note"] = "；".join(notes)

    return result


# ──────────────────────────────────────────
# 升降級檢查（Escalation Check）
# ──────────────────────────────────────────


def escalation_check(
    shelter_snaps: list[dict],
    medical_snaps: list[dict],
    forward_snaps: list[dict],
    security_snaps: list[dict],
    burn_rates: dict,
    events_open_count: int = 0,
    events_trend_up: bool = False,
) -> dict:
    """
    檢查升級 / 降級條件，回傳觸發的規則與整體等級。
    純函式，不讀 DB。

    回傳：
      {
        "triggers_met": [...],   # 升級規則
        "deescalation": [...],   # 降級規則
        "level": "normal" | "elevated" | "critical"
      }
    """
    triggers: list[dict] = []
    deesc: list[dict] = []

    # ── 升級規則 ──

    # ESC-CAP：收容率 > 80% 持續 30 分鐘
    if shelter_snaps:
        cap_snaps_30 = _snaps_within_minutes(shelter_snaps, 30)
        if len(cap_snaps_30) >= 2:
            all_above_80 = all(_bed_ratio(s) > 0.80 for s in cap_snaps_30)
            all_above_90 = all(_bed_ratio(s) > 0.90 for s in cap_snaps_30)
            if all_above_90:
                triggers.append(
                    {
                        "rule_id": "ESC-CAP",
                        "description": "收容率 > 90% 持續 30 分鐘",
                        "severity": "critical",
                        "direction": "escalation",
                    }
                )
            elif all_above_80:
                triggers.append(
                    {
                        "rule_id": "ESC-CAP",
                        "description": "收容率 > 80% 持續 30 分鐘",
                        "severity": "warning",
                        "direction": "escalation",
                    }
                )

    # ESC-RED：Red 傷患佔比突然上升
    if len(medical_snaps) >= 6:
        recent_3 = medical_snaps[:3]
        prev_3 = medical_snaps[3:6]
        avg_recent = sum(s.get("casualties_red", 0) or 0 for s in recent_3) / 3
        avg_prev = sum(s.get("casualties_red", 0) or 0 for s in prev_3) / 3
        abs_increase = avg_recent - avg_prev
        ratio = avg_recent / avg_prev if avg_prev > 0 else None
        if abs_increase >= 2 or (ratio is not None and ratio >= 1.5):
            triggers.append(
                {
                    "rule_id": "ESC-RED",
                    "description": f"Red 傷患急升（近 3 筆均值 {avg_recent:.1f} vs 前 3 筆 {avg_prev:.1f}）",
                    "severity": "warning",
                    "direction": "escalation",
                }
            )

    # ESC-INC：未結事件 > 5 且持續增加
    if events_open_count > 5 and events_trend_up:
        triggers.append(
            {
                "rule_id": "ESC-INC",
                "description": f"未結事件 {events_open_count} 件且持續增加",
                "severity": "warning",
                "direction": "escalation",
            }
        )

    # ESC-STAFF：staff_ratio > 8（超載）
    for label, snaps in [("收容組", shelter_snaps), ("醫療組", medical_snaps)]:
        if snaps:
            extra = snaps[0].get("extra") or {}
            sr = extra.get("staff_ratio")
            if sr is not None and sr > 8:
                triggers.append(
                    {
                        "rule_id": "ESC-STAFF",
                        "description": f"{label} staff_ratio={sr}（超載）",
                        "severity": "critical",
                        "direction": "escalation",
                    }
                )

    # ESC-SUPPLY：burn rate time_to_zero < 120 min
    for key, br in burn_rates.items():
        ttz = br.get("time_to_zero_min")
        if ttz is not None and ttz < 120:
            triggers.append(
                {
                    "rule_id": "ESC-SUPPLY",
                    "description": f"物資 {key} 預估 {int(ttz)} 分鐘歸零",
                    "severity": "critical",
                    "direction": "escalation",
                }
            )

    # ── 降級規則 ──

    # DE-CAP：收容率 < 50% 持續 1 小時
    if shelter_snaps:
        cap_snaps_60 = _snaps_within_minutes(shelter_snaps, 60)
        if len(cap_snaps_60) >= 2:
            all_below_50 = all(_bed_ratio(s) < 0.50 for s in cap_snaps_60)
            if all_below_50:
                deesc.append(
                    {
                        "rule_id": "DE-CAP",
                        "description": "收容率 < 50% 持續 1 小時",
                        "direction": "deescalation",
                    }
                )

    # DE-RED：無新 Red 傷患超過 45 分鐘
    if medical_snaps:
        red_snaps_45 = _snaps_within_minutes(medical_snaps, 45)
        if len(red_snaps_45) >= 2:
            # 所有快照 casualties_red == 0 或數值不變
            red_vals = [s.get("casualties_red", 0) or 0 for s in red_snaps_45]
            all_zero_or_stable = all(v == red_vals[0] for v in red_vals) or all(v == 0 for v in red_vals)
            if all_zero_or_stable:
                deesc.append(
                    {
                        "rule_id": "DE-RED",
                        "description": "無新 Red 傷患超過 45 分鐘",
                        "direction": "deescalation",
                    }
                )

    # DE-INC：events_open_count == 0
    if events_open_count == 0:
        deesc.append(
            {
                "rule_id": "DE-INC",
                "description": "無未結事件",
                "direction": "deescalation",
            }
        )

    # DE-STAFF：全部組 staff_ratio < 3
    all_staff_low = True
    for snaps in [shelter_snaps, medical_snaps]:
        if snaps:
            extra = snaps[0].get("extra") or {}
            sr = extra.get("staff_ratio")
            if sr is not None and sr >= 3:
                all_staff_low = False
                break
        # 無資料視為不適用，不阻擋降級
    if all_staff_low:
        deesc.append(
            {
                "rule_id": "DE-STAFF",
                "description": "全部組 staff_ratio < 3",
                "direction": "deescalation",
            }
        )

    # DE-SUPPLY：所有物資 burn rate rate_per_min ≤ 0 或 None
    if burn_rates:
        all_stable = all((br.get("rate_per_min") is None or br.get("rate_per_min") <= 0) for br in burn_rates.values())
        if all_stable:
            deesc.append(
                {
                    "rule_id": "DE-SUPPLY",
                    "description": "所有物資消耗穩定或無消耗",
                    "direction": "deescalation",
                }
            )

    # ── level 判斷 ──
    severities = [t["severity"] for t in triggers]
    if "critical" in severities:
        level = "critical"
    elif "warning" in severities:
        level = "elevated"
    else:
        level = "normal"

    return {
        "triggers_met": triggers,
        "deescalation": deesc,
        "level": level,
    }


def _snaps_within_minutes(snaps: list[dict], minutes: int) -> list[dict]:
    """從倒序快照列表中取出最近 N 分鐘內的快照"""
    if not snaps:
        return []
    newest_time = snaps[0].get("snapshot_time", "")
    if not newest_time:
        return snaps[:1]
    result = []
    for s in snaps:
        st = s.get("snapshot_time", "")
        if not st:
            continue
        span = _span_minutes(st, newest_time)
        if span <= minutes:
            result.append(s)
        else:
            break  # 快照倒序，一旦超出範圍後面的更老
    return result


def _bed_ratio(snap: dict) -> float:
    """計算床位使用率，避免除以零"""
    used = snap.get("bed_used", 0) or 0
    total = snap.get("bed_total", 0) or 0
    if total <= 0:
        return 0.0
    return used / total


# ──────────────────────────────────────────
# Wave 2：DCI（Data Confidence Index，資料信心度指數）
# ──────────────────────────────────────────


def data_confidence_index(
    freshness_all: dict[str, dict],
    comm_all: dict[str, dict],
    trends: list[dict],
) -> dict:
    """
    DCI = 加權平均(
      freshness_score × 0.4,
      coverage_score × 0.3,
      trend_confidence_score × 0.2,
      comm_health_score × 0.1,
    )

    freshness_all: {"medical": freshness(), "shelter": ..., ...}
    comm_all:      {"medical": comm_health(), ...}
    trends:        [trend(), trend(), ...]

    回傳 { overall: 0~100, components: {...}, level: 'high'|'medium'|'low' }
    """
    # ① 新鮮度分數：四組平均，level → 分數
    _fresh_score = {"ok": 100, "warn": 60, "crit": 30, "lkp": 0}
    fresh_scores = [_fresh_score.get(f.get("level", "lkp"), 0) for f in freshness_all.values()]
    freshness_score = sum(fresh_scores) / max(len(fresh_scores), 1)

    # ② 覆蓋率：四組中有幾組有資料（非 lkp）
    covered = sum(1 for f in freshness_all.values() if f.get("level") != "lkp")
    coverage_score = covered / max(len(freshness_all), 1) * 100

    # ③ 趨勢信心度平均
    _conf_score = {"high": 100, "medium": 70, "low": 30, "insufficient": 0}
    trend_scores = [_conf_score.get(t.get("confidence", "insufficient"), 0) for t in trends]
    trend_score = sum(trend_scores) / max(len(trend_scores), 1) if trend_scores else 0

    # ④ 通訊健康度平均
    _health_score = {"ok": 100, "warn": 60, "crit": 30, "lkp": 0}
    comm_scores = [_health_score.get(c.get("health_level", "lkp"), 0) for c in comm_all.values()]
    comm_score = sum(comm_scores) / max(len(comm_scores), 1)

    # 加權合計
    overall = freshness_score * 0.4 + coverage_score * 0.3 + trend_score * 0.2 + comm_score * 0.1
    overall = round(overall, 1)

    # 判斷等級
    if overall >= 70:
        level = "high"
    elif overall >= 40:
        level = "medium"
    else:
        level = "low"

    return {
        "overall": overall,
        "level": level,
        "components": {
            "freshness": round(freshness_score, 1),
            "coverage": round(coverage_score, 1),
            "trend_confidence": round(trend_score, 1),
            "comm_health": round(comm_score, 1),
        },
    }


def _parse_forward_units(fwd_snap: dict | None) -> list[dict]:
    """從快照解析前進小隊列表（支援多小隊 QR-FORWARD 格式）"""
    if not fwd_snap:
        return []
    extra = fwd_snap.get("extra") or {}
    units = extra.get("units")
    if units:
        # QR-FORWARD 多小隊格式
        result = []
        now_str = _now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")
        for u in units:
            last_update = u.get("last_update", fwd_snap.get("snapshot_time", now_str))
            f = freshness(last_update, "forward")
            result.append(
                {
                    "unit": u.get("unit"),
                    "casualties": u.get("casualties", {}),
                    "ccp_status": u.get("ccp_status"),
                    "vehicle_needed": u.get("vehicle_needed", 0),
                    "hazard": u.get("hazard", "none"),
                    "freshness": f,
                }
            )
        return result
    else:
        # 單小隊格式（舊式）
        f = freshness(fwd_snap.get("snapshot_time", ""), "forward")
        return [
            {
                "unit": "A",
                "casualties": {
                    "red": fwd_snap.get("casualties_red", 0),
                    "yellow": fwd_snap.get("casualties_yellow", 0),
                    "green": fwd_snap.get("casualties_green", 0),
                    "black": fwd_snap.get("casualties_black", 0),
                },
                "ccp_status": None,
                "vehicle_needed": 0,
                "hazard": "none",
                "freshness": f,
            }
        ]
