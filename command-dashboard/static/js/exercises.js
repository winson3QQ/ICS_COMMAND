/**
 * exercises.js — 演習場次管理模組（P1-14 PR-2）
 *
 * 職責：
 *   - 與 /api/exercises 互動（list / create / activate / archive）
 *   - 維護 active exercise 快取
 *   - 渲染 header 的 exercise chip（取代死掉的 ttx-toggle）
 *   - 渲染 settings 內的演習管理面板
 *
 * 後端會自動依「當前 active exercise」scope 資料；前端不再送 session_type。
 * RBAC：建立 / 啟動 / 歸檔僅指揮層（sysadmin / commander，後端強制）；
 *       非指揮層 UI 隱藏這些鈕，但仍可看 list。
 *
 * 可 import：ws.js（依既有模組邊界慣例，authFetch / 角色判斷由 ws.js re-export）
 */

import { authFetch, canUseRealModeControls } from './ws.js';

const API_BASE = location.origin;

// 模組級 active exercise 快取（renderExerciseChip 讀取）
let _activeExercise = null;

// ── API 呼叫 ────────────────────────────────────────────────────

/** 取得所有演習場次（依 created_at DESC）。失敗回空陣列。 */
export async function loadExercises() {
  try {
    const resp = await authFetch(API_BASE + '/api/exercises');
    if (!resp.ok) return [];
    const list = await resp.json();
    return Array.isArray(list) ? list : [];
  } catch (_) {
    return [];
  }
}

/** 從 list 找出 active 那筆（至多一個）；無則回 null。 */
export function getActiveExercise(list) {
  if (!Array.isArray(list)) return null;
  return list.find(ex => ex && ex.status === 'active') || null;
}

/** 建立演習（type: 'ttx' | 'real'）。 */
export async function createExercise(name, type = 'ttx') {
  return authFetch(API_BASE + '/api/exercises', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, type }),
  });
}

/** 啟動演習（設為 active）。 */
export async function activateExercise(id) {
  return authFetch(API_BASE + '/api/exercises/' + id + '/activate', { method: 'POST' });
}

/** 歸檔演習（釋放 active mutex）。 */
export async function archiveExercise(id) {
  return authFetch(API_BASE + '/api/exercises/' + id + '/archive', { method: 'POST' });
}

// ── 純函式：chip 文案 / class 決定（可單測）─────────────────────

/**
 * 決定 chip 的顯示內容與 class（純函式，便於單測）。
 * 演習(ttx) → 「演習／名稱」、ex-chip--ttx（琥珀）；實戰(real) → 「實戰／名稱」、ex-chip--real（紅）；
 * 無 active → 「無進行中場次」、ex-chip--idle。文字標籤（非圖示），顏色區分 real/ttx。
 * 樣式一律走 CSS class（DS token），不在此寫死任何顏色。
 */
export function exerciseChipView(activeExercise) {
  // 圖示沿用底圖「黑夜/白天」UI 語言：☀ 演練（ttx）/ ☾ 實戰（real）。
  // 實戰也是一場被記錄的 session（type='real'），與演練同機制 → 都可 scope / 未來 AAR 回放。
  if (activeExercise && activeExercise.name) {
    const isReal = activeExercise.type === 'real';
    return {
      text: (isReal ? '實戰／' : '演習／') + activeExercise.name,
      className: 'ex-chip ' + (isReal ? 'ex-chip--real' : 'ex-chip--ttx'),
      title: (isReal ? '實戰進行中：' : '演習進行中：') + activeExercise.name + '（點擊查看演習管理）',
    };
  }
  // 無 active＝未開任何場次（≠「實戰」；實戰要開一場 type=real 才會被記錄）
  return {
    text: '無進行中場次',
    className: 'ex-chip ex-chip--idle',
    title: '目前無進行中場次；實戰／演練皆需在演習管理啟動一場才會被記錄（點擊開啟）',
  };
}

/** 演習狀態的中文標籤（純函式）。 */
export function exerciseStatusLabel(status) {
  switch (status) {
    case 'active':   return '進行中';
    case 'setup':    return '準備中';
    case 'archived': return '已封存';
    default:         return status || '—';
  }
}

// ── chip 渲染 ───────────────────────────────────────────────────

/**
 * 更新 header 的 exercise chip。
 * 傳入 activeExercise 則用它；否則用模組快取。
 */
export function renderExerciseChip(activeExercise) {
  if (activeExercise !== undefined) _activeExercise = activeExercise;
  const chip = document.getElementById('exercise-chip');
  if (!chip) return;
  const view = exerciseChipView(_activeExercise);
  chip.textContent = view.text;
  chip.className = view.className;
  chip.title = view.title;
}

