// aar_main.js — AAR 回放頁進入點（P2-20(B)，issue #201）
//
// B1：Step mode——側欄事件流列表（40%）點選/↑↓ → T 跳該筆 → 地圖（60%）折疊 tracks ≤T。
// B2：Play mode——虛擬時鐘（rAF + wall-delta×速率，背景節流回前景自動跳補）、底部
//     slider scrub（scrub/手動跳步自動暫停）、軌跡尾跡（過去 N 分鐘 LineString）。
//     前進播放走增量折疊游標 O(Δ)；倒退 reset 全折疊（#199 定案不做 keyframe）。
//
// 安全：資料門在 server side（/timeline = COMMAND_ROLES）；渲染一律 textContent /
// createElement，不以 innerHTML 塞任何 API 資料。登入態沿 dashboard 同分頁 sessionStorage。

import { authFetch, getToken } from '../auth.js';
import { initAarMap, setPositions, setTrails, fitToPositions } from './aar_map.js';
import {
  buildReplayIndex, foldPositionsAt, stepSummary, fmtClock, TYPE_LABELS,
  stepIndexAtOrBefore, tToMs, msToT, advanceClock, makeFoldCursor, advanceFold,
  trailGeoJSON,
} from './replay_engine.js';

const el = id => document.getElementById(id);
const TRAIL_WINDOW_MIN = 10; // 尾跡窗口（分）

let _idx = { steps: [], trackIdx: [], tracksByUid: new Map() };
let _cur = -1; // 目前高亮 step（-1 = 尚未選）
let _exid = null; // 目前回放的 exercise_id（bookmark POST 用）

// ── B2 播放狀態 ─────────────────────────────────────────────────────────────
let _curT = null; // 目前虛擬時刻（ISO Z；null = 尚未定位）
let _t0 = 0; // 時間軸起訖（epoch ms）
let _t1 = 0;
let _playMs = 0; // #1：播放時鐘全精度累進器（epoch ms）。不可從 _curT 讀回——_curT 經 msToT 砍到整秒，
//                 每幀的次秒進度會被丟 → 永遠跨不過當前秒、播放卡死。故獨立累進、只在顯示時砍秒。
let _playing = false;
let _speed = 1;
let _rafId = null;
let _lastWall = 0;
let _foldCursor = makeFoldCursor();

function _setStatus(text) {
  el('aar-status').textContent = text;
}

/** 全頁訊息態（未登入 / 403 / 無資料）：清單區只放一行說明 + 返回連結照常可用 */
function _showMessage(text) {
  const list = el('aar-steps');
  list.replaceChildren();
  const li = document.createElement('li');
  li.className = 'aar-msg';
  li.textContent = text;
  list.appendChild(li);
  _setStatus('—');
}

function _renderList() {
  const list = el('aar-steps');
  list.replaceChildren();
  _idx.steps.forEach((it, i) => {
    const li = document.createElement('li');
    li.className = `aar-step t-${it.type}`;
    li.dataset.i = String(i);
    const time = document.createElement('span');
    time.className = 'aar-t';
    time.textContent = fmtClock(it.t);
    const tag = document.createElement('span');
    tag.className = 'aar-tag';
    tag.textContent = TYPE_LABELS[it.type] || it.type;
    const body = document.createElement('span');
    body.className = 'aar-sum';
    body.textContent = stepSummary(it);
    li.append(time, tag, body);
    li.addEventListener('click', () => _gotoStep(i));
    list.appendChild(li);
  });
}

/** 側欄高亮第 i 筆（僅 index 變化時捲動——播放中避免瘋狂 scrollIntoView） */
function _syncStepHighlight(i) {
  if (i === _cur) return;
  _cur = i;
  el('aar-steps').querySelectorAll('.aar-step.active').forEach(n => n.classList.remove('active'));
  const li = el('aar-steps').querySelector(`[data-i="${i}"]`);
  if (li) {
    li.classList.add('active');
    li.scrollIntoView({ block: 'nearest' });
  }
}

/**
 * B2 核心：把世界定位到時刻 T（折疊 + 尾跡 + slider/時鐘 + 側欄同步）。
 * 前進 → 增量游標；倒退 → reset 全折疊。
 */
