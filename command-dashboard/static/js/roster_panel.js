/**
 * roster_panel.js — 右欄「隊伍」名冊（#269 / #267 切片 1）
 *
 * v1 **純前端**：讀 cop_stream 的 TAK 單位，按 team_color（CoT `<__group name>`）分組顯示連線單位，
 * 紅圈計數＝離線數。納編/定址/編組動作的後端（enrollment 表、定向發送）分批接 —— 本版動作列為
 * disabled 佔位，僅呈現 UX 形狀（見 #267 group 三用途：歸屬/定址/編組）。
 *
 * 資料來源由 main.js 注入（getTakUnits），不直接依賴 map/cop_stream 模組。自帶輕量 timer 週期重繪
 * （TAK 單位走 WS、~3s 刷新足夠即時；隱藏時只更新 badge、不費 DOM）。
 */

import { affiliationFromCot } from './map/mil_symbol.js';

let _getTakUnits = () => [];
let _hasActiveExercise = () => false;
let _onEnroll = null; // #267：(uid, 'enroll'|'unenroll') => Promise；main.js 注入（指揮層才有）
let _timer = null;
let _clickBound = false;

// 隊伍名冊＝**我方人員/單位**（友軍）。敵性/中立/不明是「觀測到的接觸」（感測層、map 上），
// 非隊伍成員，不列入（例：操作員觀測到的敵情 marker a-h-* 不該出現在我方隊伍名冊）。
function _isRosterUnit(e) {
  return affiliationFromCot(e && e.type) === 'friendly';
}

function _el(id) { return document.getElementById(id); }

function _esc(s) {
  const d = document.createElement('div');
  d.textContent = s == null ? '' : String(s);
  return d.innerHTML;
}

const UNGROUPED = '未分組';

// 在線＝stale 未過（與地圖 _isAging 同一套；#161 TAK 原生對齊）。離線的 TAK 單位由後端
// 既有機制即時移出 list（cop_stream resync 隨之移除）→ 名冊只會列到還在的單位，毋須額外心跳判定。
function _isOnline(e, nowIso) {
  return !!(e && e.stale && e.stale > (nowIso || new Date().toISOString()));
}

/**
 * 名冊純資料模型（可單測，無 DOM）：先濾友軍（敵情接觸不入名冊），再按 team_color 分組
 * （未分組排最後）、算在線/離線。
 * @returns {{ groups: Array<[string, object[]]>, online: number, offline: number }}
 */
export function rosterModel(units, nowIso) {
  const now = nowIso || new Date().toISOString();
  const roster = (units || []).filter(_isRosterUnit); // 只列友軍
  const map = new Map();
  let online = 0;
  for (const e of roster) {
    const team = (e.team_color && String(e.team_color).trim()) || UNGROUPED;
    if (!map.has(team)) map.set(team, []);
    map.get(team).push(e);
    if (_isOnline(e, now)) online += 1;
  }
  const groups = [...map.entries()].sort((a, b) => {
    if (a[0] === UNGROUPED) return 1;
    if (b[0] === UNGROUPED) return -1;
    return a[0].localeCompare(b[0]);
  });
  return { groups, online, offline: roster.length - online };
}

// team 名 → 顯示色票（已知 ATAK 色名；未知用中性灰）。純顯示，不涉邏輯。
const _TEAM_HEX = {
  Cyan: '#1D9E75', Teal: '#1D9E75', Green: '#639922', Red: '#E24B4A',
  Blue: '#378ADD', 'Dark Blue': '#185FA5', Orange: '#BA7517', Yellow: '#EF9F27',
  Magenta: '#D4537E', Purple: '#7F77DD', Maroon: '#993536', Brown: '#854F0B', White: '#888780',
};
function _teamColor(team) { return _TEAM_HEX[team] || 'var(--text3)'; }

