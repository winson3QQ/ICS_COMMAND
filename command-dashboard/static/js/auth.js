// SPDX-License-Identifier: LicenseRef-Proprietary
// Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
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
// #293：session token 改放 httpOnly cookie（JS 讀不到）→ getToken() 平時回 null，僅測試手動塞時有值。
// 認證改由瀏覽器自動帶的同源 cookie 承載；getToken 保留供「若有值才附 header」的相容分支（authFetch）。
export function getToken() {
  return sessionStorage.getItem('cmd_session_id');
}

// #293：登入態判斷（非秘密）。原本全前端拿「有沒有 token」當「登入了嗎」的代理，token 藏進 cookie
// 後改用顯示用的 cmd_username（登入時寫、登出時清）當旗標。所有登入閘（WS/poll/chat/aar）改吃此函式。
export function isLoggedIn() {
  return !!sessionStorage.getItem('cmd_username');
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

// #463：出向 GeoChat compose 顯隱（對齊後端 POST /api/tak/chat = WRITE_ROLES）。
// role 集合收斂在本檔——別在各面板 inline 攤開角色清單（會與後端 WRITE_ROLES 漂移）。
export function canSendChat() {
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

// #354：判定一個帳號物件（/api/admin/accounts 回傳，帶 role + role_detail）是否為 sysadmin。
// 用於 UI 鎖定「系統內最後一個 active sysadmin」的降權控制。後端守門仍是最終防線。
function _isAccountSysadmin(a) {
  return ROLE_ALIASES.sysadmin.includes(a.role_detail) || ROLE_ALIASES.sysadmin.includes(a.role);
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
  if (!isLoggedIn()) return;  // #293：登入閘改吃非秘密旗標（token 已進 cookie，getToken 平時為 null）
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
  // P3（#348-F5）：登入欄放寬接受密語（與首登強制改連動，否則設了密語登不進）。登入不套 set-time
  // 強度策略（min6 等由 pin_policy 在「設定時」把關）；此處只擋空值，legacy 短 PIN 帳號仍能登入。
  if (!pin) { warn.textContent = '請輸入 PIN 或密語'; return; }

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
    // #293 階段2：session token 已由伺服器種進 httpOnly cookie（Set-Cookie）→ 前端不再存 token
    // （sessionStorage 存 token = XSS 可讀，正是本單要斷的根）。以下 cmd_* 為顯示資料，非憑證，保留。
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
        請設定你的新 PIN 或密語（至少 6 字元）<br>設定後 gate 解除，進入系統
      </div>
      <div class="pw-wrap" style="margin-bottom:12px;">
        <input id="ipc-new-pin" class="login-input" type="password" maxlength="128"
               placeholder="新 PIN 或密語（至少 6 字元）">
        <button type="button" class="pw-toggle" data-action="pwToggle" data-id="ipc-new-pin"
                aria-label="顯示或隱藏密碼" tabindex="-1">👁</button>
      </div>
      <div class="pw-wrap" style="margin-bottom:16px;">
        <input id="ipc-confirm-pin" class="login-input" type="password" maxlength="128"
               placeholder="確認新 PIN / 密語">
        <button type="button" class="pw-toggle" data-action="pwToggle" data-id="ipc-confirm-pin"
                aria-label="顯示或隱藏密碼" tabindex="-1">👁</button>
      </div>
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

    // P3：FE 只驗長度 6–128 + 兩次一致 + 不同初始值；可預測值/blocklist 交 BE pin_policy（422 err.detail 顯）。
    if (newPin.length < 6 || newPin.length > 128) { warnEl.textContent = 'PIN / 密語至少 6 字元'; return; }
    if (newPin !== confirmPin)               { warnEl.textContent = '兩次輸入不一致'; return; }
    if (newPin === currentPin)               { warnEl.textContent = '新 PIN 不能與初始值相同'; return; }

    submitBtn.disabled = true;
    try {
      const resp = await fetch(API_BASE + '/api/auth/change-initial-pin', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        // #293：認證靠登入時種下的 httpOnly cookie（同源自動帶），不再手動附 token header
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
  // #346：演習入口（設定→演習）限指揮層（sysadmin + commander）。
  // operator/observer 進去只剩唯讀的演習管理列表（紅藍=sysadmin、回放=COMMAND 子分頁都被擋）→ 死路，故隱藏入口。
  // 進行中場次仍由 header 的 exercise-chip 對全角色顯示，operator 不會失去演習感知。
  // 面板內子分頁的 RBAC 另由 openExercisePanel/admExerciseSub 守。
  const exSec = el('stg-exercise-section');
  if (exSec) exSec.style.display = hasAnyRole('sysadmin', 'commander') ? '' : 'none';
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
  _maskPwField('cmd-pin');   // P3：清掉上一位的 show-password 明文狀態，下一位恆預設遮蔽
  el('cmd-login-warn').textContent = '';
  el('cmd-user-badge').textContent = '';
}

// P3（#348-F5）：密碼欄復原為遮蔽 + 眼睛圖示。show-password 是 opt-in，登出/鎖定後須回預設
// 遮蔽，否則共用大螢幕上「明文」狀態會殘留給下一位使用者（肩窺）。動態 overlay 每次重建免處理。
function _maskPwField(inputId) {
  const inp = document.getElementById(inputId);
  if (inp) inp.type = 'password';
  const tog = document.querySelector('.pw-toggle[data-id="' + inputId + '"]');
  if (tog) tog.textContent = '👁';
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
    _maskPwField('pinlock-pin');   // P3：解鎖欄回預設遮蔽（避免明文狀態殘留）
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

// #419：下載產品 SBOM（CycloneDX）。READ_ROLES（所有登入角色可達）；非 release build → 後端 404。
export async function downloadSBOM() {
  let resp;
  try { resp = await authFetch(API_BASE + '/api/sbom'); }
  catch (e) { alert('SBOM 下載失敗（連線錯誤）'); return; }
  if (resp.status === 404) { alert('此 build 未附 SBOM（僅正式 release build 提供）'); return; }
  if (!resp.ok) { alert('SBOM 下載失敗（' + resp.status + '）'); return; }
  const data = await resp.json().catch(() => null);
  if (!data) { alert('SBOM 內容無法解析'); return; }
  const blob = new Blob([JSON.stringify(data, null, 2)], {type:'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'ics-sbom-' + (document.body.dataset.cmdVersion || 'current') + '.cdx.json';
  a.click();
  URL.revokeObjectURL(a.href);
  // 元件數回饋（#419）
  const n = Array.isArray(data.components) ? data.components.length : null;
  const sub = el('si-sbom-sub');
  if (sub && n != null) sub.textContent = '已下載 · ' + n + ' 個元件';
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
  'SESSION_EXPIRED':        { c:'#8b949e', zh:'逾時登出' },  // per-request：活躍 token 撞絕對逾時失效
  'SESSION_REAPED':         { c:'#6e7681', zh:'例行清理' },  // #345：批次清被丟棄 session（低訊號，與安全事件分流）
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
// export 供 dom_xss_escape.test.js 鎖跳脫行為（#293）。
export function _escAudit(s) {
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
      // #293：_auditEventLabel 回傳 _event_desc / event_code / target_id（皆使用者/TAK 撰寫的自由
      // 文字），於 sink 統一 escape，涵蓋其三條回傳路徑（與本 modal 其他欄位一致）。
      targetHtml = `<span style="color:var(--text2);font-size:11px;">${_escAudit(_auditEventLabel(log))}</span>`;
    } else if (log.target_table === 'accounts') {
      targetHtml = `<span style="color:var(--text2);font-size:11px;">${_escAudit(log.target_id || '')}</span>`;
    } else if (log.target_table === 'cop_entities') {
      targetHtml = `<span style="color:var(--text2);font-size:11px;">${_escAudit(_copLabel) || (log.target_id || '').slice(0, 12)}</span>`;
    } else if (log.target_table) {
      targetHtml = `<span style="color:var(--text3);font-size:10px;">${_escAudit(log.target_table)}</span>`;
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
// #384：移除死掉的「Admin PIN」第二道（X-Admin-PIN 無後端驗證，後台僅靠 session 角色 RBAC 把關）。
// 面板直接開（帳號管理角色才到得了此入口），無 PIN gate 畫面。

export function openAdminPanel() {
  closeSettings();
  el('admin-overlay').classList.add('show');
  el('admin-panel').classList.add('show');
  if (!_isAccountManagerSession()) {
    el('adm-main').style.display = 'none';   // 非帳號管理角色不應到此（後端 admin API 一律 403）
    return;
  }
  el('adm-main').style.display = 'flex';
  _applyAdminTabVisibility();
  admShowTab('list');
  _admLoadSysInfo();
}

export function closeAdminPanel() {
  el('admin-overlay').classList.remove('show');
  el('admin-panel').classList.remove('show');
}

// #346：演習面板（設定→演習開）。內含 segmented 子分頁 演習管理/紅藍/回放（跟帳號一致）。
// sub：開啟時直接落在哪個子分頁（預設 manage）。#473-B3 開場精靈「前往分隊面板」用 'faction' 直達。
export function openExercisePanel(sub = 'manage') {
  closeSettings();
  el('exercise-overlay').classList.add('show');
  el('exercise-panel').classList.add('show');
  // 子分頁 RBAC：紅藍 sysadmin only、回放 COMMAND_ROLES；演習管理全角色（列表，動作另 gate）。
  const facSub = el('ex-subtab-faction');
  if (facSub) facSub.style.display = _isSysadminSession() ? '' : 'none';
  const aarSub = el('ex-subtab-aar');
  if (aarSub) aarSub.style.display = canUseRealModeControls() ? '' : 'none';
  admExerciseSub(sub);  // admExerciseSub 內含 RBAC 兜底（非 sysadmin 的 faction → manage）
}

export function closeExercisePanel() {
  el('exercise-overlay').classList.remove('show');
  el('exercise-panel').classList.remove('show');
}

// #346：演習面板子分頁切換（跟 admAccountSub 同模式）。RBAC：紅藍 sysadmin、回放 COMMAND。
export function admExerciseSub(sub) {
  if (sub === 'faction' && !_isSysadminSession()) sub = 'manage';
  if (sub === 'aar' && !canUseRealModeControls()) sub = 'manage';
  if (!['manage', 'faction', 'aar'].includes(sub)) sub = 'manage';
  document.querySelectorAll('#ex-subtabs .adm-subtab').forEach(t => t.classList.toggle('active', t.dataset.sub === sub));
  const panels = { manage: 'ex-sub-manage', faction: 'adm-panel-faction', aar: 'ex-sub-aar' };
  for (const [k, id] of Object.entries(panels)) {
    const e = el(id);
    if (e) e.style.display = k === sub ? '' : 'none';
  }
  if (sub === 'manage') import('./exercises.js').then(m => m.renderExercisePanel());
  else if (sub === 'faction') admLoadFactions();
  // aar 子分頁＝純導航按鈕，無需 load
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
  // #346 RBAC：非 sysadmin（ACCOUNT_MANAGER，如 commander）只見「帳號」群；其餘群（Pi/日誌/備份/TAK）
  // 為 SYSADMIN_ONLY → 隱藏。帳號群內 sys(PIN) 子區另由 admShowTab 再 gate。
  document.querySelectorAll('.adm-tab').forEach(tab => {
    tab.style.display = _isSysadminSession() || tab.dataset.tab === 'account' ? '' : 'none';
  });
}

// #346：admin 後台 IA 重整——8 tab → 5 群（帳號群併 list+add）。演習群（紅藍/演習管理/AAR）移至設定
// 面板（見 PART B）。RBAC：非帳號群整群僅 sysadmin。#384：移除 sys(Admin PIN) 子分頁（死功能）。
const _ADM_GROUPS = {
  account: ['list', 'add'],
  pi: ['pi'],
  log: ['log'],
  data: ['data'],
  tak: ['tak'],
};
const _ADM_ALL_PANELS = ['list', 'add', 'pi', 'log', 'data', 'tak'];
let _admAccountSub = 'list';  // 帳號群 segmented 子分頁狀態（list/add，#346 免長捲）

export function admShowTab(group) {
  // 相容舊 tab 名：list/add 是帳號群的子分頁、faction 已移至設定面板
  if (['list', 'add'].includes(group)) { _admAccountSub = group; group = 'account'; }
  else if (!_ADM_GROUPS[group]) group = 'account';
  if (!_isSysadminSession() && group !== 'account') group = 'account';     // 非 sysadmin 只見帳號群
  _applyAdminTabVisibility();
  document.querySelectorAll('.adm-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === group));
  const subbar = el('adm-account-subtabs');
  if (group === 'account') {
    if (subbar) subbar.style.display = '';
    ['pi', 'log', 'data', 'tak'].forEach(k => { const e = el('adm-panel-' + k); if (e) e.style.display = 'none'; });
    admAccountSub(_admAccountSub);
  } else {
    if (subbar) subbar.style.display = 'none';
    _ADM_ALL_PANELS.forEach(k => { const e = el('adm-panel-' + k); if (e) e.style.display = k === group ? '' : 'none'; });
    if (group === 'pi') admLoadPiNodes();
    else if (group === 'log') admLoadLog();
    else if (group === 'data') admShowData();
    else if (group === 'tak') admShowTak();
  }
}

// #346：帳號群 segmented 子分頁（帳號列表 / 新增），一次顯一段、免長捲。
export function admAccountSub(sub) {
  if (!['list', 'add'].includes(sub)) sub = 'list';
  _admAccountSub = sub;
  document.querySelectorAll('#adm-account-subtabs .adm-subtab').forEach(t => t.classList.toggle('active', t.dataset.sub === sub));
  ['list', 'add'].forEach(k => { const e = el('adm-panel-' + k); if (e) e.style.display = k === sub ? '' : 'none'; });
  if (sub === 'list') admLoadAccounts();
  else if (sub === 'add') admShowAddForm();
}

// #343 紅藍隔離：admin 把連線 TAK client 分類成紅/藍/中立。per-exercise（active 場 scope）。
let _factionScopeEx = null;  // 目前分類作用的 exercise_id（admLoadFactions 設、classify 用）

const _FACTION_META = {
  blue:    { label: '🔵 藍', color: '#3b82f6' },
  red:     { label: '🔴 紅', color: '#ef4444' },
  neutral: { label: '⚪ 中立', color: '#9ca3af' },
};

export async function admLoadFactions() {
  const box = el('adm-panel-faction');
  if (!box) return;
  // active 場 scope（演習中 entity 綁該場；無 active → 待命池 null＝非演習非實戰）。動態 import 避免循環依賴。
  try {
    const ex = await import('./exercises.js');
    _factionScopeEx = ex.activeExerciseId();
  } catch { _factionScopeEx = null; }
  // #389：NULL scope 真正語意＝「沒開任何場（非演習非實戰）」，非「實戰」（實戰是 type=real 的 active 場、有 id）。
  const scopeLabel = _factionScopeEx == null ? '待命池（未開場：非演習非實戰）' : ('演習 #' + _factionScopeEx);
  const q = _factionScopeEx == null ? '' : ('?exercise_id=' + _factionScopeEx);
  const resp = await authFetch(API_BASE + '/api/admin/factions/clients' + q);
  if (!resp.ok) { box.innerHTML = '<div style="color:var(--text3);font-size:12px;padding:8px;">無法載入（需系統管理員）</div>'; return; }
  const clients = (await resp.json()).clients || [];
  // #475：未分隊在線提示——演習中新連入或漏分的裝置對指揮官隱形（fail-closed），白隊需即時分類。
  const unclassifiedN = clients.filter(c => !c.classified).length;
  const unclassifiedBanner = unclassifiedN
    ? '<div style="margin:8px 0;padding:6px 8px;border:1px solid var(--red,#ef4444);border-radius:4px;color:var(--red,#ef4444);font-size:12px;font-weight:600;">⚠ ' +
      unclassifiedN + ' 台未分隊在線 —— 對指揮官隱形，請盡快分類</div>'
    : '';
  let head =
    '<div style="font-size:11px;color:var(--text2);line-height:1.6;margin-bottom:10px;max-width:480px;">' +
    '列出<b>發證後且目前在線</b>的 TAK client。顯示<b>角色名</b>（in-app callsign，使用者可改），但分類綁<b>裝置憑證 CN</b>（小字，穩定不變）→ 改 callsign／重裝換 uid 都不丟分類。分類<b>分演習</b>：同一裝置跨場可不同陣營。<br><b>指揮官以下只看得到藍／中立</b>，紅軍與未分類者對其隱藏（fail-closed）。' +
    '<br>作用範圍：<b>' + scopeLabel + '</b>　·　共 ' + clients.length + ' 個 client' +
    '<br><span style="color:var(--text3);">⚠ 需開 <code>ICS_FACTION_ISOLATION</code> 過濾才生效（分類本身隨時可做）。</span>' +
    '<button class="adm-btn" data-action="admExerciseSub" data-sub="faction" style="margin-left:8px;">重新整理</button></div>';
  let rows = '';
  if (!clients.length) {
    // #346：TAK 沒開就沒 client → 情境感知空狀態（指引去開 TAK），而非冷冷一片空。
    let why = '目前無「發證後且在線」的 TAK client（需經面板發證的裝置連上 TAK 後才會列出）。';
    try {
      const ts = await authFetch(API_BASE + '/api/tak/status');
      if (ts.ok && !(await ts.json()).enabled) {
        why = '⚠ TAK 連線未啟用 → 沒有 client 可分類。請先到「設定 → 管理員後台 → TAK」啟用連線，等現場裝置 broadcast 後再回來分類。';
      }
    } catch { /* status 查不到就用預設訊息 */ }
    rows = '<div style="color:var(--text3);font-size:12px;padding:6px 0;line-height:1.7;max-width:480px;">' + why + '</div>';
  }
  for (const c of clients) {
    const f = c.faction;
    const badge = f
      ? '<span style="font-size:10px;font-weight:700;color:' + _FACTION_META[f].color + ';">' + _FACTION_META[f].label + '</span>'
      : '<span style="font-size:10px;color:var(--red,#ef4444);font-weight:700;">⚠ 未分類</span>';
    // #477b：現場隔離指示——分類了但實際 TAK 群不符 → ⚠ 未隔離（白隊看得到「沒真的隔開」）；
    // 已對齊 → 🛡；未分類（isolated=null）→ 不顯。
    let isoBadge = '';
    if (c.isolated === false) {
      const g = (c.actual_groups && c.actual_groups.length) ? c.actual_groups.join('/') : '（無/匿名）';
      isoBadge = '<span style="font-size:10px;font-weight:700;color:var(--red,#ef4444);" title="分類為 ' + _escAudit(f || '?') +
        ' 但現場實際 TAK 群為 ' + _escAudit(g) + ' → 未隔離。裝置連上後約 30 秒自動補推，或按一次陣營鈕手動補。">⚠ 未隔離</span>';
    } else if (c.isolated === true) {
      isoBadge = '<span style="font-size:10px;color:var(--green,#3fb950);" title="現場 TAK 群已對齊分類（隔離生效）">🛡</span>';
    }
    let btns = '';
    for (const fac of ['blue','red','neutral']) {
      const on = f === fac;
      btns += '<button class="adm-btn" data-action="adm-faction-classify" data-client-key="' + _escAudit(c.client_key) +
        '" data-callsign="' + _escAudit(c.callsign || '') + '" data-faction="' + fac + '" ' +
        'style="' + (on ? 'border-color:' + _FACTION_META[fac].color + ';color:' + _FACTION_META[fac].color + ';font-weight:700;' : '') + '">' +
        _FACTION_META[fac].label + '</button>';
    }
    rows += '<div style="display:flex;align-items:center;gap:6px;padding:5px 0;border-bottom:1px solid var(--border,#222);font-size:12px;">' +
      // 顯示 = live 角色名（in-app callsign，使用者可改）+ 穩定的 cert CN（mono 小字）；分類綁 CN。
      '<span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="角色（可變）：' + _escAudit(c.callsign || '') + ' ｜ 憑證 CN（分類依據）：' + _escAudit(c.cn || c.client_key) + '">' +
        _escAudit(c.callsign || c.client_key) +
        ((c.cn && c.cn !== c.callsign) ? '<span style="color:var(--text3);font-size:10px;margin-left:6px;font-family:monospace;">' + _escAudit(c.cn) + '</span>' : '') +
      '</span>' +
      badge + isoBadge +
      // #389：在線/離線指示（last_seen 時效近似，非真連線態）。
      '<span style="font-size:10px;" title="' + (c.online ? '近期有活動（≈在線）' : '較久無活動（≈離線）') + '">' + (c.online ? '🟢' : '⚪') + '</span>' +
      '<span style="color:var(--text3);font-size:10px;" title="最後活動時間（本地時區）">' + _escAudit(c.last_seen ? fmtLocalDT(c.last_seen) : '') + '</span>' +
      '<span style="display:flex;gap:3px;">' + btns + '</span>' +
    '</div>';
  }
  // 無 producer 物件（iTAK 繪圖等）手動 override：admin 直接點 uid 的陣營。
  const override =
    '<div style="margin-top:18px;border-top:1px solid var(--border);padding-top:14px;">' +
    '<div style="font-size:12px;font-weight:600;margin-bottom:6px;color:var(--text);">單一物件手動歸屬</div>' +
    '<div style="font-size:11px;color:var(--text2);margin-bottom:8px;max-width:480px;line-height:1.5;">' +
    'iTAK 繪圖等無法自動歸屬產生者的物件，在此用 uid 直接點陣營（manual，重解析不覆寫）。</div>' +
    '<div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;">' +
    '<input id="adm-faction-uid" placeholder="entity uid" style="flex:2;min-width:180px;font-family:monospace;">' +
    '<select id="adm-faction-override-sel"><option value="blue">🔵 藍</option><option value="red">🔴 紅</option><option value="neutral">⚪ 中立</option></select>' +
    '<button class="adm-btn" data-action="adm-faction-override">套用</button></div>' +
    '<div id="adm-faction-override-msg" style="font-size:11px;color:var(--text2);min-height:14px;margin-top:6px;"></div></div>';
  box.innerHTML = head + unclassifiedBanner + rows + override;
}

export async function admClassifyFaction(clientKey, faction, callsign) {
  const body = { client_key: clientKey, faction, callsign: callsign || null, exercise_id: _factionScopeEx };
  const resp = await authFetch(API_BASE + '/api/admin/factions/classify', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  if (!resp.ok) { alert('分類失敗（' + resp.status + '）：' + (await resp.text()).slice(0, 200)); return; }
  // #477b：分類即時回饋——ICS 分類必成，但 TAK 現場群同步是 best-effort。讀 tak_group.synced，
  // 沒推成就大聲講（不再假裝成功）。離線＝預期（會自動補推），語氣緩；其餘＝真問題。
  let data = null;
  try { data = await resp.json(); } catch { /* 無 body 就跳過回饋 */ }
  const tg = data && data.tak_group;
  if (tg && tg.synced === false) {
    const r = String(tg.reason || '');
    alert(r.indexOf('offline') >= 0
      ? 'ℹ️ 分類已記錄。該裝置目前離線 → TAK 現場隔離會在它連上後約 30 秒自動補推。'
      : '⚠ 分類已記錄，但 TAK 現場隔離未同步：' + r + '\n（可能 TAK 未配置；裝置在線後約 30 秒自動重試，或稍後再點一次）');
  }
  admLoadFactions();  // 重整（後端已 resync 廣播，地圖會自動更新；⚠ 未隔離 指示同步更新）
}

export async function admOverrideFaction() {
  const uid = el('adm-faction-uid')?.value.trim();
  const faction = el('adm-faction-override-sel')?.value || 'blue';
  const msg = el('adm-faction-override-msg');
  if (!uid) { if (msg) msg.textContent = '請輸入 entity uid'; return; }
  const resp = await authFetch(API_BASE + '/api/admin/factions/entity-override', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ uid, faction }),
  });
  if (msg) msg.textContent = resp.ok ? ('✓ 已套用 ' + _FACTION_META[faction].label) : ('失敗（' + resp.status + '）');
}

// #384：admShowSys（更改 Admin PIN 子分頁）已移除——Admin PIN 為死功能（X-Admin-PIN 無後端驗證）。

// #315 P2-26 L2：TAK tab —— 連線開關（搬自系統 tab）+ TAK 裝置證自助發放。
export function admShowTak() {
  el('adm-panel-tak').innerHTML = `
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
      <div style="font-size:13px;font-weight:600;margin-bottom:8px;color:var(--text);">📦 TAK 裝置憑證（data package）</div>
      <div style="font-size:11px;color:var(--text2);margin-bottom:10px;line-height:1.6;max-width:420px;">
        為 ATAK／iTAK 操作員裝置線上簽發 data package（憑證 + 信任根 + 連線設定一包，密碼內嵌免打）。
        裝置開啟即匯入並連上 TAK Server。憑證由 TAK 自己的 CA（ICS-TAK-SVC-CA）簽。
      </div>
      <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;max-width:420px;">
        <input id="adm-tak-callsign" placeholder="callsign（如 atak-phone-01）" style="flex:2;min-width:180px;font-family:monospace;">
        <select id="adm-tak-mode" title="目標平台">
          <option value="atak">ATAK（Android）</option>
          <option value="aware">iTAK / TAK Aware（iOS）</option>
        </select>
        <button class="adm-btn" data-action="adm-issue-tak-device" title="線上簽發並下載 data package">發裝置證</button>
      </div>
      <div id="adm-tak-device-result"></div>
      <div style="display:flex;align-items:center;gap:8px;margin-top:16px;margin-bottom:4px;">
        <span style="font-size:12px;font-weight:600;color:var(--text);">TAK Server 帳號</span>
        <button class="adm-btn" data-action="adm-reconcile-tak-refresh" title="重新向 TAK server 查實際帳號">↻ 重整</button>
        <button class="adm-btn" data-action="adm-backfill-tak-revocations" title="#318 Slice 3：把所有 ICS 已撤+有 fingerprint 的證一次補寫進 TAK 撤銷名單（補 #318 前撤的證沒寫 TAK 的洞）">↑ 撤銷補登 TAK</button>
      </div>
      <div style="font-size:11px;color:var(--text3);margin-bottom:6px;line-height:1.5;max-width:460px;">
        #401：此面板以 <b>TAK Server 為準</b>，列出 TAK 上**所有** managed user（不只 ICS 發的）。「撤銷」/「從 TAK 移除」會<b>真</b>從 TAK 刪該帳號。⚙ 基礎設施（ics-cot/ics-tak-admin）鎖死保護。reconcile 未配置時退回 ICS 盤點清單。
      </div>
      <div id="adm-tak-device-list" style="max-width:480px;"></div>
      <div style="margin-top:14px;max-width:480px;border-top:1px solid var(--border,#222);padding-top:10px;">
        <div style="font-size:12px;font-weight:600;color:var(--text);margin-bottom:2px;">撤銷盤點外的證（按 fingerprint）</div>
        <div style="font-size:11px;color:var(--text3);margin-bottom:6px;line-height:1.5;">
          非 dashboard 發、或 ICS 沒紀錄的證（如 CLI 發的），TAK 不吐它的 hash → 自行從裝置證取：
          <code style="font-size:10px;">openssl x509 -in cert.pem -noout -fingerprint -sha256</code>，貼進來撤。
        </div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;">
          <input id="adm-tak-revoke-fp" type="text" placeholder="AB:CD:…:EF（SHA-256 冒號分隔大寫）" style="flex:1;min-width:200px;font-family:monospace;font-size:11px;" />
          <button class="adm-btn" data-action="adm-revoke-by-fingerprint">撤銷此 fingerprint</button>
        </div>
      </div>
      <div style="margin-top:10px;max-width:480px;font-size:11px;color:#d9a441;border:1px solid #d9a441;border-radius:4px;padding:4px 6px;line-height:1.5;">
        ⚠ <b>撤「在線」證的 SOP</b>：TAK 把連過的證快取成有效 → 撤銷對在線/近期連過的證<b>不即時</b>。要立刻踢掉，在部署機重啟 TAK 清快取：<code style="font-size:10px;">docker restart takserver</code>（~95s，全員自動重連）。「先撤再連」的新證則即時生效、免重啟。
      </div>
    </div>`;
  _admLoadTakConn();
  admLoadTakDeviceCerts();
}

// #398 C / #401：發證前查同名用——已知 active callsign 集（TAK 帳號 + ICS 未同步），載入時更新。
let _lastTakCallsigns = new Set();

/** #398：fingerprint 短顯（首2 + … + 末2 組），方便和裝置上的證快速比對而不必看完整 64 hex。 */
function _fpShort(fp) {
  const g = String(fp || '').split(':');
  return g.length >= 4 ? g.slice(0, 2).join(':') + '…' + g.slice(-2).join(':') : (fp || '');
}

// #401：面板以 TAK server 為 SoT——主清單 = TAK 上所有 managed user。先試 reconcile；
// 未配置 / 失敗 → graceful 退回 ICS-only 盤點清單（舊行為）。
export async function admLoadTakDeviceCerts() {
  const box = el('adm-tak-device-list');
  if (!box) return;
  const rec = await authFetch(API_BASE + '/api/admin/tak/device-certs/reconcile');
  if (rec.ok) {
    const data = await rec.json();
    if (data.ok) {
      // #318 Slice 3：TAK-driven 視圖不含 ICS 盤點 → 另抓「已撤但無 fingerprint」的證（TAK 撤不掉），補誠實標示段。
      let nullFpRevoked = [];
      try {
        const dc = await authFetch(API_BASE + '/api/admin/tak/device-certs');
        if (dc.ok) nullFpRevoked = (await dc.json()).filter(c => c.status === 'revoked' && !c.fingerprint);
      } catch (e) { /* best-effort，不擋主視圖 */ }
      _renderTakDriven(box, data, nullFpRevoked);
      await _appendWgPeers(box);  // #434
      return;
    }
  }
  // fallback：reconcile 未配置 / 不可用 → ICS 盤點清單。
  const resp = await authFetch(API_BASE + '/api/admin/tak/device-certs');
  if (!resp.ok) { box.innerHTML = '<div style="color:var(--text3);font-size:12px;">無法載入（需系統管理員）</div>'; return; }
  _renderIcsOnlyList(box, await resp.json());
  await _appendWgPeers(box);  // #434
}

/** #434：在裝置證列表下方附「WireGuard peer 帳本」段（ICS 配給裝置的 VPN；唯讀，撤證連動撤 peer）。
 *  best-effort：endpoint 未配置/無權限 → 靜默跳過，不擋主視圖。 */
async function _appendWgPeers(box) {
  let peers;
  try {
    const r = await authFetch(API_BASE + '/api/admin/wg/peers');
    if (!r.ok) return;
    peers = await r.json();
  } catch (e) { return; }
  const active = (peers || []).filter(p => p.status === 'active');
  let html = '<div style="font-size:11px;color:var(--text3);margin:12px 0 4px;border-top:1px solid var(--border);padding-top:8px;">'
    + '🔐 WireGuard peer（' + active.length + '）— ICS 配給裝置的 VPN：</div>';
  if (!active.length) {
    html += '<div style="font-size:11px;color:var(--text3);">（無；發證時若配置 WG 會自動配 peer）</div>';
  } else {
    for (const p of active) {
      // TAK 裝置的 peer → 隨撤證連動撤（此處唯讀，避免與撤證重複）；WG-only peer（callsign 不在 TAK 帳號集）
      // → 給「撤除」鈕走 /wg/peers/revoke。fallback 視圖 _lastTakCallsigns 可能為空 → 一律當 WG-only（可撤）。
      const takLinked = _lastTakCallsigns.has(p.callsign);
      const tail = takLinked
        ? '<span style="color:var(--text3);font-size:10px;" title="撤對應 TAK 裝置證會連動撤此 peer">隨證撤</span>'
        : '<button class="adm-btn" data-action="adm-revoke-wg-peer" data-callsign="' + _escAudit(p.callsign || '') + '" title="撤除此 WG-only 設定（容器 peer + 帳本）">撤除</button>';
      html += '<div style="display:flex;align-items:center;gap:6px;padding:3px 0;border-bottom:1px solid var(--border);font-size:12px;">'
        + '<span style="font-family:monospace;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="' + _escAudit(p.callsign || '') + '">' + _escAudit(p.callsign || '—') + '</span>'
        + '<span style="font-family:monospace;color:var(--text2);font-size:11px;">' + _escAudit(p.address || '') + '</span>'
        + '<span style="font-size:10px;" title="' + (p.online ? '近期有握手（≈在線）' : '久未握手（≈離線）') + '">' + (p.online ? '🟢' : '⚪') + '</span>'
        + tail
        + '</div>';
    }
    html += '<div style="font-size:10px;color:var(--text3);margin-top:4px;">TAK 裝置 peer 隨撤證連動撤（標「隨證撤」）；WG-only 設定按「撤除」。</div>';
  }
  const div = document.createElement('div');
  div.innerHTML = html;
  box.appendChild(div);
}

const _RECON_ST = {
  matched: ['var(--green)', '✓ 相符'],
  mismatch: ['var(--red)', '⚠ 混用'],
  unknown: ['var(--text3)', '? 未知'],
  zombie: ['#d9a441', '👻 殭屍'],
  infra: ['var(--text3)', '⚙ 基礎設施'],
};

/** #401：TAK 為主的清單——TAK 上每個 managed user + ICS 未同步段。 */
function _renderTakDriven(box, data, nullFpRevoked = []) {
  _lastTakCallsigns = new Set([...data.tak_users.map(u => u.callsign), ...data.ics_unsynced.map(c => c.callsign)]);
  let html = '<div style="font-size:11px;color:var(--text3);margin:2px 0 4px;">TAK Server 帳號（' + data.tak_users.length + '）— 此面板以 TAK 為準：</div>';
  // #404：卡 __ANON__ 隔離破口面板級示警——producer 落匿名群 = 與任何 CA 信任的證同頻（不明證可注入/竊聽 COP）。
  const anonList = data.anon_users || [];
  if (anonList.length) {
    html += '<div style="font-size:11px;color:var(--red);margin:2px 0 6px;border:1px solid var(--red);border-radius:4px;padding:4px 6px;">' +
      '⚠ ' + anonList.length + ' 個身分在匿名群 <b>__ANON__</b>（隔離破口，應移出）：' + anonList.map(_escAudit).join('、') + '</div>';
  }
  for (const u of data.tak_users) {
    const m = _RECON_ST[u.status] || ['var(--text3)', _escAudit(u.status)];
    const plat = u.mode === 'aware' ? 'iTAK' : (u.mode ? 'ATAK' : '');
    const when = u.issued_at ? _escAudit(fmtLocalDT(u.issued_at)) : '';
    let action;
    if (u.status === 'infra') action = '<span title="ICS 自身/管理身分，鎖死保護（動了 ICS 連不上 TAK）" style="color:var(--text3);font-size:10px;">🔒 保護</span>';
    else if (u.ics_cert_id != null) action = '<button class="adm-btn" data-action="adm-revoke-tak-device" data-cert-id="' + u.ics_cert_id + '">撤銷</button>';
    else action = '<button class="adm-btn" data-action="adm-deregister-tak-user" data-callsign="' + _escAudit(u.callsign) + '">從 TAK 移除</button>';
    // #404：真破口（in_anon 且非豁免）→ 紅字 + 一鍵移出；REST-only infra（anon_exempt）→ 灰字良性、不給鈕
    // （它只有 __ANON__、移掉會 bounce 回，且不 stream 不洩漏）。strip 只移 __ANON__、保留其餘群，對 producer 安全。
    const isAnonGap = u.in_anon && !u.anon_exempt;
    const anonWarn = isAnonGap
      ? '<span title="在 __ANON__ 匿名群——與任何 CA 信任的證同頻，不明證可注入/竊聽。應移出。" style="color:var(--red);font-size:10px;">⚠ __ANON__</span>'
      : (u.anon_exempt ? '<span title="REST-only（只打 Marti API、不訂閱 :8089 串流）→ __ANON__ 不洩漏串流資料，良性；移除唯一群會 bounce 回。" style="color:var(--text3);font-size:10px;">__ANON__·REST-only（無害）</span>' : '');
    const anonBtn = isAnonGap ? '<button class="adm-btn" data-action="adm-strip-anon-tak-user" data-callsign="' + _escAudit(u.callsign) + '">移出匿名群</button>' : '';
    html += '<div style="display:flex;align-items:center;gap:6px;padding:4px 0;border-bottom:1px solid var(--border,#222);font-size:12px;flex-wrap:wrap;">' +
      '<span style="font-family:monospace;flex:1;min-width:80px;">' + _escAudit(u.callsign) + '</span>' +
      (plat ? '<span style="color:var(--text3);">' + plat + '</span>' : '') +
      '<span title="' + _escAudit('SHA-256：' + u.fingerprint) + '" style="font-family:monospace;color:var(--text3);font-size:9px;">' + _escAudit(_fpShort(u.fingerprint)) + '</span>' +
      '<span style="color:' + m[0] + ';font-size:10px;">' + m[1] + '</span>' +
      anonWarn +
      (when ? '<span style="color:var(--text3);font-size:10px;">' + when + '</span>' : '') +
      action + anonBtn +
      '</div>';
  }
  if (data.ics_unsynced.length) {
    html += '<div style="font-size:11px;color:var(--red);margin:8px 0 4px;">ICS 發了、TAK 沒有（未同步，發了連不上）：</div>';
    for (const c of data.ics_unsynced) {
      html += '<div style="display:flex;align-items:center;gap:6px;padding:3px 0;border-bottom:1px solid var(--border,#222);font-size:12px;">' +
        '<span style="font-family:monospace;flex:1;">' + _escAudit(c.callsign) + '</span>' +
        (c.non_ascii ? '<span title="中文 callsign 無法註冊 TAK managed user" style="color:var(--red);font-size:10px;">中文·連不上</span>' : '<span style="color:var(--red);font-size:10px;">未同步</span>') +
        '<button class="adm-btn" data-action="adm-revoke-tak-device" data-cert-id="' + c.cert_id + '">撤銷</button>' +
        '</div>';
    }
  }
  // #404：在線匿名連線（CA 信任但不在名冊）——reconcile 只看名冊看不到，這裡補在線視圖。最該盯的對象
  // （被刪帳號/未授權仍掛著）；隔離後它看不到/送不出 ICS 資料，但仍佔連線——真踢除＝撤銷（#318）。
  const onlineAnon = data.online_anon || [];
  if (onlineAnon.length) {
    html += '<div style="font-size:11px;color:var(--red);margin:8px 0 4px;">🔴 在線匿名連線（CA 信任、不在名冊——已踢除/未授權仍連著；已隔離看不到 ICS 資料，真踢除須撤銷 #318）：</div>';
    for (const o of onlineAnon) {
      const name = o.username || '(無 callsign)';
      html += '<div style="display:flex;align-items:center;gap:6px;padding:3px 0;border-bottom:1px solid var(--border,#222);font-size:12px;flex-wrap:wrap;">' +
        '<span style="font-family:monospace;flex:1;min-width:80px;">' + _escAudit(name) + '</span>' +
        '<span title="' + _escAudit('CoT uid：' + (o.client_uid || '')) + '" style="font-family:monospace;color:var(--text3);font-size:9px;">' + _escAudit((o.client_uid || '').slice(0, 8)) + '</span>' +
        '<span style="color:var(--red);font-size:10px;">⚠ 匿名在線</span>' +
        '</div>';
    }
  }
  // #318 Slice 3：ICS 已撤但無 fingerprint → TAK 撤不掉（此 TAK-driven 視圖不含 ICS 盤點，補誠實標示，免誤以為全已 enforce）。
  if (nullFpRevoked && nullFpRevoked.length) {
    html += '<div style="font-size:11px;color:var(--red);margin:8px 0 4px;border:1px solid var(--red);border-radius:4px;padding:4px 6px;">' +
      '⚠ ' + nullFpRevoked.length + ' 張 ICS 已撤但 <b>TAK 撤不掉</b>（無 fingerprint，#398 前發 → 需重發證 / 等過期）：' +
      nullFpRevoked.map(c => _escAudit(c.callsign)).join('、') + '</div>';
  }
  box.innerHTML = html;
}

/** ICS-only fallback（reconcile 未配置時）：列 dashboard 發過的證 + Slice 1 同步徽章。 */
function _renderIcsOnlyList(box, certs) {
  _lastTakCallsigns = new Set(certs.filter(c => c.status === 'active').map(c => c.callsign));
  if (!certs.length) { box.innerHTML = '<div style="color:var(--text3);font-size:12px;padding:4px 0;">尚未發過裝置證</div>'; return; }
  const currentByCallsign = {};
  for (const c of certs) {
    if (c.status === 'active' && c.enroll_status === 'ok' && currentByCallsign[c.callsign] == null) currentByCallsign[c.callsign] = c.id;
  }
  let rows = '';
  for (const c of certs) {
    const active = c.status === 'active';
    const plat = c.mode === 'aware' ? 'iTAK' : 'ATAK';
    const knownStatus = c.enroll_status != null && c.enroll_status !== '';
    const enrolled = c.enroll_status === 'ok';
    const superseded = active && enrolled && currentByCallsign[c.callsign] != null && currentByCallsign[c.callsign] !== c.id;
    let sync = '';
    if (active) {
      if (!knownStatus) sync = '<span title="升級前發的證，同步狀態未知" style="color:var(--text3);font-size:10px;">? 狀態未知</span>';
      else if (!enrolled) sync = '<span title="' + _escAudit('TAK enroll 結果：' + c.enroll_status) + '" style="color:var(--red);font-size:10px;">⚠ 未同步 TAK</span>';
      else if (superseded) sync = '<span title="同 callsign 有更新的證" style="color:var(--text3);font-size:10px;">↩ 已被新證取代</span>';
      else sync = '<span title="已註冊為 TAK managed user" style="color:var(--green);font-size:10px;">✓ 同步 TAK</span>';
    }
    const fp = c.fingerprint ? '<span title="' + _escAudit('SHA-256：' + c.fingerprint) + '" style="font-family:monospace;color:var(--text3);font-size:9px;">' + _escAudit(_fpShort(c.fingerprint)) + '</span>' : '';
    // #318 Slice 3：已撤但無 fingerprint → ICS 無 hash 可寫 TAK 撤銷名單 = TAK 端撤不掉（誠實標示，非帳面）。
    const tukWarn = (!active && !c.fingerprint)
      ? '<span title="無 fingerprint 紀錄（#398 前發）→ ICS 無 hash 可寫 TAK 撤銷名單；TAK 端撤不掉，需重發證 / 等憑證過期" style="color:var(--red);font-size:10px;">⚠ TAK 撤不掉</span>'
      : '';
    rows += '<div style="display:flex;align-items:center;gap:6px;padding:4px 0;border-bottom:1px solid var(--border,#222);font-size:12px;flex-wrap:wrap;">' +
        '<span style="font-family:monospace;flex:1;min-width:80px;' + (active ? '' : 'text-decoration:line-through;color:var(--text3);') + '">' + _escAudit(c.callsign) + '</span>' +
        '<span style="color:var(--text3);">' + plat + '</span>' + sync + fp + tukWarn +
        '<span style="color:var(--text3);font-size:10px;">' + _escAudit(c.issued_at ? fmtLocalDT(c.issued_at) : '') + '</span>' +
        '<span class="adm-badge ' + (active ? 'active' : 'suspended') + '">' + (active ? '有效' : '已撤銷') + '</span>' +
        (active
          ? '<button class="adm-btn" data-action="adm-revoke-tak-device" data-cert-id="' + c.id + '">撤銷</button>'
          : '<button class="adm-btn" data-action="adm-delete-tak-device" data-cert-id="' + c.id + '">刪除</button>') +
      '</div>';
  }
  box.innerHTML = rows;
}

/** #401：從 TAK 直接移除一個 managed user（殭屍帳號，無 ICS 證列可撤）。 */
export async function admDeregisterTakUser(callsign) {
  if (!confirm('從 TAK Server 移除帳號「' + callsign + '」？\n⚠ 該 callsign 將無法連 TAK。此為 TAK 端真移除（usermod -D）。')) return;
  const resp = await authFetch(API_BASE + '/api/admin/tak/users/' + encodeURIComponent(callsign) + '/deregister', { method: 'POST' });
  if (!resp.ok) { alert('移除失敗（' + resp.status + '）：' + (await resp.text()).slice(0, 200)); return; }
  admLoadTakDeviceCerts();
}

/** #404：把 TAK managed user 移出 __ANON__ 匿名群（修「與任何 CA 證同頻」隔離破口；保留其餘群）。 */
export async function admStripAnonTakUser(callsign) {
  if (!confirm('把「' + callsign + '」移出 __ANON__ 匿名群？\n保留其餘群（紅/藍/中立）。修正「與任何 CA 信任的證同頻、不明證可注入/竊聽」隔離破口。')) return;
  const resp = await authFetch(API_BASE + '/api/admin/tak/users/' + encodeURIComponent(callsign) + '/strip-anon', { method: 'POST' });
  if (!resp.ok) { alert('移出失敗（' + resp.status + '）：' + (await resp.text()).slice(0, 200)); return; }
  admLoadTakDeviceCerts();
}

export async function admRevokeTakDevice(certId) {
  // #318 層2 真撤銷：寫 TAK certificate 表（x509checkRevocation）→ 該證失去身分、降為隔離匿名（__ANON__，
  // 經 #404 隔離後看不到/送不出 ICS 資料），**非硬斷線**。reality check 實證：對「之後才連線」的證即時生效；
  // 對「在線、近期已認證」的證因 TAK 快取，需快取過期或重啟 TAK 才即時踢除。同時 #398 A 從 TAK 移除 managed user。
  if (!confirm('撤銷此 TAK 裝置證？\n\n⚠ 撤銷 = 寫進 TAK 撤銷名單 + 移除 managed user。該證會失去身分、降為隔離匿名（看不到／送不出 ICS 資料）。\n· 之後才連線的證：即時生效。\n· 目前在線、近期已認證的證：因 TAK 快取，需快取過期或重啟 TAK 才即時踢除。\n（被新證取代的舊列只標撤銷、不動 TAK。）')) return;
  const resp = await authFetch(API_BASE + '/api/admin/tak/device-certs/' + certId + '/revoke', { method: 'POST' });
  if (!resp.ok) { alert('撤銷失敗（' + resp.status + '）'); return; }
  const body = await resp.json();
  const d = body.deregister, t = body.tak_revoke;
  // 撤銷名單寫入結果（#318）：只在「非乾淨成功」時提示，避免每次都跳框。
  let warn = '';
  if (t === 'tak-db-not-configured') warn += '\n⚠ TAK DB 未配置 → 未寫撤銷名單（僅 ICS 帳面，未真 enforce）。';
  else if (t === 'no-fingerprint') warn += '\n⚠ 此證無 fingerprint（升級前）→ 無法寫撤銷名單（需重發證）。';
  else if (t && t.indexOf('tak-db-error') === 0) warn += '\n⚠ 寫 TAK 撤銷名單失敗：' + t + '（ICS 帳面已撤，請查 TAK DB）。';
  // 從 TAK 移除 managed user 的失敗（registrar 未配置/逾時）。
  const dOk = ['deregistered', 'skipped-superseded', 'skipped-infra', 'skipped-ambiguous'].indexOf(d) >= 0;
  if (d && !dOk) warn += '\n· 從 TAK 移除 managed user 未成功：' + d + '（registrar 未配置或逾時）。';
  if (warn) alert('已撤銷（ICS 帳面）。' + warn);
  admLoadTakDeviceCerts();
}

// #325：刪除已撤銷的盤點紀錄（清理累積 revoked；刪紀錄 ≠ 撤證）。
export async function admDeleteTakDevice(certId) {
  if (!confirm('刪除此已撤銷的裝置證盤點紀錄？\n（僅刪 ICS 盤點紀錄，不影響憑證本身——真撤銷 = 寫 TAK 撤銷名單，見「撤銷」鈕，#318。）')) return;
  const resp = await authFetch(API_BASE + '/api/admin/tak/device-certs/' + certId, { method: 'DELETE' });
  if (!resp.ok) { alert('刪除失敗（' + resp.status + '）'); return; }
  admLoadTakDeviceCerts();
}

// #318 Slice 3：把所有 ICS 已撤+有 fingerprint 的證一次補寫進 TAK 撤銷名單（補 #318 前撤的證沒寫 TAK 的洞）。
export async function admBackfillTakRevocations() {
  if (!confirm('把所有「ICS 已撤、有 fingerprint」的證一次補寫進 TAK 撤銷名單？\n\n補洞:#318 上線前撤的證當時只設 ICS 帳面、沒寫 TAK,故 TAK 端從不擋。\n· 無 fingerprint 的舊證補不了(需重發)。\n· ⚠ 對已在線/快取的證,撤銷實際生效仍需重啟 TAK。')) return;
  const resp = await authFetch(API_BASE + '/api/admin/tak/revocations/backfill', { method: 'POST' });
  if (!resp.ok) { alert('backfill 失敗（' + resp.status + '）'); return; }
  const b = await resp.json();
  if (!b.ok && b.reason === 'tak-db-not-configured') { alert('TAK DB 未配置 → 無法 backfill（撤銷僅 ICS 帳面，未真 enforce）。'); return; }
  let msg = '已推進 ' + b.pushed + ' / ' + b.total_with_fingerprint + ' 張（有 fingerprint）到 TAK 撤銷名單。';
  if (b.skipped_no_fingerprint) msg += '\n⚠ ' + b.skipped_no_fingerprint + ' 張無 fingerprint → TAK 撤不掉（需重發）。';
  if (b.errors && b.errors.length) msg += '\n⚠ ' + b.errors.length + ' 張寫入失敗（查 TAK DB）。';
  msg += '\n（在線/快取證需重啟 TAK 才即時生效。）';
  alert(msg);
  admLoadTakDeviceCerts();
}

// #318 Slice 3 part③：按 SHA-256 fingerprint 直接撤盤點外/非 dashboard 發的證。
export async function admRevokeByFingerprint() {
  const inp = el('adm-tak-revoke-fp');
  const fp = (inp?.value || '').trim().toUpperCase();
  if (!fp) { alert('請貼上 SHA-256 fingerprint（冒號分隔大寫）。'); return; }
  if (!/^([0-9A-F]{2}:){31}[0-9A-F]{2}$/.test(fp)) { alert('格式須為 SHA-256 冒號分隔大寫（32 段，如 AB:CD:…:EF）。'); return; }
  if (!confirm('撤銷此 fingerprint 的證？\n' + fp + '\n\n會寫進 TAK 撤銷名單。⚠ 對在線/快取證需重啟 TAK 才即時生效。')) return;
  const resp = await authFetch(API_BASE + '/api/admin/tak/revocations/by-fingerprint', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ fingerprint: fp }),
  });
  if (resp.status === 403) { alert('禁撤基礎設施證（ics-cot/ics-tak-admin）—— 會毀 ICS 對 TAK 的控制面。'); return; }
  if (resp.status === 422) { alert('fingerprint 格式錯誤（須 SHA-256 冒號分隔大寫 32 段）。'); return; }
  if (!resp.ok) { alert('撤銷失敗（' + resp.status + '）。'); return; }
  const b = await resp.json();
  if (b.ok) { alert('已寫入 TAK 撤銷名單。\n（在線/快取證需重啟 TAK 才即時生效。）'); if (inp) inp.value = ''; }
  else if (b.reason === 'tak-db-not-configured') alert('TAK DB 未配置 → 無法撤銷（撤銷僅 ICS 帳面，未真 enforce）。');
  else alert('撤銷未成功：' + (b.reason || '?'));
  admLoadTakDeviceCerts();
}

// #315：發 TAK 裝置 data package（不自動下載；先給結果框 + 下載/分享）。
export async function admIssueTakDevice() {
  const callsign = el('adm-tak-callsign')?.value.trim();
  const mode = el('adm-tak-mode')?.value || 'atak';
  if (!callsign) { alert('請輸入 callsign'); return; }
  // callsign = TAK managed-user 帳號(cert CN)。TAK new-user API 實測規則：**至少 4 字、僅限
  // 字母/數字/. _ -**(不可空格、@、中文，不可 - 開頭)。原本只擋非 ASCII → BB/GGW 這種短英數溜過去
  // 被 TAK 400→502。改成照 TAK 規則發前硬擋,訊息講清楚 + 中文名請設在 App 顯示 callsign。
  if (!/^[A-Za-z0-9._-]{4,}$/.test(callsign) || callsign.startsWith('-')) {
    alert('「' + callsign + '」不符 TAK 帳號規則。\n\n憑證帳號(= TAK username)需:\n· 至少 4 個字\n· 僅限 英數 與 . _ -\n· 不可有空格、@、中文,不可 - 開頭\n\n想要中文名:憑證帳號用英數(如 wensheng),中文「文生」到 ATAK/iTAK App 內設「顯示 callsign」——地圖一樣顯中文。');
    return;
  }
  // #398 C / #401：callsign 已在 TAK（或 ICS 未同步列）→ 重發會覆寫 TAK fingerprint、作廢舊證。發前警告。
  if (_lastTakCallsigns.has(callsign)) {
    if (!confirm('callsign「' + callsign + '」已存在。\n⚠ TAK 一個 callsign 只認一張證——重發會作廢舊證，用舊包的裝置會連不上、須重匯新包。\n繼續重發？')) return;
  }
  const resp = await authFetch(API_BASE + '/api/admin/tak/device-cert?callsign='
    + encodeURIComponent(callsign) + '&mode=' + mode, { method: 'POST' });
  if (resp.status === 503) { alert('TAK 裝置發證未配置（step-ca daemon 未接，或對外 TAK 位址未設 TAK_DEVICE_CONNECT_HOST）。'); return; }
  if (resp.status === 422) { alert('callsign 不合法或平台錯誤'); return; }
  if (!resp.ok) { alert('發證失敗（' + resp.status + '）：' + (await resp.text()).slice(0, 200)); return; }
  const enrollStatus = resp.headers.get('X-TAK-Enroll-Status') || 'unknown';  // #398 D：surface 同步結果
  const wgStatus = resp.headers.get('X-WG-Status') || 'skipped';  // #434：WG 配置結果（ok/skipped/失敗 reason）
  const blob = await resp.blob();
  const blobUrl = URL.createObjectURL(blob);
  _showTakDeviceResult(callsign, mode, blob, blobUrl, enrollStatus, wgStatus);
  admLoadTakDeviceCerts();  // #317：發完刷新盤點列表
}

function _showTakDeviceResult(callsign, mode, blob, blobUrl, enrollStatus, wgStatus) {
  const box = el('adm-tak-device-result');
  if (!box) { URL.revokeObjectURL(blobUrl); return; }
  box.innerHTML = '';
  const synced = enrollStatus === 'ok' || enrollStatus == null;
  const banner = document.createElement('div');
  // #398 D：未同步 TAK → 邊框轉警示色，明確告知「裝置連不上」而非靜默成功。
  banner.style.cssText = 'border:1px solid ' + (synced ? 'var(--green,#2ea043)' : 'var(--red,#f85149)') + ';border-radius:6px;padding:8px;margin-top:8px;font-size:12px;';
  const label = document.createElement('div');
  label.style.cssText = 'color:var(--text2);margin-bottom:6px;line-height:1.5;';
  const plat = mode === 'aware' ? 'iTAK / TAK Aware（iOS）' : 'ATAK（Android）';
  label.textContent = '✅ 已簽發 ' + callsign + ' 的 ' + plat + ' data package（密碼內嵌、免打）。'
    + '把 .zip 弄到裝置 → TAK app 匯入 data package → 自動帶憑證連上 TAK Server。';
  if (!synced) {
    const warn = document.createElement('div');
    warn.style.cssText = 'color:var(--red,#f85149);margin-bottom:6px;line-height:1.5;font-weight:600;';
    warn.textContent = '⚠ 未同步 TAK（' + enrollStatus + '）：證已簽發，但沒註冊成 TAK managed user → 裝置匯入後會連不上。'
      + '請確認 registrar 運作後重發，或改用英數 callsign。';
    banner.appendChild(warn);
  }
  // #434：WG 配置結果——bundle 是否含 WireGuard 設定（一站式包 = TAK 證 + WG conf + QR）。
  if (wgStatus === 'ok') {
    const wg = document.createElement('div');
    wg.style.cssText = 'color:var(--green,#2ea043);margin-bottom:6px;line-height:1.5;';
    wg.textContent = '🔐 已附 WireGuard VPN：解壓後掃 wireguard-qr.png（或匯 wireguard.conf）進 WireGuard app → 啟用 → 再開 TAK。';
    banner.appendChild(wg);
  } else if (wgStatus && wgStatus !== 'skipped') {
    const wg = document.createElement('div');
    wg.style.cssText = 'color:var(--yellow,#d29922);margin-bottom:6px;line-height:1.5;';
    wg.textContent = '⚠ WireGuard 未配（' + wgStatus + '）：本包僅含 TAK 證，VPN 需另配。';
    banner.appendChild(wg);
  }
  const row = document.createElement('div');
  row.style.cssText = 'display:flex;align-items:center;gap:6px;flex-wrap:wrap;';
  const dlBtn = document.createElement('button');
  dlBtn.className = 'adm-btn';
  dlBtn.textContent = '⬇ 下載 .zip';
  dlBtn.addEventListener('click', () => {
    const a = document.createElement('a');
    a.href = blobUrl; a.download = callsign + '-dp.zip';
    document.body.appendChild(a); a.click(); a.remove();
  });
  row.appendChild(dlBtn);
  const file = _makeFile(callsign + '-dp.zip', blob, 'application/zip');
  if (file && navigator.canShare?.({ files: [file] })) {
    const shareBtn = document.createElement('button');
    shareBtn.className = 'adm-btn';
    shareBtn.textContent = '📤 分享 / 存檔（轉交裝置）';
    shareBtn.addEventListener('click', async () => {
      try { await navigator.share({ files: [file], title: callsign + '-dp.zip' }); }
      catch (e) {
        if (e?.name === 'AbortError') return;  // 使用者取消
        dlBtn.click();  // #330：桌機 Chrome canShare 回 true 但 share 檔案丟 NotAllowedError → 退回下載
      }
    });
    row.appendChild(shareBtn);
  }
  banner.appendChild(label);
  banner.appendChild(row);
  box.appendChild(banner);
}

// VPN-gate 儀表板：給帳號（單獨連 ICS 的人）發其 WireGuard VPN 設定（conf + QR），label = username。
// 接在帳號管理的裝置憑證面板（與 mTLS 登入證同處、同身分）；TAK 裝置使用者的 VPN 隨發 TAK 證自動配。
export async function admIssueVpn(username) {
  if (!username) return;
  const resp = await authFetch(API_BASE + '/api/admin/wg/issue?label=' + encodeURIComponent(username), { method: 'POST' });
  if (resp.status === 503) { alert('WG 未配置（部署層設 WG_QUEUE_DIR / WG_SERVER_PUBKEY / WG_ENDPOINT）。'); return; }
  if (resp.status === 422) { alert('帳號名不合法或為保留身分'); return; }
  if (!resp.ok) { alert('WG 配置失敗（' + resp.status + '）：' + (await resp.text()).slice(0, 200)); return; }
  const blob = await resp.blob();
  const blobUrl = URL.createObjectURL(blob);
  _showWgResult(username, blob, blobUrl);
}

function _showWgResult(label, blob, blobUrl) {
  const box = el('adm-vpn-result-' + label);
  if (!box) { URL.revokeObjectURL(blobUrl); return; }
  box.innerHTML = '';
  const banner = document.createElement('div');
  banner.style.cssText = 'border:1px solid var(--green,#2ea043);border-radius:6px;padding:8px;margin-top:8px;font-size:12px;';
  const lab = document.createElement('div');
  lab.style.cssText = 'color:var(--text2);margin-bottom:6px;line-height:1.5;';
  lab.textContent = '🔐 已配 ' + label + ' 的 WireGuard 設定。轉交給對方：掃 wireguard-qr.png（或匯 wireguard.conf）→ 啟用隧道 → 照原網址開儀表板登入。';
  const row = document.createElement('div');
  row.style.cssText = 'display:flex;align-items:center;gap:6px;flex-wrap:wrap;';
  const dlBtn = document.createElement('button');
  dlBtn.className = 'adm-btn';
  dlBtn.textContent = '⬇ 下載 .zip';
  dlBtn.addEventListener('click', () => {
    const a = document.createElement('a');
    a.href = blobUrl; a.download = label + '-wg.zip';
    document.body.appendChild(a); a.click(); a.remove();
  });
  row.appendChild(dlBtn);
  const file = _makeFile(label + '-wg.zip', blob, 'application/zip');
  if (file && navigator.canShare?.({ files: [file] })) {
    const shareBtn = document.createElement('button');
    shareBtn.className = 'adm-btn';
    shareBtn.textContent = '📤 分享 / 存檔';
    shareBtn.addEventListener('click', async () => {
      try { await navigator.share({ files: [file], title: label + '-wg.zip' }); }
      catch (e) { if (e?.name === 'AbortError') return; dlBtn.click(); }
    });
    row.appendChild(shareBtn);
  }
  banner.appendChild(lab);
  banner.appendChild(row);
  box.appendChild(banner);
}

// 撤 WG-only peer（按 callsign/label）。TAK 裝置 peer 走撤證連動，不經此。
export async function admRevokeWgPeer(callsign) {
  if (!callsign) return;
  if (!confirm('撤除 WG 設定「' + callsign + '」？\n該裝置會立即失去 VPN 連線（容器 peer + 帳本一併撤）。')) return;
  const resp = await authFetch(API_BASE + '/api/admin/wg/peers/revoke?callsign=' + encodeURIComponent(callsign), { method: 'POST' });
  if (resp.status === 404) { alert('無 active WG peer：' + callsign); return; }
  if (resp.status === 422) { alert('callsign 不合法或為保留身分'); return; }
  if (!resp.ok) { alert('撤除失敗（' + resp.status + '）'); return; }
  admLoadTakDeviceCerts();  // 刷新帳本
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

// #384：admChangeAdminPin（更改 Admin PIN）已移除——死功能。

function _admRoleOptions(selectedRole = '', sysadminOnly = false) {
  const roles = sysadminOnly
    ? ['系統管理員', '指揮官', '操作員', '觀察員']
    : ['操作員', '觀察員'];
  return roles.map(role =>
    '<option value="' + role + '"' + (selectedRole === role ? ' selected' : '') + '>' + role + '</option>'
  ).join('');
}

export async function admLoadAccounts() {
  const resp = await authFetch(API_BASE + '/api/admin/accounts', {});
  if (!resp.ok) {
    const msg = _isCommanderSession()
      ? '無法載入下屬帳號，請重新登入後再試。'
      : '無法載入帳號列表。';
    el('adm-panel-list').innerHTML =
      '<div style="color:var(--red);font-size:12px;padding:16px;">' + msg + '</div>';
    return;
  }
  const accounts = await resp.json();
  // #354：系統內唯一的 active sysadmin → UI 禁用其降權控制（角色 / 停用），與後端守門對齊。
  // 僅 count===1 時鎖；多 sysadmin 時不鎖（後端 409 仍兜底，含 UI 繞不過的並發窗口 #369）。
  const _activeSysadmins = accounts.filter(x => x.status === 'active' && _isAccountSysadmin(x));
  const _lastSysadminUser = _activeSysadmins.length === 1 ? _activeSysadmins[0].username : null;
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
    // #354：最後一個 active sysadmin → 鎖角色 select + 停用鈕（防自鎖）。
    const _isLastSysadmin = a.username === _lastSysadminUser;
    const _lockAttr = _isLastSysadmin ? ' disabled title="系統內最後一個系統管理員，不可降級／停用（防自鎖）"' : '';
    // #293 DOM-XSS 自衛：username / display_name / role / status 後端皆為無 charset 限制的 str
    //（schemas/admin.py AccountCreateIn），塞進 innerHTML 前一律 escape。username 同時進 id/
    // data-* 屬性，escape 後瀏覽器 entity-decode 與後續 el('adm-edit-'+username) 查找仍一致。
    const _u = _escAudit(a.username);
    const _dn = _escAudit(a.display_name || '');
    const _role = _escAudit(a.role);
    const _st = _escAudit(a.status);
    html += '<div class="adm-account-card" id="adm-card-' + _u + '">' +
      '<div class="adm-account-row">' +
        '<div><span class="adm-account-name">' + _u + '</span>' +
          (a.display_name ? ' <span style="color:var(--text3);font-size:11px;">' + _dn + '</span>' : '') +
        '</div>' +
        '<div style="display:flex;gap:4px;">' +
          '<span class="adm-badge role">' + _role + '</span>' +
          '<span class="adm-badge ' + statusCls + '">' + statusLabel + '</span>' +
        '</div>' +
      '</div>' +
      '<div class="adm-btns">' +
        '<button class="adm-btn" data-action="adm-toggle-edit" data-username="' + _u + '">編輯</button>' +
        '<button class="adm-btn" data-action="adm-toggle-status" data-username="' + _u + '" data-status="' + _st + '"' + _lockAttr + '>' + (a.status === 'active' ? '停用' : '啟用') + '</button>' +
        // #275 wave B：裝置憑證（mTLS 第二因子）管理，sysadmin only
        (_isSysadminSession() ? '<button class="adm-btn" data-action="adm-toggle-certs" data-username="' + _u + '">🔑 裝置憑證</button>' : '') +
      '</div>' +
      '<div class="adm-certs-panel" id="adm-certs-' + _u + '" style="display:none;margin-top:8px;"></div>' +
      '<div class="adm-edit-form" id="adm-edit-' + _u + '" style="display:none;">' +
        // #348-F5 P2b：移除手動改 PIN 欄 → 「重設為臨時 PIN」按鈕（系統產隨機值、首登強制改、一次性顯示）。
        '<label>PIN</label>' +
        '<button class="adm-btn" data-action="adm-reset-pin" data-username="' + _u + '">🔑 重設為臨時 PIN</button>' +
        '<label>角色</label>' +
        '<select id="adm-role-' + _u + '"' + _lockAttr + '>' + _admRoleOptions(a.role, _isSysadminSession()) + '</select>' +
        (_isLastSysadmin ? '<div style="color:var(--text3);font-size:11px;margin-top:2px;">最後一個系統管理員，角色已鎖定（防自鎖）</div>' : '') +
        '<label>顯示名稱</label>' +
        '<input id="adm-dname-' + _u + '" value="' + _dn + '">' +
        '<div style="display:flex;gap:6px;">' +
          '<button class="adm-btn" data-action="adm-save-edit" data-username="' + _u + '">儲存</button>' +
          '<button class="adm-btn" data-action="adm-toggle-edit" data-username="' + _u + '">取消</button>' +
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
      '<select id="adm-certfmt-' + username + '" title="iOS 選描述檔（免打密碼）；Windows/iMac/Android 選安裝包">' +
        '<option value="zip">安裝包 .zip（Windows / iMac / Android，含 root CA + 說明）</option>' +
        '<option value="mobileconfig">iOS 描述檔（免打密碼）</option>' +
        '<option value="p12">裸 .p12（進階，無 root CA / 說明）</option>' +
      '</select>' +
      '<button class="adm-btn" data-action="adm-issue-cert" data-username="' + username + '" title="線上向 step-ca 簽發並自動綁定">發憑證</button>' +
      '<button class="adm-btn" data-action="adm-bind-cert" data-username="' + username + '" title="已有離線簽好的憑證時，只綁定 CN">僅綁定</button>' +
      '<button class="adm-btn" data-action="adm-download-rootca" title="桌機信任 ICS server 憑證用（Windows/iMac 共用同一張 root CA）">下載 root CA</button>' +
      '<button class="adm-btn" data-action="adm-issue-vpn" data-username="' + username + '" title="發此帳號的 WireGuard VPN 設定——VPN-gate 下，單獨連 ICS 的人需掛 VPN 才連得到儀表板">📶 發 VPN</button>' +
    '</div>' +
    '<div id="adm-vpn-result-' + username + '"></div>' +
    '<div style="font-size:11px;color:var(--text3);margin-top:6px;line-height:1.5;"><b>發憑證</b>：線上向 step-ca 簽一張 + 自動綁定。<br>· <b>桌機（Windows / iMac / Android）</b>選「<b>安裝包 .zip</b>」——一個檔內含 憑證 + root CA + <b>分平台安裝 README</b>，照 README 裝即可（取代原本一堆步驟）。匯入密碼發證後顯示在此（安全考量不放包內）。<br>· <b>iPhone / iPad</b>選「<b>iOS 描述檔</b>」（.mobileconfig，密碼內嵌→點開直接裝、免手打）。<br>· <b>僅綁定</b>：已離線簽好時只綁 CN ↔ 帳號。一帳號可綁多台，撤銷即時失效。憑證須綁到本帳號才登得進（cert-bound session）；Mac/Chrome 換證後須完全重開 Chrome。（「下載 root CA」單獨鈕保留作備援，安裝包內已附。）</div>';
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

// #327：下載 step-ca root CA（桌機信任 ICS server 憑證用；Windows/iMac 共用）。
export async function admDownloadRootCa() {
  const resp = await authFetch(API_BASE + '/api/admin/ca/root');
  if (resp.status === 503) { alert('root CA 取得失敗：step-ca 線上發證未配置。'); return; }
  if (!resp.ok) { alert('下載 root CA 失敗（' + resp.status + '）'); return; }
  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = 'ics-root-ca.pem';
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}

export async function admIssueCert(username) {
  const cn = el('adm-certcn-' + username)?.value.trim();
  const label = el('adm-certlabel-' + username)?.value.trim();
  const fmt = el('adm-certfmt-' + username)?.value || 'zip';
  if (!cn) { alert('請輸入裝置憑證 CN'); return; }
  const resp = await authFetch(API_BASE + '/api/admin/accounts/' + username + '/certs/issue?fmt=' + fmt, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cert_cn: cn, label: label || null }),
  });
  if (resp.status === 503) { alert('線上發證未配置（step-ca daemon 未接）。請改用 deploy/step-ca 離線簽好後按「僅綁定」。'); return; }
  if (resp.status === 409) { alert('此 CN 已被有效綁定（撤銷後才可重發）'); return; }
  if (!resp.ok) { alert('發證失敗（' + resp.status + '）：' + (await resp.text()).slice(0, 200)); return; }
  // #307：p12 匯入密碼每張隨機，由後端 X-P12-Password header 帶回（同源可讀；mobileconfig 無此 header，密碼已內嵌）。
  const p12pass = resp.headers.get('X-P12-Password') || '';
  const blob = await resp.blob();
  const blobUrl = URL.createObjectURL(blob);
  await admLoadCerts(username);
  if (fmt === 'mobileconfig') {
    _showMobileconfigResult(username, cn, blob, blobUrl);  // #312：密碼內嵌、不顯示
  } else {
    // #307 缺口1：不自動下載。iOS 一拿到 .p12 即攔成安裝、蓋掉畫面 → 先顯示密碼、手動觸發。
    // fmt='zip'（桌機安裝包，預設）與 'p12'（裸證，進階）共用結果框，只差下載檔名/型別。
    _showP12Result(username, cn, p12pass, blob, blobUrl, fmt);
  }
}

// #312：iOS 描述檔發證結果——密碼已內嵌（不顯示），給下載/分享鈕（textContent 防 XSS）。
function _showMobileconfigResult(username, cn, blob, blobUrl) {
  const box = el('adm-certs-' + username);
  if (!box) { URL.revokeObjectURL(blobUrl); return; }
  const banner = document.createElement('div');
  banner.style.cssText = 'border:1px solid var(--green,#2ea043);border-radius:6px;padding:8px;margin-bottom:8px;font-size:12px;';
  const label = document.createElement('div');
  label.style.cssText = 'color:var(--text2);margin-bottom:6px;line-height:1.5;';
  label.textContent = '✅ 已產生 iOS 描述檔 ' + cn + '.mobileconfig 並自動綁定（憑證密碼已內嵌、安裝免打）。'
    + '在目標 iOS 裝置開啟 → 設定 →「已下載描述檔」→ 安裝（含信任根 + 裝置身分）。'
    + '⚠ 安裝時 iOS 會要求「解鎖此裝置的密碼」＝該 iPhone/iPad 的螢幕鎖密碼，不是憑證密碼；'
    + '「未簽署」屬正常（自建描述檔未做數位簽章）。裝好後連網站登入仍需 PIN。';
  const row = document.createElement('div');
  row.style.cssText = 'display:flex;align-items:center;gap:6px;flex-wrap:wrap;';
  const dlBtn = document.createElement('button');
  dlBtn.className = 'adm-btn';
  dlBtn.textContent = '⬇ 下載描述檔';
  dlBtn.addEventListener('click', () => {
    const a = document.createElement('a');
    a.href = blobUrl; a.download = cn + '.mobileconfig';
    document.body.appendChild(a); a.click(); a.remove();
  });
  row.appendChild(dlBtn);
  const mcFile = _makeFile(cn + '.mobileconfig', blob, 'application/x-apple-aspen-config');
  if (mcFile && navigator.canShare?.({ files: [mcFile] })) {
    const shareBtn = document.createElement('button');
    shareBtn.className = 'adm-btn';
    shareBtn.textContent = '📤 分享 / 存檔（轉交別台）';
    shareBtn.addEventListener('click', async () => {
      try { await navigator.share({ files: [mcFile], title: cn + '.mobileconfig' }); }
      catch (e) {
        if (e?.name === 'AbortError') return;  // 使用者取消
        dlBtn.click();  // #330：桌機 Chrome share 檔案丟 NotAllowedError → 退回下載
      }
    });
    row.appendChild(shareBtn);
  }
  banner.appendChild(label);
  banner.appendChild(row);
  box.insertBefore(banner, box.firstChild);
}

// iPhone / iPad（含 iPadOS 13+ 偽裝成 MacIntel）偵測。
function _isIOS() {
  return /iP(hone|ad|od)/.test(navigator.userAgent) ||
    (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
}

// #307 缺口1+衍生子缺口：發證後常駐顯示密碼 + 手動下載/分享鈕（textContent 防 XSS）。
function _showP12Result(username, cn, pass, blob, blobUrl, fmt) {
  const box = el('adm-certs-' + username);
  if (!box) { URL.revokeObjectURL(blobUrl); return; }
  const isZip = fmt === 'zip';
  const fname = isZip ? cn + '-ics.zip' : cn + '.p12';
  const mime = isZip ? 'application/zip' : 'application/x-pkcs12';
  const banner = document.createElement('div');
  banner.style.cssText = 'border:1px solid var(--green,#2ea043);border-radius:6px;padding:8px;margin-bottom:8px;font-size:12px;';

  const label = document.createElement('div');
  label.style.cssText = 'color:var(--text2);margin-bottom:6px;line-height:1.5;';
  label.textContent = isZip
    ? '✅ 已簽發並自動綁定 ' + cn + '。請先複製下方匯入密碼，再下載安裝包——解開後照內附 README 分平台安裝。'
      + '本訊息關閉後密碼無法再取得：'
    : '✅ 已簽發並自動綁定 ' + cn + '。請先複製下方密碼，再點「下載 / 安裝」'
      + '（iOS 點下載即跳安裝、屆時需輸入此密碼）。本訊息關閉後密碼無法再取得：';

  const row = document.createElement('div');
  row.style.cssText = 'display:flex;align-items:center;gap:6px;flex-wrap:wrap;';
  const code = document.createElement('code');
  code.style.cssText = 'flex:1;min-width:140px;font-size:14px;user-select:all;word-break:break-all;background:var(--bg2,#161b22);padding:4px 6px;border-radius:4px;';
  code.textContent = pass || '(未取得密碼，請改用 CLI 發證)';
  const copyBtn = document.createElement('button');
  copyBtn.className = 'adm-btn';
  copyBtn.textContent = '複製密碼';
  copyBtn.addEventListener('click', () => {
    if (pass) navigator.clipboard?.writeText(pass);
    copyBtn.textContent = '已複製';
  });
  const dlBtn = document.createElement('button');
  dlBtn.className = 'adm-btn';
  dlBtn.textContent = isZip ? '⬇ 下載安裝包 .zip' : '⬇ 下載 / 安裝 .p12';
  dlBtn.addEventListener('click', () => {
    const a = document.createElement('a');
    a.href = blobUrl; a.download = fname;
    document.body.appendChild(a); a.click(); a.remove();
  });
  row.appendChild(code);
  row.appendChild(copyBtn);
  row.appendChild(dlBtn);

  // #307 缺口1：iOS 上「下載」會被攔成本機安裝、存不了檔。Web Share API 走 iOS 原生
  // 分享單 → 可「儲存到檔案 / AirDrop」轉交別台裝置。支援檔案分享時才顯示此鈕。
  const shareFile = _makeFile(fname, blob, mime);
  const canShare = shareFile && navigator.canShare?.({ files: [shareFile] });
  if (canShare) {
    const shareBtn = document.createElement('button');
    shareBtn.className = 'adm-btn';
    shareBtn.textContent = '📤 分享 / 存檔（轉交別台）';
    shareBtn.addEventListener('click', async () => {
      try {
        await navigator.share({ files: [shareFile], title: fname });
      } catch (e) {
        // #330/Mac：桌機 Chrome/Safari `canShare` 回 true、但實際 share 檔案丟 NotAllowedError
        // （"Permission denied"）→ 該瀏覽器不真支援檔案分享，**退回下載**（非真失敗，別嚇使用者）。
        // AbortError = 使用者主動取消分享單，不動作。Windows 若 share 成功則不進此分支、行為不變。
        if (e?.name !== 'AbortError') dlBtn.click();
      }
    });
    row.appendChild(shareBtn);
  }

  banner.appendChild(label);
  banner.appendChild(row);

  if (_isIOS()) {
    const hint = document.createElement('div');
    hint.style.cssText = 'color:var(--text3);margin-top:6px;line-height:1.5;';
    hint.textContent = '⚠ iOS：「下載 / 安裝」會直接裝到「本機這支裝置」。'
      + '若要發證給「別台」裝置，請用「分享 / 存檔」→ 儲存到檔案或 AirDrop 給目標機。';
    banner.appendChild(hint);
  }

  box.insertBefore(banner, box.firstChild);
}

// 把 blob 包成 File（Web Share 需要 File 物件）；不支援 File 建構則回 null。
function _makeFile(name, blob, type) {
  try {
    return new window.File([blob], name, { type });
  } catch {
    return null;
  }
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
  // #348-F5 P2b：改 PIN 移出此處 → 「重設為臨時 PIN」按鈕（admResetPin）。儲存只處理角色 + 顯示名稱。
  const newRole = el('adm-role-' + username)?.value;
  const newDname = el('adm-dname-' + username)?.value.trim();
  const headers = {'Content-Type': 'application/json'};
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

// #348-F5 P2b：重設帳號 PIN → 系統產隨機臨時 PIN（不收 admin 自設）、標記首登強制改、一次性顯示。
export async function admResetPin(username) {
  if (!confirm('重設「' + username + '」的 PIN？\n系統會產生一組臨時 PIN，使用者下次登入須立即修改。\n（此臨時值只顯示一次，無法事後再取得）')) return;
  const resp = await authFetch(API_BASE + '/api/admin/accounts/' + username + '/pin', {
    method: 'PUT',
  });
  if (!resp.ok) { alert('重設失敗（' + resp.status + '）'); return; }
  const data = await resp.json().catch(() => ({}));
  _showTempPin(username, data.temp_pin);
}

// #348-F5 P2b：一次性顯示系統產臨時 PIN（建立 / 重設帳號後）。醒目大字 + 警語；關閉後即無法再取得。
// 用「自帶 overlay」而非 openModal（#overlay z-index 210）—— 因本流程在「帳號管理」面板（#admin-panel
// z-index 300）之上觸發，共用 modal 會被面板蓋住（看不到）。故自建 z-index 10000 的 overlay 確保最上層。
function _showTempPin(username, pin) {
  if (!pin) return;
  const ov = document.createElement('div');
  ov.id = 'temp-pin-overlay';
  ov.style.cssText = 'position:fixed;inset:0;z-index:10000;background:rgba(0,0,0,.7);' +
    'display:flex;align-items:center;justify-content:center;';
  ov.innerHTML =
    '<div style="background:var(--surface,#16213e);border:1px solid var(--border,#333);border-radius:12px;' +
                'padding:24px 20px;max-width:340px;width:90%;text-align:center;color:var(--text,#fff);">' +
      '<div style="font-size:14px;font-weight:700;margin-bottom:10px;">臨時 PIN</div>' +
      '<div style="font-size:13px;color:var(--text2,#aaa);margin-bottom:6px;">帳號 <b>' + _escAudit(username) + '</b> 的臨時 PIN</div>' +
      '<div style="font-family:var(--mono,monospace);font-size:30px;font-weight:700;letter-spacing:4px;' +
                  'color:var(--yellow,#e3b341);margin:10px 0;">' + _escAudit(pin) + '</div>' +
      '<div style="font-size:12px;color:var(--red,#e74c3c);margin:10px 0 16px;">⚠️ 僅顯示一次，請立即記下交給使用者；<br>使用者首次登入後須立即修改。</div>' +
      '<button class="adm-btn" id="temp-pin-ok">我已記下</button>' +
    '</div>';
  document.body.appendChild(ov);
  ov.querySelector('#temp-pin-ok').addEventListener('click', () => ov.remove());
}

export async function admToggleStatus(username, current) {
  const newStatus = current === 'active' ? 'suspended' : 'active';
  await authFetch(API_BASE + '/api/admin/accounts/' + username + '/status', {
    method:'PUT',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({status: newStatus}),
  });
  admLoadAccounts();
}

export async function admDelete(username) {
  if (!confirm('確定刪除帳號 ' + username + '？')) return;
  await authFetch(API_BASE + '/api/admin/accounts/' + username, {
    method:'DELETE',
  });
  admLoadAccounts();
}

export function admShowAddForm() {
  // #348-F5 P2b：移除 PIN 欄——系統產隨機臨時 PIN，建立後一次性顯示供轉交（admin 不自設）。
  el('adm-panel-add').innerHTML =
    '<div class="adm-add-form">' +
      '<label>帳號</label><input id="adm-add-user" placeholder="帳號">' +
      '<label>角色</label><select id="adm-add-role">' + _admRoleOptions('', _isSysadminSession()) + '</select>' +
      '<label>顯示名稱（選填）</label><input id="adm-add-dname" placeholder="顯示名稱">' +
      '<div style="font-size:11px;color:var(--text3);margin:4px 0;">建立後系統會產生一組臨時 PIN，僅顯示一次，交給使用者首登後須立即修改。</div>' +
      '<button class="adm-add-btn" data-action="adm-add-account">新增帳號</button>' +
      '<div id="adm-add-warn" style="font-size:11px;color:var(--red);min-height:16px;"></div>' +
    '</div>';
}

export async function admAddAccount() {
  const username = el('adm-add-user')?.value.trim();
  const role = el('adm-add-role')?.value;
  const displayName = el('adm-add-dname')?.value.trim();
  const warn = el('adm-add-warn');
  warn.textContent = '';
  if (!username) { warn.textContent = '請輸入帳號'; return; }
  // #348-F5 P2b：不再送 pin，後端產隨機臨時 PIN 並回傳。
  const resp = await authFetch(API_BASE + '/api/admin/accounts', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({username, role, display_name: displayName || null}),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    warn.textContent = err.detail || '新增失敗';
    return;
  }
  const data = await resp.json().catch(() => ({}));
  _showTempPin(username, data.temp_pin);   // 一次性顯示臨時 PIN
  admShowTab('list');
}

export async function admLoadLog() {
  const resp = await authFetch(API_BASE + '/api/admin/audit-log?limit=50', {});
  if (!resp.ok) return;
  const logs = await resp.json();
  let html = '';
  for (const log of logs) {
    html += '<div class="adm-log-entry">' +
      '<span class="adm-log-time">' + (_fmtLocalDT(log.created_at) || '') + '</span> ' +
      '<span class="adm-log-action">' + _escAudit(log.action_type || '') + '</span> ' +
      '<span style="color:var(--text2);">' + _escAudit(log.operator || '') + '</span> ' +
      '<span style="color:var(--text3);font-size:10px;">' + _escAudit(log.target_table || '') + '/' + _escAudit((log.target_id || '').slice(0,8)) + '</span>' +
    '</div>';
  }
  el('adm-panel-log').innerHTML = html || '<div style="color:var(--text3);text-align:center;padding:20px;">無日誌</div>';
}

// ── Pi 節點管理 ────────────────────────────────────────────────
let _lastCreatedApiKey = null;

export async function admLoadPiNodes() {
  const resp = await authFetch(API_BASE + '/api/admin/pi-nodes', {});
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
        <div style="font-size:12px;font-weight:600;">${dot} ${_escAudit(n.label || n.unit_id)}</div>
        <div style="font-size:10px;color:var(--text3);">${_escAudit(n.unit_id)} · key ...${_escAudit(n.api_key_suffix)} · ${seen}</div>
      </div>
      <button data-action="adm-rekey-pi-node" data-unit-id="${_escAudit(n.unit_id)}" style="padding:3px 8px;font-size:10px;background:var(--surface2);color:var(--text);border:1px solid var(--border);border-radius:3px;cursor:pointer;">換 Key</button>
      <button data-action="adm-delete-pi-node" data-unit-id="${_escAudit(n.unit_id)}" style="padding:3px 8px;font-size:10px;background:var(--surface2);color:var(--red);border:1px solid var(--border);border-radius:3px;cursor:pointer;">刪除</button>
    </div>`;
  }
  el('adm-panel-pi').innerHTML = html;
}

export async function admCreatePiNode() {
  const unit_id = el('pi-new-unit')?.value;
  const label = el('pi-new-label')?.value.trim() || unit_id;
  const resp = await authFetch(API_BASE + '/api/admin/pi-nodes', {
    method:'POST', headers:{'Content-Type':'application/json'},
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
    method:'POST',
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
    method:'DELETE',
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
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({url: API_BASE}),
    });
    if (!r1.ok) { resultEl.textContent = '設定 command_url 失敗（檢查 Pi Admin PIN）'; resultEl.style.color='var(--red)'; return; }
    const r2 = await fetch(piUrl + '/admin/pi-api-key', {
      method:'POST', headers:{'Content-Type':'application/json'},
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
});

// ── 認證初始化（供 main.js 呼叫）───────────────────────────────
export async function authInit(options = {}) {
  _onEnterDashboard = options.onEnterDashboard || null;
  // #293 階段2：session token 在 httpOnly cookie（JS 讀不到）→ 靠呼叫 heartbeat（同源自動帶 cookie）
  // 判斷是否仍登入，並由回應還原顯示態（新分頁/reload 皆適用；未登入回 401 → 落到登入頁）。
  {
    try {
      const resp = await fetch(API_BASE + '/api/auth/heartbeat');
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