function _setT(isoT) {
  if (Number.isNaN(tToMs(isoT))) return; // legacy 垃圾 ref_t 等非法 T → 忽略（review 防禦）
  if (_curT !== null && isoT < _curT) _foldCursor = makeFoldCursor(); // 倒退 reset
  _curT = isoT;
  setPositions(advanceFold(_idx.trackIdx, _foldCursor, isoT));
  setTrails(trailGeoJSON(_idx.tracksByUid, isoT, TRAIL_WINDOW_MIN));
  const ms = tToMs(isoT);
  const slider = el('aar-slider');
  if (slider) slider.value = String(ms);
  el('aar-clock').textContent = fmtClock(isoT);
  if (_idx.steps.length) _syncStepHighlight(stepIndexAtOrBefore(_idx.steps, isoT));
  const btn = el('aar-bookmark-btn');
  if (btn) {
    btn.disabled = false;
    btn.title = '在目前 T 打課程標記（P2-21）';
  }
}

function _gotoStep(i) {
  if (i < 0 || i >= _idx.steps.length) return;
  _pause(); // 手動跳步 = 使用者要看細節 → 自動暫停（播放器直覺）
  const it = _idx.steps[i];
  _setT(it.t);
  _setStatus(`T = ${fmtClock(it.t)}（第 ${i + 1}/${_idx.steps.length} 筆・${TYPE_LABELS[it.type] || it.type}）`);
}

// ── B2 虛擬時鐘 ─────────────────────────────────────────────────────────────

function _tick(now) {
  if (!_playing) return;
  // delta 上限 1s：分頁隱藏時 rAF 凍結（dogfood 實測 hidden → 0 回呼），回前景單幀
  // delta 會是整段隱藏時長 → 不 cap 會瞬間跳補（4x 下離開 5 分鐘 = 跳 20 分鐘）。
  // cap 後回前景從離開處繼續，符合「回放暫停在你離開的地方」直覺。
  const delta = Math.min(now - _lastWall, 1000);
  _lastWall = now;
  const { tMs, ended } = advanceClock(_playMs, delta, _speed, _t1);  // #1：以全精度 _playMs 累進，非 _curT 讀回
  _playMs = tMs;
  _setT(msToT(tMs));
  _setStatus(`▶ 播放中 ${_speed}x — T = ${fmtClock(_curT)}`);
  if (ended) {
    _pause();
    _setStatus(`⏹ 回放結束（T = ${fmtClock(_curT)}）`);
    return;
  }
  _rafId = requestAnimationFrame(_tick);
}

function _play() {
  if (_playing || !_idx.steps.length) return;
  if (_curT === null || tToMs(_curT) >= _t1) _curT = msToT(_t0); // 未定位/已到底 → 從頭
  _playMs = tToMs(_curT); // #1：播放從目前顯示位置起，之後在 _tick 全精度累進（不再經 _curT 讀回）
  _playing = true;
  el('aar-play-btn').textContent = '⏸';
  _lastWall = performance.now();
  _rafId = requestAnimationFrame(_tick);
}

function _pause() {
  if (!_playing) return;
  _playing = false;
  el('aar-play-btn').textContent = '▶';
  if (_rafId) cancelAnimationFrame(_rafId);
  _rafId = null;
}

function _wirePlaybar() {
  el('aar-play-btn')?.addEventListener('click', () => (_playing ? _pause() : _play()));
  el('aar-speed')?.addEventListener('change', (e) => {
    _speed = Number(e.target.value) || 1;
  });
  el('aar-slider')?.addEventListener('input', (e) => {
    _pause(); // scrub 自動暫停（拖到一半不被時鐘拉走）
    _setT(msToT(Number(e.target.value)));
    _setStatus(`T = ${fmtClock(_curT)}（slider）`);
  });
}

// ── 課程標記 bookmark（P2-21 #204：ref_t = 目前 T）──────────────────────────

async function _refreshBookmarks() {
  const box = el('aar-bookmarks');
  if (!box || !_exid) return;
  const r = await authFetch(`/api/exercises/${encodeURIComponent(_exid)}/aar`);
  if (!r.ok) return; // 列表失敗不擋回放（bookmark 為輔助）
  const entries = await r.json();
  box.replaceChildren();
  (Array.isArray(entries) ? entries : [])
    .filter(e => e.category === 'bookmark' && e.ref_t)
    .forEach((e) => {
      const chip = document.createElement('span');
      chip.className = 'aar-bm';
      chip.textContent = `🔖 ${fmtClock(e.ref_t)} ${e.content || ''}`;
      chip.title = `跳到 ${e.ref_t}（${e.created_by || ''}）`;
      chip.addEventListener('click', () => {
        _pause();
        _setT(e.ref_t); // 直接定位到標記時點（不必貼齊 step——B2 任意 T 都能折疊）
        _setStatus(`T = ${fmtClock(e.ref_t)}（書籤）`);
      });
      box.appendChild(chip);
    });
}

