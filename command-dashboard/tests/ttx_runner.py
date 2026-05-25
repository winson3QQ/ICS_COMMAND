#!/usr/bin/env python3
"""
TTX 情境測試 Runner — 載入預建情境並透過 API 注入系統。

用法：
  python tests/ttx_runner.py --list                         # 列出可用情境
  python tests/ttx_runner.py --scenario 01_baseline         # 跑單一情境
  python tests/ttx_runner.py --scenario 01_baseline --batch # 無延遲全灌
  python tests/ttx_runner.py --all                          # 逐一跑全部情境
  python tests/ttx_runner.py --scenario 02 --api http://192.168.100.10:8000

每個情境會：
1. 自動設定 admin PIN + 建立測試帳號（如果不存在）
2. 建立 TTX session（session_type='exercise'）
3. 載入情境 injects
4. 依序 push 每個 inject
5. 結束 session
"""

import argparse
import glob
import json
import os
import sys
import time
import urllib.request
import urllib.error

API = "http://127.0.0.1:8000"
ADMIN_PIN = "1234"
TEST_USER = "ttx_runner"
TEST_PIN = "1234"


def _req(method, path, body=None, token=None, admin_pin=None, api=API):
    """送 HTTP request，回傳 (status_code, response_dict)"""
    url = api + path
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Session-Token"] = token
    if admin_pin:
        headers["X-Admin-PIN"] = admin_pin
    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8"))
        except Exception:
            detail = {"detail": str(e)}
        return e.code, detail
    except Exception as e:
        return 0, {"detail": str(e)}


def setup_auth(api=API):
    """確保 admin PIN 已設定 + 測試帳號存在 → 回傳 session token"""
    # 檢查 admin 狀態
    code, d = _req("GET", "/api/admin/status", api=api)
    if code != 200:
        print(f"  ✗ 無法連線 {api}：{d}")
        return None

    # 設定 admin PIN（如果尚未設定）
    if not d.get("admin_pin_setup"):
        code2, d2 = _req("POST", "/api/config/admin_pin",
                         {"value": ADMIN_PIN}, api=api)
        if code2 not in (200, 405):
            # 嘗試直接寫 config
            pass

    # 嘗試建立測試帳號（已存在會 409，正常）
    _req("POST", "/api/admin/accounts", {
        "username": TEST_USER,
        "pin": TEST_PIN,
        "role": "指揮官",
        "role_detail": "測試主持人",
        "display_name": "TTX Runner",
    }, admin_pin=ADMIN_PIN, api=api)

    # 登入
    code, d = _req("POST", "/api/auth/login", {
        "username": TEST_USER,
        "pin": TEST_PIN,
    }, api=api)
    if code != 200 or not d.get("ok"):
        print(f"  ✗ 登入失敗：{d}")
        return None
    return d["session_id"]


