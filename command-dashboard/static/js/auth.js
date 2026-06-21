/**
 * auth.js — 認證模組（root module）
 *
 * 職責：login / logout / session / token 持有 + PinLock + Settings + Admin + Pi 節點管理
 * 不可 import 其他業務模組。
 *
 * 對外 export：
 *   getToken()           — 取目前 session token
 *   clearSession()       — 登出並清除狀態（ws.js 的 _pollActive 透過 onClearSession callback 協調）
 *   onAuthChange(cb)     — 登入 / 登出事件監聽
 *   authFetch(url, opts) — 帶 token 的 fetch，401 自動登出
 *   PinLock              — 閒置鎖定物件
 *   getCurrentOperator() — 取目前登入帳號
 */

'use strict';

const el = id => document.getElementById(id);
const API_BASE = location.origin;

// ── session callbacks ──────────────────────────────────────────
const _authListeners = [];
let _onEnterDashboard = null;
let _openModal = null;
let _closeModal = null;
const SESSION_STATUS_INTERVAL_MS = 30000;
let _sessionStatusTimer = null;
let _sessionCountdownTimer = null;
let _sessionCountdownRemaining = 0;
let _sessionWarningVisible = false;
let _sessionLastUserActivityAt = Date.now();
let _sessionMaxIdleSeconds = null;
let _sessionWarningThresholdSeconds = 120;
let _sessionActivityListenersStarted = false;

/** 登入 / 登出時通知訂閱者 */
function _notifyAuth(type) {
  _authListeners.forEach(cb => cb(type));
}

export function onAuthChange(cb) {
  _authListeners.push(cb);
}

// ── token 存取 ─────────────────────────────────────────────────
export function getToken() {
  return sessionStorage.getItem('cmd_session_id');
}

export function getRoleDetail() {
  return sessionStorage.getItem('cmd_role_detail') || '';
}

const ROLE_ALIASES = {
  sysadmin: ['sysadmin', '系統管理員', 'admin'],
  commander: ['commander', '指揮官'],
  operator: ['operator', '操作員'],
  observer: ['observer', '觀察員'],
};

export function hasAnyRole(...roleDetails) {
  const roleDetail = getRoleDetail();
  const role = sessionStorage.getItem('cmd_role') || '';
  const values = new Set([roleDetail, role]);
  for (const roleKey of roleDetails) {
    for (const alias of ROLE_ALIASES[roleKey] || [roleKey]) {
      if (values.has(alias)) return true;
    }
  }
  return false;
}

export function canCreateEvents() {
  return hasAnyRole('sysadmin', 'commander', 'operator');
}

export function canAccessMapObjects() {
  return hasAnyRole('sysadmin', 'commander', 'operator');
}

export function canUseRealModeControls() {
  return hasAnyRole('sysadmin', 'commander');
}

function _isSysadminSession() {
  const roleDetail = sessionStorage.getItem('cmd_role_detail');
  const role = sessionStorage.getItem('cmd_role');
  return roleDetail === 'sysadmin' || role === '系統管理員' || role === 'admin';
}

function _isCommanderSession() {
  const roleDetail = sessionStorage.getItem('cmd_role_detail');
  const role = sessionStorage.getItem('cmd_role');
  return roleDetail === 'commander' || role === '指揮官';
}

function _isAccountManagerSession() {
  return _isSysadminSession() || _isCommanderSession();
}

export function getCurrentOperator() {
  return sessionStorage.getItem('cmd_username') || '指揮部';
}

// ── authFetch ──────────────────────────────────────────────────
/** 所有 API 呼叫統一加 session token；401 自動觸發登出 */
export async function authFetch(url, opts = {}) {
  const token = getToken();
  if (token) {
    opts.headers = Object.assign({}, opts.headers || {}, {'X-Session-Token': token});
  }
  const resp = await fetch(url, opts);
  if (resp.status === 401) {
    _expireToLogin('閒置過久, 請重新登入');
    return resp;
  }
  return resp;
}

// ── session 清除 ───────────────────────────────────────────────
export function clearSession() {
  stopSessionStatusPolling();
  _sessionMaxIdleSeconds = null;
  sessionStorage.removeItem('cmd_session_id');
  sessionStorage.removeItem('cmd_username');
  sessionStorage.removeItem('cmd_role');
  sessionStorage.removeItem('cmd_role_detail');
  sessionStorage.removeItem('cmd_display_name');
  sessionStorage.removeItem('cmd_login_time');
  PinLock.clear();
  _notifyAuth('logout');
}

function _markSessionUserActivity() {
  if (_sessionWarningVisible) return;
  _sessionLastUserActivityAt = Date.now();
}

function _sessionUxLog(event, data = {}, level = 'info') {
  const logger = level === 'warn' ? console.warn : console.info;
  if (typeof logger !== 'function') return;
  try {
    logger('[ICS_SESSION_UX]', Object.assign({ event, component: 'session_ux' }, data));
  } catch (_) {
    // Logging must never block session recovery UX.
  }
}

function _startSessionActivityListeners() {
  if (_sessionActivityListenersStarted) return;
  _sessionActivityListenersStarted = true;
  ['click','keydown','touchstart','pointerdown','scroll'].forEach(evt =>
    document.addEventListener(evt, _markSessionUserActivity, {passive:true})
  );
}

function _resetSessionLocalIdle({ force = false } = {}) {
  if (force) {
    _sessionLastUserActivityAt = Date.now();
    return;
  }
  _markSessionUserActivity();
}

function _setLoginWarning(message) {
  const warn = el('cmd-login-warn');
  if (warn) warn.textContent = message || '';
}

function _showLoginScreen(message) {
  const login = el('login-screen');
  if (login) login.style.display = '';
  _setLoginWarning(message);
}

function _expireToLogin(message) {
  _sessionUxLog('session_expired_to_login', { message }, 'warn');
  clearSession();
  _showLoginScreen(message);
  const badge = el('cmd-user-badge');
  if (badge) badge.textContent = '';
}

function _sessionOverlay() {
  return el('session-warning-overlay');
}

function _setSessionWarningError(message) {
  const err = el('session-warning-error');
  if (err) err.textContent = message || '';
}

function _renderSessionWarningCountdown() {
  const countdown = el('session-warning-countdown');
  if (countdown) countdown.textContent = String(Math.max(0, Math.ceil(_sessionCountdownRemaining)));
}

function _clearSessionCountdownTimer() {
  if (_sessionCountdownTimer) {
    clearInterval(_sessionCountdownTimer);
    _sessionCountdownTimer = null;
  }
}

function _releaseSessionWarningFocus(overlay) {
  const active = document.activeElement;
  if (!active || !overlay) return;
  const activeInOverlay =
    active === overlay ||
    (typeof overlay.contains === 'function' && overlay.contains(active)) ||
    (typeof active.id === 'string' && active.id.startsWith('session-warning-'));
  if (!activeInOverlay) return;
  if (typeof active.blur === 'function') active.blur();
  if (document.activeElement === active && typeof document.body?.focus === 'function') {
    document.body.focus();
  }
}

function _hideSessionWarning() {
  _sessionWarningVisible = false;
  _clearSessionCountdownTimer();
  _setSessionWarningError('');
  const overlay = _sessionOverlay();
  if (overlay) {
    _releaseSessionWarningFocus(overlay);
    overlay.classList.remove('show');
    overlay.setAttribute('aria-hidden', 'true');
  }
}

function _showSessionWarning(idleRemainingSeconds) {
  const overlay = _sessionOverlay();
  if (!overlay) return;
  const remaining = Math.max(0, Math.ceil(Number(idleRemainingSeconds) || 0));
  if (!_sessionWarningVisible) {
    _sessionUxLog('session_warning_shown', { idle_remaining_seconds: remaining });
  }
  _sessionWarningVisible = true;
  _sessionCountdownRemaining = remaining;
  overlay.classList.add('show');
  overlay.setAttribute('aria-hidden', 'false');
  _setSessionWarningError('');
  _renderSessionWarningCountdown();
  if (!_sessionCountdownTimer) {
    _sessionCountdownTimer = setInterval(() => {
      _sessionCountdownRemaining -= 1;
      _renderSessionWarningCountdown();
      if (_sessionCountdownRemaining <= 0) {
        _expireToLogin('Session 已過期, 請重新登入');
      }
    }, 1000);
  }
  const focusContinue = () => el('session-warning-continue')?.focus();
  if (typeof requestAnimationFrame === 'function') requestAnimationFrame(focusContinue);
  else focusContinue();
}

async function _pollSessionStatus() {
  if (!getToken()) return;
  let resp;
  try {
    resp = await authFetch(API_BASE + '/api/session/status');
  } catch (e) {
    return;
  }
  if (resp.status === 401) {
    _expireToLogin('閒置過久, 請重新登入');
    return;
  }
  if (!resp.ok) return;
  const data = await resp.json().catch(() => ({}));
  if (data.valid === false) {
    _expireToLogin('閒置過久, 請重新登入');
    return;
  }
  const idleRemaining = Number(data.idle_remaining_seconds);
  const warningThreshold = Number(data.warning_threshold_seconds);
  if (!Number.isFinite(idleRemaining) || !Number.isFinite(warningThreshold)) return;
  _sessionWarningThresholdSeconds = warningThreshold;
  _sessionMaxIdleSeconds = Math.max(_sessionMaxIdleSeconds || 0, idleRemaining);
  const localIdleSeconds = Math.max(0, (Date.now() - _sessionLastUserActivityAt) / 1000);
  const localIdleRemaining = _sessionMaxIdleSeconds - localIdleSeconds;
  const effectiveIdleRemaining = Math.min(idleRemaining, localIdleRemaining);
  if (effectiveIdleRemaining <= _sessionWarningThresholdSeconds) _showSessionWarning(effectiveIdleRemaining);
  else if (_sessionWarningVisible) _hideSessionWarning();
}

export function startSessionStatusPolling() {
  stopSessionStatusPolling();
  _startSessionActivityListeners();
  _pollSessionStatus();
  _sessionStatusTimer = setInterval(_pollSessionStatus, SESSION_STATUS_INTERVAL_MS);
}

export function stopSessionStatusPolling() {
  if (_sessionStatusTimer) {
    clearInterval(_sessionStatusTimer);
    _sessionStatusTimer = null;
  }
  _hideSessionWarning();
}

