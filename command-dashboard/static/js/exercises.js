// SPDX-License-Identifier: LicenseRef-Proprietary
// Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
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
 * RBAC：建立 / 歸檔僅指揮層（sysadmin / commander）；**啟動（開場）僅 sysadmin**（#473-B1，開場含
 *       紅藍分隊＝白隊之責）；刪除僅 sysadmin。皆後端強制；非指揮層 UI 隱藏這些鈕，但仍可看 list。
 *
 * 可 import：ws.js（依既有模組邊界慣例，authFetch / 角色判斷由 ws.js re-export）
 */

import { hasAnyRole, closeExercisePanel } from './auth.js';
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

// #258 β-1：目前 active 演習模式（'ttx' | 'real' | null）。讀模組快取 _activeExercise，於
// initExerciseChip（首連啟動）與 exercise:switched WS 事件刷新。map.js 編輯閘據此 mode-aware ——
// TTX 才放行編輯外部 TAK 來源物件（對齊後端 _require_editable_source；server 仍為權威，前端僅避免露出
// 會被 403 的鈕；快取若暫時過時，後端仍 403 擋住、不破安全）。
// 註：重連期間若漏接 exercise_switched，快取可能短暫過時 → 由 #265（server 端切換時更新各連線 scope）統一根治。
export function activeExerciseType() {
  return _activeExercise?.type || null;
}

