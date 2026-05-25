/**
 * cop.js — COP 指揮情境圖協調器（C1-F CSP 模組化，v3.0.0）
 *
 * 職責：
 *   - poll() / refresh() 主迴圈
 *   - renderZoneA（量能摘要 + 全局狀態燈）
 *   - renderZoneC（右側面板：裁示 + 事件追蹤）
 *   - _renderConnDots（連線燈號）
 *   - _renderZoneANoSnap（無快照 fallback）
 *   - openModal / closeModal（通用 modal）
 *   - showIpiBreakdown / _openZoneByType（IPI 分解 + 節點鑽探）
 *   - switchLeftPanel / switchDecTab（左側面板 / 裁示切換）
 *   - buildSlice → openModal（切面快照 modal）
 *
 * 可 import：ws.js / charts.js / decisions.js / events.js / map.js / auth.js
 * 不可 import：ai.js / ttx.js（stubs）
 *
 * 跨模組通訊（向下接收 CustomEvent）：
 *   cop:pollNow      → 立即執行 poll()
 *   cop:refresh      → 立即執行 refresh()（不拉 API）
 */

import { authFetch, isPollActive, setPollActive, getSessionType } from './ws.js';
import {
  getSeries, setDashData, renderSparklines, setPill, buildSliceHtml,
  getExpandedSpark, rate, minsUntil,
} from './charts.js';
import { initDecisions, renderDecisionList } from './decisions.js';
import {
  initEvents, renderZoneC as evRenderZoneC,
  _evtCardDidHighlight, openEventForm,
  showZoneDetail,
} from './events.js';
import {
  initMap, renderMapOverlay, refreshLeafletMarkers,
  getMapConfig, findZoneByEventId, saveMapConfig,
} from './map.js';
import { getCurrentOperator, closeSettings, getAdminPin, closeAdminPanel } from './auth.js';

const API_BASE = location.origin;

// ══════════════════════════════════════════════════════════════
// 狀態（cop.js 擁有 _data；其他模組透過 getData() 回呼讀取）
// ══════════════════════════════════════════════════════════════
let _data = null;
let _decTab = 'pending';

/** 外部（events.js / map.js）透過 getData() 取得最新 dashboard 資料 */
export function getData() { return _data; }

// ══════════════════════════════════════════════════════════════
// 通用 Modal
// ══════════════════════════════════════════════════════════════

export function openModal(title, bodyHtml, footerHtml = '') {
  const t = document.getElementById('modal-title');
  const b = document.getElementById('modal-body');
  const f = document.getElementById('modal-footer');
  const o = document.getElementById('overlay');
  if (t) t.innerHTML = title;
  if (b) b.innerHTML = bodyHtml;
  if (f) f.innerHTML = footerHtml;
  if (o) o.className = 'show';
}

export function closeModal() {
  const o = document.getElementById('overlay');
  if (o) o.className = '';
  sessionStorage.removeItem('_openEventId');
  sessionStorage.removeItem('_openZoneModal');
}

// ══════════════════════════════════════════════════════════════
// 自訂確認 modal（取代瀏覽器 confirm）
// ══════════════════════════════════════════════════════════════
let _confirmCb = null;

export function appConfirm(title, msg) {
  return new Promise(resolve => {
    _confirmCb = resolve;
    const ct = document.getElementById('confirm-title');
    const cm = document.getElementById('confirm-msg');
    const co = document.getElementById('confirm-overlay');
    if (ct) ct.textContent = title;
    if (cm) cm.textContent = msg;
    if (co) co.style.display = 'flex';
  });
}

export function confirmResolve(val) {
  const co = document.getElementById('confirm-overlay');
  if (co) co.style.display = 'none';
  if (_confirmCb) { _confirmCb(val); _confirmCb = null; }
}

// ══════════════════════════════════════════════════════════════
// 重設指揮部資料庫（設定面板：重設 DB）
// ══════════════════════════════════════════════════════════════