/** 渲染名冊到 #roster-body + 更新 tab 紅圈（離線數）。隱藏時也會更新 badge（cheap）。 */
export function renderRoster() {
  const now = new Date().toISOString();
  const { groups, offline } = rosterModel(_getTakUnits(), now); // rosterModel 內已濾友軍

  // badge：離線數（「未編人數」要等 enrollment 後端，#269 step6 先用 offline 佔位）
  const badge = _el('roster-tab-badge');
  if (badge) {
    badge.textContent = offline > 0 ? String(offline) : '';
    badge.style.display = offline > 0 ? 'inline-block' : 'none';
  }

  const body = _el('roster-body');
  if (!body) return;
  if (!groups.length) {
    body.innerHTML = '<div style="color:var(--text3);font-size:11px;padding:8px;">尚無友軍單位</div>';
    return;
  }

  // #267：有 active 演習時，NULL 單位＝常駐候選（疊看送進來的）→ 加「常駐」標記以與在場單位區分；
  // 無 active 演習時所有單位都是常駐、無對照 → 不標（徒增雜訊）。active 狀態由 main.js 注入
  // （不能只看「清單有無在場單位」—— 演習剛開、尚無人在場時會誤判）。
  const markStanding = _hasActiveExercise();

  let html = '';
  for (const [team, list] of groups) {
    const online = list.filter((e) => _isOnline(e, now)).length;
    html += '<div style="display:flex;align-items:center;gap:6px;padding:5px 6px;background:var(--surface2);border-radius:3px;margin:3px 0 1px;">'
      + '<span style="width:9px;height:9px;border-radius:2px;flex-shrink:0;background:' + _teamColor(team) + ';"></span>'
      + '<span style="font-weight:600;color:var(--text);flex:1;">' + _esc(team) + '</span>'
      + '<span style="color:var(--text3);font-size:9px;">' + online + ' / ' + list.length + '</span>'
      + '</div>';
    for (const e of list) {
      const on = _isOnline(e, now);
      const meta = [_esc(e.role || ''), e.battery != null ? e.battery + '%' : ''].filter(Boolean).join(' · ');
      // #267：演習中——有納編能力（指揮層）→ 常駐單位給「納編」鈕、在場單位給「退編」鈕；
      // 無能力（onEnroll 缺）則退回純「常駐」標。無 active 演習時不顯（皆常駐、無對照）。
      const _bs = 'font-size:8px;border:1px solid var(--border);border-radius:3px;padding:0 4px;flex-shrink:0;';
      let actionEl = '';
      if (markStanding) {
        if (_onEnroll) {
          actionEl = e.exercise_id == null
            ? `<span data-roster-enroll="enroll" data-uid="${_esc(e.uid)}" style="${_bs}color:var(--green);cursor:pointer;">納編</span>`
            : `<span data-roster-enroll="unenroll" data-uid="${_esc(e.uid)}" style="${_bs}color:var(--text3);cursor:pointer;">退編</span>`;
        } else if (e.exercise_id == null) {
          actionEl = `<span style="${_bs}color:var(--text3);">常駐</span>`;
        }
      }
      html += '<div style="display:flex;align-items:center;gap:7px;padding:4px 6px 4px 14px;border-bottom:1px solid var(--border);">'
        + '<span style="width:7px;height:7px;border-radius:50%;flex-shrink:0;background:' + (on ? 'var(--green)' : 'var(--text3)') + ';"></span>'
        + '<span style="flex:1;min-width:0;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + _esc(e.callsign || e.uid) + '</span>'
        + actionEl
        + '<span style="color:var(--text3);font-size:9px;flex-shrink:0;">' + meta + '</span>'
        + '</div>';
    }
  }
  // 動作列佔位（#267 group 三用途之 定址）—— 發訊息/分享標記後端分批接；納編/退編已上線（per-unit 鈕）。
  html += '<div style="display:flex;gap:4px;flex-wrap:wrap;padding:8px 6px;color:var(--text3);font-size:9px;border-top:1px solid var(--border);margin-top:4px;">'
    + '<span style="opacity:.5;">發訊息｜分享標記（後端接入中）</span>'
    + '</div>';
  body.innerHTML = html;
}

/** #267 納編/退編 鈕的委派點擊（綁在 #roster-body，innerHTML 重繪不掉）。 */
function _onBodyClick(ev) {
  const btn = ev.target.closest && ev.target.closest('[data-roster-enroll]');
  if (!btn || !_onEnroll) return;
  const uid = btn.getAttribute('data-uid');
  const action = btn.getAttribute('data-roster-enroll');
  if (!uid) return;
  btn.style.opacity = '0.4'; // 即時回饋（WS create/delete 回來後重繪定案）
  Promise.resolve(_onEnroll(uid, action)).then(renderRoster).catch(() => renderRoster());
}

/** main.js 注入 TAK 單位來源 + 納編 callback + 啟動週期重繪 + 綁 tab 切換即時重繪。 */
export function initRoster({ getTakUnits, getHasActiveExercise, onEnroll } = {}) {
  if (typeof getTakUnits === 'function') _getTakUnits = getTakUnits;
  if (typeof getHasActiveExercise === 'function') _hasActiveExercise = getHasActiveExercise;
  if (typeof onEnroll === 'function') _onEnroll = onEnroll;
  document.addEventListener('right-tab:switched', (e) => {
    if (e?.detail?.tab === 'roster') renderRoster();
  });
  if (!_clickBound) {
    _el('roster-body')?.addEventListener('click', _onBodyClick);
    _clickBound = true;
  }
  if (_timer) clearInterval(_timer);
  _timer = setInterval(renderRoster, 3000);
  renderRoster();
}

/** 停止週期重繪（logout / lock 清理，防 timer 殘留）。main.js onAuthChange 呼叫。 */
export function stopRoster() {
  if (_timer) {
    clearInterval(_timer);
    _timer = null;
  }
}
