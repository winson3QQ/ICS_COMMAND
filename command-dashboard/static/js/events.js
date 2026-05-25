/**
 * events.js — 事件管理模組（C1-F CSP 模組化，v3.0.0）
 *
 * 職責：
 *   - NAPSG_EVENTS / NAPSG_GROUPS 常數（與 map.js 共用，由此為 single source of truth）
 *   - 事件表單（openEventForm / closeEventForm / submitEvent）
 *   - 事件處置 modal（showEventProcessModal / showEventDetail）
 *   - 節點 modal（showZoneDetail / _renderZoneModal）
 *   - 右側事件追蹤列表（renderZoneC 事件段，由 cop.js 呼叫）
 *   - 期限調整 dialog
 *   - 事件篩選 / 分組狀態
 *
 * 可 import：ws.js
 * 不可 import：map.js / cop.js / charts.js / decisions.js
 *   → 需要 map.js 函式時，透過 initEvents({ ... }) 注入
 *   → 需要 cop.js 函式時，透過 initEvents({ ... }) 注入
 *
 * AC-2 合規：本模組所有 innerHTML 模板不包含 onclick=
 *           互動元素一律使用 data-action / data-id / data-change-action
 */

import { authFetch, canCreateEvents } from './ws.js';

const API_BASE = location.origin;

// ══════════════════════════════════════════════════════════════
// NAPSG 危害事件類型（single source of truth；map.js re-export 此模組）
// ══════════════════════════════════════════════════════════════
export const NAPSG_EVENTS = {
  // ── 安全威脅 ──────────────────────────────────────────────────
  explosive:    { label: '疑似爆裂物', group: 'security', icon: 'explosive', abbr: '爆', defaultAssigned: 'forward',  severity: 'critical' },
  drone:        { label: '無人機威脅', group: 'security', icon: 'drone',     abbr: '機', defaultAssigned: 'security', severity: 'critical' },
  violent:      { label: '暴力事件',   group: 'security', icon: 'shield',    abbr: '暴', defaultAssigned: 'security', severity: 'critical' },
  unknown_person:{ label: '不明人士',  group: 'security', icon: 'eye',       abbr: '人', defaultAssigned: 'security', severity: 'warning'  },
  perimeter:    { label: '管制區異常', group: 'security', icon: 'shield',    abbr: '域', defaultAssigned: 'security', severity: 'warning'  },
  crowd:        { label: '秩序問題',   group: 'security', icon: 'shield',    abbr: '眾', defaultAssigned: 'security', severity: 'warning'  },
  // ── 搜救行動 ──────────────────────────────────────────────────
  rescue:       { label: '受困救援',   group: 'rescue',   icon: 'run',       abbr: '救', defaultAssigned: 'forward',  severity: 'warning'  },
  qrf:          { label: 'QRF 出動',   group: 'rescue',   icon: 'run',       abbr: 'QR', defaultAssigned: 'forward',  severity: 'warning'  },
  // ── 醫療緊急 ──────────────────────────────────────────────────
  mci:          { label: '大量傷亡',   group: 'medical',  icon: 'event',     abbr: 'MCI', defaultAssigned: 'medical', severity: 'critical' },
  emergency:    { label: '緊急病症',   group: 'medical',  icon: 'event',     abbr: '急', defaultAssigned: 'medical',  severity: 'critical' },
  infectious:   { label: '傳染疑慮',   group: 'medical',  icon: 'event',     abbr: '疫', defaultAssigned: 'medical',  severity: 'warning'  },
  // ── 收容照護 ──────────────────────────────────────────────────
  capacity:     { label: '量能超載',   group: 'care',     icon: 'event',     abbr: '滿', defaultAssigned: null,       severity: 'warning'  },
  isolation:    { label: '隔離事件',   group: 'care',     icon: 'shield',    abbr: '隔', defaultAssigned: 'shelter',  severity: 'warning'  },
  person_need:  { label: '人員狀況',   group: 'care',     icon: 'person',    abbr: '護', defaultAssigned: 'shelter',  severity: 'info'     },
  // ── 基礎設施 ──────────────────────────────────────────────────
  comm_fail:    { label: '通訊異常',   group: 'infra',    icon: 'event',     abbr: '訊', defaultAssigned: 'command',  severity: 'warning'  },
  facility:     { label: '設施異常',   group: 'infra',    icon: 'event',     abbr: '設', defaultAssigned: 'command',  severity: 'info'     },
  equipment:    { label: '設備故障',   group: 'infra',    icon: 'event',     abbr: '器', defaultAssigned: 'command',  severity: 'info'     },
  // ── 行動管理 ──────────────────────────────────────────────────
  evacuation:   { label: '撤離',       group: 'ops',      icon: 'run',       abbr: '疏', defaultAssigned: 'command',  severity: 'warning'  },
  resource:     { label: '資源調度',   group: 'ops',      icon: 'event',     abbr: '物', defaultAssigned: 'command',  severity: 'info'     },
  situation:    { label: '現場變化',   group: 'ops',      icon: 'event',     abbr: '況', defaultAssigned: null,        severity: 'info'     },
  hazard:       { label: '危害回報',   group: 'ops',      icon: 'event',     abbr: '危', defaultAssigned: null,        severity: 'info'     },
  other:        { label: '其他',       group: 'ops',      icon: 'event',     abbr: '他', defaultAssigned: null,        severity: 'info'     },
};

export const NAPSG_GROUPS = {
  security: '安全威脅',
  rescue:   '搜救行動',
  medical:  '醫療緊急',
  care:     '收容照護',
  infra:    '基礎設施',
  ops:      '行動管理',
};

// ══════════════════════════════════════════════════════════════
// 依賴注入（cop.js 在 initEvents 時提供）
// ══════════════════════════════════════════════════════════════
let _getData              = null;  // () => _data
let _getCurrentOperator   = null;  // () => string
let _closeModal           = null;  // () => void
let _openModal            = null;  // (title, body) => void
let _doPoll               = null;  // async () => void
let _appConfirm           = null;  // (title, msg) => Promise<bool>
let _findZoneByEventId    = null;  // (id) => zone | null
let _showEventProcessModal_ext = null; // (zone) => void（外部 cop.js 呼叫）
let _renderZoneC_ext      = null;  // (d) => void

export function initEvents({
  getData, getCurrentOperator, closeModal, openModal,
  doPoll, appConfirm, findZoneByEventId,
  showEventProcessModal, renderZoneC,
}) {
  _getData             = getData;
  _getCurrentOperator  = getCurrentOperator;
  _closeModal          = closeModal;
  _openModal           = openModal;
  _doPoll              = doPoll;
  _appConfirm          = appConfirm;
  _findZoneByEventId   = findZoneByEventId;
  _showEventProcessModal_ext = showEventProcessModal;
  _renderZoneC_ext     = renderZoneC;

  // 監聽 cop.js 傳來的 show process modal 事件
  document.addEventListener('events:showProcessModal', (e) => {
    const { zone } = e.detail || {};
    if (zone) showEventProcessModal(zone);
  });
}

// ══════════════════════════════════════════════════════════════
// 輔助格式化
// ══════════════════════════════════════════════════════════════