// ── 演習管理面板渲染 ────────────────────────────────────────────

/** 日期格式化（YYYY-MM-DD HH:MM；無值回 —）。純顯示用。 */
function _fmtDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso.endsWith && iso.endsWith('Z') ? iso : iso + 'Z');
  if (isNaN(d.getTime())) return '—';
  const p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** 簡易 HTML escape，避免演習名稱含特殊字元破版。 */
function _esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/**
 * 填入演習管理面板（settings 內 #stg-exercise-section）。
 * 會載入 list、更新 chip、渲染列表 + 建立表單。
 * 非指揮層隱藏建立 / 啟動 / 歸檔鈕（仍可看 list）。
 */
export async function renderExercisePanel() {
  const body = document.getElementById('ex-panel-body');
  if (!body) return;
  const canManage = canUseRealModeControls();   // sysadmin / commander
  const list = await loadExercises();
  _activeExercise = getActiveExercise(list);
  renderExerciseChip(_activeExercise);

  let html = '';

  // 建立表單（僅指揮層）
  if (canManage) {
    html += `<div class="ex-create">
      <input id="ex-new-name" class="ex-input" placeholder="演習名稱" maxlength="80">
      <select id="ex-new-type" class="ex-input ex-select">
        <option value="ttx">桌上推演 (TTX)</option>
        <option value="real">實兵演練 (real)</option>
      </select>
      <button class="ex-btn ex-btn--primary" data-action="exCreate">建立</button>
    </div>
    <div id="ex-create-warn" class="ex-warn"></div>`;
  }

  // 列表
  if (!list.length) {
    html += '<div class="ex-empty">目前沒有演習場次。</div>';
  } else {
    html += '<div class="ex-list">';
    for (const ex of list) {
      const isActive = ex.status === 'active';
      const rowCls = 'ex-row' + (isActive ? ' ex-row--active' : '');
      const typeLabel = ex.type === 'real' ? '實兵' : 'TTX';
      let actions = '';
      if (canManage) {
        if (ex.status !== 'active') {
          actions += `<button class="ex-btn" data-action="exActivate" data-id="${_esc(ex.id)}">啟動</button>`;
        }
        if (ex.status !== 'archived') {
          actions += `<button class="ex-btn" data-action="exArchive" data-id="${_esc(ex.id)}">歸檔</button>`;
        }
      }
      html += `<div class="${rowCls}">
        <div class="ex-row-main">
          <div class="ex-row-name">${_esc(ex.name)}</div>
          <div class="ex-row-meta">${typeLabel} · ${exerciseStatusLabel(ex.status)} · ${_fmtDate(ex.created_at)}</div>
        </div>
        <div class="ex-row-actions">${actions}</div>
      </div>`;
    }
    html += '</div>';
  }

  body.innerHTML = html;
}

// ── data-action handler（由 main.js dispatch 呼叫）─────────────────

/** 建立演習（讀表單）。成功後重渲染面板。 */
export async function handleExCreate() {
  const nameEl = document.getElementById('ex-new-name');
  const typeEl = document.getElementById('ex-new-type');
  const warn = document.getElementById('ex-create-warn');
  if (warn) warn.textContent = '';
  const name = (nameEl?.value || '').trim();
  const type = typeEl?.value || 'ttx';
  if (!name) {
    if (warn) warn.textContent = '請輸入演習名稱';
    return;
  }
  const resp = await createExercise(name, type);
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    if (warn) warn.textContent = err.detail || '建立失敗';
    return;
  }
  await renderExercisePanel();
}

/** 啟動演習。成功後重渲染；衝突（已有 active）顯示後端訊息。 */
export async function handleExActivate(id) {
  const resp = await activateExercise(id);
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    const warn = document.getElementById('ex-create-warn');
    if (warn) warn.textContent = err.detail || '啟動失敗';
    return;
  }
  await renderExercisePanel();
}

/** 歸檔演習。成功後重渲染。 */
export async function handleExArchive(id) {
  const resp = await archiveExercise(id);
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    const warn = document.getElementById('ex-create-warn');
    if (warn) warn.textContent = err.detail || '歸檔失敗';
    return;
  }
  await renderExercisePanel();
}

/** 重新整理面板。 */
export async function handleExRefresh() {
  await renderExercisePanel();
}

/** 初始化 chip（登入後呼叫）：載入 list、設定快取、渲染 chip。 */
export async function initExerciseChip() {
  const list = await loadExercises();
  _activeExercise = getActiveExercise(list);
  renderExerciseChip(_activeExercise);
}