async function _addBookmark() {
  if (_curT === null || !_exid) return;
  // B2：用目前虛擬 T（Play/scrub 中也準），備註預設為 ≤T 最近一筆事件摘要
  const near = _idx.steps[stepIndexAtOrBefore(_idx.steps, _curT)];
  const content = window.prompt('課程標記備註：', near ? stepSummary(near) : '');
  if (content === null) return;
  const r = await authFetch(`/api/exercises/${encodeURIComponent(_exid)}/aar`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ category: 'bookmark', content, ref_t: _curT }),
  });
  if (!r.ok) {
    _setStatus(`標記失敗（HTTP ${r.status}）`);
    return;
  }
  _setStatus(`✓ 已標記 ${fmtClock(_curT)}`);
  await _refreshBookmarks();
}

function _wireKeys() {
  document.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowRight') {
      e.preventDefault();
      _gotoStep(_cur + 1);
    } else if (e.key === 'ArrowUp' || e.key === 'ArrowLeft') {
      e.preventDefault();
      _gotoStep(_cur - 1);
    } else if (e.key === ' ') {
      e.preventDefault(); // 空白鍵 = 播放/暫停（播放器慣例）
      if (_playing) _pause(); else _play();
    }
  });
}

/** 無 ?exercise_id= → 列出場次供選（COMMAND_ROLES 才拉得到 timeline，list 本身 READ_ROLES）。 */
async function _renderPicker() {
  const r = await authFetch('/api/exercises');
  if (!r.ok) {
    _showMessage(`無法取得演習清單（HTTP ${r.status}）`);
    return;
  }
  const exercises = await r.json();
  if (!Array.isArray(exercises) || exercises.length === 0) {
    _showMessage('沒有任何演習場次');
    return;
  }
  const list = el('aar-steps');
  list.replaceChildren();
  exercises.forEach((ex) => {
    const li = document.createElement('li');
    li.className = 'aar-step';
    const a = document.createElement('a');
    a.href = `?exercise_id=${encodeURIComponent(ex.id)}`;
    a.textContent = `#${ex.id} ${ex.name || ''}（${ex.status || ''}）`;
    li.appendChild(a);
    list.appendChild(li);
  });
  _setStatus('選擇要回放的場次');
}

async function _loadTimeline(exid) {
  _exid = exid;
  _setStatus('載入時間軸…');
  const r = await authFetch(`/api/exercises/${encodeURIComponent(exid)}/timeline`);
  if (r.status === 403) {
    _showMessage('需要指揮層權限（commander / sysadmin）才能回放 AAR。');
    return;
  }
  if (r.status === 404) {
    _showMessage(`演習 #${exid} 不存在`);
    return;
  }
  if (!r.ok) {
    _showMessage(`時間軸載入失敗（HTTP ${r.status}）`);
    return;
  }
  const data = await r.json();
  _idx = buildReplayIndex(data.items);
  el('aar-title').textContent = `AAR 回放 — 演習 #${exid}`;
  if (data.meta?.truncated) {
    el('aar-truncated').style.display = '';
  }
  if (_idx.steps.length === 0) {
    _showMessage('這場演習沒有時間軸資料');
    return;
  }
  _renderList();
  // B2 播放列：slider domain = [t_start, t_end] epoch ms（meta 缺欄時從 steps 兜底——契約防禦）
  const meta = data.meta || {};
  _t0 = tToMs(meta.t_start ?? _idx.steps[0].t);
  _t1 = tToMs(meta.t_end ?? _idx.steps[_idx.steps.length - 1].t);
  const slider = el('aar-slider');
  slider.min = String(_t0);
  slider.max = String(_t1);
  slider.value = String(_t0);
  el('aar-playbar').hidden = false;
  // 開場：視野收到「全部事件折疊完」的單位範圍，但 T 停在第一筆前（未選）
  fitToPositions(foldPositionsAt(_idx.trackIdx, _idx.steps[_idx.steps.length - 1].t));
  _setStatus(`共 ${_idx.steps.length} 筆（${fmtClock(msToT(_t0))} ~ ${fmtClock(msToT(_t1))}）— 點選任一筆或按 ▶ 開始`);
  await _refreshBookmarks(); // 既有課程標記 chips（點擊跳該時點）
}

async function main() {
  initAarMap('aar-map');
  _wireKeys();
  _wirePlaybar();
  el('aar-bookmark-btn')?.addEventListener('click', _addBookmark);
  if (!getToken()) {
    _showMessage('未登入——請從指揮台（同分頁）進入本頁。');
    return;
  }
  const exid = new URLSearchParams(location.search).get('exercise_id');
  if (!exid) {
    await _renderPicker();
    return;
  }
  await _loadTimeline(exid);
}

main();