export function _fmtLocal(iso, timeOnly) {
  if (!iso) return '';
  const d = new Date(iso.endsWith('Z') ? iso : iso + 'Z');
  const pad = n => String(n).padStart(2, '0');
  const time = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  if (timeOnly) return time;
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${time}`;
}

export function _countdownStr(deadline) {
  if (!deadline) return '';
  const ts = deadline.endsWith('Z') ? deadline : deadline + 'Z';
  const diff = new Date(ts).getTime() - Date.now();
  const absDiff = Math.abs(diff);
  const min = Math.floor(absDiff / 60000);
  const sec = Math.floor((absDiff % 60000) / 1000);
  const timeStr = min + ':' + String(sec).padStart(2, '0');
  return diff < 0 ? '逾時 ' + timeStr : '剩餘 ' + timeStr;
}

export function _evTypeLabel(ev) {
  if (!ev) return '';
  return NAPSG_EVENTS[ev.event_type]?.label || ev.event_type || ev.description || '';
}

export function _parseNotes(notesField) {
  if (!notesField) return [];
  if (Array.isArray(notesField)) return notesField;
  try { return JSON.parse(notesField); } catch { return []; }
}

function _decisionAge(createdAt) {
  if (!createdAt) return '';
  const ts = createdAt.endsWith('Z') ? createdAt : createdAt + 'Z';
  const diff = (Date.now() - new Date(ts).getTime()) / 60000;
  if (diff < 1) return '<1m';
  if (diff < 60) return Math.floor(diff) + 'm';
  return Math.floor(diff / 60) + 'h' + Math.floor(diff % 60) + 'm';
}

function _truncate(s, n) { return s && s.length > n ? s.slice(0, n) + '…' : (s || ''); }

// ══════════════════════════════════════════════════════════════
// 事件表單
// ══════════════════════════════════════════════════════════════

export function openEventForm(prefilledUnit, prefilledLocation, zoneId) {
  if (!canCreateEvents()) return;
  const el = id => document.getElementById(id);
  if (prefilledUnit) el('ev-unit').value = prefilledUnit;
  if (prefilledLocation && typeof prefilledLocation === 'object' && prefilledLocation.lat != null) {
    // 轉 MGRS：透過 CustomEvent 要求 map.js 轉換
    document.dispatchEvent(new CustomEvent('events:latlngToMGRS', {
      detail: { lat: prefilledLocation.lat, lng: prefilledLocation.lng, targetId: 'ev-location' },
    }));
  } else if (typeof prefilledLocation === 'string') {
    el('ev-location').value = '';
  }
  if (zoneId) el('ev-zone-id').value = zoneId;
  if (!el('ev-operator').value) el('ev-operator').value = _getCurrentOperator?.() || '';
  _updateEvTypeFromCategories();
  _syncEvSeverity();
  el('event-overlay').className = 'show';
}

export function closeEventForm() {
  const el = id => document.getElementById(id);
  el('event-overlay').className = '';
  el('ev-desc').value = '';
  el('ev-location').value = '';
  if (el('ev-decision')) el('ev-decision').checked = false;
  el('ev-zone-id').value = '';
}

export function _syncEvSeverity() {
  const el = id => document.getElementById(id);
  const typeKey = el('ev-type').value;
  const sev = NAPSG_EVENTS[typeKey]?.severity || 'warning';
  const radio = document.querySelector(`input[name="ev-sev"][value="${sev}"]`);
  if (radio) radio.checked = true;
}

export function _updateEvTypeFromCategories() {
  const el = id => document.getElementById(id);
  const sel = el('ev-type');
  if (!sel) return;
  sel.innerHTML = '';
  let prevGroup = null;
  Object.entries(NAPSG_EVENTS).forEach(([k, v]) => {
    if (v.group !== prevGroup) {
      const grpOpt = document.createElement('option');
      grpOpt.disabled = true;
      grpOpt.textContent = `── ${NAPSG_GROUPS[v.group] || v.group} ──`;
      sel.appendChild(grpOpt);
      prevGroup = v.group;
    }
    const opt = document.createElement('option');
    opt.value = k; opt.textContent = v.label;
    sel.appendChild(opt);
  });
}

export async function submitEvent() {
  if (!canCreateEvents()) return;
  const el = id => document.getElementById(id);
  const severity = document.querySelector('input[name="ev-sev"]:checked')?.value || 'warning';
  const sessionType = (() => {
    try { return window.__sessionType || 'real'; } catch { return 'real'; }
  })();
  const body = {
    reported_by_unit:          el('ev-unit').value,
    event_type:                el('ev-type').value,
    severity,
    description:               el('ev-desc').value,
    operator_name:             el('ev-operator').value || _getCurrentOperator?.() || '',
    location_desc:             el('ev-location').value || null,
    needs_commander_decision:  el('ev-decision').checked,
    location_zone_id:          el('ev-zone-id').value || null,
    session_type:              sessionType,
  };
  if (!body.description) { el('ev-desc').focus(); return; }
  try {
    const resp = await authFetch(API_BASE + '/api/events', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (resp.ok) {
      const created = await resp.json();
      closeEventForm();

      // 樂觀更新
      const _data = _getData?.();
      if (_data?.events) {
        const now = new Date().toISOString();
        const optimisticEvent = {
          reported_by_unit: body.reported_by_unit,
          event_type:       body.event_type,
          severity:         body.severity,
          description:      body.description,
          operator_name:    body.operator_name,
          location_desc:    body.location_desc || null,
          location_zone_id: body.location_zone_id || null,
          assigned_unit:    null,
          status:           'open',
          occurred_at:      now,
          created_at:       now,
          notes:            null,
          ...created,
        };
        if (!_data.events.find(e => e.id === optimisticEvent.id)) {
          _data.events.unshift(optimisticEvent);
        }
        import('./charts.js').then(m => {
          _renderZoneC_ext?.(m.getSeries());
        });
        // 若 zone modal 還開著，重繪事件頁
        if (_zoneModalZone && document.getElementById('overlay')?.className === 'show') {
          _zoneModalTab = 'events';
          _renderZoneModal();
        }
      }

      // 背景 poll
      _doPoll?.();

      // 詢問是否在地圖標記位置
      document.dispatchEvent(new CustomEvent('map:startEventPin', {
        detail: {
          id:        created.id,
          eventCode: created.event_code,
          label:     NAPSG_EVENTS[body.event_type]?.label || body.event_type || body.description,
          type:      body.event_type,
          severity:  body.severity,
        },
      }));
    } else {
      alert('送出失敗：' + resp.status);
    }
  } catch (e) {
    alert('送出失敗：' + e.message);
  }
}

// ══════════════════════════════════════════════════════════════
// 事件處置 Modal
// ══════════════════════════════════════════════════════════════

export function showEventDetail(ev) {
  const fakeZone = {
    id:         ev.location_zone_id || ev.id,
    event_id:   ev.id,
    event_code: ev.event_code,
    label:      _evTypeLabel(ev),
    icon:       'event',
  };
  sessionStorage.setItem('_openEventId', ev.id);
  showEventProcessModal(fakeZone);
}

export function showEventProcessModal(zone) {
  if (zone.event_id) sessionStorage.setItem('_openEventId', zone.event_id);
  const _data = _getData?.();
  const allEvts = _data?.events || [];
  const ev = zone.event_id
    ? allEvts.find(e => e.id === zone.event_id)
    : allEvts.find(e => e.location_zone_id === zone.id);
  if (!ev) {
    _showZoneDetailDirect(zone);
    return;
  }

  const el = id => document.getElementById(id);
  const sevLabel = { critical: '緊急', warning: '警告', info: '一般' }[ev.severity] || ev.severity;
  const sevColor = ev.severity === 'critical' ? 'var(--red)' : ev.severity === 'warning' ? 'var(--yellow)' : 'var(--text3)';
  const statusLabel = { open: '未結', in_progress: '處理中', resolved: '已結案', closed: '已關閉' }[ev.status] || ev.status;
  const statusC = (ev.status === 'resolved' || ev.status === 'closed') ? 'var(--green)' : 'var(--yellow)';
  const unitLabel = { forward: '前進組', security: '安全組', shelter: '收容組', medical: '醫療組', command: '指揮部' }[ev.reported_by_unit] || ev.reported_by_unit;
  const notes = _parseNotes(ev.notes);
  const isOpen = ev.status === 'open' || ev.status === 'in_progress';
  const countdown = ev.response_deadline ? _countdownStr(ev.response_deadline) : '';
  const countdownColor = countdown.startsWith('逾時') ? 'var(--red)' : 'var(--yellow)';

  import('./map.js').then(m => {
    const iconHtml = `<span style="display:inline-flex;vertical-align:middle;margin-right:4px;">${m.renderIcon(zone.icon)}</span>`;
    el('modal-title').innerHTML = `${iconHtml} ${(ev.event_code || '').replace(/-\d{4}-/, '-')}　${_evTypeLabel(ev)}`;
  });

  let html = '';
  // 狀態 + 倒數
  html += `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">`;
  html += `<div style="display:flex;gap:6px;align-items:center;">`;
  html += `<span style="padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600;background:${sevColor}22;color:${sevColor};">${sevLabel}</span>`;
  html += `<span style="padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600;background:${statusC}22;color:${statusC};">${statusLabel}</span>`;
  html += `</div>`;
  if (countdown && isOpen) {
    html += `<div style="font-size:12px;font-weight:700;color:${countdownColor};cursor:pointer;" data-action="resetDeadlineMenu" data-id="${ev.id}" title="點擊重設時間">⏱ ${countdown}</div>`;
  } else if (countdown) {
    html += `<div style="font-size:12px;font-weight:700;color:var(--text3);">⏱ ${countdown}</div>`;
  }
  html += `</div>`;

  const unitLabels = { forward: '前進組', security: '安全組', shelter: '收容組', medical: '醫療組', command: '指揮部' };
  const assignedLabel = unitLabels[ev.assigned_unit] || '—';
  const assignedColor = ev.assigned_unit ? 'var(--green)' : 'var(--text3)';
  html += `<div style="font-size:11px;color:var(--text2);margin-bottom:10px;line-height:1.6;">`;
  html += `回報：${unitLabel}　${ev.operator_name}<br>`;
  html += `<span style="display:inline-flex;align-items:center;gap:6px;">指派處理：<span style="color:${assignedColor};font-weight:600;">${assignedLabel}</span>`;
  html += `<select style="font-size:10px;padding:1px 4px;background:var(--surface2);border:1px solid var(--border);color:var(--text);border-radius:3px;font-family:var(--mono);" data-change-action="setAssignedUnit" data-id="${ev.id}">`;
  html += `<option value="">—</option>`;
  ['forward', 'security', 'shelter', 'medical', 'command'].forEach(u => {
    html += `<option value="${u}" ${ev.assigned_unit === u ? 'selected' : ''}>${unitLabels[u]}</option>`;
  });
  html += `</select></span><br>`;
  html += `發生：${_fmtLocal(ev.occurred_at)}　期限：${_fmtLocal(ev.response_deadline)}`;
  html += `</div>`;

  // 處置紀錄
  html += `<div style="border-top:1px solid var(--border);padding-top:8px;margin-bottom:8px;">`;
  html += `<div style="font-size:10px;font-weight:600;color:var(--text3);margin-bottom:4px;">處置紀錄</div>`;
  if (notes.length > 0) {
    notes.forEach(n => {
      html += `<div style="font-size:11px;padding:4px 0;border-bottom:1px solid rgba(48,54,61,.3);">`;
      html += `<span style="color:var(--text3);">${_fmtLocal(n.time, true)}</span>`;
      html += n.by ? ` <span style="color:#DAA520;font-weight:600;">${n.by}</span>` : '';
      html += ` — ${n.text}`;
      html += `</div>`;
    });
  } else {
    html += `<div style="font-size:11px;color:var(--text3);">無處置紀錄</div>`;
  }
  html += `</div>`;

  // 操作區（未結案）
  if (isOpen) {
    html += `<div style="border-top:1px solid var(--border);padding-top:8px;">`;
    html += `<input id="ev-proc-note" placeholder="輸入處置紀錄..." autocomplete="off" style="width:100%;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:6px 8px;border-radius:4px;font-size:11px;font-family:var(--mono);margin-bottom:6px;">`;
    html += `<div style="display:flex;gap:6px;">`;
    html += `<button data-action="autoSaveAndAction" data-id="${ev.id}" data-ev-action="close" style="flex:1;padding:7px;background:transparent;color:var(--text3);border:none;border-radius:4px;font-size:11px;font-weight:700;cursor:pointer;font-family:var(--mono);">關閉</button>`;
    if (ev.status === 'open') {
      html += `<button data-action="autoSaveAndAction" data-id="${ev.id}" data-ev-action="in_progress" style="flex:1;padding:7px;background:var(--yellow);color:#fff;border:none;border-radius:4px;font-size:11px;font-weight:700;cursor:pointer;font-family:var(--mono);">處理中</button>`;
    }
    html += `<button data-action="autoSaveAndAction" data-id="${ev.id}" data-ev-action="resolved" style="flex:1;padding:7px;background:var(--green);color:#fff;border:none;border-radius:4px;font-size:11px;font-weight:700;cursor:pointer;font-family:var(--mono);">結案</button>`;
    html += `</div>`;
    html += `</div>`;
  }

  el('modal-body').innerHTML = html;
  el('overlay').className = 'show';
}

