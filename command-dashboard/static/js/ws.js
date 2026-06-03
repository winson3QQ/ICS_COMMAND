/**
 * ws.js — HTTP Polling 連線管理層（C1-F CSP 模組化，v3.0.0）
 *
 * 職責：
 *   - 管理 _pollActive 旗標與輪詢計時器
 *   - 訂閱 auth.js 的 onAuthChange 事件以控制輪詢啟停
 *   - 暴露資料訂閱介面（onData），讓 cop.js 接收最新 dashboard 資料
 *   - re-export authFetch 供其他模組使用
 *
 * 可 import：auth.js
 * 不可 import：map.js / events.js / decisions.js / cop.js
 *
 * 對外 export：connect(), disconnect(), send(), onMessage(), onData(), isPollActive(),
 *              setPollActive(), authFetch（re-export）
 */

import {
  getToken,
  onAuthChange,
  authFetch,
  canAccessMapObjects,
  canCreateEvents,
  canUseRealModeControls,
} from './auth.js';

// ── 常數 ────────────────────────────────────────────────────────
const API_BASE = location.origin;
const POLL_INTERVAL = 5000;   // ms

// ── 狀態 ────────────────────────────────────────────────────────
let _pollActive = false;
let _pollTimer  = null;
// P1-14 PR-2：session_type 已退役（後端自動依 active exercise scope，前端不再送）。

// 資料訂閱者清單
const _dataCallbacks = [];

// ── 訂閱認證狀態變化 ────────────────────────────────────────────
onAuthChange((event) => {
  if (event === 'login' || event === 'unlock') {
    _startPolling();
  } else if (event === 'logout' || event === 'lock') {
    _stopPolling();
  }
});

// ── 內部輪詢函式 ─────────────────────────────────────────────────
async function _doPoll() {
  if (!_pollActive) return;
  try {
    const resp = await authFetch(API_BASE + '/api/dashboard', { signal: AbortSignal.timeout(5000) });
    if (!resp.ok) throw new Error(resp.status);
    const data = await resp.json();

    // 通知所有訂閱者
    _dataCallbacks.forEach(cb => {
      try { cb(data); } catch(e) { console.error('[ws.js] onData callback 錯誤', e); }
    });

    // 指揮部連線燈號（綠）
    _updateServerDot('ok', '指揮部');
  } catch(e) {
    _updateServerDot('crit', null);
    console.warn('[ws.js] poll 錯誤', e.message);
  }
}

function _updateServerDot(level, label) {
  // dot class（cd-server）由 cop.js 的獨立 health interval 統一管理
  // ws.js 只更新 label 文字，不碰 dot className，避免與 health 燈競爭
  const lbl = document.getElementById('cl-server');
  if (!lbl) return;
  if (level === 'ok') {
    lbl.textContent = label || '指揮部';
  } else {
    lbl.textContent = '指揮部 離線';
  }
}

function _startPolling() {
  if (_pollActive) return;
  _pollActive = true;
  _doPoll();   // 立即執行一次
  _pollTimer = setInterval(_doPoll, POLL_INTERVAL);
}

function _stopPolling() {
  _pollActive = false;
  if (_pollTimer) {
    clearInterval(_pollTimer);
    _pollTimer = null;
  }
}

// ── 公開 API ────────────────────────────────────────────────────

/**
 * 啟動輪詢（通常由 cop.js 在初始化後呼叫；
 * 認證後的自動啟動已由 onAuthChange 處理）
 */
export function connect() {
  if (getToken()) _startPolling();
}

/** 手動停止輪詢 */
export function disconnect() {
  _stopPolling();
}

/** 訂閱資料更新。cb(data) 每次 poll 成功後被呼叫 */
export function onData(cb) {
  if (typeof cb === 'function') _dataCallbacks.push(cb);
}

/** WebSocket 相容介面：目前 C1-F 前端資料層仍使用 HTTP polling。 */
export function send(_message) {
  console.warn('[ws.js] send() ignored: HTTP polling transport has no outbound channel');
  return false;
}

/** WebSocket 相容別名，供模組邊界測試與未來替換 transport 使用。 */
export function onMessage(cb) {
  onData(cb);
}

/** 查詢目前輪詢狀態 */
export function isPollActive() {
  return _pollActive;
}

/** 強制設定輪詢狀態（供 cop.js 在需要時立即重新啟停輪詢）*/
export function setPollActive(active) {
  if (active && !_pollActive) {
    _startPolling();
  } else if (!active && _pollActive) {
    _stopPolling();
  }
}

/** 手動觸發一次 poll（不受 _pollActive 限制，供 cop.js 在操作後立即刷新） */
export async function forcePoll() {
  await _doPoll();
}

// re-export authFetch，讓其他模組可直接從 ws.js import
export {
  authFetch,
  canAccessMapObjects,
  canCreateEvents,
  canUseRealModeControls,
};
