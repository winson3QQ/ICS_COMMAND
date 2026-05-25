/**
 * decisions.js — 裁示 Modal 與操作模組（C1-F CSP 模組化，v3.0.0）
 *
 * 職責：
 *   - showDecisionModal：顯示待裁示或已裁示詳情
 *   - submitDecision / escalateDecision / closeDecision：裁示操作
 *   - _decisionAge / _truncate：輔助格式化
 *
 * 可 import：ws.js
 * 不可 import：map.js / events.js / charts.js / cop.js
 */

import { authFetch } from './ws.js';

const API_BASE = location.origin;

// 目前開啟的裁示 ID（modal 開啟期間有效）
let _currentDecisionId = null;

// 外部注入的資料存取函式（由 cop.js 在初始化時設定）
let _getData = null;           // () => _data
let _getCurrentOperator = null; // () => string
let _closeModal = null;        // () => void
let _doPoll = null;            // async () => void

/** cop.js 初始化時呼叫，注入跨模組依賴 */
export function initDecisions({ getData, getCurrentOperator, closeModal, doPoll }) {
  _getData = getData;
  _getCurrentOperator = getCurrentOperator;
  _closeModal = closeModal;
  _doPoll = doPoll;
}

// ══════════════════════════════════════════════════════════════
// 格式化輔助
// ══════════════════════════════════════════════════════════════

export function _decisionAge(createdAt) {
  if (!createdAt) return '';
  const ts = createdAt.endsWith('Z') ? createdAt : createdAt + 'Z';
  const diff = (Date.now() - new Date(ts).getTime()) / 60000;
  if (diff < 1) return '<1m';
  if (diff < 60) return Math.floor(diff) + 'm';
  return Math.floor(diff/60) + 'h' + Math.floor(diff%60) + 'm';
}

export function _truncate(s, n) { return s && s.length > n ? s.slice(0, n) + '…' : (s || ''); }

// ══════════════════════════════════════════════════════════════
// 裁示 Modal
// ══════════════════════════════════════════════════════════════

export function showDecisionModal(decId) {
  const data = _getData?.();
  const decisions = ((data?.decisions?.pending || []).concat(data?.decisions?.decided || []));
  const dec = decisions.find(d => d.id === decId);
  if (!dec) return;
  _currentDecisionId = decId;

  const age = _decisionAge(dec.created_at);
  const sevColor = dec.severity === 'critical' ? 'var(--red)' : 'var(--yellow)';

  let html = `<div style="margin-bottom:12px;">`;
  const decSevLabel = {critical:'緊急',warning:'警告',info:'一般'}[dec.severity] || dec.severity;
  const decTypeLabel = {initial:'初始裁示',revision:'修正',escalation:'升級',closure:'結案'}[dec.decision_type] || dec.decision_type;
  html += `<span style="display:inline-block;padding:2px 8px;border-radius:3px;background:${sevColor}33;color:${sevColor};font-size:10px;font-weight:700;">${decSevLabel}</span>`;
  html += `<span style="font-size:10px;color:var(--text3);margin-left:8px;">${decTypeLabel} · ${age}</span>`;
  html += `</div>`;
  html += `<div style="font-size:14px;font-weight:700;margin-bottom:8px;">${dec.decision_title}</div>`;
  html += `<div style="font-size:11px;color:var(--text2);margin-bottom:12px;">${dec.impact_description}</div>`;

  html += `<div class="slice-card" style="margin-bottom:8px;">`;
  html += `<div class="slice-card-title">建議動作 A</div>`;
  html += `<div style="font-size:12px;">${dec.suggested_action_a}</div>`;
  html += `</div>`;
  if (dec.suggested_action_b) {
    html += `<div class="slice-card" style="margin-bottom:8px;">`;
    html += `<div class="slice-card-title">建議動作 B</div>`;
    html += `<div style="font-size:12px;">${dec.suggested_action_b}</div>`;
    html += `</div>`;
  }

  if (dec.status === 'pending') {
    html += `<div style="margin-top:12px;">`;
    html += `<label style="font-size:10px;color:var(--text3);">裁示備註</label>`;
    html += `<input id="dec-note" placeholder="（選填）" style="width:100%;background:var(--surface2);border:1px solid var(--border);color:var(--text);padding:6px 8px;border-radius:4px;font-size:11px;font-family:var(--mono);margin:4px 0 8px;">`;
    html += `<div style="display:flex;gap:6px;">`;
    html += `<button data-action="submitDecision" data-status="approved" style="flex:1;padding:8px;background:var(--green);color:#fff;border:none;border-radius:5px;font-size:12px;font-weight:700;cursor:pointer;font-family:var(--mono);">✓ 核可</button>`;
    html += `<button data-action="submitDecision" data-status="hold" style="flex:1;padding:8px;background:var(--yellow);color:#fff;border:none;border-radius:5px;font-size:12px;font-weight:700;cursor:pointer;font-family:var(--mono);">⏸ 保留</button>`;
    html += `<button data-action="submitDecision" data-status="redirect" style="flex:1;padding:8px;background:var(--surface2);color:var(--text);border:1px solid var(--border);border-radius:5px;font-size:12px;font-weight:700;cursor:pointer;font-family:var(--mono);">↗ 轉交</button>`;
    html += `</div>`;
    html += `<div style="display:flex;gap:6px;margin-top:6px;">`;
    html += `<button data-action="escalateDecision" data-id="${dec.id}" style="flex:1;padding:6px;background:var(--red);color:#fff;border:none;border-radius:5px;font-size:11px;font-weight:700;cursor:pointer;font-family:var(--mono);">⬆ 升級</button>`;
    html += `<button data-action="closeDecision" data-id="${dec.id}" style="flex:1;padding:6px;background:var(--surface2);color:var(--text2);border:1px solid var(--border);border-radius:5px;font-size:11px;font-weight:700;cursor:pointer;font-family:var(--mono);">✓ 結案</button>`;
    html += `</div></div>`;
  } else {
    html += `<div style="margin-top:12px;font-size:11px;color:var(--text2);">已裁示：${dec.status} by ${dec.decided_by || '—'}</div>`;
  }

  const modalTitle = document.getElementById('modal-title');
  const modalBody  = document.getElementById('modal-body');
  const overlay    = document.getElementById('overlay');
  if (modalTitle) modalTitle.textContent = '⚖ 裁示事項';
  if (modalBody)  modalBody.innerHTML = html;
  if (overlay)    overlay.className = 'show';
}