export async function continueSessionFromWarning() {
  const btn = el('session-warning-continue');
  if (btn) btn.disabled = true;
  try {
    const resp = await authFetch(API_BASE + '/api/auth/heartbeat');
    if (resp.status === 401) {
      _expireToLogin('閒置過久, 請重新登入');
      return;
    }
    if (resp.ok) {
      _resetSessionLocalIdle({ force: true });
      _sessionMaxIdleSeconds = null;
      _hideSessionWarning();
      _sessionUxLog('session_continue_success');
      await _pollSessionStatus();
      return;
    }
    _setSessionWarningError('暫時無法續期, 請重新登入');
    _sessionUxLog('session_continue_failed', { status: resp.status }, 'warn');
  } catch (e) {
    _sessionUxLog('session_continue_failed', { error: e?.message || 'unknown' }, 'warn');
    _setSessionWarningError('暫時無法續期, 請重新登入');
  } finally {
    if (btn) btn.disabled = false;
  }
}

export async function logoutFromSessionWarning() {
  _sessionUxLog('session_warning_logout_requested');
  await cmdLogout();
}

// ── 登入 ───────────────────────────────────────────────────────
export async function handleCmdLogin() {
  const btn = el('cmd-login-btn');
  const warn = el('cmd-login-warn');
  const username = el('cmd-username').value.trim();
  const pin = el('cmd-pin').value.trim();
  warn.textContent = '';

  if (!username) { warn.textContent = '請輸入帳號'; return; }
  if (!/^\d{4,6}$/.test(pin)) { warn.textContent = 'PIN 須為 4-6 位數字'; return; }

  btn.disabled = true;
  try {
    const resp = await fetch(API_BASE + '/api/auth/login', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({username, pin}),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      warn.textContent = err.detail || '登入失敗';
      return;
    }
    const data = await resp.json();
    sessionStorage.setItem('cmd_session_id', data.session_id);
    sessionStorage.setItem('cmd_username', data.username);
    sessionStorage.setItem('cmd_role', data.role);
    sessionStorage.setItem('cmd_role_detail', data.role_detail || '');
    sessionStorage.setItem('cmd_display_name', data.display_name);
    sessionStorage.setItem('cmd_login_time', new Date().toISOString());
    _resetSessionLocalIdle();
    // 儲存 PIN hash 供 PinLock 客戶端解鎖用
    // crypto.subtle 僅在 secure context（HTTPS / localhost）可用；HTTP LAN 環境略過
    try {
      const enc = new TextEncoder();
      const salt = crypto.getRandomValues(new Uint8Array(16));
      const key = await crypto.subtle.importKey('raw', enc.encode(pin), 'PBKDF2', false, ['deriveBits']);
      const bits = await crypto.subtle.deriveBits({name:'PBKDF2',salt,iterations:100000,hash:'SHA-256'}, key, 256);
      sessionStorage.setItem('cmd_pin_hash', Array.from(new Uint8Array(bits)).map(b=>b.toString(16).padStart(2,'0')).join(''));
      sessionStorage.setItem('cmd_pin_salt', Array.from(salt).map(b=>b.toString(16).padStart(2,'0')).join(''));
    } catch (_) { /* HTTP 環境，PinLock hash 略過 */ }

    if (data.must_change_pin) {
      // C1-A first-run gate：強制改 PIN 後才進 dashboard
      _showInitialPinChangeForm(username, pin);
      return;
    }
    _enterDashboard();
  } catch(e) {
    warn.textContent = '連線失敗';
  } finally {
    btn.disabled = false;
  }
}