export async function confirmResetDB() {
  // 此 action 從 admin 面板系統分頁觸發，admPin 已在登入面板時快取
  // 若從別處觸發（無快取）再 fallback 到 prompt
  const cachedPin = getAdminPin();
  closeAdminPanel();
  closeSettings();

  const ok = await appConfirm(
    '重設指揮部資料庫',
    '將清除所有快照、事件、裁示、Pi 批次資料。\n帳號和 Pi 節點註冊不受影響。\n\n此操作無法復原，確定繼續？'
  );
  if (!ok) return;

  const pin = cachedPin || prompt('請輸入管理員 PIN 確認：');
  if (!pin) return;

  try {
    const resp = await authFetch(API_BASE + '/api/admin/reset-db', {
      method: 'POST',
      headers: { 'X-Admin-PIN': pin },
    });
    if (!resp.ok) {
      let detail = resp.status;
      try { detail = (await resp.json()).detail || resp.status; } catch (e) { /* ignore */ }
      alert('重設失敗：' + detail);
      return;
    }
    // 同步清除地圖上所有事件 marker（保留永久 pin 據點）
    const mapCfg = getMapConfig();
    if (mapCfg) {
      for (const m of Object.values(mapCfg.maps || {})) {
        m.zones = (m.zones || []).filter(z => z.icon === 'pin');
        m.flows = [];
      }
      await saveMapConfig();
    }
    alert('資料庫已重設，頁面將重新整理。');
    location.reload();
  } catch (e) {
    alert('錯誤：' + e.message);
  }
}

// ══════════════════════════════════════════════════════════════
// IPI 分解 + 節點鑽探
// ══════════════════════════════════════════════════════════════

export async function showIpiBreakdown() {
  let allIncs = [];
  for (const unitId of ['shelter', 'medical']) {
    try {
      const resp = await authFetch(API_BASE + `/api/pi-data/${unitId}/list`);
      if (!resp.ok) continue;
      const data = await resp.json();
      if (data.offline) continue;
      const incs = (data.grouped?.incidents || []).map(r => ({ ...r.record, _unit: unitId }));
      allIncs = allIncs.concat(incs);
    } catch (e) { /* 忽略個別 Pi 離線 */ }
  }
  const open = allIncs.filter(i => i.status !== '已結案' && i.status !== 'closed');
  const incLabels = {
    security_threat: '安全威脅', infectious_risk: '傳染疑慮', resource_shortage: '物資短缺',
    capacity_overload: '量能超載', medication_mgmt: '藥品管理', language_assist: '語言協助',
    noise_disturbance: '災民喧嘩', other: '其他',
  };
  const unitLabels = { shelter: '收容組', medical: '醫療組' };
  const sevDot = s => s === '高' ? '🔴' : s === '中' ? '🟡' : '🟢';

  let html = '<div style="font-size:11px;">';
  if (open.length === 0) {
    html += '<div style="color:var(--text3);text-align:center;padding:20px;">無未結組內事件</div>';
  } else {
    html += `<div style="color:var(--text3);margin-bottom:8px;">未結組內事件（${open.length} 件），IPI = 高×3 + 中×1</div>`;
    for (const i of open) {
      const type = incLabels[i.type] || i.type || '?';
      const unit = unitLabels[i._unit] || i._unit || '?';
      const linked = (i.linked_persons || []).join(', ');
      html += `<div style="padding:6px 8px;margin:3px 0;background:var(--surface);border-radius:3px;display:flex;justify-content:space-between;align-items:center;">`;
      html += `<div><span>${sevDot(i.severity)}</span> <b>${type}</b> <span style="color:var(--text3);font-size:10px;">${unit}</span>`;
      if (linked) html += `<div style="font-size:10px;color:var(--text3);margin-top:2px;">關聯：${linked}</div>`;
      html += '</div>';
      html += `<span style="font-size:10px;color:var(--text3);">${i.severity || ''}</span>`;
      html += '</div>';
    }
  }
  html += '</div>';
  openModal('組內事件壓力（IPI）', html);
}

export function openZoneByType(nodeType) {
  const mapCfg = getMapConfig();
  let zone = null;
  if (mapCfg) {
    for (const mapKey of Object.keys(mapCfg.maps || {})) {
      const found = (mapCfg.maps[mapKey].zones || []).find(z => z.node_type === nodeType && !z.event_id);
      if (found) { zone = found; break; }
    }
  }
  if (!zone) zone = {
    id: 'virtual_' + nodeType,
    label: nodeType === 'shelter' ? '收容組' : '醫療組',
    node_type: nodeType, icon: 'pin',
  };
  showZoneDetail(zone);
}

// ══════════════════════════════════════════════════════════════
// 切換左側面板群組 / 裁示頁籤
// ══════════════════════════════════════════════════════════════