export async function updateEventStatus(eventId, status) {
  const _data = _getData?.();
  if (status === 'resolved') {
    const ev = (_data?.events || []).find(e => e.id === eventId);
    const notes = ev ? _parseNotes(ev.notes) : [];
    if (notes.length === 0) return;
  }
  try {
    const resp = await authFetch(
      API_BASE + '/api/events/' + eventId + '/status?status=' + status +
      '&operator=' + encodeURIComponent(_getCurrentOperator?.() || ''), { method: 'PATCH' }
    );
    if (resp.ok) { _closeModal?.(); await _doPoll?.(); }
  } catch (e) { alert('更新失敗：' + e.message); }
}

// ── 自動儲存輸入框文字 + 執行動作 ──
export async function _autoSaveAndAction(eventId, action) {
  const input = document.getElementById('ev-proc-note');
  const text = input?.value?.trim();
  if (text) {
    try {
      await authFetch(API_BASE + '/api/events/' + eventId + '/notes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, operator: _getCurrentOperator?.() || '' }),
      });
    } catch (e) { /* 忽略 */ }
  }
  if (action === 'close') { _closeModal?.(); await _doPoll?.(); return; }

  if (action === 'resolved') {
    await _doPoll?.();
    const _data = _getData?.();
    const ev = (_data?.events || []).find(e => e.id === eventId);
    const notes = ev ? _parseNotes(ev.notes) : [];
    if (notes.length === 0) {
      if (input) { input.focus(); input.style.borderColor = 'var(--red)'; setTimeout(() => { if(input) input.style.borderColor = ''; }, 2000); }
      return;
    }
    const ok = await _appConfirm?.('結案確認', '確定將此事件結案？');
    if (!ok) return;
  }

  try {
    const resp = await authFetch(
      API_BASE + '/api/events/' + eventId + '/status?status=' + action +
      '&operator=' + encodeURIComponent(_getCurrentOperator?.() || ''), { method: 'PATCH' }
    );
    if (resp.ok) {
      if (action === 'resolved') {
        // 通知 map.js 移除對應 marker
        document.dispatchEvent(new CustomEvent('map:removeEventZone', { detail: { eventId } }));
      }
      _closeModal?.();
      await _doPoll?.();
    }
  } catch (e) { alert('更新失敗：' + e.message); }
}

export async function _setAssignedUnit(eventId, newUnit) {
  const _data = _getData?.();
  const unitLabels = { forward: '前進組', security: '安全組', shelter: '收容組', medical: '醫療組', command: '指揮部' };
  const ev = (_data?.events || []).find(e => e.id === eventId);
  const oldUnit = ev?.assigned_unit || null;
  if ((oldUnit || '') === (newUnit || '')) return;
  try {
    await authFetch(API_BASE + '/api/events/' + eventId, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ assigned_unit: newUnit || null }),
    });
    const from = oldUnit ? (unitLabels[oldUnit] || oldUnit) : '—';
    const to   = newUnit ? (unitLabels[newUnit] || newUnit) : '—';
    await authFetch(API_BASE + '/api/events/' + eventId + '/notes', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: `指派變更：${from} → ${to}`, operator: _getCurrentOperator?.() || '' }),
    });
    await _doPoll?.();
    const zone = _findZoneByEventId?.(eventId);
    if (zone) showEventProcessModal(zone);
  } catch (e) { console.error('[SET-ASSIGNED]', e); }
}

// ══════════════════════════════════════════════════════════════
// 期限調整 Dialog
// ══════════════════════════════════════════════════════════════

let _dlState = { sign: 1, minutes: 30, eventId: null, baseDeadline: null };