function _showInitialPinChangeForm(username, currentPin) {
  // C1-A：first-run 改 PIN 表單，覆蓋登入畫面
  const loginScreen = el('login-screen');
  const overlay = document.createElement('div');
  overlay.id = 'initial-pin-overlay';
  overlay.style.cssText = [
    'position:fixed;inset:0;background:var(--bg,#1a1a2e);',
    'display:flex;align-items:center;justify-content:center;z-index:9999;',
  ].join('');
  overlay.innerHTML = `
    <div style="background:var(--card,#16213e);border-radius:12px;padding:40px 32px;
                max-width:360px;width:90%;text-align:center;">
      <div style="font-size:22px;font-weight:700;color:var(--text,#fff);margin-bottom:8px;">
        🔐 首次設定
      </div>
      <div style="font-size:13px;color:var(--text-muted,#aaa);margin-bottom:24px;">
        請設定你的新 PIN（4-6 位數字）<br>設定後 gate 解除，進入系統
      </div>
      <input id="ipc-new-pin" class="login-input" type="password" inputmode="numeric"
             maxlength="6" placeholder="新 PIN（4-6 位數字）"
             style="margin-bottom:12px;">
      <input id="ipc-confirm-pin" class="login-input" type="password" inputmode="numeric"
             maxlength="6" placeholder="確認新 PIN"
             style="margin-bottom:16px;">
      <button id="ipc-submit" class="login-btn">確認設定</button>
      <div id="ipc-warn" style="font-size:12px;color:var(--red,#e74c3c);
                                  min-height:18px;margin-top:10px;"></div>
    </div>`;
  document.body.appendChild(overlay);

  const submitBtn = document.getElementById('ipc-submit');
  const warnEl   = document.getElementById('ipc-warn');

  submitBtn.addEventListener('click', async () => {
    const newPin     = document.getElementById('ipc-new-pin').value.trim();
    const confirmPin = document.getElementById('ipc-confirm-pin').value.trim();
    warnEl.textContent = '';

    if (!/^\d{4,6}$/.test(newPin))          { warnEl.textContent = 'PIN 須為 4-6 位數字'; return; }
    if (newPin !== confirmPin)               { warnEl.textContent = '兩次 PIN 不一致'; return; }
    if (newPin === currentPin)               { warnEl.textContent = '新 PIN 不能與初始 PIN 相同'; return; }

    submitBtn.disabled = true;
    try {
      const resp = await fetch(API_BASE + '/api/auth/change-initial-pin', {
        method:  'POST',
        headers: {
          'Content-Type':   'application/json',
          'X-Session-Token': sessionStorage.getItem('cmd_session_id') || '',
        },
        body: JSON.stringify({current_pin: currentPin, new_pin: newPin}),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        warnEl.textContent = err.detail || '設定失敗，請重試';
        return;
      }
      // PIN 改成功：更新 sessionStorage 的 pin hash，移除 overlay，進 dashboard
      try {
        const enc  = new TextEncoder();
        const salt = crypto.getRandomValues(new Uint8Array(16));
        const key  = await crypto.subtle.importKey('raw', enc.encode(newPin), 'PBKDF2', false, ['deriveBits']);
        const bits = await crypto.subtle.deriveBits({name:'PBKDF2',salt,iterations:100000,hash:'SHA-256'}, key, 256);
        sessionStorage.setItem('cmd_pin_hash', Array.from(new Uint8Array(bits)).map(b=>b.toString(16).padStart(2,'0')).join(''));
        sessionStorage.setItem('cmd_pin_salt', Array.from(salt).map(b=>b.toString(16).padStart(2,'0')).join(''));
      } catch (_) { /* HTTP 環境略過 */ }
      overlay.remove();
      _enterDashboard();
    } catch(e) {
      warnEl.textContent = '連線失敗，請重試';
    } finally {
      submitBtn.disabled = false;
    }
  });
}

function _enterDashboard() {
  el('login-screen').style.display = 'none';
  el('cmd-user-badge').textContent = sessionStorage.getItem('cmd_display_name') || sessionStorage.getItem('cmd_username');
  // 角色限制
  el('stg-admin-section').style.display = _isAccountManagerSession() ? '' : 'none';
  // #66 PR-C1：事件分類編輯為 sysadmin-only（後端 POST=SYSADMIN_ONLY），比帳號管理段更嚴
  const taxSec = el('stg-taxonomy-section');
  if (taxSec) taxSec.style.display = hasAnyRole('sysadmin') ? '' : 'none';
  // 更新 settings footer
  el('stg-user-info').textContent = (sessionStorage.getItem('cmd_display_name') || '') + ' (' + sessionStorage.getItem('cmd_role') + ')　' + (_fmtLocalDT(sessionStorage.getItem('cmd_login_time') || '') || '').slice(11,19);
  PinLock.start();
  _notifyAuth('login');
  if (typeof _onEnterDashboard === 'function') _onEnterDashboard();
}

// ── 登出 ───────────────────────────────────────────────────────
export async function cmdLogout() {
  closeSettings();
  try {
    await authFetch(API_BASE + '/api/auth/logout', {method:'POST'});
  } catch(e) {}
  clearSession();
  el('login-screen').style.display = '';
  el('cmd-username').value = '';
  el('cmd-pin').value = '';
  el('cmd-login-warn').textContent = '';
  el('cmd-user-badge').textContent = '';
}

// ── PinLock ────────────────────────────────────────────────────
export const PinLock = (() => {
  const IDLE_MS = 270000;
  const WARN_S = 30;
  let _timer = null, _warnTimer = null, _countdown = 0;

  function _reset() {
    clearTimeout(_timer);
    clearTimeout(_warnTimer);
    _countdown = 0;
    el('idle-countdown').style.display = 'none';
    el('idle-countdown').textContent = '';
    _timer = setTimeout(_warn, IDLE_MS);
  }

  function _warn() {
    _countdown = WARN_S;
    el('idle-countdown').style.display = '';
    _tick();
  }

  function _tick() {
    if (_countdown <= 0) { _lock(); return; }
    el('idle-countdown').textContent = _countdown + 's';
    _countdown--;
    _warnTimer = setTimeout(_tick, 1000);
  }

  function _lock() {
    el('pin-lock-overlay').classList.add('show');
    el('pinlock-user').textContent = sessionStorage.getItem('cmd_display_name') || sessionStorage.getItem('cmd_username') || '';
    el('pinlock-pin').value = '';
    el('pinlock-warn').textContent = '';
    _notifyAuth('lock');
  }

  async function unlock() {
    const pin = el('pinlock-pin').value.trim();
    if (!pin) { el('pinlock-warn').textContent = '請輸入 PIN'; return; }
    const stored = sessionStorage.getItem('cmd_pin_hash');
    const saltHex = sessionStorage.getItem('cmd_pin_salt');
    if (!stored || !saltHex) { cmdLogout(); return; }
    const enc = new TextEncoder();
    const salt = new Uint8Array(saltHex.match(/.{2}/g).map(h => parseInt(h, 16)));
    const key = await crypto.subtle.importKey('raw', enc.encode(pin), 'PBKDF2', false, ['deriveBits']);
    const bits = await crypto.subtle.deriveBits({name:'PBKDF2',salt,iterations:100000,hash:'SHA-256'}, key, 256);
    const hash = Array.from(new Uint8Array(bits)).map(b=>b.toString(16).padStart(2,'0')).join('');
    if (hash !== stored) { el('pinlock-warn').textContent = 'PIN 錯誤'; return; }
    el('pin-lock-overlay').classList.remove('show');
    try { await authFetch(API_BASE + '/api/auth/heartbeat'); } catch(e) {}
    _notifyAuth('unlock');
    _reset();
  }

  function start() {
    ['click','keydown','touchstart','scroll'].forEach(evt =>
      document.addEventListener(evt, _reset, {passive:true})
    );
    _reset();
  }

  function clear() {
    clearTimeout(_timer);
    clearTimeout(_warnTimer);
    el('idle-countdown').style.display = 'none';
  }

  return { start, clear, unlock, resetIdle: _reset };
})();

export function unlockPinLock() {
  return PinLock.unlock();
}

export function setModalHandlers({ openModal, closeModal } = {}) {
  _openModal = typeof openModal === 'function' ? openModal : null;
  _closeModal = typeof closeModal === 'function' ? closeModal : null;
}

// ── Settings ───────────────────────────────────────────────────
export function openSettings() {
  el('settings-overlay').classList.add('show');
  el('settings-panel').classList.add('show');
}

export function closeSettings() {
  el('settings-overlay').classList.remove('show');
  el('settings-panel').classList.remove('show');
}

// 「指揮部設定」（command_post_name / command_post_location）已移除：vestigial —
// 全 codebase 無消費端（設了不顯示），且位置改由站內地圖 + on-demand 地圖節點承載。
// 設定面板該區段改名「站內地圖」（只留地圖設定）。

export function exportDashboardJSON(data) {
  if (!data) return;
  const blob = new Blob([JSON.stringify(data, null, 2)], {type:'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'dashboard_' + new Date().toISOString().slice(0,19).replace(/:/g,'-') + '.json';
  a.click();
  URL.revokeObjectURL(a.href);
}

export async function showAuditLog(existingLogs = null, activeFilter = 'all') {
  if (Array.isArray(existingLogs)) {
    _auditRenderModal(existingLogs, activeFilter);
    return;
  }
  const resp = await authFetch(API_BASE + '/api/audit_log?limit=200');
  if (!resp.ok) return;
  const logs = await resp.json();
  _auditRenderModal(logs, activeFilter);
}

// ── 日期格式化 ─────────────────────────────────────────────────
export function fmtLocalDT(isoStr) {
  if (!isoStr) return '';
  const d = new Date(isoStr.endsWith('Z') ? isoStr : isoStr + 'Z');
  const p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

// 內部別名（向下相容）
const _fmtLocalDT = fmtLocalDT;

// ── Audit log ──────────────────────────────────────────────────
const _AUDIT_BADGE = {
  'login':                  { c:'#388bfd', zh:'登入' },
  'logout':                 { c:'#8b949e', zh:'登出' },
  'SESSION_LOGOUT':         { c:'#8b949e', zh:'登出' },  // 後端實際 action_type（auth.py logout）
  'SESSION_EXPIRED':        { c:'#8b949e', zh:'逾時登出' },  // session 逾時失效（含批次清理被丟棄 session）
  'IDLE_KICKED':            { c:'#e3b341', zh:'閒置登出' },  // idle 逾時踢出
  'BINDING_MISMATCH_IP':    { c:'#f85149', zh:'IP變更' },    // session 綁定不符（安全）
  'BINDING_MISMATCH_UA':    { c:'#f85149', zh:'裝置變更' },
  'event_created':          { c:'#3fb950', zh:'新增事件' },
  'event_status_updated':   { c:'#e3b341', zh:'更新狀態' },
  'event_note_added':       { c:'#79c0ff', zh:'補充備註' },
  'decision_created':       { c:'#d2a8ff', zh:'新增裁示' },
  'decision_made':          { c:'#ff7b72', zh:'裁示決定' },
  'snapshot_received':      { c:'#56d364', zh:'收快照' },
  'db_reset':               { c:'#f85149', zh:'重設DB' },
  'exercise_reset':         { c:'#f85149', zh:'重設演練' },
  'exercise_created':       { c:'#3fb950', zh:'建立演習' },
  'exercise_status_updated':{ c:'#e3b341', zh:'演習狀態' },
  'exercise_deleted':       { c:'#f85149', zh:'刪除演習' },
  'account_created':        { c:'#388bfd', zh:'建立帳號' },
  'account_status_updated': { c:'#e3b341', zh:'帳號狀態' },
  'account_pin_reset':      { c:'#e3b341', zh:'重設PIN' },
  'account_role_updated':   { c:'#d2a8ff', zh:'更新角色' },
  'account_deleted':        { c:'#f85149', zh:'刪除帳號' },
  'config_updated':         { c:'#8b949e', zh:'更新設定' },
  'three_pass_sync':        { c:'#79c0ff', zh:'三通同步' },
  'conflict_resolved':      { c:'#56d364', zh:'衝突解決' },
  'manual_input':           { c:'#56d364', zh:'手動輸入' },
  'pi_node_created':        { c:'#388bfd', zh:'新增Pi節點' },
  'pi_node_deleted':        { c:'#f85149', zh:'刪除Pi節點' },
  // #93：COP 地圖操作（標繪 = zone/route/polygon/設施/事件圖釘）。短 label 避免撐破 badge 欄。
  'cop_entity_created':     { c:'#3fb950', zh:'新增標繪' },
  'cop_entity_updated':     { c:'#e3b341', zh:'移動標繪' },
  'cop_entity_deleted':     { c:'#f85149', zh:'刪除標繪' },
  'ttx_inject_fired':       { c:'#d2a8ff', zh:'注入觸發' },
  'manual_record_synced':   { c:'#56d364', zh:'手動同步' },
};

function _auditEventLabel(log) {
  if (log._event_desc) return log._event_desc.length > 28 ? log._event_desc.slice(0,26)+'…' : log._event_desc;
  try {
    const d = JSON.parse(log.detail || '{}');
    if (d.event_code) return d.event_code;
  } catch(e) {}
  return (log.target_id || '').slice(0,8) || '—';
}

// 稽核日誌欄位 render 進 innerHTML 前的 escape（belt-and-braces；callsign 等已過後端
// validate_no_unsafe_strings，這層防未來驗證鬆動成 XSS sink）。
function _escAudit(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
}

const _AUDIT_FILTERS = [
  { key:'all',     zh:'全部' },
  { key:'account', zh:'帳號', match: a => a.startsWith('login') || a.toLowerCase().includes('logout') || a.startsWith('account') || a.startsWith('SESSION_') || a === 'IDLE_KICKED' || a.startsWith('BINDING_') },
  { key:'event',   zh:'事件', match: a => a.startsWith('event') || a.startsWith('decision') },
  { key:'system',  zh:'系統', match: a => ['snapshot_received','db_reset','exercise_reset','config_updated','three_pass_sync','conflict_resolved','pi_node_created','pi_node_deleted','manual_input'].includes(a) },
];

function _auditRenderModal(logs, activeFilter) {
  const filtered = activeFilter === 'all' ? logs
    : logs.filter(l => {
        const def = _AUDIT_FILTERS.find(f => f.key === activeFilter);
        return def?.match?.(l.action_type || '');
      });

  const chips = _AUDIT_FILTERS.map(f =>
    `<span data-action="audit-filter" data-filter="${f.key}" data-logs-key="latest"
      style="cursor:pointer;padding:3px 10px;border-radius:12px;font-size:10px;white-space:nowrap;
             background:${f.key===activeFilter?'var(--yellow)':'var(--surface2)'};
             color:${f.key===activeFilter?'#000':'var(--text2)'};border:1px solid var(--border);">${f.zh}</span>`
  ).join('');

  let lastDate = '';
  let rows = '';
  for (const log of filtered) {
    const dt = _fmtLocalDT(log.created_at);
    const dateStr = dt.slice(0,10);
    if (dateStr !== lastDate) {
      lastDate = dateStr;
      rows += `<div style="font-size:10px;color:var(--text3);padding:8px 0 4px;border-top:1px solid var(--border);margin-top:4px;">${dateStr}</div>`;
    }

    const _at = log.action_type || '';
    // fallback：未在 _AUDIT_BADGE 的 action_type 截斷顯示，避免長英文（如新 cop_entity_*）撐破 badge 欄
    // _escAudit：action_type 雖為後端固定 enum（非外部輸入），仍與本檔其他 innerHTML 欄位一致跳脫（belt-and-braces）
    let badge = _AUDIT_BADGE[_at] || { c: '#8b949e', zh: _escAudit(_at.length > 8 ? _at.slice(0, 7) + '…' : _at) };
    // #93：cop_entity_* 依 detail.kind 給具體名詞（節點/路線/範圍/設施/圖釘），比泛稱「標繪」清楚；
    //   update 若改到座標 → 「移動」否則「更新」。target 欄顯實際名稱（callsign）。
    let _copLabel = '';
    if (_at.startsWith('cop_entity_')) {
      let _d = {};
      try { _d = JSON.parse(log.detail || '{}'); } catch (e) {}
      const _kindZh = { zone: '節點', route: '路線', polygon: '範圍', infra: '設施', event: '圖釘', contact: '敵情標記' };
      const _isMove = Array.isArray(_d.fields) && _d.fields.some(f => f === 'lat' || f === 'lon');
      const _verb = _at.endsWith('_created') ? '新增' : _at.endsWith('_deleted') ? '刪除' : (_isMove ? '移動' : '更新');
      badge = { c: badge.c, zh: _verb + (_kindZh[_d.kind] || '標繪') };
      _copLabel = _d.label || '';
    }
    const badgeHtml = `<span style="display:inline-block;max-width:100%;overflow:hidden;text-overflow:ellipsis;padding:1px 7px;border-radius:10px;font-size:10px;font-weight:600;background:${badge.c}22;color:${badge.c};border:1px solid ${badge.c}55;white-space:nowrap;">${badge.zh}</span>`;

    let targetHtml = '';
    if (log.target_table === 'events') {
      targetHtml = `<span style="color:var(--text2);font-size:11px;">${_auditEventLabel(log)}</span>`;
    } else if (log.target_table === 'accounts') {
      targetHtml = `<span style="color:var(--text2);font-size:11px;">${_escAudit(log.target_id || '')}</span>`;
    } else if (log.target_table === 'cop_entities') {
      targetHtml = `<span style="color:var(--text2);font-size:11px;">${_escAudit(_copLabel) || (log.target_id || '').slice(0, 12)}</span>`;
    } else if (log.target_table) {
      targetHtml = `<span style="color:var(--text3);font-size:10px;">${log.target_table}</span>`;
    }

    rows += `<div style="display:grid;grid-template-columns:90px 88px 80px 1fr;gap:6px;align-items:center;padding:5px 2px;border-bottom:1px solid rgba(255,255,255,.04);">
      <span style="font-family:var(--mono);font-size:10px;color:var(--text3);">${dt.slice(11)}</span>
      ${badgeHtml}
      <span style="font-size:11px;color:var(--text3);">${_escAudit(log.operator || '—')}</span>
      ${targetHtml}
    </div>`;
  }

  if (!rows) rows = `<div style="color:var(--text3);font-size:11px;padding:20px 0;text-align:center;">（無紀錄）</div>`;

  // 暫存 logs 供篩選 chip 使用
  window._auditLogsCache = logs;

  const bodyHtml = `
    <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px;">${chips}</div>
    <div style="max-height:55vh;overflow-y:auto;">${rows}</div>`;

  openModal('稽核日誌', bodyHtml, `<button class="adm-btn" data-action="close-modal">關閉</button>`);
}

// ── Admin 面板 ─────────────────────────────────────────────────
let _admPin = '';

/** 提供給 cop.js 等模組讀取已快取的 Admin PIN（admin 面板登入後設定） */
export function getAdminPin() { return _admPin; }

export function openAdminPanel() {
  closeSettings();
  el('admin-overlay').classList.add('show');
  el('admin-panel').classList.add('show');
  el('adm-pin-input').value = '';
  el('adm-pin-warn').textContent = '';
  if (_isAccountManagerSession()) {
    _admPin = '';
    el('adm-pin-screen').style.display = 'none';
    el('adm-main').style.display = 'flex';
    _applyAdminTabVisibility();
    admShowTab('list');
    _admLoadSysInfo();
    return;
  }
  el('adm-pin-screen').style.display = '';
  el('adm-main').style.display = 'none';
  el('adm-pin-warn').textContent = '需要系統管理員權限';
}

export function closeAdminPanel() {
  el('admin-overlay').classList.remove('show');
  el('admin-panel').classList.remove('show');
  _admPin = '';
}

export async function adminLogin() {
  const pin = el('adm-pin-input').value.trim();
  if (_isAccountManagerSession() && !pin) {
    el('adm-pin-screen').style.display = 'none';
    el('adm-main').style.display = 'flex';
    _applyAdminTabVisibility();
    admShowTab('list');
    _admLoadSysInfo();
    return;
  }
  if (!pin) { el('adm-pin-warn').textContent = '請輸入 PIN'; return; }
  const resp = await authFetch(API_BASE + '/api/admin/accounts', {
    headers: {'X-Admin-PIN': pin},
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    const detail = body.detail || '';
    if (resp.status === 503) {
      el('adm-pin-warn').textContent = '⚠️ Admin PIN 尚未設定，請查看伺服器啟動 log';
    } else if (resp.status === 423) {
      const localDetail = detail.replace(
        /\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z/,
        s => new Date(s).toLocaleTimeString('zh-TW', {hour:'2-digit', minute:'2-digit'})
      );
      el('adm-pin-warn').textContent = '🔒 ' + (localDetail || '管理員 PIN 已鎖定，請稍後再試');
    } else {
      el('adm-pin-warn').textContent = detail.includes('剩餘') ? '❌ ' + detail : '❌ 管理員 PIN 錯誤';
    }
    el('adm-pin-input').value = '';
    el('adm-pin-input').focus();
    return;
  }
  _admPin = pin;
  el('adm-pin-screen').style.display = 'none';
  el('adm-main').style.display = 'flex';
  admShowTab('list');
  _admLoadSysInfo();
}

async function _admLoadSysInfo() {
  if (!_isSysadminSession()) return;
  try {
    const r = await authFetch(API_BASE + '/api/admin/status');
    if (!r.ok) return;
    const d = await r.json();
    const si = el('adm-sysinfo');
    if (si) {
      si.innerHTML =
        `<span>cmd <b>${document.body.dataset.cmdVersion || '—'}</b></span>` +
        `<span>DB schema <b>v${d.schema_version ?? '—'}</b></span>` +
        `<span>帳號 <b>${d.active_accounts}</b></span>`;
    }
  } catch { /* 靜默失敗 */ }
}

function _applyAdminTabVisibility() {
  document.querySelectorAll('.adm-tab').forEach(tab => {
    const key = tab.dataset.tab;
    tab.style.display = _isSysadminSession() || key === 'list' || key === 'add' ? '' : 'none';
  });
}

export function admShowTab(tab) {
  if (!_isSysadminSession() && !['list','add'].includes(tab)) tab = 'list';
  _applyAdminTabVisibility();
  const tabs = ['list','add','pi','log','data','sys'];
  document.querySelectorAll('.adm-tab').forEach((t, i) => {
    t.classList.toggle('active', tabs[i] === tab);
  });
  tabs.forEach(k => { el('adm-panel-' + k).style.display = k === tab ? '' : 'none'; });
  if (tab === 'list') admLoadAccounts();
  if (tab === 'add') admShowAddForm();
  if (tab === 'pi') admLoadPiNodes();
  if (tab === 'log') admLoadLog();
  if (tab === 'data') admShowData();
  if (tab === 'sys') admShowSys();
}

export function admShowSys() {
  el('adm-panel-sys').innerHTML = `
    <div style="margin-bottom:24px;">
      <div style="font-size:13px;font-weight:600;margin-bottom:8px;color:var(--text);">📡 TAK 連線</div>
      <div style="font-size:11px;color:var(--text2);margin-bottom:10px;line-height:1.6;max-width:360px;">
        runtime 啟用／停用與 TAK Server 的 CoT 串流連線（不重啟服務）。<br>
        連線參數於部署時備妥，此處只負責開關。<span style="color:var(--red);">關閉 = 整個 COP 態勢中斷。</span>
      </div>
      <label style="display:flex;align-items:center;gap:10px;cursor:pointer;max-width:360px;">
        <input id="adm-tak-toggle" type="checkbox" data-action="adm-toggle-tak"
               style="width:18px;height:18px;cursor:pointer;" disabled>
        <span id="adm-tak-toggle-label" style="font-size:13px;color:var(--text);">載入中…</span>
      </label>
      <div id="adm-tak-status" style="font-size:11px;color:var(--text2);margin-top:8px;min-height:16px;"></div>
    </div>
    <div style="margin-bottom:24px;border-top:1px solid var(--border);padding-top:16px;">
      <div style="font-size:13px;font-weight:600;margin-bottom:12px;color:var(--text);">🔑 更改 Admin PIN</div>
      <div style="display:flex;flex-direction:column;gap:8px;max-width:320px;">
        <input id="adm-sys-old-pin" class="login-input" type="password" inputmode="numeric"
               maxlength="6" placeholder="目前 Admin PIN">
        <input id="adm-sys-new-pin" class="login-input" type="password" inputmode="numeric"
               maxlength="6" placeholder="新 PIN（4-6 位數字）">
        <input id="adm-sys-new-pin2" class="login-input" type="password" inputmode="numeric"
               maxlength="6" placeholder="確認新 PIN">
        <button class="login-btn" data-action="adm-change-pin" style="margin-top:4px;">更改 Admin PIN</button>
        <div id="adm-sys-warn" style="font-size:12px;color:var(--red);min-height:16px;"></div>
      </div>
    </div>`;
  _admLoadTakConn();
}

// ── P1-12b（#228）「備份／重設」tab（in-dashboard，取代 orphaned admin_backups.html）──
// 端點走 _check_system_admin（session）；authFetch 自帶 X-Session-Token。

export function admShowData() {
  el('adm-panel-data').innerHTML = `
    <div style="margin-bottom:20px;">
      <div style="font-size:13px;font-weight:600;margin-bottom:8px;color:var(--red);">⚠ 重設指揮部資料庫</div>
      <div style="font-size:11px;color:var(--text2);margin-bottom:12px;line-height:1.6;">
        清除所有快照、事件、裁示、演習、COP、Pi 批次資料。<br>
        <b>帳號和 Pi 節點註冊不受影響</b>，Pi 端資料也不受影響。<br>
        <span style="color:var(--yellow);">重設只清資料庫，<b>不會刪備份檔（下方清單）</b>；清空前會自動備份當前。</span><br>
        <span style="color:var(--red);">此操作無法復原。</span>
      </div>
      <button class="login-btn" data-action="confirmResetDB"
              style="background:var(--red);color:#fff;border:none;max-width:320px;">重設指揮部資料庫</button>
    </div>
    <div style="border-top:1px solid var(--border);padding-top:16px;">
      <div style="font-size:13px;font-weight:600;margin-bottom:8px;color:var(--text);">💾 整包資料備份 / 還原</div>
      <div style="font-size:11px;color:var(--text2);margin-bottom:10px;line-height:1.6;max-width:460px;">
        每個備份是整個 <code>data/</code> 的加密快照（資料庫 + 地圖設定 + 上傳檔）。<span style="color:var(--text3);">需部署層設定 BACKUP_KEY。</span><br>
        <b>「來源」欄</b>：<b>手動</b>＝你按鈕建的；<b>演習結束 / 還原前 / 重設前 / 關機</b>＝系統在這些時機<b>自動備份</b>（防呆，怕你忘）。<br>
        <b>備份到 USB / 異地</b>：按該筆「下載」存出 <code>.tar.gz.enc</code>（已加密，要有 BACKUP_KEY 才能還原），再複製到隨身碟。<br>
        <span style="color:var(--text3);">自動保留：留最近 10 筆 / 30 天內；<b>演習結束與還原前備份永久保留</b>，其餘老檔在手動備份時自動清理。</span>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;">
        <button class="login-btn" data-action="admBackupNow" style="max-width:160px;">立即整包備份</button>
        <button class="login-btn" data-action="admRefreshBackups" style="max-width:120px;background:var(--bg3);">重新整理</button>
      </div>
      <div id="adm-backup-detail" style="margin-top:8px;font-size:11px;color:var(--text2);min-height:16px;"></div>
      <div data-action="admToggleBackupList" style="margin-top:6px;font-size:12px;font-weight:600;color:var(--text);cursor:pointer;user-select:none;">
        <span id="adm-bk-arrow">▾</span> 備份清單 <span id="adm-bk-count" style="color:var(--text3);font-weight:400;"></span>
      </div>
      <div id="adm-backup-list" style="margin-top:6px;font-size:11px;max-height:280px;overflow-y:auto;border:1px solid var(--border);border-radius:4px;"></div>
      <div id="adm-backup-dir" style="margin-top:6px;font-size:10px;color:var(--text3);"></div>
      <div style="margin-top:14px;border-top:1px dashed var(--border);padding-top:12px;">
        <div style="font-size:12px;color:var(--text2);margin-bottom:6px;">⤴ 從外部檔還原（USB / 異地拿回的 .enc）。覆蓋當前 data/、先自動備份當前、有進行中演習則拒絕、還原後需重啟。清單裡的備份請用該筆的「還原」。</div>
        <input id="adm-restore-file" type="file" accept=".enc" style="font-size:11px;max-width:300px;">
        <button class="login-btn" data-action="admRestore" style="background:var(--red);color:#fff;border:none;max-width:160px;margin-top:4px;">上傳外部檔還原</button>
      </div>
    </div>`;
  admRefreshBackups();
}

function _fmtBytes(n) {
  if (n < 1024) return n + ' B';
  if (n < 1048576) return (n / 1024).toFixed(1) + ' KB';
  return (n / 1048576).toFixed(1) + ' MB';
}

function _triggerLabel(b) {
  const tag = (color, text, tip) => `<span style="color:${color};" title="${tip}">${text}</span>`;
  if (b.is_pre_restore) return tag('var(--yellow)', '還原前', '系統在還原前自動備份當前狀態（防呆）');
  switch (b.trigger) {
    case 'archive':  return tag('var(--green)', '演習結束', '演習歸檔時自動備份（含演習 metadata）');
    case 'manual':   return tag('var(--text)', '手動', '你按「立即整包備份」建立');
    case 'shutdown': return tag('var(--text2)', '關機', '服務正常關閉時自動備份');
    case 'pre-reset-db':
    case 'pre-reset-exercise': return tag('var(--yellow)', '重設前', '系統在重設資料庫前自動備份（防呆）');
    default: return tag('var(--text3)', '整包', '');
  }
}

export function admToggleBackupList() {
  const box = el('adm-backup-list');
  const arrow = el('adm-bk-arrow');
  if (!box) return;
  const open = box.style.display === 'none';
  box.style.display = open ? '' : 'none';
  if (arrow) arrow.textContent = open ? '▾' : '▸';
}

export async function admRefreshBackups() {
  const box = el('adm-backup-list');
  if (!box) return;
  try {
    const r = await authFetch(API_BASE + '/api/admin/user-data-backups');
    if (!r.ok) { box.innerHTML = '<span style="color:var(--text3);">無法載入（需系統管理員）</span>'; return; }
    const d = await r.json();
    const dir = el('adm-backup-dir');
    if (dir) dir.textContent = '伺服器備份目錄：' + (d.backup_dir || '—');
    const cnt = el('adm-bk-count');
    if (cnt) cnt.textContent = `（${d.total} 筆）`;
    if (!d.backups.length) { box.innerHTML = '<div style="padding:8px;color:var(--text3);">（尚無整包備份）</div>'; return; }
    const head = `<div style="position:sticky;top:0;background:var(--bg2);display:flex;gap:8px;color:var(--text3);font-weight:600;padding:4px 8px;border-bottom:1px solid var(--border);">
        <span style="flex:1;">檔名</span><span style="width:60px;">來源</span><span style="width:90px;">演習</span>
        <span style="width:52px;text-align:right;">大小</span><span style="width:182px;"></span>
      </div>`;
    box.innerHTML = head + d.backups.map((b, i) => {
      const ex = b.exercise ? _escAudit(b.exercise) : '<span style="color:var(--text3);">—</span>';
      const n = _escAudit(b.name);
      return `<div style="display:flex;gap:8px;align-items:center;padding:4px 8px;border-bottom:1px solid var(--border);">
        <span style="flex:1;word-break:break-all;cursor:pointer;" data-action="admToggleDetail" data-name="${n}" data-idx="${i}" title="點看詳情">${n}</span>
        <span style="width:60px;">${_triggerLabel(b)}</span>
        <span style="width:90px;word-break:break-all;">${ex}</span>
        <span style="width:52px;text-align:right;color:var(--text3);">${_fmtBytes(b.size_bytes)}</span>
        <span style="width:182px;display:flex;gap:4px;">
          <button class="login-btn" data-action="admToggleDetail" data-name="${n}" data-idx="${i}"
                  style="background:var(--bg3);font-size:10px;padding:2px 6px;" title="內容清單 / 演習 / 建立時間">詳情</button>
          <button class="login-btn" data-action="admDownloadBackup" data-name="${n}"
                  style="background:var(--bg3);font-size:10px;padding:2px 6px;">下載</button>
          <button class="login-btn" data-action="admRestoreFromList" data-name="${n}"
                  style="background:var(--red);color:#fff;border:none;font-size:10px;padding:2px 6px;">還原</button>
        </span>
      </div>
      <div id="bk-det-${i}" style="display:none;padding:6px 12px;background:var(--bg);border-bottom:1px solid var(--border);color:var(--text2);"></div>`;
    }).join('');
  } catch { box.innerHTML = '<div style="padding:8px;color:var(--red);">載入失敗</div>'; }
}

// master-detail accordion：點檔名/詳情 → 在該列正下方展開 manifest（首開 lazy fetch）
export async function admToggleDetail(name, idx) {
  const box = el('bk-det-' + idx);
  if (!box) return;
  const opening = box.style.display === 'none';
  box.style.display = opening ? '' : 'none';
  if (opening && !box.dataset.loaded) {
    box.textContent = '載入中…';
    try {
      const r = await authFetch(API_BASE + '/api/admin/user-data-backups/' + encodeURIComponent(name) + '/manifest');
      if (!r.ok) { box.textContent = '無法讀取詳情（' + r.status + '）'; return; }
      box.innerHTML = _renderManifest((await r.json()).manifest);
      box.dataset.loaded = '1';
    } catch (e) { box.textContent = '錯誤：' + e.message; }
  }
}

// 下載加密備份檔（存 USB / 異地）。authFetch 取檔 → blob → 觸發瀏覽器下載。
export async function admDownloadBackup(name) {
  try {
    const r = await authFetch(API_BASE + '/api/admin/user-data-backups/' + encodeURIComponent(name) + '/download');
    if (!r.ok) { alert('下載失敗（' + r.status + '）'); return; }
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  } catch (e) { alert('下載錯誤：' + e.message); }
}

export async function admBackupNow() {
  if (!confirm('立即執行整包 data/ 備份？')) return;
  const detail = el('adm-backup-detail');
  try {
    const r = await authFetch(API_BASE + '/api/admin/user-data-backups', { method: 'POST' });
    if (!r.ok) { alert('備份失敗（' + r.status + '）：' + ((await r.json().catch(() => ({}))).detail || '')); return; }
    const d = await r.json();
    if (detail) detail.textContent = `✅ 備份完成：${_fmtBytes(d.size_bytes)}（${d.manifest.files.length} 檔）`;
    admRefreshBackups();
  } catch (e) { alert('錯誤：' + e.message); }
}

function _renderManifest(m) {
  const ex = m.exercise
    ? `演習：${_escAudit(m.exercise.name)}（${_escAudit(m.exercise.type)} / ${_escAudit(m.exercise.status)}）`
    : '演習：（無 — 實戰池 / 系統層）';
  const files = (m.files || []).map(f => `<li>${_escAudit(f)}</li>`).join('');
  return `建立：${_escAudit(m.created_at)} · app ${_escAudit(m.app_version)} · 來源：${_escAudit(m.trigger)}<br>${ex}`
    + ` · ${(m.files || []).length} 檔<ul style="columns:2;margin:4px 0;">${files}</ul>`;
}

function _renderRestoreResult(d) {
  const detail = el('adm-backup-detail');
  if (!detail) return;
  detail.innerHTML = `<span style="color:var(--green);">✅ 還原完成`
    + (d.pre_restore ? `（當前已備份為 <code>${_escAudit(d.pre_restore)}</code>）` : '') + '</span>'
    + '<br><span style="color:var(--yellow);">⚠️ 請重啟指揮部服務讓新資料生效。</span><br>' + _renderManifest(d.manifest);
}

// 還原清單裡某一筆（伺服器端，免下載再上傳）
export async function admRestoreFromList(name) {
  if (!confirm(`確定以「${name}」覆蓋當前 data/？\n系統會先自動備份當前為 pre-restore-*，還原後需重啟服務。`)) return;
  try {
    const r = await authFetch(API_BASE + '/api/admin/user-data-backups/' + encodeURIComponent(name) + '/restore', { method: 'POST' });
    if (!r.ok) { alert('還原失敗（' + r.status + '）：' + ((await r.json().catch(() => ({}))).detail || '')); return; }
    _renderRestoreResult(await r.json());
    admRefreshBackups();
  } catch (e) { alert('錯誤：' + e.message); }
}

// 從外部檔（USB / 異地拿回）上傳還原
export async function admRestore() {
  const input = el('adm-restore-file');
  if (!input || !input.files || !input.files[0]) { alert('請先選擇 .tar.gz.enc 備份檔'); return; }
  const f = input.files[0];
  if (!f.name.endsWith('.enc')) { alert('僅接受 .tar.gz.enc 整包備份檔'); return; }
  if (!confirm(`確定以「${f.name}」覆蓋當前 data/？\n系統會先自動備份當前為 pre-restore-*，還原後需重啟服務。`)) return;
  const fd = new FormData();
  fd.append('file', f);
  try {
    const r = await authFetch(API_BASE + '/api/admin/restore', { method: 'POST', body: fd });
    if (!r.ok) { alert('還原失敗（' + r.status + '）：' + ((await r.json().catch(() => ({}))).detail || '')); return; }
    _renderRestoreResult(await r.json());
    admRefreshBackups();
  } catch (e) { alert('錯誤：' + e.message); }
}

// P2-24（#164）：把 status 物件描述成系統 tab 的唯讀連線狀態行。
// 分類邏輯**鏡像** tak_light_state.js 的 `takConnState()`（header 燈用同一套狀態界線）。
// **不直接 import**：auth.js 是 root module、受 `module_boundaries_enforced` 測試強制零 import；
// 故此處內聯一份等價分類。改其一須同步另一。
// configured/running/connected 由後端唯讀回報；admin 不在此設定 config（部署層職責），只看健康。
// #222：connected = 可收可發 → 一律綠；入向 CoT age 只附註不降級（無入向串流屬正常，非故障）。
function _admRenderTakStatus(s) {
  const line = el('adm-tak-status');
  if (!line) return;
  let txt, color;
  if (!s.enabled) { txt = '● 已停用'; color = 'var(--text3)'; }
  else if (s.configured === false) { txt = '⚠ 已啟用，但連線參數未備妥（部署層問題，非此處設定）'; color = 'var(--yellow)'; }
  else if (s.running === false) { txt = '✕ 已啟用，但訂閱未啟動（檢查後端 log）'; color = 'var(--red)'; }
  else if (!s.connected) { txt = '✕ 已啟用 · 未連線（背景重連中）'; color = 'var(--red)'; }
  else {  // #222：連上即綠「可收發」；入向 CoT age 僅附註，不再因無串流變黃
    txt = '● 已啟用 · 已連線（可收發）'
      + (s.last_cot_age_s != null ? `，${s.last_cot_age_s}s 前收到 CoT` : '，尚無入向串流');
    color = 'var(--green)';
  }
  line.textContent = txt;
  line.style.color = color;
}

async function _admLoadTakConn() {
  const toggle = el('adm-tak-toggle');
  const label = el('adm-tak-toggle-label');
  if (!toggle) return;
  try {
    const r = await authFetch(API_BASE + '/api/tak/status', { signal: AbortSignal.timeout(3000) });
    if (!r.ok) throw new Error(r.status);
    const s = await r.json();
    toggle.checked = !!s.enabled;
    toggle.disabled = false;
    if (label) label.textContent = s.enabled ? 'TAK 連線：啟用' : 'TAK 連線：停用';
    _admRenderTakStatus(s);
  } catch (e) {
    toggle.disabled = true;
    if (label) label.textContent = 'TAK 連線：狀態查詢失敗';
    const line = el('adm-tak-status');
    if (line) { line.textContent = '✕ ' + (e.message || e); line.style.color = 'var(--red)'; }
  }
}

export async function admToggleTak() {
  const toggle = el('adm-tak-toggle');
  const label = el('adm-tak-toggle-label');
  if (!toggle) return;
  const desired = toggle.checked;          // checkbox 已被使用者點成新狀態
  toggle.disabled = true;
  if (label) label.textContent = desired ? '啟用中…' : '停用中…';
  try {
    const r = await authFetch(API_BASE + '/api/tak/connection', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: desired }),
    });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) {
      // 後端拒絕（403 非 sysadmin / 422）→ 還原 checkbox，顯示原因
      toggle.checked = !desired;
      const line = el('adm-tak-status');
      if (line) { line.textContent = '✕ ' + (body.detail || ('操作失敗（' + r.status + '）')); line.style.color = 'var(--red)'; }
      if (label) label.textContent = toggle.checked ? 'TAK 連線：啟用' : 'TAK 連線：停用';
      return;
    }
    // 成功：重抓完整 status（含 configured/connected）誠實描述狀態行 +（toggle 由 _admLoadTakConn 還原）
    document.dispatchEvent(new CustomEvent('tak:connection-changed'));   // 立即重抓 header 燈
    await _admLoadTakConn();
  } catch (e) {
    // 網路錯誤 / timeout（authFetch 拋出）：POST 未成立 → 還原 checkbox 至原狀並提示，
    // 否則 UI 停在樂觀值且無任何回饋（unhandled rejection）。
    toggle.checked = !desired;
    const line = el('adm-tak-status');
    if (line) { line.textContent = '✕ 連線失敗：' + (e.message || e); line.style.color = 'var(--red)'; }
    if (label) label.textContent = toggle.checked ? 'TAK 連線：啟用' : 'TAK 連線：停用';
  } finally {
    toggle.disabled = false;
  }
}

export async function admChangeAdminPin() {
  const oldPin  = el('adm-sys-old-pin').value.trim();
  const newPin  = el('adm-sys-new-pin').value.trim();
  const newPin2 = el('adm-sys-new-pin2').value.trim();
  const warn    = el('adm-sys-warn');
  warn.textContent = '';

  if (!oldPin || !newPin || !newPin2) { warn.textContent = '⚠️ 請填寫所有欄位'; return; }
  if (!/^\d{4,6}$/.test(newPin))     { warn.textContent = '⚠️ 新 PIN 須為 4-6 位數字'; return; }
  if (newPin !== newPin2)             { warn.textContent = '⚠️ 新 PIN 兩次輸入不一致'; return; }

  const resp = await authFetch(API_BASE + '/api/admin/pin', {
    method: 'PUT',
    headers: {'X-Admin-PIN': oldPin, 'Content-Type': 'application/json'},
    body: JSON.stringify({new_pin: newPin}),
  });
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const detail = body.detail || '';
    if (resp.status === 423) {
      warn.textContent = '🔒 ' + detail.replace(
        /\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z/,
        s => new Date(s).toLocaleTimeString('zh-TW', {hour:'2-digit', minute:'2-digit'})
      );
    } else {
      warn.textContent = '❌ ' + (detail || '更改失敗，請確認目前 PIN 是否正確');
    }
    return;
  }
  _admPin = newPin;
  el('adm-sys-old-pin').value = '';
  el('adm-sys-new-pin').value = '';
  el('adm-sys-new-pin2').value = '';
  warn.style.color = 'var(--green, #4caf50)';
  warn.textContent = '✅ Admin PIN 已更新';
  setTimeout(() => { warn.textContent = ''; warn.style.color = 'var(--red)'; }, 3000);
}