export async function submitDecision(action) {
  if (!_currentDecisionId) return;
  const note = document.getElementById('dec-note')?.value || '';
  try {
    const resp = await authFetch(API_BASE + '/api/decisions/' + _currentDecisionId + '/decide', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ action, decided_by: _getCurrentOperator?.(), execution_note: note })
    });
    if (resp.ok) {
      _closeModal?.();
      _currentDecisionId = null;
      await _doPoll?.();
    } else {
      const err = await resp.json().catch(() => ({}));
      alert('裁示失敗：' + (err.detail || resp.status));
    }
  } catch(e) {
    alert('裁示失敗：' + e.message);
  }
}

export async function escalateDecision(decId) {
  const data = _getData?.();
  const decisions = data?.decisions?.pending || [];
  const dec = decisions.find(d => d.id === decId);
  if (!dec) return;
  try {
    const resp = await authFetch(API_BASE + '/api/decisions', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        decision_type: 'escalation',
        severity: 'critical',
        decision_title: dec.decision_title,
        impact_description: dec.impact_description + '（升級）',
        suggested_action_a: dec.suggested_action_a,
        created_by: '指揮官',
        parent_decision_id: decId,
        primary_event_id: dec.primary_event_id,
      })
    });
    if (resp.ok) { _closeModal?.(); await _doPoll?.(); }
  } catch(e) { alert('升級失敗：' + e.message); }
}

export async function closeDecision(decId) {
  try {
    const resp = await authFetch(API_BASE + '/api/decisions/' + decId + '/decide', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ action: 'completed', decided_by: _getCurrentOperator?.(), execution_note: '結案' })
    });
    if (resp.ok) { _closeModal?.(); await _doPoll?.(); }
  } catch(e) { alert('結案失敗：' + e.message); }
}

/** 列表輔助（供 cop.js renderZoneC 使用）*/
export function renderDecisionList(decs) {
  let html = '';
  decs.forEach(dec => {
    const age = _decisionAge(dec.created_at);
    const sevC = dec.severity==='critical'?'var(--red)':'var(--yellow)';
    const statusLabel = dec.status==='pending'?'待裁示':'已裁示：'+dec.status;
    html += `<div style="padding:6px 8px;margin-bottom:4px;background:var(--surface2);border-radius:5px;border-left:3px solid ${sevC};cursor:pointer;" data-action="showDecisionModal" data-id="${dec.id}">`;
    html += `<div style="display:flex;justify-content:space-between;">`;
    html += `<span style="font-size:11px;font-weight:700;">${_truncate(dec.decision_title, 30)}</span>`;
    html += `<span style="font-size:9px;color:var(--text3);">${statusLabel}</span>`;
    html += `</div>`;
    html += `<div style="font-size:9px;color:var(--text3);margin-top:2px;">${dec.decision_type} · ${age}</div>`;
    html += `</div>`;
  });
  return html;
}