export function _resetDeadlineMenu(eventId) {
  const _data = _getData?.();
  const ev = (_data?.events || []).find(e => e.id === eventId);
  const base = ev?.response_deadline || new Date().toISOString();
  _dlState = { sign: 1, minutes: 30, eventId, baseDeadline: base };

  const fmtDlg = (iso) => {
    const d = new Date(iso.endsWith('Z') ? iso : iso + 'Z');
    const pad = n => String(n).padStart(2, '0');
    return `${pad(d.getMonth() + 1)}/${pad(d.getDate())}  ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  };
  const nowLocal  = fmtDlg(new Date().toISOString());
  const baseLocal = fmtDlg(base);

  const segBase  = `padding:7px 18px;font-size:15px;font-weight:700;cursor:pointer;border:none;transition:background .15s,color .15s;`;
  const cellStyle = `flex:1;text-align:center;padding:10px 12px;background:var(--bg3);border-radius:8px;`;
  const labelStyle = `font-size:10px;color:var(--text3);letter-spacing:.06em;margin-bottom:5px;`;
  const valStyle   = `font-size:16px;font-weight:700;font-family:var(--mono);color:var(--text1);`;

  let html = '';
  html += `<div style="display:flex;gap:10px;margin-bottom:16px;">`;
  html += `<div style="${cellStyle}"><div style="${labelStyle}">現在時間</div><div style="${valStyle}">${nowLocal}</div></div>`;
  html += `<div style="${cellStyle}"><div style="${labelStyle}">現有期限</div><div style="${valStyle}">${baseLocal}</div></div>`;
  html += `</div>`;
  html += `<div style="border-top:1px solid var(--border);margin-bottom:14px;"></div>`;
  html += `<div style="display:flex;align-items:center;justify-content:center;gap:14px;margin-bottom:12px;">`;
  html += `<div style="display:flex;border-radius:8px;overflow:hidden;border:1px solid var(--border);">`;
  html += `<button id="dl-btn-minus" data-action="dlSetSign" data-sign="-1" style="${segBase}background:var(--bg3);color:var(--text3);">−</button>`;
  html += `<button id="dl-btn-plus"  data-action="dlSetSign" data-sign="1"  style="${segBase}background:var(--green);color:#fff;">＋</button>`;
  html += `</div>`;
  const stepBtn = `width:26px;height:26px;border-radius:50%;border:1px solid var(--border);background:var(--bg3);color:var(--text2);font-size:14px;font-weight:700;cursor:pointer;line-height:1;`;
  html += `<div style="display:flex;align-items:center;gap:6px;">`;
  html += `<button data-action="dlAdjust" data-delta="-10" style="${stepBtn}">‹</button>`;
  html += `<div id="dl-min-display" style="font-size:30px;font-weight:700;font-family:var(--mono);min-width:52px;text-align:center;color:var(--text1);">30</div>`;
  html += `<button data-action="dlAdjust" data-delta="10" style="${stepBtn}">›</button>`;
  html += `</div>`;
  html += `<div style="font-size:13px;color:var(--text2);">分鐘</div>`;
  html += `</div>`;
  html += `<div style="display:flex;justify-content:center;gap:8px;margin-bottom:14px;">`;
  [10, 30, 60, 120].forEach(m => {
    html += `<button data-action="dlSetMin" data-min="${m}" style="padding:3px 13px;border-radius:12px;border:1px solid var(--border);background:var(--bg3);color:var(--text2);font-size:11px;font-family:var(--mono);cursor:pointer;">${m}</button>`;
  });
  html += `</div>`;
  html += `<div style="border-top:1px solid var(--border);margin-bottom:12px;"></div>`;
  html += `<div style="display:flex;align-items:center;gap:12px;">`;
  html += `<div style="flex:1;text-align:center;">`;
  html += `<div style="${labelStyle}">調整後期限</div>`;
  html += `<div id="dl-new-display" style="font-size:16px;font-weight:700;font-family:var(--mono);color:var(--green);">—</div>`;
  html += `</div>`;
  html += `<button data-action="applyDeadline" style="padding:10px 28px;border-radius:8px;border:none;background:var(--green);color:#fff;font-size:14px;font-weight:700;cursor:pointer;white-space:nowrap;">套用</button>`;
  html += `</div>`;

  const titleEl = document.getElementById('modal-title');
  const bodyEl  = document.getElementById('modal-body');
  const overlayEl = document.getElementById('overlay');
  if (titleEl) titleEl.textContent = '⏱ 調整期限';
  if (bodyEl)  bodyEl.innerHTML = html;
  if (overlayEl) overlayEl.className = 'show';
  _dlUpdatePreview();
}

export function _dlSetSign(sign) {
  _dlState.sign = sign;
  const mBtn = document.getElementById('dl-btn-minus');
  const pBtn = document.getElementById('dl-btn-plus');
  if (mBtn) { mBtn.style.background = sign === -1 ? 'var(--red)' : 'var(--bg3)'; mBtn.style.color = sign === -1 ? '#fff' : 'var(--text3)'; }
  if (pBtn) { pBtn.style.background = sign ===  1 ? 'var(--green)' : 'var(--bg3)'; pBtn.style.color = sign ===  1 ? '#fff' : 'var(--text3)'; }
  _dlUpdatePreview();
}

export function _dlAdjust(delta) {
  _dlState.minutes = Math.max(5, Math.min(240, _dlState.minutes + delta));
  const d = document.getElementById('dl-min-display');
  if (d) d.textContent = _dlState.minutes;
  _dlUpdatePreview();
}

export function _dlSetMin(m) {
  _dlState.minutes = m;
  const d = document.getElementById('dl-min-display');
  if (d) d.textContent = m;
  _dlUpdatePreview();
}

function _dlUpdatePreview() {
  const d = document.getElementById('dl-new-display');
  if (!d || !_dlState.baseDeadline) return;
  const newDl = new Date(new Date(_dlState.baseDeadline).getTime() + _dlState.sign * _dlState.minutes * 60000);
  const pad = n => String(n).padStart(2, '0');
  d.textContent = `${pad(newDl.getMonth() + 1)}/${pad(newDl.getDate())}  ${pad(newDl.getHours())}:${pad(newDl.getMinutes())}`;
  d.style.color = _dlState.sign === -1 ? 'var(--red)' : 'var(--green)';
}

export function _applyDeadline() {
  const { eventId, sign, minutes } = _dlState;
  _resetDeadline(eventId, sign * minutes);
}

async function _resetDeadline(eventId, deltaMins) {
  try {
    const resp = await authFetch(API_BASE + '/api/events/' + eventId + '/deadline', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ delta_minutes: deltaMins, operator: _getCurrentOperator?.() || '' }),
    });
    if (!resp.ok) throw new Error(await resp.text());
    const { new_deadline } = await resp.json();
    const direction = deltaMins > 0 ? '延長' : '提前';
    const localStr = _fmtLocal(new_deadline);
    await authFetch(API_BASE + '/api/events/' + eventId + '/notes', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text: `期限${direction} ${Math.abs(deltaMins)} 分鐘（新期限：${localStr}）`,
        operator: _getCurrentOperator?.() || '',
      }),
    });
    _closeModal?.();
    await _doPoll?.();
    const zone = _findZoneByEventId?.(eventId);
    if (zone) showEventProcessModal(zone);
  } catch (e) { alert('調整失敗：' + e.message); }
}

// ══════════════════════════════════════════════════════════════
// 節點 Modal（showZoneDetail）
// ══════════════════════════════════════════════════════════════

let _zoneModalZone = null;
export let _zoneModalTab = 'events';

export function setZoneModalTab(tab) { _zoneModalTab = tab; sessionStorage.setItem('_zoneTab', tab); }

function _showZoneDetailDirect(zone) { showZoneDetail(zone); }

export function showZoneDetail(zone) {
  const _data = _getData?.();
  if (!_data) return;
  _zoneModalZone = zone;
  const hasData = zone.node_type === 'shelter' || zone.node_type === 'medical';
  const savedTab = sessionStorage.getItem('_zoneTab');
  if (savedTab && (savedTab !== 'data' || hasData)) {
    _zoneModalTab = savedTab;
  } else {
    _zoneModalTab = hasData ? 'data' : 'events';
  }
  sessionStorage.setItem('_openZoneModal', JSON.stringify({ id: zone.id, node_type: zone.node_type }));
  _renderZoneModal();
}

export function _renderZoneModal() {
  const zone = _zoneModalZone;
  const _data = _getData?.();
  if (!zone || !_data) return;
  const el = id => document.getElementById(id);
  const calc = _data.calc || {};
  const nt = zone.node_type;
  const piNodes = _data?.pi_nodes || [];
  const piNode = piNodes.find(n => n.unit_id === nt);
  let freshLabel, freshLvl, freshColor;
  if (piNode && piNode.last_seen_at) {
    const ageSec = Math.round((Date.now() - new Date(piNode.last_seen_at).getTime()) / 1000);
    freshLabel = ageSec < 60 ? `${ageSec}秒前` : `${Math.round(ageSec / 60)}分前`;
    freshLvl = ageSec < 30 ? 'ok' : ageSec < 90 ? 'warn' : 'crit';
  } else if (piNode) {
    freshLabel = '未連線'; freshLvl = 'lkp';
  } else {
    const nd = nt ? calc[nt] : null;
    freshLabel = nd?.freshness?.label || '無資料'; freshLvl = nd?.freshness?.level || 'lkp';
  }
  freshColor = freshLvl === 'ok' ? 'var(--green)' : freshLvl === 'warn' ? 'var(--yellow)' : freshLvl === 'crit' ? 'var(--red)' : 'var(--text3)';

  const hasData = nt === 'shelter' || nt === 'medical';
  let tabsHtml = '<div style="display:flex;gap:2px;margin-bottom:12px;">';
  if (hasData) tabsHtml += _zoneTabBtn('data', '📊 數據');
  tabsHtml += _zoneTabBtn('events', '📋 事件');
  tabsHtml += _zoneTabBtn('decisions', '⚖ 裁示');
  tabsHtml += '</div>';

  let body = '';
  if (_zoneModalTab === 'data' && hasData) body = _zoneDataTab(zone);
  else if (_zoneModalTab === 'events')   body = _zoneEventsTab(zone);
  else if (_zoneModalTab === 'decisions') body = _zoneDecisionsTab(zone);

  import('./map.js').then(m => {
    const iconHtml = `<span style="display:inline-flex;vertical-align:middle;margin-right:4px;">${m.renderIcon(zone.icon)}</span>`;
    el('modal-title').innerHTML = `${iconHtml} ${zone.label} <span style="font-size:10px;color:${freshColor};margin-left:6px;">${freshLabel}</span>`;
  });
  el('modal-body').innerHTML = tabsHtml + body;
  el('overlay').className = 'show';

  const autoEl = el('modal-body').querySelector('.l3-autoload');
  if (autoEl) { const unit = autoEl.dataset.unit; autoEl.id = 'l3-container'; setTimeout(() => document.dispatchEvent(new CustomEvent('map:loadL3Records', { detail: { unit } })), 50); }
  const pwaLoader = el('modal-body').querySelector('.pwa-incidents-loader');
  if (pwaLoader) { const unit = pwaLoader.dataset.unit; setTimeout(() => _loadPwaIncidents(unit), 80); }
}

function _zoneTabBtn(key, label) {
  const active = _zoneModalTab === key;
  return `<button data-action="zoneTab" data-tab="${key}" style="padding:4px 12px;border-radius:4px;border:1px solid ${active ? 'var(--green)' : 'var(--border)'};background:${active ? 'rgba(26,158,82,.2)' : 'var(--surface2)'};color:${active ? '#fff' : 'var(--text3)'};font-size:11px;font-weight:600;cursor:pointer;font-family:var(--mono);">${label}</button>`;
}

function _zoneDataTab(zone) {
  if (zone.node_type === 'shelter' || zone.node_type === 'medical') {
    return `<div id="l3-container" style="margin-top:4px;"></div>
      <div class="l3-autoload" data-unit="${zone.node_type}"></div>`;
  }
  return '<div style="color:var(--text3)">此據點無詳細資料</div>';
}

function _zoneEventsTab(zone) {
  const _data = _getData?.();
  const unit = zone.node_type || '';
  const allEvents = _data?.events || [];
  const seen = new Set();
  const events = (unit
    ? allEvents.filter(e => e.reported_by_unit === unit || e.assigned_unit === unit)
    : allEvents
  ).filter(e => { if (seen.has(e.id)) return false; seen.add(e.id); return true; });

  const isOpen = e => e.status !== 'resolved' && e.status !== 'closed';
  const openEvs   = events.filter(isOpen);
  const closedEvs = events.filter(e => !isOpen(e));

  const latStr = zone.lat != null ? zone.lat : '';
  const lngStr = zone.lng != null ? zone.lng : '';
  let html = `<div style="margin-bottom:8px;display:flex;align-items:center;gap:6px;">`;
  html += `<button data-action="openEventForm" data-unit="${unit}" data-lat="${latStr}" data-lng="${lngStr}" data-zone="${zone.id}" style="padding:4px 12px;background:var(--green);color:#fff;border:none;border-radius:4px;font-size:11px;font-weight:600;cursor:pointer;font-family:var(--mono);">📝 新增事件</button>`;
  if (events.length > 0) {
    html += `<span style="font-size:10px;color:var(--text3);">${openEvs.length} 筆進行中`;
    if (closedEvs.length > 0) html += `・${closedEvs.length} 筆已結案`;
    html += `</span>`;
  }
  html += `</div>`;
  if (events.length === 0) {
    html += '<div style="color:var(--text3);font-size:11px;margin-bottom:8px;">無 ICS 事件記錄</div>';
  } else {
    openEvs.forEach(ev => { html += _eventCardHTML(ev, false, unit); });
    if (closedEvs.length > 0) {
      html += `<div style="font-size:9px;color:var(--text3);padding:4px 0 3px;font-weight:600;border-bottom:1px solid var(--border);margin:8px 0 4px;">已結案</div>`;
      closedEvs.forEach(ev => { html += _eventCardHTML(ev, true, unit); });
    }
  }
  if (unit === 'shelter' || unit === 'medical') {
    const topMargin = events.length > 0 ? '10px' : '0';
    html += `<div style="font-size:9px;color:var(--text3);padding:4px 0 3px;font-weight:600;border-bottom:1px solid var(--border);margin:${topMargin} 0 4px;">PWA 組內事件</div>`;
    html += `<div id="pwa-incidents-container"><span style="color:var(--text3);font-size:10px;">載入中...</span></div>`;
    html += `<div class="pwa-incidents-loader" data-unit="${unit}" style="display:none;"></div>`;
  }
  return html;
}

export async function _loadPwaIncidents(unitId) {
  const container = document.getElementById('pwa-incidents-container');
  if (!container) return;
  try {
    const resp = await authFetch(API_BASE + `/api/pi-data/${unitId}/list`);
    if (!resp.ok) { container.innerHTML = '<span style="color:var(--text3);font-size:10px;">無法取得資料</span>'; return; }
    const data = await resp.json();
    if (data.offline) { container.innerHTML = '<span style="color:var(--yellow);font-size:10px;">⚠ Pi 節點離線</span>'; return; }
    const incLabels = { security_threat: '安全威脅', infectious_risk: '傳染疑慮', resource_shortage: '物資短缺', capacity_overload: '量能超載', medication_mgmt: '藥品管理', language_assist: '語言協助', noise_disturbance: '災民喧嘩', other: '其他' };
    const incidents = (data.grouped?.incidents || []).map(r => r.record || {});
    if (incidents.length === 0) { container.innerHTML = '<span style="color:var(--text3);font-size:10px;">無組內事件</span>'; return; }
    const sevColor = s => s === '高' ? 'var(--red)' : s === '中' ? 'var(--yellow)' : 'var(--text3)';
    const stColor  = s => (s === '已結案' || s === 'closed') ? 'var(--green)' : 'var(--yellow)';
    let html = '';
    incidents.forEach(inc => {
      const label  = incLabels[inc.type] || inc.type || '事件';
      const sev    = inc.severity || '';
      const st     = inc.status   || '';
      const desc   = inc.description ? `<div style="font-size:9px;color:var(--text3);margin-top:2px;">${inc.description.slice(0, 60)}</div>` : '';
      const dimmed = (st === '已結案' || st === 'closed') ? 'opacity:.5;' : '';
      html += `<div style="padding:6px 10px;margin-bottom:4px;background:var(--surface2);border-radius:5px;border-left:3px solid ${sevColor(sev)};${dimmed}">`;
      html += `<div style="display:flex;justify-content:space-between;align-items:center;">`;
      html += `<span style="font-size:10px;font-weight:600;">${label}</span>`;
      html += `<span style="font-size:9px;color:${stColor(st)};font-weight:600;">${st}</span>`;
      html += `</div>${desc}</div>`;
    });
    container.innerHTML = html;
  } catch (e) { if (container) container.innerHTML = '<span style="color:var(--text3);font-size:10px;">載入失敗</span>'; }
}

function _zoneDecisionsTab(zone) {
  const _data = _getData?.();
  const unit = zone.node_type || '';
  const allDecs = (_data?.decisions?.pending || []).concat(_data?.decisions?.decided || []);
  const events = _data?.events || [];
  const unitEventIds = new Set(events.filter(e => e.reported_by_unit === unit).map(e => e.id));
  const filtered = unit ? allDecs.filter(d => d.primary_event_id && unitEventIds.has(d.primary_event_id)) : allDecs.slice(0, 10);
  if (filtered.length === 0) return '<div style="color:var(--text3);font-size:11px;">無相關裁示事項</div>';
  // 委派給 decisions.js 的 renderDecisionList（已有 data-action 版本）
  let html = '';
  filtered.forEach(dec => {
    const age  = _decisionAge(dec.created_at);
    const sevC = dec.severity === 'critical' ? 'var(--red)' : 'var(--yellow)';
    const statusLabel = dec.status === 'pending' ? '待裁示' : '已裁示：' + dec.status;
    html += `<div style="padding:6px 8px;margin-bottom:4px;background:var(--surface2);border-radius:5px;border-left:3px solid ${sevC};cursor:pointer;" data-action="showDecisionModal" data-id="${dec.id}">`;
    html += `<div style="display:flex;justify-content:space-between;">`;
    html += `<span style="font-size:11px;font-weight:700;">${_truncate(dec.decision_title, 30)}</span>`;
    html += `<span style="font-size:9px;color:var(--text3);">${statusLabel}</span>`;
    html += `</div>`;
    html += `<div style="font-size:9px;color:var(--text3);margin-top:2px;">${dec.decision_type} · ${age}</div>`;
    html += '</div>';
  });
  return html;
}

function _eventCardHTML(ev, dimmed, viewUnit) {
  const sevC = ev.severity === 'critical' ? 'var(--red)' : ev.severity === 'warning' ? 'var(--yellow)' : 'var(--text3)';
  const statusLabel = { open: '未結', in_progress: '處理中', resolved: '已結案', closed: '已關閉' }[ev.status] || ev.status;
  const statusC = (ev.status === 'resolved' || ev.status === 'closed') ? 'var(--green)' : 'var(--yellow)';
  const notes = _parseNotes(ev.notes);
  const isOpen = ev.status === 'open' || ev.status === 'in_progress';
  const _uLabel = { command: '指揮部', forward: '前進組', security: '安全組', shelter: '收容組', medical: '醫療組' };
  let roleTag = '';
  if (viewUnit) {
    const iReporter = ev.reported_by_unit === viewUnit;
    const iAssigned = ev.assigned_unit === viewUnit;
    if (iReporter && iAssigned) {
      roleTag = `<span style="font-size:9px;padding:1px 5px;border-radius:3px;background:rgba(56,139,253,.15);color:#58a6ff;border:1px solid rgba(56,139,253,.3);white-space:nowrap;">回報・承辦</span>`;
    } else if (iReporter) {
      const assignee = ev.assigned_unit ? `→ ${_uLabel[ev.assigned_unit] || ev.assigned_unit}` : '';
      roleTag = `<span style="font-size:9px;padding:1px 5px;border-radius:3px;background:rgba(56,139,253,.15);color:#58a6ff;border:1px solid rgba(56,139,253,.3);white-space:nowrap;">回報${assignee}</span>`;
    } else if (iAssigned) {
      const reporter = ev.reported_by_unit ? `${_uLabel[ev.reported_by_unit] || ev.reported_by_unit} →` : '';
      roleTag = `<span style="font-size:9px;padding:1px 5px;border-radius:3px;background:rgba(210,153,34,.15);color:#e3b341;border:1px solid rgba(210,153,34,.3);white-space:nowrap;">${reporter} 指派協助</span>`;
    }
  }
  const _typeLabel = _evTypeLabel(ev);
  const _descSub = ev.description && ev.description !== _typeLabel ? ev.description : '';

  let html = `<div style="padding:8px 10px;margin-bottom:6px;background:var(--surface2);border-radius:5px;border-left:3px solid ${sevC};${dimmed ? 'opacity:.5;' : ''}">`;
  html += `<div style="display:flex;justify-content:space-between;align-items:center;gap:6px;">`;
  html += `<span style="font-size:12px;font-weight:700;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${_typeLabel}</span>`;
  html += `<div style="display:flex;align-items:center;gap:4px;flex-shrink:0;">${roleTag}<span style="font-size:10px;color:${statusC};font-weight:600;">${statusLabel}</span></div>`;
  html += `</div>`;
  if (_descSub) html += `<div style="font-size:10px;color:var(--text2);margin-top:1px;font-style:italic;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;">${_descSub}</div>`;
  html += `<div style="font-size:10px;color:var(--text3);margin-top:3px;">回報：${ev.operator_name}　${_decisionAge(ev.occurred_at || ev.created_at)}</div>`;
  if (notes.length > 0) {
    html += `<div style="margin-top:6px;border-top:1px solid var(--border);padding-top:4px;">`;
    html += `<div style="font-size:9px;color:var(--text3);margin-bottom:2px;">處置紀錄</div>`;
    notes.forEach(n => {
      html += `<div style="font-size:10px;padding:2px 0;border-bottom:1px solid rgba(48,54,61,.3);">`;
      html += `<span style="color:var(--text3);">${_fmtLocal(n.time, true)}</span>`;
      html += n.by ? ` <span style="color:#DAA520;font-weight:600;">${n.by}</span>` : '';
      html += ` — ${n.text}`;
      html += `</div>`;
    });
    html += `</div>`;
  }
  if (isOpen) {
    html += `<div style="margin-top:6px;border-top:1px solid var(--border);padding-top:6px;">`;
    html += `<div style="display:flex;gap:4px;margin-bottom:4px;">`;
    html += `<input id="ev-note-${ev.id}" placeholder="追加處置紀錄..." style="flex:1;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:4px 6px;border-radius:3px;font-size:10px;font-family:var(--mono);">`;
    html += `<button data-action="addEventNote" data-id="${ev.id}" style="padding:4px 8px;background:var(--surface2);color:var(--text);border:1px solid var(--border);border-radius:3px;font-size:10px;cursor:pointer;font-family:var(--mono);">追加</button>`;
    html += `</div>`;
    html += `<div style="display:flex;gap:4px;">`;
    if (ev.status === 'open') {
      html += `<button data-action="updateEvAndRefresh" data-id="${ev.id}" data-ev-action="in_progress" style="flex:1;padding:4px;background:var(--yellow);color:#fff;border:none;border-radius:3px;font-size:10px;font-weight:600;cursor:pointer;font-family:var(--mono);">處理中</button>`;
    }
    const hasNotes = notes.length > 0;
    if (hasNotes) {
      html += `<button data-action="updateEvAndRefresh" data-id="${ev.id}" data-ev-action="resolved" style="flex:1;padding:4px;background:var(--green);color:#fff;border:none;border-radius:3px;font-size:10px;font-weight:600;cursor:pointer;font-family:var(--mono);">結案</button>`;
    } else {
      html += `<button disabled style="flex:1;padding:4px;background:var(--border);color:var(--text3);border:none;border-radius:3px;font-size:10px;cursor:not-allowed;font-family:var(--mono);" title="請先追加處置紀錄">結案</button>`;
    }
    html += `</div>`;
    html += `</div>`;
  }
  html += `</div>`;
  return html;
}

export async function _addEventNote(eventId) {
  const input = document.getElementById('ev-note-' + eventId);
  const text = input?.value?.trim();
  if (!text) { input?.focus(); return; }
  try {
    const resp = await authFetch(API_BASE + '/api/events/' + eventId + '/notes', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, operator: _getCurrentOperator?.() || '' }),
    });
    if (resp.ok) { await _doPoll?.(); _renderZoneModal(); }
    else alert('追加失敗：' + resp.status);
  } catch (e) { alert('追加失敗：' + e.message); }
}