function _admRoleOptions(selectedRole = '', sysadminOnly = false) {
  const roles = sysadminOnly
    ? ['系統管理員', '指揮官', '操作員', '觀察員']
    : ['操作員', '觀察員'];
  return roles.map(role =>
    '<option value="' + role + '"' + (selectedRole === role ? ' selected' : '') + '>' + role + '</option>'
  ).join('');
}

export async function admLoadAccounts() {
  const resp = await authFetch(API_BASE + '/api/admin/accounts', {headers:{'X-Admin-PIN':_admPin}});
  if (!resp.ok) {
    const msg = _isCommanderSession()
      ? '無法載入下屬帳號，請重新登入後再試。'
      : '無法載入帳號列表。';
    el('adm-panel-list').innerHTML =
      '<div style="color:var(--red);font-size:12px;padding:16px;">' + msg + '</div>';
    return;
  }
  const accounts = await resp.json();
  let html = '';
  if (!accounts.length) {
    html = '<div style="color:var(--text2);font-size:12px;padding:16px;line-height:1.7;">' +
      (_isCommanderSession()
        ? '目前沒有可管理的下屬帳號。指揮官可新增或管理操作員 / 觀察員帳號。'
        : '目前沒有帳號。') +
      '<div style="margin-top:12px;"><button class="adm-btn" data-action="admShowTab" data-tab="add">新增帳號</button></div>' +
      '</div>';
    el('adm-panel-list').innerHTML = html;
    return;
  }
  for (const a of accounts) {
    const statusCls = a.status === 'active' ? 'active' : 'suspended';
    const statusLabel = a.status === 'active' ? '啟用' : '停用';
    html += '<div class="adm-account-card" id="adm-card-' + a.username + '">' +
      '<div class="adm-account-row">' +
        '<div><span class="adm-account-name">' + a.username + '</span>' +
          (a.display_name ? ' <span style="color:var(--text3);font-size:11px;">' + a.display_name + '</span>' : '') +
        '</div>' +
        '<div style="display:flex;gap:4px;">' +
          '<span class="adm-badge role">' + a.role + '</span>' +
          '<span class="adm-badge ' + statusCls + '">' + statusLabel + '</span>' +
        '</div>' +
      '</div>' +
      '<div class="adm-btns">' +
        '<button class="adm-btn" data-action="adm-toggle-edit" data-username="' + a.username + '">編輯</button>' +
        '<button class="adm-btn" data-action="adm-toggle-status" data-username="' + a.username + '" data-status="' + a.status + '">' + (a.status === 'active' ? '停用' : '啟用') + '</button>' +
        // #275 wave B：裝置憑證（mTLS 第二因子）管理，sysadmin only
        (_isSysadminSession() ? '<button class="adm-btn" data-action="adm-toggle-certs" data-username="' + a.username + '">🔑 裝置憑證</button>' : '') +
      '</div>' +
      '<div class="adm-certs-panel" id="adm-certs-' + a.username + '" style="display:none;margin-top:8px;"></div>' +
      '<div class="adm-edit-form" id="adm-edit-' + a.username + '" style="display:none;">' +
        '<label>新 PIN（4-6 位數字，留空不改）</label>' +
        '<input id="adm-newpin-' + a.username + '" type="password" inputmode="numeric" maxlength="6" placeholder="新 PIN">' +
        '<label>角色</label>' +
        '<select id="adm-role-' + a.username + '">' + _admRoleOptions(a.role, _isSysadminSession()) + '</select>' +
        '<label>顯示名稱</label>' +
        '<input id="adm-dname-' + a.username + '" value="' + (a.display_name || '') + '">' +
        '<div style="display:flex;gap:6px;">' +
          '<button class="adm-btn" data-action="adm-save-edit" data-username="' + a.username + '">儲存</button>' +
          '<button class="adm-btn" data-action="adm-toggle-edit" data-username="' + a.username + '">取消</button>' +
        '</div>' +
      '</div>' +
    '</div>';
  }
  if (!accounts.length) html = '<div style="color:var(--text3);text-align:center;padding:20px;">無帳號</div>';
  el('adm-panel-list').innerHTML = html;
}