/** #267：目前 active 演習 id（無 active → null）。roster 納編/退編 endpoint `/{id}/enroll` 用。 */
export function activeExerciseId() {
  return _activeExercise?.id ?? null;
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

/** 刪除演習（級聯清資料；sysadmin-only，後端 SYSADMIN_ONLY + 進行中擋）。 */
export async function deleteExercise(id) {
  return authFetch(API_BASE + '/api/exercises/' + id, { method: 'DELETE' });
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
        // #473-B1：開場（啟動）收 sysadmin——開場流程含紅藍分隊（白隊之責，commander 恆藍不經手）。
        // 後端 SYSADMIN_ONLY 為真實邊界；此處隱藏鈕避免 commander 點了吃 403。
        if (ex.status !== 'active' && hasAnyRole('sysadmin')) {
          // #473-B3：「啟動」改開開場精靈（分隊→清圖→確認開始），不再直接 activate。
          actions += `<button class="ex-btn" data-action="exWizard" data-id="${_esc(ex.id)}" data-name="${_esc(ex.name)}">啟動</button>`;
        }
        // 歸檔＝結束進行中的演習，故只對 active 顯示（準備中尚未啟動、archived 已歸檔皆不顯）。
        if (ex.status === 'active') {
          actions += `<button class="ex-btn" data-action="exArchive" data-id="${_esc(ex.id)}">歸檔</button>`;
        }
        // 刪除（級聯清資料）限 sysadmin；進行中不可刪（需先歸檔）
        if (hasAnyRole('sysadmin') && ex.status !== 'active') {
          actions += `<button class="ex-btn ex-btn--danger" data-action="exDelete" data-id="${_esc(ex.id)}" data-name="${_esc(ex.name)}">刪除</button>`;
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

/**
 * 啟動演習（開場精靈第③步呼叫）。成功→重渲染面板並回 true；失敗（如衝突：已有 active）→ 顯示
 * 後端訊息並回 false。錯誤優先寫精靈的 `#ex-wiz-start-warn`（精靈開著時），退回面板的 `#ex-create-warn`。
 * 回傳 boolean 讓 dispatch 只在成功時關精靈 + 刷新場次。
 */
export async function handleExActivate(id) {
  const warn = document.getElementById('ex-wiz-start-warn') || document.getElementById('ex-create-warn');
  try {
    const resp = await activateExercise(id);
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      if (warn) warn.textContent = err.detail || '啟動失敗';
      return false;
    }
    await renderExercisePanel();
    return true;
  } catch (e) {
    // authFetch 走 fetch()——網路失敗（離線/連線拒絕/DNS）會 throw 而非回 resp.ok=false。
    // 不 catch 會讓精靈「開始」鈕靜默無反饋（dispatch 的 .then(ok) 收不到 false）。
    if (warn) warn.textContent = '啟動失敗：' + (e.message || '網路錯誤');
    return false;
  }
}

// ── 開場精靈（#473-B3）──────────────────────────────────────────
// 把三段既有能力縫成一條開場引導流程：① 紅藍分隊（導流到既有面板，不重造）→ ② 選擇性清圖
// （呼叫 B2 的 /api/admin/clear-residual，可跳過）→ ③ 確認開始記錄（activate）。單一 modal 三段
// 清單（非分頁 stepper），全走 data-action 委派（CSP-safe）。sysadmin-only（開場＝白隊之責，#473-B1）。

/**
 * 開啟開場精靈。id/name 由「啟動」鈕的 data-* 帶入（name 僅顯示，openModal title 用 textContent 防 XSS）。
 * sysadmin gate 在此（後端 activate/clear-residual 皆 SYSADMIN_ONLY 為真實邊界；此處避免露鈕）。
 */
export async function openExOpenWizard(id, name) {
  if (!hasAnyRole('sysadmin')) return;
  // 精靈用通用 modal（#overlay z=210），演習面板 #exercise-overlay z=290 會蓋住它 → 先關演習面板，
  // 讓精靈乾淨浮在地圖上（避免「精靈在演習面板後」）。「前往分隊面板」再重開面板落在分隊分頁。
  closeExercisePanel();
  const { openModal } = await import('./cop.js');
  const body = `
    <div class="ex-wiz">
      <div class="ex-wiz-step">
        <div class="ex-wiz-h">① 紅藍分隊</div>
        <div class="ex-wiz-d">把連上的裝置分到紅／藍／中立。指揮官恆為藍隊，分類是白隊（導調）之責。</div>
        <button class="ex-btn" data-action="exWizGoFaction">前往分隊面板</button>
      </div>
      <div class="ex-wiz-step">
        <div class="ex-wiz-h">② 選擇性清圖（可跳過）</div>
        <div class="ex-wiz-d">清掉上一場的裝置殘影，保留你自建的路線／區域／釘住標記；仍在線的裝置會自動重報。</div>
        <button class="ex-btn" data-action="exWizClear">清除殘留</button>
        <span id="ex-wiz-clear-result" class="ex-wiz-result"></span>
      </div>
      <div class="ex-wiz-step">
        <div class="ex-wiz-h">③ 開始記錄</div>
        <div class="ex-wiz-d">確認以上就緒後，啟動演習開始記錄。</div>
        <button class="ex-btn ex-btn--primary" data-action="exWizStart" data-id="${_esc(id)}">開始記錄（啟動）</button>
        <span id="ex-wiz-start-warn" class="ex-wiz-result"></span>
      </div>
    </div>`;
  const footer = '<button class="ex-btn" data-action="closeModal">取消</button>';
  openModal('開場精靈 · ' + (name || ''), body, footer);
}

/** 精靈步驟②：確認後呼叫 clear-residual，把「清了幾個／保留幾個」inline 回饋到精靈。 */
export async function handleExWizardClear() {
  const { appConfirm } = await import('./cop.js');
  const result = document.getElementById('ex-wiz-clear-result');
  const ok = await appConfirm(
    '清除殘留',
    '將清掉外部裝置的殘影（保留你自建的路線／區域／釘住標記）。\n仍在線的裝置會自動重報。\n此操作可復原（軟刪除）。確定清除？'
  );
  if (!ok) return;
  try {
    const resp = await authFetch(API_BASE + '/api/admin/clear-residual', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      // 後端 OP-2 強制 body confirm:"RESET"（比照 reset-*，不依賴前端 dialog）。
      body: JSON.stringify({ confirm: 'RESET' }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      if (result) result.textContent = '清除失敗：' + (err.detail || resp.status);
      return;
    }
    const data = await resp.json();
    if (result) result.textContent = `已清 ${data.cleared} 個殘留，保留 ${data.kept} 個永久物件。`;
  } catch (e) {
    if (result) result.textContent = '錯誤：' + e.message;
  }
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

/** 刪除演習（sysadmin）。confirm 警告級聯清資料；成功後重渲染。 */
export async function handleExDelete(id, name) {
  const warn = document.getElementById('ex-create-warn');
  if (warn) warn.textContent = '';
  // 破壞性：連同該場所有事件/圖釘/裁示/紀錄一併刪除，不可復原
  const ok = (typeof window !== 'undefined' && window.confirm)
    ? window.confirm(`確定刪除演習「${name || id}」？\n將連同該場所有事件、地圖物件、裁示、紀錄一併刪除，無法復原。`)
    : true;
  if (!ok) return;
  const resp = await deleteExercise(id);
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    if (warn) warn.textContent = err.detail || '刪除失敗';
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