export async function _updateEvAndRefresh(eventId, status) {
  try {
    const resp = await authFetch(
      API_BASE + '/api/events/' + eventId + '/status?status=' + status +
      '&operator=' + encodeURIComponent(_getCurrentOperator?.() || ''), { method: 'PATCH' }
    );
    if (resp.ok) {
      if (status === 'resolved') {
        document.dispatchEvent(new CustomEvent('map:removeEventZone', { detail: { eventId } }));
      }
      await _doPoll?.();
      _renderZoneModal();
    }
  } catch (e) { alert('更新失敗：' + e.message); }
}

// ══════════════════════════════════════════════════════════════
// 右側事件列表（cop.js renderZoneC 呼叫）
// ══════════════════════════════════════════════════════════════

// 事件篩選 / 分組狀態
let _evtFilter = null;      // null / 'overdue' / 'pending' / 'progress' / 'resolved'
let _evtGroupMode = 'assigned';  // 'assigned' / 'reported' / null
export let _evtCardDidHighlight = false;
let _evtCardTimer = null;
let _highlightedEventId = null;

export function _toggleEvtFilter(filter) {
  _evtFilter = (_evtFilter === filter) ? null : filter;
  import('./charts.js').then(m => { _renderZoneC_ext?.(m.getSeries()); });
}