export function admToggleEdit(username) {
  const form = el('adm-edit-' + username);
  if (form) form.style.display = form.style.display === 'none' ? '' : 'none';
}

// ── #275 wave B：per-device 裝置憑證（mTLS 第二因子）綁定/撤銷 ──────────────
// 簽證在主機外走 step-ca（deploy/step-ca/issue-client-cert.sh）；此 UI 管 CN↔帳號
// 綁定授權。一帳號可綁多台裝置；撤銷即時失效（後端 check_session 查表）。sysadmin only。

export function admToggleCerts(username) {
  const box = el('adm-certs-' + username);
  if (!box) return;
  const opening = box.style.display === 'none';
  box.style.display = opening ? '' : 'none';
  if (opening) admLoadCerts(username);
}

export async function admLoadCerts(username) {
  const box = el('adm-certs-' + username);
  if (!box) return;
  box.innerHTML = '<div style="color:var(--text3);font-size:12px;padding:6px;">載入中…</div>';
  const resp = await authFetch(API_BASE + '/api/admin/accounts/' + username + '/certs');
  if (!resp.ok) {
    box.innerHTML = '<div style="color:var(--red);font-size:12px;padding:6px;">無法載入裝置憑證（需系統管理員）</div>';
    return;
  }
  const certs = await resp.json();
  const certRow = (c) => {
    const active = c.status === 'active';
    return '<div style="display:flex;align-items:center;gap:6px;padding:4px 0;border-bottom:1px solid var(--border,#222);font-size:12px;">' +
        '<span style="font-family:monospace;flex:1;' + (active ? '' : 'text-decoration:line-through;color:var(--text3);') + '">' + _escAudit(c.cert_cn) + '</span>' +
        (c.label ? '<span style="color:var(--text3);">' + _escAudit(c.label) + '</span>' : '') +
        '<span class="adm-badge ' + (active ? 'active' : 'suspended') + '">' + (active ? '有效' : '已撤銷') + '</span>' +
        (active ? '<button class="adm-btn" data-action="adm-revoke-cert" data-username="' + username + '" data-cert-id="' + c.id + '">撤銷</button>' : '') +
      '</div>';
  };
  // #307 缺口 2：active 恆顯示；revoked 預設摺疊（toggle 展開）+ 可一鍵清除。
  const actives = certs.filter(c => c.status === 'active');
  const revoked = certs.filter(c => c.status !== 'active');
  let rows = actives.map(certRow).join('');
  if (!certs.length) rows = '<div style="color:var(--text3);font-size:12px;padding:6px;">尚無裝置憑證</div>';
  if (revoked.length) {
    rows +=
      '<div style="display:flex;align-items:center;gap:6px;margin-top:6px;">' +
        '<button class="adm-btn" data-action="adm-toggle-revoked" data-username="' + username + '">已撤銷（' + revoked.length + '）▾</button>' +
        '<button class="adm-btn" data-action="adm-purge-revoked" data-username="' + username + '" title="永久刪除所有已撤銷列（audit log 保留）">清除已撤銷</button>' +
      '</div>' +
      '<div id="adm-revoked-' + username + '" style="display:none;margin-top:4px;">' +
        revoked.map(certRow).join('') +
      '</div>';
  }
  box.innerHTML =
    rows +
    '<div style="display:flex;gap:4px;margin-top:8px;flex-wrap:wrap;">' +
      '<input id="adm-certcn-' + username + '" placeholder="裝置憑證 CN（如 指揮官-手機）" style="flex:2;min-width:180px;font-family:monospace;">' +
      '<input id="adm-certlabel-' + username + '" placeholder="標籤（選填，如 指揮官手機）" style="flex:1;min-width:120px;">' +
      '<button class="adm-btn" data-action="adm-issue-cert" data-username="' + username + '" title="線上向 step-ca 簽發並下載 .p12，自動綁定">發憑證</button>' +
      '<button class="adm-btn" data-action="adm-bind-cert" data-username="' + username + '" title="已有離線簽好的憑證時，只綁定 CN">僅綁定</button>' +
    '</div>' +
    '<div style="font-size:11px;color:var(--text3);margin-top:6px;line-height:1.5;"><b>發憑證</b>：線上向 step-ca 簽一張並下載 .p12（匯入裝置/瀏覽器）+ 自動綁定。<b>僅綁定</b>：已用 deploy/step-ca 離線簽好時，只綁 CN ↔ 帳號。一帳號可綁多台裝置，撤銷即時失效。</div>';
}