export function switchLeftPanel(group) {
  sessionStorage.setItem('_leftPanelGroup', group);
  document.querySelectorAll('.left-panel-group').forEach(p => {
    p.style.display = (p.dataset.group === group) ? '' : 'none';
  });
  document.querySelectorAll('.left-tab').forEach(b => {
    b.classList.toggle('active', b.dataset.group === group);
  });
  setTimeout(() => {
    const d = getSeries();
    if (d) renderSparklines(d, getSessionType(), openZoneByType, showIpiBreakdown);
  }, 50);
}

export function switchDecTab(tab) {
  _decTab = tab;
  document.querySelectorAll('.dec-tab').forEach(b => {
    b.classList.toggle('active', b.dataset.tab === tab);
  });
  const d = getSeries();
  renderZoneC(d);
}

// ══════════════════════════════════════════════════════════════
// Poll / Refresh 主迴圈
// ══════════════════════════════════════════════════════════════

function _healthValue(value) {
  return value === null || typeof value === 'undefined' ? 'unknown' : String(value);
}

function _healthLightLevel(health) {
  if (!health || typeof health !== 'object') return 'crit';
  if (health.status === 'ok' && health.db_writable === true) return 'ok';
  if (health.db_writable === false) return 'crit';
  if (health.status === 'degraded' || health.status === 'initializing') return 'warn';
  return 'crit';
}

function _healthTooltip(health) {
  return [
    'Command health',
    `status: ${_healthValue(health?.status)}`,
    `db_writable: ${_healthValue(health?.db_writable)}`,
    `disk_free_mb: ${_healthValue(health?.disk_free_mb)}`,
    `disk_free_pct: ${_healthValue(health?.disk_free_pct)}%`,
    `db_latency_ms: ${_healthValue(health?.db_latency_ms)}ms`,
    `schema_version: ${_healthValue(health?.schema_version)}`,
    `db_path: ${_healthValue(health?.db_path)}`,
  ].join('\n');
}

// 只更新 cd-server 燈點本身（dot class + title），不動 label
// 由 _refreshCommandHealthLight 的獨立 interval 呼叫，與 dashboard 連線狀態解耦
function _setHealthDot(level, title) {
  const srv = document.getElementById('cd-server');
  if (srv) {
    srv.className = 'conn-dot ' + level;
    srv.title = title;
  }
}

// 獨立輪詢 /api/health，不需 auth、不依附 dashboard 成功路徑
// initCop() 以 setInterval 每 5s 呼叫一次
async function _refreshCommandHealthLight() {
  try {
    const resp = await fetch(API_BASE + '/api/health', { signal: AbortSignal.timeout(3000) });
    if (!resp.ok) throw new Error(resp.status);
    const health = await resp.json();
    const level = _healthLightLevel(health);
    _setHealthDot(level, _healthTooltip(health));
  } catch (e) {
    _setHealthDot(
      'crit',
      ['Command health', 'status: unavailable', `error: ${e.message || e}`].join('\n')
    );
  }
}

export async function poll() {
  if (!isPollActive()) return;
  try {
    const sessionType = getSessionType();
    const dashUrl = API_BASE + '/api/dashboard' + (sessionType === 'exercise' ? '?session_type=exercise' : '');
    const resp = await authFetch(dashUrl, { signal: AbortSignal.timeout(5000) });
    if (!resp.ok) throw new Error(resp.status);
    _data = await resp.json();
    setDashData(_data);
    // dashboard 連線成功：只更新 label，dot 由獨立 health interval 控制
    const lbl = document.getElementById('cl-server');
    if (lbl) {
      lbl.textContent = '指揮部';
      lbl.title = '指揮部後端\n● Dashboard 回應中';
    }
  } catch (e) {
    // dashboard 連線失敗：只更新 label，dot 由獨立 health interval 控制
    const lbl = document.getElementById('cl-server');
    if (lbl) {
      lbl.textContent = _data ? '指揮部 離線' : '指揮部 待連線';
      lbl.title = '指揮部後端\n● Dashboard 無回應\n' + (e.message || '');
    }
  }
  refresh();
}

export function refresh() {
  const d = getSeries();
  if (!d) {
    _renderZoneANoSnap();
  } else {
    renderZoneA(d);
    renderSparklines(d, getSessionType(), openZoneByType, showIpiBreakdown);
  }
  _renderConnDots();
  renderZoneC(d);
  renderMapOverlay();
  // 站外 Leaflet：刷新 marker 嚴重度顏色
  refreshLeafletMarkers();
}