def list_scenarios(scenario_dir):
    """列出可用情境"""
    files = sorted(glob.glob(os.path.join(scenario_dir, "*.json")))
    if not files:
        print("  （無情境檔案）")
        return []
    print(f"\n可用情境（{len(files)} 個）：\n")
    scenarios = []
    for f in files:
        with open(f, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        sid = data.get("id", os.path.basename(f).replace(".json", ""))
        name = data.get("name", "")
        desc = data.get("description", "")[:60]
        n = len(data.get("injects", []))
        dur = data.get("duration_min", "?")
        print(f"  {sid:30s}  {n:2d} injects  {str(dur):>3s}min  {name}")
        scenarios.append(sid)
    print()
    return scenarios


def _now_utc():
    """回傳當前 UTC ISO 字串"""
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _patch_live_timestamp(payload, seq_counter):
    """live 模式：把 snapshot 的 t 和 snapshot_id 換成當前時間"""
    if isinstance(payload, dict) and "t" in payload:
        payload["t"] = _now_utc()
    if isinstance(payload, dict) and "snapshot_id" in payload:
        payload["snapshot_id"] = f"live-{seq_counter:06d}-{int(time.time()*1000) % 100000}"
    # forward units 的 last_update 也要換
    if isinstance(payload, dict):
        for u in payload.get("extra", {}).get("units", []):
            if "last_update" in u:
                u["last_update"] = _now_utc()
    return payload


def run_scenario(scenario_id, scenario_dir, token, api=API, batch=False,
                 live=False, live_duration=120):
    """執行單一情境。live 模式用當前時間 + 壓縮延遲讓 Dashboard 即時感受。"""
    # 找到情境檔
    fpath = None
    for f in glob.glob(os.path.join(scenario_dir, "*.json")):
        basename = os.path.basename(f).replace(".json", "")
        with open(f, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("id") == scenario_id or basename == scenario_id or basename.startswith(scenario_id):
            fpath = f
            break
    if not fpath:
        print(f"  ✗ 情境 '{scenario_id}' 不存在")
        return False

    with open(fpath, "r", encoding="utf-8") as fh:
        scenario = json.load(fh)

    name = scenario.get("name", scenario_id)
    injects = scenario.get("injects", [])
    expected = scenario.get("expected_dashboard", {})
    print(f"\n{'='*60}")
    print(f"情境：{name}")
    print(f"說明：{scenario.get('description', '')}")
    print(f"Injects：{len(injects)} 筆，預估 {scenario.get('duration_min', '?')} 分鐘")
    print(f"{'='*60}\n")

    # 建立 TTX session
    code, sess = _req("POST", "/api/ttx/sessions", {
        "session_name": name,
        "facilitator": TEST_USER,
        "scenario_id": scenario.get("id"),
    }, token=token, api=api)
    if code != 200:
        print(f"  ✗ 建立 session 失敗：{sess}")
        return False
    session_id = sess["id"]
    print(f"  Session: {session_id[:8]}...")

    # 開始演練
    _req("POST", f"/api/ttx/sessions/{session_id}/start", token=token, api=api)

    # 批次匯入 injects
    code, result = _req("POST", f"/api/ttx/sessions/{session_id}/injects", {
        "injects": injects,
    }, token=token, api=api)
    if code != 200:
        print(f"  ✗ 匯入 injects 失敗：{result}")
        return False
    print(f"  匯入 {result.get('imported', 0)} 筆 injects")

    # 取得所有 inject ID
    code, inject_list = _req("GET", f"/api/ttx/sessions/{session_id}/injects",
                             token=token, api=api)
    if code != 200:
        print(f"  ✗ 讀取 injects 失敗：{inject_list}")
        return False

    # 計算 live 模式的時間壓縮比
    max_offset = max((inj.get("scheduled_offset_min") or 0) for inj in inject_list) or 1
    if live:
        sec_per_min = live_duration / max_offset  # 例：120 秒 / 16 分鐘 = 7.5 秒/模擬分鐘
        print(f"  ▶ Live 模式：{max_offset} 模擬分鐘 → {live_duration} 秒（{sec_per_min:.1f} 秒/分鐘）")
        print(f"  ▶ 請在瀏覽器開 Dashboard → 點「演練」按鈕觀看\n")

    # 依序 push
    ok = 0
    err = 0
    prev_offset = 0
    live_seq = 0
    for i, inj in enumerate(inject_list):
        inject_id = inj["id"]
        title = inj.get("title", "")
        offset = inj.get("scheduled_offset_min") or 0

        # 延遲策略
        if live and offset > prev_offset:
            delay = (offset - prev_offset) * sec_per_min
            for remaining in range(int(delay), 0, -1):
                sys.stdout.write(f"\r  ⏳ 下一波 inject 在 {remaining} 秒後... （T+{offset}min）  ")
                sys.stdout.flush()
                time.sleep(1)
            sys.stdout.write("\r" + " " * 60 + "\r")
        elif not batch and not live and offset > prev_offset:
            delay = min((offset - prev_offset) * 0.5, 3)
            time.sleep(delay)
        prev_offset = offset

        # push inject（live 模式加 ?live=1 讓 server 端替換時間戳）
        push_url = f"/api/ttx/sessions/{session_id}/inject/{inject_id}/push"
        if live:
            push_url += "?live=true"
        code, result = _req("POST", push_url, token=token, api=api)
        if code == 200 and result.get("ok"):
            ok += 1
            status = "✓"
        else:
            err += 1
            status = f"✗ {code}"

        pct = (i + 1) / len(inject_list) * 100
        sys.stdout.write(f"\r  [{pct:5.1f}%] {status} T+{offset:>3d}min  {title[:40]}")
        sys.stdout.flush()

    print(f"\n\n  完成：{ok} 成功 / {err} 失敗")

    # 結束演練
    _req("POST", f"/api/ttx/sessions/{session_id}/end", token=token, api=api)

    # Dashboard 驗證提示
    if expected:
        print(f"\n  ▶ Dashboard 驗證（切到演練模式）：")
        for k, v in expected.items():
            print(f"    • {k}: {v}")

    print()
    return err == 0


def main():
    parser = argparse.ArgumentParser(description="TTX 情境測試 Runner")
    parser.add_argument("--list", action="store_true", help="列出可用情境")
    parser.add_argument("--scenario", type=str, help="執行指定情境（ID 或前綴）")
    parser.add_argument("--all", action="store_true", help="逐一執行所有情境")
    parser.add_argument("--batch", action="store_true", help="無延遲全灌（預設有縮時延遲）")
    parser.add_argument("--live", action="store_true", help="Live 模式：用當前時間、按比例延遲，Dashboard 即時觀看")
    parser.add_argument("--duration", type=int, default=120, help="Live 模式總時長秒數（預設 120 = 2 分鐘）")
    parser.add_argument("--api", type=str, default=API, help=f"Command Server URL（預設 {API}）")
    args = parser.parse_args()

    # 情境目錄
    scenario_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scenarios")
    if not os.path.isdir(scenario_dir):
        print(f"情境目錄不存在：{scenario_dir}")
        sys.exit(1)

    if args.list:
        list_scenarios(scenario_dir)
        return

    if not args.scenario and not args.all:
        parser.print_help()
        return

    # 認證
    print("認證中...")
    token = setup_auth(args.api)
    if not token:
        print("認證失敗，請確認 server 已啟動")
        sys.exit(1)
    print(f"  ✓ 已登入（{TEST_USER}）\n")

    if args.scenario:
        success = run_scenario(args.scenario, scenario_dir, token, args.api,
                               args.batch, live=args.live, live_duration=args.duration)
        sys.exit(0 if success else 1)

    if args.all:
        scenarios = list_scenarios(scenario_dir)
        total = len(scenarios)
        passed = 0
        for i, sid in enumerate(scenarios):
            print(f"\n[{i+1}/{total}]", end="")
            if run_scenario(sid, scenario_dir, token, args.api, args.batch,
                            live=args.live, live_duration=args.duration):
                passed += 1
        print(f"\n{'='*60}")
        print(f"結果：{passed}/{total} 通過")
        print(f"{'='*60}")
        sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