export async function admBindCert(username) {
  const cn = el('adm-certcn-' + username)?.value.trim();
  const label = el('adm-certlabel-' + username)?.value.trim();
  if (!cn) { alert('請輸入裝置憑證 CN'); return; }
  const resp = await authFetch(API_BASE + '/api/admin/accounts/' + username + '/certs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cert_cn: cn, label: label || null }),
  });
  if (resp.status === 409) { alert('此 CN 已被有效綁定（撤銷後才可重綁）'); return; }
  if (!resp.ok) { alert('綁定失敗（' + resp.status + '）'); return; }
  admLoadCerts(username);
}

export async function admIssueCert(username) {
  const cn = el('adm-certcn-' + username)?.value.trim();
  const label = el('adm-certlabel-' + username)?.value.trim();
  if (!cn) { alert('請輸入裝置憑證 CN'); return; }
  const resp = await authFetch(API_BASE + '/api/admin/accounts/' + username + '/certs/issue', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cert_cn: cn, label: label || null }),
  });
  if (resp.status === 503) { alert('線上發證未配置（step-ca daemon 未接）。請改用 deploy/step-ca 離線簽好後按「僅綁定」。'); return; }
  if (resp.status === 409) { alert('此 CN 已被有效綁定（撤銷後才可重發）'); return; }
  if (!resp.ok) { alert('發證失敗（' + resp.status + '）：' + (await resp.text()).slice(0, 200)); return; }
  // 下載簽出的 p12（含私鑰，匯入裝置/瀏覽器）
  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = cn + '.p12';
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
  alert('已簽發並下載 ' + cn + '.p12（匯入密碼見部署設定，預設 icsclient）。已自動綁定此帳號。');
  admLoadCerts(username);
}

