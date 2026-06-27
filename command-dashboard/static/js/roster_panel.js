// SPDX-License-Identifier: LicenseRef-Proprietary
// Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
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

// 隊伍名冊＝**編成單位（TAK 端點：裝置/人員）**，#358-2 起以 **faction 為組織主軸**（見 rosterModel）。
// 「是不是單位」用 **team_color（自報 __group）** 判定——TAK client self-SA 必帶 __group（隊色），
// 而 marker / 點位（敵情接觸、繪圖、事件）無 __group → 藉此把 marker 擋在編成外。
// 納入條件：① 有 team_color（= self-SA 端點，即使自報 a-h 敵對亦為我方裝置 → 修「藍方裝置誤報 a-h
//   不進藍軍」的困惑）；或 ② 自報友軍（a-f，保留原行為，catch 無 __group 的友軍單位）。
// 排除：marker（a-n/a-u/a-h 點位，無 team_color 且非友軍）—— 即使被分類連帶帶上 faction 也不入編成。
function _isRosterUnit(e) {
  if (!e) return false;
  if (e.team_color && String(e.team_color).trim()) return true;
  return affiliationFromCot(e.type) === 'friendly';
}

function _el(id) { return document.getElementById(id); }

// #292：完整跳脫（含引號）。原 textContent→innerHTML 技巧只跳脫 `< > &`、**不跳脫引號**，
// 而本檔用於屬性脈絡 `data-uid="${_esc(e.uid)}"` → uid（來自 CoT，可被偽造裝置控制）含 `"`
// 可突破屬性、注入事件處理器（屬性脈絡 XSS）。對齊 events.js/map.js 的完整跳脫。export 供測試。
export function _esc(s) {
  return String(s == null ? '' : s).replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]),
  );
}

// #358-2：faction 為隊伍組織主軸。key→顯示標籤 + 區塊色（藍/紅/中立/未分類）+ 排序。
const _FACTION_NONE = '_none';
const FACTION_LABEL = { blue: '藍軍', red: '紅軍', neutral: '中立', [_FACTION_NONE]: '未分類' };
const FACTION_HEX = { blue: '#378ADD', red: '#E24B4A', neutral: '#888780', [_FACTION_NONE]: 'var(--text3)' };
const FACTION_ORDER = ['blue', 'red', 'neutral', _FACTION_NONE]; // 未分類排末

// 在線＝stale 未過（與地圖 _isAging 同一套；#161 TAK 原生對齊）。離線的 TAK 單位由後端
// 既有機制即時移出 list（cop_stream resync 隨之移除）→ 名冊只會列到還在的單位，毋須額外心跳判定。
function _isOnline(e, nowIso) {
  return !!(e && e.stale && e.stale > (nowIso || new Date().toISOString()));
}

/**
 * 名冊純資料模型（可單測，無 DOM）：#358-2 起**按 faction 分組**（藍/紅/中立/未分類，未分類排末），
 * 成員 = _isRosterUnit（有 faction 或自報友軍）。team_color 降為單位層細節（render 時的小色點）。
 * commander 經 WS faction 過濾本只收自己 faction → 其 groups 自然只剩該 faction；admin 全見得紅藍兩區。
 * @returns {{ groups: Array<[string, object[]]>, online: number, offline: number }}
 */
export function rosterModel(units, nowIso) {
  const now = nowIso || new Date().toISOString();
  const roster = (units || []).filter(_isRosterUnit);
  const map = new Map();
  let online = 0;
  for (const e of roster) {
    const fac = (e.faction && String(e.faction).trim()) || _FACTION_NONE;
    if (!map.has(fac)) map.set(fac, []);
    map.get(fac).push(e);
    if (_isOnline(e, now)) online += 1;
  }
  const groups = FACTION_ORDER.filter((k) => map.has(k)).map((k) => [k, map.get(k)]);
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
  const { groups, offline } = rosterModel(_getTakUnits(), now); // rosterModel 內按 faction 分組（#358-2）

  // badge：離線數（「未編人數」要等 enrollment 後端，#269 step6 先用 offline 佔位）
  const badge = _el('roster-tab-badge');
  if (badge) {
    badge.textContent = offline > 0 ? String(offline) : '';
    badge.style.display = offline > 0 ? 'inline-block' : 'none';
  }

  const body = _el('roster-body');
  if (!body) return;
  if (!groups.length) {
    body.innerHTML = '<div style="color:var(--text3);font-size:11px;padding:8px;">尚無編成單位</div>';
    return;
  }

  // #267：有 active 演習時，NULL 單位＝常駐候選（疊看送進來的）→ 加「常駐」標記以與在場單位區分；
  // 無 active 演習時所有單位都是常駐、無對照 → 不標（徒增雜訊）。active 狀態由 main.js 注入
  // （不能只看「清單有無在場單位」—— 演習剛開、尚無人在場時會誤判）。
  const markStanding = _hasActiveExercise();

  let html = '';
  for (const [fac, list] of groups) {
    const online = list.filter((e) => _isOnline(e, now)).length;
    // #358-2：群標頭 = faction（藍軍/紅軍/中立/未分類）+ faction 區塊色。
    html += '<div style="display:flex;align-items:center;gap:6px;padding:5px 6px;background:var(--surface2);border-radius:3px;margin:3px 0 1px;">'
      + '<span style="width:9px;height:9px;border-radius:2px;flex-shrink:0;background:' + (FACTION_HEX[fac] || 'var(--text3)') + ';"></span>'
      + '<span style="font-weight:600;color:var(--text);flex:1;">' + _esc(FACTION_LABEL[fac] || fac) + '</span>'
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
      // #358-2：team_color（自報隊色）降為單位層細節——faction 群內以小方點呈現（無則不顯）。
      const teamDot = e.team_color
        ? '<span title="' + _esc(e.team_color) + '" style="width:7px;height:7px;border-radius:2px;flex-shrink:0;background:' + _teamColor(String(e.team_color).trim()) + ';"></span>'
        : '';
      html += '<div style="display:flex;align-items:center;gap:7px;padding:4px 6px 4px 14px;border-bottom:1px solid var(--border);">'
        + '<span style="width:7px;height:7px;border-radius:50%;flex-shrink:0;background:' + (on ? 'var(--green)' : 'var(--text3)') + ';"></span>'
        + teamDot
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