// ══════════════════════════════════════════════════════════════
// Zone A：量能摘要（有快照版）
// ══════════════════════════════════════════════════════════════

export function renderZoneA(d) {
  const N = d.N;
  const calc = _data?.calc || {};

  // 量能狀態燈
  const sp = d.sPct[N - 1], mp = d.mPct[N - 1];
  const capMax = Math.max(sp, mp);
  const capLvl = capMax >= 90 ? 'crit' : capMax >= 70 ? 'warn' : 'ok';

  const totalIPI = d.incHigh[N - 1] * 3 + d.incMed[N - 1];
  const incLvl = totalIPI >= 6 ? 'crit' : totalIPI >= 3 ? 'warn' : 'ok';

  const ivLast = d.ivPct[N - 1], oxLast = d.oxPct[N - 1], toL = d.toPct[N - 1];
  const minSupply = Math.min(ivLast, oxLast, toL);
  const resLvl = minSupply <= 20 ? 'crit' : minSupply <= 40 ? 'warn' : 'ok';

  const levels = [capLvl, incLvl, resLvl];
  const overall = levels.includes('crit') ? 'crit' : levels.includes('warn') ? 'warn' : 'ok';
  const lamp = document.getElementById('status-lamp');
  if (lamp) {
    lamp.className = overall;
    lamp.textContent = overall === 'crit' ? '警報' : overall === 'warn' ? '注意' : '正常';
    const reasons = [];
    const lvlLabel = { ok: '正常', warn: '注意', crit: '警報' };
    if (capLvl !== 'ok') reasons.push(`量能：${lvlLabel[capLvl]}（收${sp}% 醫${mp}%）`);
    if (incLvl !== 'ok') reasons.push(`事件 IPI：${totalIPI}`);
    if (resLvl !== 'ok') reasons.push(`物資最低：${minSupply}%`);
    lamp.title = reasons.length ? reasons.join('\n') : '全局正常';
  }

  // 收容 / 醫療人數
  const sBu = d.sBu[N - 1], mBu = d.mBu[N - 1];
  const shelEl = document.getElementById('counts-shelter');
  const medEl = document.getElementById('counts-medical');
  if (shelEl) shelEl.textContent = `收容 ${sBu} 人`;
  if (medEl) medEl.textContent = `醫療 ${mBu} 人`;

  // DCI
  const dci = calc.data_confidence?.overall;
  const dciEl = document.getElementById('dci-val');
  const freshLabels = { ok: '即時', warn: '稍舊', crit: '過時', lkp: '未連線' };
  if (dciEl) {
    if (dci != null) {
      dciEl.textContent = dci;
      dciEl.style.color = dci >= 70 ? 'var(--green)' : dci >= 40 ? 'var(--yellow)' : 'var(--red)';
    }
    const chh = calc.comm_health || {};
    const dciParts = ['資料信心指數 (DCI)'];
    ['shelter', 'medical', 'forward', 'security'].forEach(k => {
      const name = { shelter: '收容', medical: '醫療', forward: '前進', security: '安全' }[k];
      const fl = chh[k]?.health_level || 'lkp';
      dciParts.push(`${name}：${freshLabels[fl] || fl}`);
    });
    const dciEl2 = document.getElementById('dci');
    if (dciEl2) dciEl2.title = dciParts.join('\n');
  }
}

// ══════════════════════════════════════════════════════════════
// Zone A：量能摘要（無快照 fallback）
// ══════════════════════════════════════════════════════════════

