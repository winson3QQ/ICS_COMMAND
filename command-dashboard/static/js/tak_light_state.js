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
 *   stale         啟用·連上·無串流(>120s) → 黃(warn)
 *   ok            啟用·連上·近期有 CoT  → 綠(ok)
 *
 * 向後相容：舊後端 status 無 configured/running 欄位時，`=== false` 不成立 → 不落入
 * unconfigured/failed，行為退回 P2-23 的 enabled/connected/age 三態。
 */
export function takConnState(s) {
  if (!s.enabled) return 'disabled';
  if (s.configured === false) return 'unconfigured';
  if (s.running === false) return 'failed';
  if (!s.connected) return 'disconnected';
  const age = s.last_cot_age_s;
  if (age == null || age > 120) return 'stale'; // 連上但無串流
  return 'ok';
}

// header 連線燈：狀態碼 → { level, title }（燈色 + tooltip）。
export function takLightState(s) {
  const age = s.last_cot_age_s;
  switch (takConnState(s)) {
    case 'disabled': return { level: 'lkp', title: 'TAK：未啟用' };
    case 'unconfigured': return { level: 'warn', title: 'TAK：已啟用 · 連線參數未備妥（部署層）' };
    case 'failed': return { level: 'crit', title: 'TAK：啟動失敗（檢查後端 log）' };
    case 'disconnected': return { level: 'crit', title: 'TAK：斷線（背景重連中）' };
    case 'stale': return {
      level: 'warn',
      title: 'TAK：連線中 · 無串流' + (age != null ? `（${age}s 前最後 CoT）` : '（尚未收到 CoT）'),
    };
    default: return { level: 'ok', title: `TAK：連線中（${age}s 前收到 CoT）` };
  }
}