export async function admRevokeCert(username, certId) {
  if (!confirm('撤銷此裝置憑證？該裝置將立即無法登入（活躍 session 一併失效）。')) return;
  const resp = await authFetch(API_BASE + '/api/admin/accounts/' + username + '/certs/' + certId, { method: 'DELETE' });
  if (!resp.ok) { alert('撤銷失敗（' + resp.status + '）'); return; }
  admLoadCerts(username);
}

// #307 缺口 2：展開/摺疊已撤銷列。
export function admToggleRevoked(username) {
  const box = el('adm-revoked-' + username);
  if (box) box.style.display = box.style.display === 'none' ? '' : 'none';
}

// #307 缺口 2：清除所有已撤銷列（audit log 保留）。
export async function admPurgeRevoked(username) {
  if (!confirm('永久刪除此帳號所有「已撤銷」的憑證記錄？\n（稽核日誌會保留，僅清掉列表死記錄）')) return;
  const resp = await authFetch(API_BASE + '/api/admin/accounts/' + username + '/certs/revoked', { method: 'DELETE' });
  if (!resp.ok) { alert('清除失敗（' + resp.status + '）'); return; }
  admLoadCerts(username);
}

export async function admSaveEdit(username) {
  const newPin = el('adm-newpin-' + username)?.value.trim();
  const newRole = el('adm-role-' + username)?.value;
  const newDname = el('adm-dname-' + username)?.value.trim();
  const headers = {'X-Admin-PIN': _admPin, 'Content-Type': 'application/json'};
  if (newPin) {
    if (!/^\d{4,6}$/.test(newPin)) { alert('PIN 須為 4-6 位數字'); return; }
    await authFetch(API_BASE + '/api/admin/accounts/' + username + '/pin', {method:'PUT', headers, body:JSON.stringify({new_pin:newPin})});
  }
  if (newRole) {
    await authFetch(API_BASE + '/api/admin/accounts/' + username + '/role', {
      method:'PUT',
      headers,
      body: JSON.stringify({role: newRole}),
    });
  }
  // 顯示名稱：原本讀了 newDname 卻沒送出（dead input）。改為實際 PUT，並接後端 422（XSS / 過長）。
  if (newDname !== undefined && newDname !== null) {
    const r = await authFetch(API_BASE + '/api/admin/accounts/' + username + '/display-name', {
      method:'PUT',
      headers,
      body: JSON.stringify({display_name: newDname}),
    });
    if (r && !r.ok) { alert('顯示名稱更新失敗（含不允許字元或超過 64 字）'); return; }
  }
  admLoadAccounts();
}

export async function admToggleStatus(username, current) {
  const newStatus = current === 'active' ? 'suspended' : 'active';
  await authFetch(API_BASE + '/api/admin/accounts/' + username + '/status', {
    method:'PUT',
    headers:{'X-Admin-PIN':_admPin,'Content-Type':'application/json'},
    body: JSON.stringify({status: newStatus}),
  });
  admLoadAccounts();
}

export async function admDelete(username) {
  if (!confirm('確定刪除帳號 ' + username + '？')) return;
  await authFetch(API_BASE + '/api/admin/accounts/' + username, {
    method:'DELETE', headers:{'X-Admin-PIN':_admPin},
  });
  admLoadAccounts();
}

export function admShowAddForm() {
  el('adm-panel-add').innerHTML =
    '<div class="adm-add-form">' +
      '<label>帳號</label><input id="adm-add-user" placeholder="帳號">' +
      '<label>PIN（4-6 位數字）</label><input id="adm-add-pin" type="password" inputmode="numeric" maxlength="6" placeholder="PIN">' +
      '<label>角色</label><select id="adm-add-role">' + _admRoleOptions('', _isSysadminSession()) + '</select>' +
      '<label>顯示名稱（選填）</label><input id="adm-add-dname" placeholder="顯示名稱">' +
      '<button class="adm-add-btn" data-action="adm-add-account">新增帳號</button>' +
      '<div id="adm-add-warn" style="font-size:11px;color:var(--red);min-height:16px;"></div>' +
    '</div>';
}