function _renderZoneANoSnap() {
  const allEvts = _data?.events || [];
  const openEvts = allEvts.filter(e => e.status === 'open' || e.status === 'in_progress');
  const hasCrit = openEvts.some(e => e.severity === 'critical');
  const hasWarn = openEvts.some(e => e.severity === 'warning');
  const overall = hasCrit ? 'crit' : hasWarn ? 'warn' : 'ok';
  const lamp = document.getElementById('status-lamp');
  if (lamp) {
    lamp.className = overall;
    lamp.textContent = overall === 'crit' ? '警報' : overall === 'warn' ? '注意' : '正常';
    const critEvts = openEvts.filter(e => e.severity === 'critical').length;
    const warnEvts = openEvts.filter(e => e.severity === 'warning').length;
    lamp.title = `未結事件 ${openEvts.length} 件\n緊急 ${critEvts} / 警告 ${warnEvts}`;
  }
  const shelEl = document.getElementById('counts-shelter');
  const medEl = document.getElementById('counts-medical');
  if (shelEl) shelEl.textContent = openEvts.length > 0 ? `未結事件 ${openEvts.length} 件` : '等待資料中⋯';
  if (medEl) medEl.textContent = '';
  const dciEl = document.getElementById('dci-val');
  const evtCount = allEvts.length;
  if (dciEl) {
    if (evtCount > 0) {
      dciEl.textContent = Math.min(evtCount * 5, 50);
      dciEl.style.color = 'var(--yellow)';
    } else {
      dciEl.textContent = '—';
      dciEl.style.color = 'var(--text3)';
    }
    const dciEl2 = document.getElementById('dci');
    if (dciEl2) dciEl2.title = `資料信心指數 (DCI)\n尚未收到快照\n事件記錄：${evtCount} 筆`;
  }
}

// ══════════════════════════════════════════════════════════════
// 連線燈號
// ══════════════════════════════════════════════════════════════

function _renderConnDots() {
  const ch = _data?.calc?.comm_health || {};
  const piNodes = _data?.pi_nodes || [];
  const dotMap = { shelter: 'cd-shelter', medical: 'cd-medical' };
  const lblMap = { shelter: 'cl-shelter', medical: 'cl-medical' };
  const labels = { shelter: '收容', medical: '醫療' };

  ['shelter', 'medical'].forEach(k => {
    const dot = document.getElementById(dotMap[k]);
    const lbl = document.getElementById(lblMap[k]);
    const node = piNodes.find(n => n.unit_id === k);
    let level = ch[k]?.health_level || 'lkp';
    if (node && node.last_seen_at) {
      const age = Date.now() - new Date(node.last_seen_at).getTime();
      level = age < 30000 ? 'ok' : age < 90000 ? 'warn' : 'crit';
    } else if (node && !node.last_seen_at) {
      level = 'lkp';
    }
    if (dot) dot.className = 'conn-dot ' + level;
    const tipMap = {
      ok: '● 正常（30 秒內有推送）',
      warn: '● 注意（30~90 秒無推送）',
      crit: '● 離線（超過 90 秒無推送）',
      lkp: '○ 未連線（尚未收到任何推送）',
    };
    let tip = tipMap[level] || '';
    if (node && node.last_seen_at) {
      const secsAgo = Math.round((Date.now() - new Date(node.last_seen_at).getTime()) / 1000);
      const agoStr = secsAgo < 60 ? `${secsAgo} 秒前` : `${Math.round(secsAgo / 60)} 分鐘前`;
      tip += `\n最後心跳：${agoStr}`;
      if (node.last_data_at) {
        const dataAgo = Math.round((Date.now() - new Date(node.last_data_at).getTime()) / 1000);
        const dataStr = dataAgo < 60 ? `${dataAgo} 秒前` : `${Math.round(dataAgo / 60)} 分鐘前`;
        tip += `\n最後資料：${dataStr}`;
      } else {
        tip += '\n尚無資料推送';
      }
    } else if (node) {
      tip += '\n已註冊但從未推送';
    } else {
      tip += '\n尚未在指揮部註冊 Pi 節點';
    }
    if (dot) dot.title = labels[k] + ' Pi\n' + tip;
    if (lbl) lbl.title = labels[k] + ' Pi\n' + tip;
    if (lbl) {
      const tag = level === 'lkp' ? ' 未連線' : level === 'crit' ? ' 離線' : '';
      lbl.textContent = labels[k] + tag;
    }
  });
}

// ══════════════════════════════════════════════════════════════
// Zone C：右側面板（裁示 + 事件追蹤）
// ══════════════════════════════════════════════════════════════

