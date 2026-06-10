// aar_main.js — AAR 回放頁進入點（P2-20(B) B1，issue #201）
//
// Step mode：側欄事件流列表（40%）→ 點任一筆 / ↑↓ 鍵 → T 跳該筆 → 地圖（60%）
// 折疊 tracks 到 ≤T 畫單位位置。唯讀；登入態沿 dashboard 同分頁 sessionStorage
// （新分頁拿不到 token = 設計如此，見 #201 雷 1）。
//
// 安全：資料門在 server side（/timeline = COMMAND_ROLES）；本頁對 401/403 只做引導。
// 渲染一律 textContent / createElement，不以 innerHTML 塞任何 API 資料（chat 內文等）。

import { authFetch, getToken } from '../auth.js';
import { initAarMap, setPositions, fitToPositions } from './aar_map.js';
import {
  buildReplayIndex, foldPositionsAt, stepSummary, fmtClock, TYPE_LABELS,
  stepIndexAtOrBefore,
} from './replay_engine.js';

const el = id => document.getElementById(id);

let _idx = { steps: [], trackIdx: [] };
let _cur = -1; // 目前 step（-1 = 尚未選）
let _exid = null; // 目前回放的 exercise_id（bookmark POST 用）

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

function _gotoStep(i) {
  if (i < 0 || i >= _idx.steps.length) return;
  _cur = i;
  const it = _idx.steps[i];
  // 高亮目前列 + 捲到可視
  el('aar-steps').querySelectorAll('.aar-step.active').forEach(n => n.classList.remove('active'));
  const li = el('aar-steps').querySelector(`[data-i="${i}"]`);
  if (li) {
    li.classList.add('active');
    li.scrollIntoView({ block: 'nearest' });
  }
  // 折疊到該筆的 T → 上圖
  setPositions(foldPositionsAt(_idx.trackIdx, it.t));
  _setStatus(`T = ${fmtClock(it.t)}（第 ${i + 1}/${_idx.steps.length} 筆・${TYPE_LABELS[it.type] || it.type}）`);
  const btn = el('aar-bookmark-btn');
  if (btn) {
    btn.disabled = false;
    btn.title = '在目前 T 打課程標記（P2-21）';
  }
}

// ── 課程標記 bookmark（P2-21 #204：ref_t = 目前 step 的 T）─────────────────

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
      chip.addEventListener('click', () => _gotoStep(stepIndexAtOrBefore(_idx.steps, e.ref_t)));
      box.appendChild(chip);
    });
}

async function _addBookmark() {
  if (_cur < 0 || !_exid) return;
  const it = _idx.steps[_cur];
  // prompt 取備註（CSP 安全、零依賴）；取消 → 不送
  const content = window.prompt('課程標記備註：', stepSummary(it));
  if (content === null) return;
  const r = await authFetch(`/api/exercises/${encodeURIComponent(_exid)}/aar`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ category: 'bookmark', content, ref_t: it.t }),
  });
  if (!r.ok) {
    _setStatus(`標記失敗（HTTP ${r.status}）`);
    return;
  }
  _setStatus(`✓ 已標記 ${fmtClock(it.t)}`);
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
  // 開場：視野收到「全部事件折疊完」的單位範圍，但 T 停在第一筆前（未選）
  fitToPositions(foldPositionsAt(_idx.trackIdx, _idx.steps[_idx.steps.length - 1].t));
  _setStatus(`共 ${_idx.steps.length} 筆（${fmtClock(data.meta.t_start)} ~ ${fmtClock(data.meta.t_end)}）— 點選任一筆開始`);
  await _refreshBookmarks(); // 既有課程標記 chips（點擊跳該時點）
}

async function main() {
  initAarMap('aar-map');
  _wireKeys();
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