export async function admAddAccount() {
  const username = el('adm-add-user')?.value.trim();
  const pin = el('adm-add-pin')?.value.trim();
  const role = el('adm-add-role')?.value;
  const displayName = el('adm-add-dname')?.value.trim();
  const warn = el('adm-add-warn');
  warn.textContent = '';
  if (!username) { warn.textContent = '請輸入帳號'; return; }
  if (!/^\d{4,6}$/.test(pin)) { warn.textContent = 'PIN 須為 4-6 位數字'; return; }
  const resp = await authFetch(API_BASE + '/api/admin/accounts', {
    method:'POST',
    headers:{'X-Admin-PIN':_admPin,'Content-Type':'application/json'},
    body: JSON.stringify({username, pin, role, display_name: displayName || null}),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    warn.textContent = err.detail || '新增失敗';
    return;
  }
  admShowTab('list');
}

export async function admLoadLog() {
  const resp = await authFetch(API_BASE + '/api/admin/audit-log?limit=50', {headers:{'X-Admin-PIN':_admPin}});
  if (!resp.ok) return;
  const logs = await resp.json();
  let html = '';
  for (const log of logs) {
    html += '<div class="adm-log-entry">' +
      '<span class="adm-log-time">' + (_fmtLocalDT(log.created_at) || '') + '</span> ' +
      '<span class="adm-log-action">' + (log.action_type || '') + '</span> ' +
      '<span style="color:var(--text2);">' + (log.operator || '') + '</span> ' +
      '<span style="color:var(--text3);font-size:10px;">' + (log.target_table || '') + '/' + (log.target_id || '').slice(0,8) + '</span>' +
    '</div>';
  }
  el('adm-panel-log').innerHTML = html || '<div style="color:var(--text3);text-align:center;padding:20px;">無日誌</div>';
}

// ── Pi 節點管理 ────────────────────────────────────────────────
let _lastCreatedApiKey = null;

export async function admLoadPiNodes() {
  const resp = await authFetch(API_BASE + '/api/admin/pi-nodes', {headers:{'X-Admin-PIN':_admPin}});
  if (!resp.ok) return;
  const nodes = await resp.json();
  let html = '';

  html += `<div style="padding:8px 0;border-bottom:1px solid var(--border);margin-bottom:8px;">
    <div style="font-size:11px;font-weight:700;margin-bottom:6px;">註冊新節點</div>
    <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">
      <select id="pi-new-unit" style="padding:4px 8px;font-size:11px;background:var(--surface);color:var(--text);border:1px solid var(--border);border-radius:3px;">
        <option value="shelter">shelter（收容組）</option>
        <option value="medical">medical（醫療組）</option>
        <option value="forward">forward（前進組）</option>
        <option value="security">security（安全組）</option>
      </select>
      <input id="pi-new-label" placeholder="顯示名稱" style="padding:4px 8px;font-size:11px;background:var(--surface);color:var(--text);border:1px solid var(--border);border-radius:3px;width:120px;">
      <button data-action="adm-create-pi-node" style="padding:4px 12px;font-size:11px;background:var(--blue);color:#fff;border:none;border-radius:3px;cursor:pointer;">建立</button>
    </div>
  </div>`;

  html += `<div id="pi-key-display" style="display:none;padding:8px;margin-bottom:8px;background:#1a2332;border:1px solid var(--yellow);border-radius:4px;">
    <div style="font-size:10px;color:var(--yellow);font-weight:700;margin-bottom:4px;">⚠ API Key（僅顯示一次，請複製）</div>
    <div id="pi-key-value" style="font-size:10px;font-family:var(--mono);word-break:break-all;color:var(--text);user-select:all;margin-bottom:6px;"></div>
    <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">
      <button data-action="pi-copy-key" style="padding:3px 10px;font-size:10px;background:var(--surface2);color:var(--text);border:1px solid var(--border);border-radius:3px;cursor:pointer;">複製 Key</button>
      <input id="pi-admin-url" placeholder="Pi Admin URL（如 http://127.0.0.1:8766）" style="padding:3px 6px;font-size:10px;background:var(--surface);color:var(--text);border:1px solid var(--border);border-radius:3px;flex:1;min-width:180px;">
      <button data-action="adm-push-key-to-pi" style="padding:3px 10px;font-size:10px;background:var(--green);color:#000;border:none;border-radius:3px;cursor:pointer;font-weight:600;">推送至 Pi</button>
    </div>
    <div id="pi-push-result" style="font-size:10px;margin-top:4px;min-height:14px;"></div>
  </div>`;

  if (nodes.length === 0) {
    html += '<div style="color:var(--text3);text-align:center;padding:20px;font-size:11px;">尚未註冊任何 Pi 節點</div>';
  }
  for (const n of nodes) {
    let dotColor = 'var(--text3)', dotAnim = '', dotLabel = 'never';
    if (n.last_seen_at) {
      const age = Date.now() - new Date(n.last_seen_at).getTime();
      if (age < 30000)      { dotColor = 'var(--green)';  dotLabel = 'online'; }
      else if (age < 90000) { dotColor = 'var(--yellow)'; dotAnim = 'animation:blink 1s infinite;'; dotLabel = 'degrading'; }
      else                  { dotColor = 'var(--red)';    dotAnim = 'animation:blink .6s infinite;'; dotLabel = 'offline'; }
    }
    const dot = `<span style="color:${dotColor};${dotAnim}" title="${dotLabel}">●</span>`;
    const seen = n.last_seen_at ? _fmtLocalDT(n.last_seen_at) : '從未連線';
    html += `<div style="display:flex;align-items:center;padding:8px 0;border-bottom:1px solid var(--border);gap:8px;">
      <div style="flex:1;">
        <div style="font-size:12px;font-weight:600;">${dot} ${n.label || n.unit_id}</div>
        <div style="font-size:10px;color:var(--text3);">${n.unit_id} · key ...${n.api_key_suffix} · ${seen}</div>
      </div>
      <button data-action="adm-rekey-pi-node" data-unit-id="${n.unit_id}" style="padding:3px 8px;font-size:10px;background:var(--surface2);color:var(--text);border:1px solid var(--border);border-radius:3px;cursor:pointer;">換 Key</button>
      <button data-action="adm-delete-pi-node" data-unit-id="${n.unit_id}" style="padding:3px 8px;font-size:10px;background:var(--surface2);color:var(--red);border:1px solid var(--border);border-radius:3px;cursor:pointer;">刪除</button>
    </div>`;
  }
  el('adm-panel-pi').innerHTML = html;
}

export async function admCreatePiNode() {
  const unit_id = el('pi-new-unit')?.value;
  const label = el('pi-new-label')?.value.trim() || unit_id;
  const resp = await authFetch(API_BASE + '/api/admin/pi-nodes', {
    method:'POST', headers:{'X-Admin-PIN':_admPin,'Content-Type':'application/json'},
    body: JSON.stringify({unit_id, label}),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(()=>({}));
    alert(err.detail || '建立失敗');
    return;
  }
  const result = await resp.json();
  _lastCreatedApiKey = result.api_key;
  admLoadPiNodes().then(() => {
    el('pi-key-display').style.display = '';
    el('pi-key-value').textContent = result.api_key;
    el('pi-push-result').textContent = '';
  });
}

export async function admRekeyPiNode(unitId) {
  if (!confirm(`確定要重新產生 ${unitId} 的 API Key？舊 Key 將立即失效。`)) return;
  const resp = await authFetch(API_BASE + `/api/admin/pi-nodes/${unitId}/rekey`, {
    method:'POST', headers:{'X-Admin-PIN':_admPin},
  });
  if (!resp.ok) { alert('操作失敗'); return; }
  const result = await resp.json();
  _lastCreatedApiKey = result.api_key;
  admLoadPiNodes().then(() => {
    el('pi-key-display').style.display = '';
    el('pi-key-value').textContent = result.api_key;
    el('pi-push-result').textContent = '';
  });
}

export async function admDeletePiNode(unitId) {
  if (!confirm(`確定刪除 ${unitId} 節點？`)) return;
  await authFetch(API_BASE + `/api/admin/pi-nodes/${unitId}`, {
    method:'DELETE', headers:{'X-Admin-PIN':_admPin},
  });
  admLoadPiNodes();
}

export async function admPushKeyToPi() {
  const piUrl = el('pi-admin-url')?.value.trim().replace(/\/$/,'');
  const key = el('pi-key-value')?.textContent;
  const resultEl = el('pi-push-result');
  if (!piUrl) { resultEl.textContent = '請輸入 Pi Admin URL'; resultEl.style.color = 'var(--red)'; return; }
  if (!key) { resultEl.textContent = '無 API Key'; resultEl.style.color = 'var(--red)'; return; }
  resultEl.textContent = '推送中...'; resultEl.style.color = 'var(--text3)';
  try {
    const r1 = await fetch(piUrl + '/admin/command-url', {
      method:'POST', headers:{'X-Admin-PIN':'1234','Content-Type':'application/json'},
      body: JSON.stringify({url: API_BASE}),
    });
    if (!r1.ok) { resultEl.textContent = '設定 command_url 失敗（檢查 Pi Admin PIN）'; resultEl.style.color='var(--red)'; return; }
    const r2 = await fetch(piUrl + '/admin/pi-api-key', {
      method:'POST', headers:{'X-Admin-PIN':'1234','Content-Type':'application/json'},
      body: JSON.stringify({api_key: key}),
    });
    if (!r2.ok) { resultEl.textContent = '設定 api_key 失敗'; resultEl.style.color='var(--red)'; return; }
    resultEl.textContent = '✓ 已推送 command_url + api_key 至 Pi'; resultEl.style.color = 'var(--green)';
  } catch(e) {
    resultEl.textContent = '連線失敗：' + e.message; resultEl.style.color = 'var(--red)';
  }
}

// ── 通用 Modal（由 main.js 注入 handler，auth.js 不 import 業務模組）───────
function openModal(title, body, footer) {
  if (_openModal) _openModal(title, body, footer);
}
function closeModal() {
  if (_closeModal) _closeModal();
}

// ── 鍵盤快捷鍵（Enter 觸發登入 / 解鎖）──────────────────────────
document.addEventListener('keydown', e => {
  if (_sessionWarningVisible && e.key === 'Escape') {
    e.preventDefault();
    e.stopPropagation();
    return;
  }
  if (e.key !== 'Enter') return;
  const ls = el('login-screen');
  if (ls && ls.style.display !== 'none' && document.activeElement &&
      (document.activeElement.id === 'cmd-username' || document.activeElement.id === 'cmd-pin')) {
    handleCmdLogin();
    return;
  }
  const pl = el('pin-lock-overlay');
  if (pl && pl.classList.contains('show') && document.activeElement?.id === 'pinlock-pin') {
    PinLock.unlock();
    return;
  }
  if (document.activeElement?.id === 'adm-pin-input') {
    adminLogin();
  }
});

// ── 認證初始化（供 main.js 呼叫）───────────────────────────────
export async function authInit(options = {}) {
  _onEnterDashboard = options.onEnterDashboard || null;
  const token = sessionStorage.getItem('cmd_session_id');
  if (token) {
    try {
      const resp = await fetch(API_BASE + '/api/auth/heartbeat', {
        headers: {'X-Session-Token': token}
      });
      if (resp.ok) {
        const data = await resp.json().catch(() => ({}));
        if (data.username) sessionStorage.setItem('cmd_username', data.username);
        if (data.role) sessionStorage.setItem('cmd_role', data.role);
        if (data.role_detail) sessionStorage.setItem('cmd_role_detail', data.role_detail);
        if (data.display_name) sessionStorage.setItem('cmd_display_name', data.display_name);
        _resetSessionLocalIdle();
        _enterDashboard();
        return 'ok';
      }
    } catch(e) {}
    clearSession();
  }
  el('login-screen').style.display = '';
  return 'need-login';
}