export function renderZoneC(d) {
  const N = d ? d.N : 0;
  const calc = _data?.calc || {};

  // 物資倒數
  const br = calc.burn_rates || {};
  const ivTTZ = br.iv?.time_to_zero_min;
  const oxTTZ = br.oxygen?.time_to_zero_min;
  const toTTZ = br.tourniquet?.time_to_zero_min;
  const minTTZ = [ivTTZ, oxTTZ, toTTZ].filter(v => v != null && v >= 0);
  const supplyMin = minTTZ.length > 0 ? Math.min(...minTTZ) : null;
  setPill('zc-supply', 'zc-supply-t', supplyMin, 120, 240);

  // 容量倒數
  const medCountdown = calc.medical?.countdown_to_red;
  let capMins = medCountdown?.minutes_to_threshold;
  if (capMins == null && d) {
    const capR = rate(d.mPct);
    capMins = capR > 0 ? minsUntil(d.mPct[N - 1], 90, capR, 5) : null;
  }
  setPill('zc-cap', 'zc-cap-t', capMins, 60, 120);

  // 人力倒數
  const staffR = d ? rate(d.opsM) : 0;
  const staffMins = (staffR > 0 && d) ? minsUntil(d.opsM[N - 1], 8, staffR, 5) : null;
  setPill('zc-staff', 'zc-staff-t', staffMins, 30, 60);

  // 升降級
  const esc = calc.escalation;
  const escEl = document.getElementById('zc-esc-list');
  if (esc && escEl) {
    let html = '';
    (esc.triggers_met || []).forEach(t => {
      const cls = t.severity === 'critical' ? 'crit' : 'warn';
      html += `<div class="zc-esc-item"><span class="tag ${cls}">${t.rule_id}</span>${t.description}</div>`;
    });
    (esc.deescalation || []).forEach(t => {
      html += `<div class="zc-esc-item"><span class="tag ok">${t.rule_id}</span>${t.description}</div>`;
    });
    if (!html) html = '<div style="color:var(--green);font-size:11px;">無觸發條件</div>';
    escEl.innerHTML = html;
  }

  // 裁示列表（委派給 decisions.js）
  const pendingDecs = _data?.decisions?.pending || [];
  const decidedDecs = _data?.decisions?.decided || [];
  const decCountEl = document.getElementById('dec-count');
  if (decCountEl) decCountEl.textContent = pendingDecs.length;

  const decListEl = document.getElementById('right-dec-list');
  if (decListEl) {
    const list = _decTab === 'pending' ? pendingDecs : decidedDecs;
    if (list.length === 0) {
      decListEl.innerHTML = _decTab === 'pending'
        ? '<div style="color:var(--green);font-size:11px;">無待裁示事項</div>'
        : '<div style="color:var(--text3);font-size:11px;">無已裁示紀錄</div>';
    } else {
      decListEl.innerHTML = renderDecisionList(list, _decTab === 'decided');
    }
  }

  // 事件列表（委派給 events.js）
  evRenderZoneC(_data, d);
}

// ══════════════════════════════════════════════════════════════
// 初始化（main.js 呼叫一次）
// ══════════════════════════════════════════════════════════════

export function initCop() {
  // 注入跨模組依賴
  initDecisions({
    getData,
    getCurrentOperator,
    closeModal,
    doPoll: poll,
  });
  initEvents({
    getData,
    getCurrentOperator,
    closeModal,
    openModal,
    doPoll: poll,
    appConfirm,
    findZoneByEventId,
    showEventProcessModal: _showEventProcessModalWrapper,
    renderZoneC,
  });
  initMap({
    getData,
    getCurrentOperator,
    doPoll: poll,
    openModal,
    closeModal,
    openEventForm,
    showEventProcessModal: _showEventProcessModalWrapper,
    showZoneDetail,
  });

  // 獨立 health 燈輪詢：/api/health 不需 auth，每 5s 更新 cd-server 燈點
  // 與 dashboard poll 解耦，即使 /api/dashboard 失敗也能正確反映後端健康狀態
  _refreshCommandHealthLight();
  setInterval(_refreshCommandHealthLight, 5000);

  // 監聽 CustomEvent 向上通知
  document.addEventListener('cop:pollNow', () => poll());
  document.addEventListener('cop:refresh', () => refresh());
  document.addEventListener('charts:expandSparkChanged', () => {
    const d = getSeries();
    if (d) renderSparklines(d, getSessionType(), openZoneByType, showIpiBreakdown);
  });
}

/**
 * 包裝函式：cop.js 協調 showEventProcessModal（events.js 匯出）
 * 此包裝讓 map.js 的 onOpenEventModal CustomEvent 也能走同一路徑
 */
function _showEventProcessModalWrapper(zone) {
  // events.js 的 showEventProcessModal 透過 initEvents 注入
  // 此處透過 CustomEvent 觸發，保持模組解耦
  document.dispatchEvent(new CustomEvent('events:showProcessModal', { detail: { zone } }));
}