export function _toggleEvtGroupMode(mode) {
  _evtGroupMode = (_evtGroupMode === mode) ? null : mode;
  import('./charts.js').then(m => { _renderZoneC_ext?.(m.getSeries()); });
}

export function clearEvtFilter() {
  _evtFilter = null;
  _evtGroupMode = null;
}

// 右側事件追蹤（cop.js 呼叫）
export function renderZoneC(data, d) {
  const el = id => document.getElementById(id);
  const allEvents = data?.events || [];
  const openEvents = allEvents.filter(e => e.status === 'open' || e.status === 'in_progress');
  const resolvedEvents = allEvents.filter(e => e.status === 'resolved' || e.status === 'closed');
  const now0 = Date.now();
  const overdueCount = openEvents.filter(e => {
    if (!e.response_deadline) return false;
    const ts = e.response_deadline.endsWith('Z') ? e.response_deadline : e.response_deadline + 'Z';
    return new Date(ts).getTime() < now0;
  }).length;
  const pendingCount = openEvents.filter(e => e.status === 'open').length;
  const progCount = openEvents.filter(e => e.status === 'in_progress').length;

  const openTotalEl = el('evt-open-total');
  if (openTotalEl) openTotalEl.textContent = openEvents.length > 0 ? openEvents.length : '';

  const overdueEl = el('evt-overdue');
  if (overdueEl) {
    if (overdueCount > 0) {
      overdueEl.innerHTML = `<span style="color:var(--red);font-weight:700;cursor:pointer;animation:pulse 1s infinite;" data-action="toggleEvtFilter" data-filter="overdue">逾${overdueCount}</span>`;
    } else {
      overdueEl.innerHTML = '';
    }
  }

  const statsEl = el('evt-stats');
  if (statsEl) {
    let statsHtml = '';
    const _af = _evtFilter;
    if (pendingCount > 0) statsHtml += `<span style="color:#e67e22;font-weight:700;cursor:pointer;${_af === 'pending' ? 'text-decoration:underline;' : ''}" data-action="toggleEvtFilter" data-filter="pending">待處理${pendingCount}</span> `;
    if (progCount > 0) statsHtml += `<span style="color:var(--yellow);cursor:pointer;${_af === 'progress' ? 'text-decoration:underline;' : ''}" data-action="toggleEvtFilter" data-filter="progress">處理${progCount}</span> `;
    if (resolvedEvents.length > 0) statsHtml += `<span style="opacity:.5;cursor:pointer;" data-action="toggleEvtFilter" data-filter="resolved">結案${resolvedEvents.length}</span>`;
    statsEl.innerHTML = statsHtml;
  }

  const filterBar = el('evt-filter-bar');
  if (filterBar) {
    const filterLabels = { overdue: '逾時', pending: '待處理', progress: '處理中', resolved: '已結案' };
    const barParts = [];
    if (_evtFilter) barParts.push('篩選：' + filterLabels[_evtFilter]);
    if (_evtGroupMode === 'assigned') barParts.push('按處理組別分類');
    if (_evtGroupMode === 'reported') barParts.push('按回報組別分類');
    if (barParts.length > 0) {
      filterBar.innerHTML = `<span>${barParts.join(' · ')}</span><span style="cursor:pointer;margin-left:auto;color:var(--text3);" data-action="clearEvtFilter">✕ 清除</span>`;
      filterBar.style.display = 'flex';
      filterBar.style.justifyContent = 'space-between';
      filterBar.style.alignItems = 'center';
    } else {
      filterBar.style.display = 'none';
    }
  }

  const _grpActive = 'background:rgba(26,158,82,.2);color:#fff;border-color:var(--green);';
  const _grpInact  = 'background:transparent;color:var(--text3);border-color:var(--border);';
  const _grpR = document.getElementById('evt-grp-reported');
  const _grpA = document.getElementById('evt-grp-assigned');
  if (_grpR) _grpR.style.cssText += _evtGroupMode === 'reported' ? _grpActive : _grpInact;
  if (_grpA) _grpA.style.cssText += _evtGroupMode === 'assigned' ? _grpActive : _grpInact;

  const unitNames = { forward: '前進組', security: '安全組', shelter: '收容組', medical: '醫療組', command: '指揮部' };
  const unitColors = { forward: '#e68c1e', security: '#3366e6', shelter: '#d4b200', medical: '#e03030', command: '#22b05a' };

  let filteredOpen = openEvents;
  let filteredResolved = resolvedEvents;
  if (_evtFilter === 'overdue') {
    filteredOpen = openEvents.filter(e => {
      if (!e.response_deadline) return false;
      const ts = e.response_deadline.endsWith('Z') ? e.response_deadline : e.response_deadline + 'Z';
      return new Date(ts).getTime() < now0;
    });
    filteredResolved = [];
  } else if (_evtFilter === 'pending') {
    filteredOpen = openEvents.filter(e => e.status === 'open');
    filteredResolved = [];
  } else if (_evtFilter === 'progress') {
    filteredOpen = openEvents.filter(e => e.status === 'in_progress');
    filteredResolved = [];
  } else if (_evtFilter === 'resolved') {
    filteredOpen = [];
  }

  let evtHtml = '';
  if (filteredOpen.length === 0 && filteredResolved.length === 0) {
    evtHtml = _evtFilter
      ? '<div style="color:var(--text3);font-size:11px;padding:8px;">無符合篩選的事件</div>'
      : '<div style="color:var(--green);font-size:12px;padding:8px;">無事件</div>';
  } else {
    const now = Date.now();
    filteredOpen.sort((a, b) => {
      const aOd = a.response_deadline && new Date(a.response_deadline.endsWith('Z') ? a.response_deadline : a.response_deadline + 'Z').getTime() < now;
      const bOd = b.response_deadline && new Date(b.response_deadline.endsWith('Z') ? b.response_deadline : b.response_deadline + 'Z').getTime() < now;
      return (aOd === bOd) ? 0 : aOd ? -1 : 1;
    });

    const _evtCardHtml = (ev) => {
      const cd = ev.response_deadline ? _countdownStr(ev.response_deadline) : '';
      let tagColor = 'var(--yellow)';
      let _isOverdue = false;
      if (ev.response_deadline) {
        const ts = ev.response_deadline.endsWith('Z') ? ev.response_deadline : ev.response_deadline + 'Z';
        const dl = new Date(ts).getTime();
        if (dl < now) { tagColor = 'var(--red)'; _isOverdue = true; }
        else {
          const cr = new Date((ev.occurred_at || ev.created_at).endsWith('Z') ? (ev.occurred_at || ev.created_at) : (ev.occurred_at || ev.created_at) + 'Z').getTime();
          if ((dl - now) < (dl - cr) * 0.5) tagColor = '#e67e22';
        }
      }
      const sevColor = ev.severity === 'critical' ? 'var(--red)' : ev.severity === 'warning' ? 'var(--yellow)' : 'var(--text3)';
      const uName  = unitNames[ev.reported_by_unit] || '';
      const asgKey = ev.assigned_unit;
      const asgName = asgKey && asgKey !== ev.reported_by_unit ? (unitNames[asgKey] || asgKey) : '';
      const overdueAnim = _isOverdue ? 'animation:pulse 1s infinite;' : '';
      const typeLabel = NAPSG_EVENTS[ev.event_type]?.label || ev.event_type || ev.description;
      const extraDesc = ev.description && ev.description !== typeLabel ? ev.description : '';
      let h = '';
      h += `<div style="cursor:pointer;padding:5px 8px;margin-bottom:2px;background:var(--surface2);border-radius:5px;border-left:3px solid ${sevColor};" data-action="openEventByCode" data-id="${ev.id}" data-longpress-id="${ev.id}">`;
      h += `<div style="display:flex;justify-content:space-between;align-items:center;gap:4px;">`;
      h += `<span style="font-size:10px;font-weight:600;flex:1;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;">${typeLabel}</span>`;
      h += `<span style="font-size:9px;font-weight:700;color:${tagColor};flex-shrink:0;${overdueAnim}">${cd}</span>`;
      h += `</div>`;
      if (extraDesc) h += `<div style="font-size:9px;color:var(--text3);overflow:hidden;white-space:nowrap;text-overflow:ellipsis;margin-top:1px;font-style:italic;">${extraDesc}</div>`;
      h += `<div style="display:flex;align-items:center;margin-top:2px;gap:0;">`;
      h += `<span style="font-size:9px;color:var(--text3);flex-shrink:0;">${uName}</span>`;
      h += `<span style="font-size:9px;color:var(--text3);flex:1;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;padding:0 6px;opacity:.7;">${asgName ? '→ ' + asgName : ''}</span>`;
      h += `<span style="font-size:8px;color:var(--text3);flex-shrink:0;">${(ev.event_code || '').replace(/-\d{4}-/, '-')}</span>`;
      h += `</div>`;
      h += `</div>`;
      return h;
    };

    const _renderGrouped = (evList, keyFn, showUnassigned) => {
      const groups = {};
      const unitOrder = ['forward', 'security', 'shelter', 'medical', 'command'];
      evList.forEach(ev => {
        const u = keyFn(ev);
        if (!groups[u]) groups[u] = [];
        groups[u].push(ev);
      });
      unitOrder.forEach(u => {
        if (!groups[u] || groups[u].length === 0) return;
        evtHtml += `<div style="font-size:9px;color:var(--text3);padding:6px 0 2px;font-weight:600;border-bottom:1px solid var(--border);">── ${unitNames[u] || u} ──</div>`;
        groups[u].forEach(ev => { evtHtml += _evtCardHtml(ev); });
      });
      if (showUnassigned && groups['__unassigned']?.length > 0) {
        evtHtml += `<div style="font-size:9px;color:var(--text3);padding:6px 0 2px;font-weight:600;border-bottom:1px solid var(--border);opacity:.6;">── 未指派 ──</div>`;
        groups['__unassigned'].forEach(ev => { evtHtml += _evtCardHtml(ev); });
      }
    };

    if (_evtGroupMode === 'assigned') {
      _renderGrouped(filteredOpen, ev => ev.assigned_unit || '__unassigned', true);
    } else if (_evtGroupMode === 'reported') {
      _renderGrouped(filteredOpen, ev => ev.reported_by_unit || 'command', false);
    } else {
      filteredOpen.forEach(ev => { evtHtml += _evtCardHtml(ev); });
    }

    if (filteredResolved.length > 0) {
      const _resolvedCard = (ev) => {
        const sevColor = ev.severity === 'critical' ? 'var(--red)' : ev.severity === 'warning' ? 'var(--yellow)' : 'var(--text3)';
        const uName  = unitNames[ev.reported_by_unit] || '';
        const asgKey = ev.assigned_unit;
        const asgName = asgKey && asgKey !== ev.reported_by_unit ? (unitNames[asgKey] || asgKey) : '';
        const typeLabel = NAPSG_EVENTS[ev.event_type]?.label || ev.event_type || ev.description;
        const extraDesc = ev.description && ev.description !== typeLabel ? ev.description : '';
        let h = `<div style="cursor:pointer;padding:5px 8px;margin-bottom:2px;background:var(--surface2);border-radius:5px;border-left:3px solid ${sevColor};opacity:.45;" data-action="openEventByCode" data-id="${ev.id}">`;
        h += `<div style="display:flex;justify-content:space-between;align-items:center;gap:4px;">`;
        h += `<span style="font-size:10px;font-weight:600;flex:1;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;">${typeLabel}</span>`;
        h += `<span style="font-size:9px;color:var(--text3);flex-shrink:0;">已結案</span>`;
        h += `</div>`;
        if (extraDesc) h += `<div style="font-size:9px;color:var(--text3);overflow:hidden;white-space:nowrap;text-overflow:ellipsis;margin-top:1px;font-style:italic;">${extraDesc}</div>`;
        h += `<div style="display:flex;align-items:center;margin-top:2px;gap:0;">`;
        h += `<span style="font-size:9px;color:var(--text3);flex-shrink:0;">${uName}</span>`;
        h += `<span style="font-size:9px;color:var(--text3);flex:1;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;padding:0 6px;opacity:.7;">${asgName ? '→ ' + asgName : ''}</span>`;
        h += `<span style="font-size:8px;color:var(--text3);flex-shrink:0;">${(ev.event_code || '').replace(/-\d{4}-/, '-')}</span>`;
        h += `</div></div>`;
        return h;
      };

      evtHtml += `<div style="font-size:9px;color:var(--text3);padding:5px 0 2px;font-weight:600;">已結案</div>`;
      if (_evtGroupMode) {
        const rGroups = {};
        const unitOrder = ['forward', 'security', 'shelter', 'medical', 'command'];
        filteredResolved.forEach(ev => {
          const u = _evtGroupMode === 'assigned' ? (ev.assigned_unit || '__unassigned') : (ev.reported_by_unit || 'command');
          if (!rGroups[u]) rGroups[u] = [];
          rGroups[u].push(ev);
        });
        unitOrder.forEach(u => {
          if (!rGroups[u] || rGroups[u].length === 0) return;
          evtHtml += `<div style="font-size:9px;color:var(--text3);padding:4px 0 2px;font-weight:600;border-bottom:1px solid var(--border);opacity:.6;">── ${unitNames[u] || u} ──</div>`;
          rGroups[u].forEach(ev => { evtHtml += _resolvedCard(ev); });
        });
        if (_evtGroupMode === 'assigned' && rGroups['__unassigned']?.length > 0) {
          evtHtml += `<div style="font-size:9px;color:var(--text3);padding:4px 0 2px;font-weight:600;opacity:.6;">── 未指派 ──</div>`;
          rGroups['__unassigned'].forEach(ev => { evtHtml += _resolvedCard(ev); });
        }
      } else {
        filteredResolved.forEach(ev => { evtHtml += _resolvedCard(ev); });
      }
    }
  }
  const listEl = el('right-evt-list');
  if (listEl) { listEl.innerHTML = evtHtml; _resizeEvtList(); }
}

