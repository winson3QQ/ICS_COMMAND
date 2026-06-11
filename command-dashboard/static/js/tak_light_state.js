/**
 * tak_light_state.js — TAK 連線燈狀態機（P2-23 #163 / P2-24 #164）。
 *
 * 零依賴純函式。`takConnState()` 是**單一狀態分類器**：把 GET /api/tak/status 物件
 * 歸到一個狀態碼，header 燈（`takLightState`）與管理面板狀態行（auth.js `_admRenderTakStatus`）
 * 各自把同一狀態碼映射成自己的呈現，**避免兩套判斷樹漂移 / 兩處示警不一致**。
 *
 * P2-24 關鍵：用 running / configured 區分三種「開了卻不綠」，消除舊版「沒 task 在跑
 * 卻顯示斷線重連中」的謊報——
 *   disabled      未啟用              → 灰(lkp)
 *   unconfigured  啟用·連線參數未備妥   → 黃(warn)（部署層問題，非 admin 在 UI 修）
 *   failed        啟用·已備妥·task 沒起 → 紅(crit)「啟動失敗」（非「重連中」，根本沒 task 在重連）
 *   disconnected  啟用·task 在跑·未連上 → 紅(crit)「斷線（背景重連中）」（這時才是真重連）
 *   ok            啟用·連上              → 綠(ok)「已連線（可收發）」
 *
 * #222：connected = ICS 作為 TAK client 已連上 server = **可收可發**，故一律綠。入向 CoT
 *   有無（last_cot_age_s）只反映「別的 client 有沒有在推」，非 ICS 連線健康——單一 client /
 *   安靜網段時無入向屬正常，舊版把它降為黃「無串流」會誤判可正常廣播的連線為故障。age 移入 tooltip。
 *
 * 向後相容：舊後端 status 無 configured/running 欄位時，`=== false` 不成立 → 不落入
 * unconfigured/failed，行為退回 enabled/connected 判定（connected 即綠）。
 */
export function takConnState(s) {
  if (!s.enabled) return 'disabled';
  if (s.configured === false) return 'unconfigured';
  if (s.running === false) return 'failed';
  if (!s.connected) return 'disconnected';
  return 'ok'; // #222：connected → 綠，不再因無入向串流降級為黃
}

// header 連線燈：狀態碼 → { level, title }（燈色 + tooltip）。
export function takLightState(s) {
  const age = s.last_cot_age_s;
  switch (takConnState(s)) {
    case 'disabled': return { level: 'lkp', title: 'TAK：未啟用' };
    case 'unconfigured': return { level: 'warn', title: 'TAK：已啟用 · 連線參數未備妥（部署層）' };
    case 'failed': return { level: 'crit', title: 'TAK：啟動失敗（檢查後端 log）' };
    case 'disconnected': return { level: 'crit', title: 'TAK：斷線（背景重連中）' };
    // #222：連上即綠「可收發」；入向 CoT age 僅作 tooltip 資訊，不降級顏色。
    default: return {
      level: 'ok',
      title: 'TAK：已連線（可收發）' + (age != null ? `，${age}s 前收到 CoT` : '，尚無入向串流'),
    };
  }
}