export function _resizeEvtList() {
  const container = document.getElementById('right-events');
  const list = document.getElementById('right-evt-list');
  if (!container || !list) return;
  let usedH = 0;
  for (const child of container.children) {
    if (child === list) break;
    usedH += child.offsetHeight;
  }
  const availH = container.clientHeight - usedH;
  list.style.maxHeight = Math.max(availH, 50) + 'px';
}

let _expandedSection = sessionStorage.getItem('_expandedSection') || null;

export function toggleRightExpand(section = 'events') {
  if (_expandedSection === section) _applyRightExpand(null);
  else _applyRightExpand(section);
}

export function _applyRightExpand(section) {
  const el = id => document.getElementById(id);
  _expandedSection = section;
  sessionStorage.setItem('_expandedSection', section || '');
  const panel   = el('panel-right');
  const left    = el('panel-left');
  const divider = el('right-events')?.nextElementSibling;
  if (!section) {
    panel?.classList.remove('expanded');
    left?.classList.remove('collapsed');
    if (el('right-events')) { el('right-events').style.display = ''; el('right-events').style.flex = '6'; }
    if (el('right-decisions')) { el('right-decisions').style.display = ''; el('right-decisions').style.flex = '4'; }
    if (divider) divider.style.display = '';
    const arrow = el('evt-expand-arrow');
    if (arrow) arrow.textContent = '▶';
  } else {
    panel?.classList.add('expanded');
    left?.classList.add('collapsed');
    if (divider) divider.style.display = 'none';
    if (section === 'events') {
      if (el('right-events')) { el('right-events').style.display = ''; el('right-events').style.flex = '1'; }
      if (el('right-decisions')) el('right-decisions').style.display = 'none';
      const arrow = el('evt-expand-arrow');
      if (arrow) arrow.textContent = '◀';
    } else {
      if (el('right-decisions')) { el('right-decisions').style.display = ''; el('right-decisions').style.flex = '1'; }
      if (el('right-events')) el('right-events').style.display = 'none';
    }
  }
  requestAnimationFrame(_resizeEvtList);
}

// ── 長按事件卡片 → 地圖 highlight ──
export function _evtCardDown(eventId) {
  _evtCardDidHighlight = false;
  _evtCardTimer = setTimeout(() => {
    _evtCardTimer = null;
    _evtCardDidHighlight = true;
    document.dispatchEvent(new CustomEvent('map:highlightEvent', { detail: { eventId } }));
  }, 600);
}

export function _evtCardUp() {
  if (_evtCardTimer) { clearTimeout(_evtCardTimer); _evtCardTimer = null; }
  if (_evtCardDidHighlight) {
    document.dispatchEvent(new CustomEvent('map:unhighlightEvent'));
    setTimeout(() => { _evtCardDidHighlight = false; }, 100);
  }
}

export function openEventByCode(eventId) {
  if (_evtCardDidHighlight) return;
  const zone = _findZoneByEventId?.(eventId);
  if (zone) {
    showEventProcessModal(zone);
  } else {
    const _data = _getData?.();
    const ev = (_data?.events || []).find(e => e.id === eventId);
    if (ev) showEventDetail(ev);
  }
}

// ── events.js 對 map.js 通知的監聽 ──
// map.js 直接監聽 'map:loadL3Records' 並執行載入；events.js 不再需要轉發。
