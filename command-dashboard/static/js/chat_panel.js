// SPDX-License-Identifier: LicenseRef-Proprietary
// Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
/**
 * chat_panel.js — 右欄「通聯」單一流面板（#213 b1）。
 *
 * GeoChat（CoT b-t-f）入向 P2-07 只「存進 chats 表」沒「送」→ 指揮看不到現場通聯。
 * 本模組消費 GET /api/chat（READ_ROLES），把當前場通聯渲染成**單一時間流**：
 *   - 每則前掛 `[room]` 標籤；room（聊天室）清單**動態**從實際出現的 group 生成 filter chips
 *     （不寫死 全體/隊伍/O/C/DM——真實房間名待 reality check，泛型渲染避免基於假設）。
 *   - 「事件追蹤」分頁時，通聯 tab 掛**紅圈未讀數**（地圖不閃——#213 #1 裁示）。
 *   - **TAK 停用 → 通聯 tab 整個隱藏**，與 header TAK 燈同源（cop.js `_refreshTakLight` 算出
 *     `takConnState()` 後派 `tak:conn-state` 事件，本模組只聽不另判，對齊 #164「燈號不謊報」紀律）。
 *
 * 安全：message 後端已 `html.escape`（chat_service 紅線）；本層以 `decodeChatMessage` 還原
 *   那固定 5 種實體後**經 textContent 落 DOM**（純文字、無 innerHTML sink），雙重防 XSS。
 *
 * 範圍邊界（移出 b1，各自後置）：b3 marker 連結 / 欄位盤點(#193) / O/C 識別與限可見 / b2 即時 WS。
 */

import { authFetch, canSendChat, isLoggedIn } from './auth.js';

// 同 cop.js 慣例：各模組各自定義（auth.js 的 API_BASE 非 export）。typeof 守門讓純函式
// 能在無 location 的 vitest node 環境被 import（不影響瀏覽器：location 必存在）。
const API_BASE = typeof location !== 'undefined' ? location.origin : '';
// b2（#213）：即時 WS 推播（chat:new）為主要更新管道；poll 降為 safety-net——補
// WS 斷線/重連空窗的對帳（重連後一次全量 GET resync，對齊 cop_stream 紀律）。
const POLL_MS = 30000;
// 與 poll 的 GET limit 對齊：WS 增量 append 也以此為窗上限，防 poll 間隔內無界增長
// （兩路對同一窗一致；超出最舊者捨棄，poll 下輪 wholesale replace 亦同窗）。
const LIVE_CAP = 200;

// ── 純函式（可單測，無 DOM 依賴）─────────────────────────────────────────────

/**
 * 還原 chat_service `html.escape(quote=True)` 產生的固定 5 種實體。
 * **非通用 HTML 解析**——只字串對映這 5 種；最終仍經 textContent 落 DOM（無 innerHTML），
 * 故即使有漏網真標籤也只當文字顯示、不執行。`&amp;` 最後還原避免 `&amp;lt;` 二次解碼。
 */
export function decodeChatMessage(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#x27;/g, "'")
    .replace(/&amp;/g, '&');
}

// #250：廣播房 locale 別名（ATAK 中文 / iTAK 英文 / 其他 locale 補這裡）。
// 同一個廣播房不同語言名 → 收斂；且**併入「全部」**（不另立房 chip，廣播訊息歸全部）。
const _BROADCAST_ROOMS = new Set(['All Chat Rooms', '所有聊天室']);

/** room 顯示標籤：group 為空（DM / 無房間）→ 「直接」；廣播房別名 → 「廣播」（中英收斂）。 */
export function roomLabel(group) {
  if (group == null || group === '') return '直接';
  if (_BROADCAST_ROOMS.has(group)) return '廣播';  // #250：中英 locale 收斂為單一標籤
  return group;
}

/** 從通聯陣列取**出現過**的 distinct room（依首次出現序，供動態 chips）。
 *  #250：廣播房（All Chat Rooms / 所有聊天室）併入「全部」，不另立房 chip——
 *  避免中英重複 chip + 與「全部」語意撞。廣播訊息仍在串流顯示（inline [廣播] 標籤）。 */
export function distinctRooms(chats) {
  const seen = [];
  for (const c of chats || []) {
    if (_BROADCAST_ROOMS.has(c.group)) continue;  // 廣播歸「全部」，不單獨成房 chip
    const label = roomLabel(c.group);
    if (!seen.includes(label)) seen.push(label);
  }
  return seen;
}

/** 依 room 標籤過濾（room 為 null/'__all__' → 不過濾）。 */
export function filterChatsByRoom(chats, room) {
  if (room == null || room === '__all__') return chats || [];
  return (chats || []).filter(c => roomLabel(c.group) === room);
}

/** 未讀數＝id > lastSeenId 的筆數（id server 端遞增、單調）。 */
export function countUnread(chats, lastSeenId) {
  return (chats || []).filter(c => Number(c.id) > Number(lastSeenId || 0)).length;
}

/** 最大 id（空 → 維持原 lastSeenId）。 */
export function maxChatId(chats, fallback = 0) {
  return (chats || []).reduce((m, c) => Math.max(m, Number(c.id) || 0), Number(fallback) || 0);
}

/** 併一筆即時通聯（b2）：已存在（同 id）→ 原陣列不動；否則附末端（升序，最新在下）。
 *  去重以防 WS 推播與 poll resync 重複同一筆。回傳新陣列或原陣列（純函式）。 */
export function mergeLiveChat(chats, chat) {
  const list = chats || [];
  if (!chat || chat.id == null) return list;
  if (list.some(c => Number(c.id) === Number(chat.id))) return list;
  // 併入後依 (t, id) 排序——對齊 poll 的 ORDER BY t ASC, id ASC：時鐘偏移的遲到訊息
  // 不會錯位在最末，且 poll 重排後位置一致（兩路同序，不會 poll 後跳位）。
  return [...list, chat].sort((a, b) =>
    (a.t < b.t ? -1 : a.t > b.t ? 1 : Number(a.id) - Number(b.id)));
}

/** GeoChat 的 sender_uid 是 `GeoChat.<senderUid>.<room>.<id>` → 取中段 senderUid（= 來源 marker uid）。
 *  **假設 device uid 無 '.'**（TAK 慣例：ANDROID-<serial> / GUID 皆無點；b2 dogfood 實證；含點的
 *  federated uid 罕見、會被 split 截斷 → 該單位過濾失準，屬已知邊界）。非 GeoChat 格式原樣回傳
 *  （= 直接拿 sender_uid 當 uid 比對，相容非 iTAK client）。 */
export function parseSenderUid(senderUid) {
  if (typeof senderUid !== 'string') return senderUid;
  return senderUid.startsWith('GeoChat.') ? (senderUid.split('.')[1] || senderUid) : senderUid;
}

/** #216：依當前過濾脈絡組出向 GeoChat 的 POST body（純函式，可單測）。
 *  選單位 → DM（recipient_uid[+callsign]）；選命名房 → chatroom；否則全體廣播（僅 message）。 */
export function buildChatBody(message, sender, room) {
  const body = { message };
  if (sender && sender.uid) {
    body.recipient_uid = sender.uid;
    if (sender.callsign) body.recipient_callsign = sender.callsign;
  } else if (room && room !== '__all__') {
    body.chatroom = room;
  }
  return body;
}

/** #216：送出目標提示文字（純函式，可單測）。 */
export function composeTargetLabel(sender, room) {
  if (sender && sender.uid) return `→ 私訊 ${sender.callsign || sender.uid}`;
  if (room && room !== '__all__') return `→ ${room}`;
  return '→ 廣播（全體）';
}

/** by-sender 過濾（b3-1）：以**解析後 uid 精準比對**（uid 唯一）。sender = {uid, callsign}；
 *  無 sender → 不過濾。**不用 callsign 比對**——callsign 非唯一，兩單位同 callsign 會誤混
 *  （code-review）；callsign 僅供 sender chip 顯示，不參與匹配。 */
export function filterChatsBySender(chats, sender) {
  if (!sender || !sender.uid) return chats || [];
  return (chats || []).filter(c => parseSenderUid(c.sender_uid) === sender.uid);
}

// ── 狀態 + DOM（dashboard runtime）──────────────────────────────────────────

let _chats = [];
let _activeRoom = '__all__';
let _activeSender = null;   // b3-1：點地圖 TAK marker → {uid, callsign} by-sender 過濾（蓋過 room）
// #250：已讀水位持久化——module 變數每次 reload 歸 0 → 重開 session 全變未讀（之前讀過的也算）。
// 存 localStorage、init 時還原，讓「已讀」跨 reload 保留。
const _LASTSEEN_KEY = 'ics_chat_lastSeenId';
function _loadLastSeen() {
  try { return Number(window.localStorage.getItem(_LASTSEEN_KEY)) || 0; } catch (_) { return 0; }
}
let _lastSeenId = _loadLastSeen();   // 已讀水位（跨 reload 持久；localStorage 非 sessionStorage）
/** 更新已讀水位並落地 localStorage（單調不退）。 */
function _setLastSeen(id) {
  const n = Number(id) || 0;
  if (n <= _lastSeenId) return;
  _lastSeenId = n;
  try { window.localStorage.setItem(_LASTSEEN_KEY, String(n)); } catch (_) { /* 私密模式等 → 忽略 */ }
}
/** #250 fix（純函式，供測試）：persisted 水位 > 現有 chat 最大 id（DB 換/reset；跨副本 DB id
 *  序列獨立）→ 夾回 max；否則原樣回傳。空 chats 不夾（無資訊）。 */
export function clampWatermark(lastSeen, chats) {
  const max = maxChatId(chats, 0);
  return ((chats || []).length && max < Number(lastSeen || 0)) ? max : Number(lastSeen || 0);
}
/** stale 水位防護：夾回後落地 localStorage（**可下降**，與 _setLastSeen 單調不同）。否則新 chat
 *  （較低 id）`countUnread(id > 水位)` 恆 0、紅圈永不跳（review 抓出 + dogfood 實證）。 */
function _clampLastSeenIfStale(chats) {
  const clamped = clampWatermark(_lastSeenId, chats);
  if (clamped !== _lastSeenId) {
    _lastSeenId = clamped;
    try { window.localStorage.setItem(_LASTSEEN_KEY, String(clamped)); } catch (_) { /* 忽略 */ }
  }
}
let _currentTab = 'events'; // 右欄當前分頁（events | chat | roster | decisions，#269）
let _takDisabled = false;
let _sendingChat = false;   // #216：出向 compose 送出中旗標（擋 Enter/click 重複送）
let _pollTimer = null;

function _el(id) { return document.getElementById(id); }

/** 初始化：綁 TAK 狀態 + 即時通聯事件 + 啟動 safety-net poll。data-action 委派在 main.js。 */
export function initChatPanel() {
  document.addEventListener('tak:conn-state', (e) => _applyTakState(e?.detail?.state));
  document.addEventListener('chat:new', (e) => _onLiveChat(e?.detail)); // b2：WS 即時推播
  document.addEventListener('chat:resync', () => refreshChatNow()); // #475：重分隊改既有 chats faction → 即時重取（顯/藏）
  document.addEventListener('map:unitSelected', (e) => _onUnitSelected(e?.detail)); // b3-1：點 marker 過濾
  document.addEventListener('map:senderNotLocated', (e) => _onUnitSelected(e?.detail)); // b3-2：訊息無座標 → fallback by-sender
  _initCompose(); // #216：出向 compose（依角色顯隱 + Enter 送出）
  if (_pollTimer) clearInterval(_pollTimer);
  _pollTimer = setInterval(() => _poll(), POLL_MS);
  _poll();
}

// #216：出向 GeoChat compose ─────────────────────────────────────────────────
// #463：放寬到 WRITE_ROLES（後端 POST /api/tak/chat = WRITE_ROLES）——operator 是一線
// 操作訊息者，通聯雙向屬其職責。observer 仍唯讀，compose 不顯示（否則會看到必 403 的送出框）。
// role 集合收斂在 auth.js canSendChat（勿 inline 攤開清單，防與後端 WRITE_ROLES 漂移）。
function _canSendChat() { return canSendChat(); }

/** 依角色顯隱 compose（WRITE_ROLES 才出）。initChatPanel 可能在登入前跑（role 未進
 *  sessionStorage）→ refreshChatNow（登入後 hook）會再套一次，否則 commander 也看不到送出框。 */
function _applyComposeVisibility() {
  const box = _el('chat-compose');
  if (box) box.style.display = _canSendChat() ? 'flex' : 'none';
}

/** compose 初始化：綁 Enter 鍵送出（一次）+ 套用角色顯隱 + 渲染初始目標。 */
function _initCompose() {
  const input = _el('chat-compose-input');
  if (input) {
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); chatSend(); }
    });
  }
  _applyComposeVisibility();
  _renderComposeTarget();
}

/** 更新送出目標提示——跟隨當前過濾脈絡：選單位→DM、選房間→該房、否則→全體廣播。 */
function _renderComposeTarget() {
  const el = _el('chat-compose-target');
  if (el) el.textContent = composeTargetLabel(_activeSender, _activeRoom);
}

/** 顯示 compose 錯誤（短暫）。 */
function _composeError(status) {
  const el = _el('chat-compose-err');
  if (!el) return;
  const msg = status === 409 ? 'TAK 已停用，無法發送'
    : status === 403 ? '無發送權限'
    : status === 422 ? '訊息含不允許的字元或過長'
    : '送出失敗，請重試';
  el.textContent = msg;
  el.style.display = 'block';
}

/** 送出一則出向 GeoChat（data-action='chatSend' / Enter）。目標跟隨當前過濾脈絡。
 *  不本地樂觀插入——訊息經 server 回送（WS chat:new）進串流，避免與回送重複（後端 uid 冪等）。 */
export async function chatSend() {
  const input = _el('chat-compose-input');
  // review fix：in-flight 旗標擋重複送——Enter keydown 不經 button.disabled，連按會送出重複
  // 通聯（後端每次 mint 新 msg_id → 兩筆真實 GeoChat 上現場，非回送冪等可救）。
  if (!input || _takDisabled || !_canSendChat() || _sendingChat) return;
  const message = (input.value || '').trim();
  const errEl = _el('chat-compose-err');
  if (errEl) errEl.style.display = 'none';
  if (!message) return;
  const body = buildChatBody(message, _activeSender, _activeRoom);
  _sendingChat = true;
  const btn = _el('chat-compose-send');
  if (btn) btn.disabled = true;
  try {
    const resp = await authFetch(API_BASE + '/api/tak/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (resp.ok) {
      input.value = '';            // 清空＝送出成功的回饋；訊息隨 server 回送進串流
    } else {
      _composeError(resp.status);
    }
  } catch (_) {
    _composeError(0);
  } finally {
    _sendingChat = false;
    if (btn) btn.disabled = false;
  }
}

/** #250：登入後（onEnterDashboard）立即補一次 poll——initChatPanel 的首次 _poll 在登入前跑、
 *  因 !getToken() 早退，原本要等下一個 30s interval 通聯才填（事件 poll() 登入後即跑 → 通聯比事件晚出）。
 *  本函式讓通聯與事件同步在登入後立即載入。 */
export function refreshChatNow() {
  _applyComposeVisibility();  // #216：登入後角色才在 sessionStorage → 此時才能正確顯隱 compose
  _poll();
}

/** WS 即時通聯（chat:new，b2）→ 去重併入 + 重繪。與 poll 同渲染路徑。 */
function _onLiveChat(chat) {
  const merged = mergeLiveChat(_chats, chat);
  if (merged === _chats) return; // 重複（已由 poll 或前一則帶入），不重繪
  // 是否帶出新房間（→ 才重建 chips）。#250 fix：廣播房併「全部」不成 chip → 不算新房間，
  // 否則每則廣播都誤判 newRoom=true、多跑一次 _renderChips（無謂 DOM 重建）。
  const newRoom = !_BROADCAST_ROOMS.has(chat?.group) && !distinctRooms(_chats).includes(roomLabel(chat?.group));
  _chats = merged;
  if (_chats.length > LIVE_CAP) _chats = _chats.slice(-LIVE_CAP); // 對齊 poll 窗，防無界增長
  if (_currentTab === 'chat') _setLastSeen(maxChatId(_chats, _lastSeenId));
  _renderUnread();
  if (newRoom) _renderChips();  // 只在房間集合改變時重建 chips（常見情況=既有房間，省 DOM 重建）
  if (_currentTab === 'chat') _renderStream();
}

// 右欄四 tab（#269）：事件追蹤 ｜ 通聯 ｜ 隊伍 ｜ 待裁示。各整欄高、display 切換、各帶紅圈計數。
const RIGHT_TABS = ['events', 'chat', 'roster', 'decisions'];
const TAK_TABS = new Set(['chat', 'roster']); // TAK 停用時隱藏（無 TAK 即無意義）

/** 右欄分頁切換（#269 通用多 tab）。切到通聯 → 清未讀水位；切到隊伍 → 派事件給 roster_panel 重繪。 */
export function switchRightTab(tab) {
  if (!RIGHT_TABS.includes(tab)) tab = 'events';
  if (TAK_TABS.has(tab) && _takDisabled) return; // 停用時不可切入 TAK 相關頁
  _currentTab = tab;
  for (const t of RIGHT_TABS) {
    const content = _el('right-' + t);
    if (content) content.style.display = (t === tab) ? 'flex' : 'none';
    _el('rtab-' + t)?.classList.toggle('active', t === tab);
  }
  // per-session 記憶（#269）：多幕僚各 session 停自己那頁，重整頁面回到同一 tab。
  try { sessionStorage.setItem('_activeRightTab', tab); } catch (_) { /* private mode */ }
  // 解耦通知（roster_panel / 未來其他頁自行訂閱重繪，避免 chat_panel 直接 import）。
  document.dispatchEvent(new CustomEvent('right-tab:switched', { detail: { tab } }));
  if (tab === 'chat') {
    _setLastSeen(maxChatId(_chats, _lastSeenId));
    _renderUnread();
    _renderStream();
  }
}

/** 還原 per-session 記憶的 tab（main.js 於登入後、TAK 狀態套用後呼叫）。 */
export function restoreRightTab() {
  let tab = 'events';
  try { tab = sessionStorage.getItem('_activeRightTab') || 'events'; } catch (_) { /* private mode */ }
  if (TAK_TABS.has(tab) && _takDisabled) tab = 'events'; // 記憶的是 TAK 頁但現在停用 → 退回事件
  switchRightTab(tab);
}

/** TAK 狀態套用：停用 → 隱藏通聯/隊伍 tab（並把停留在該頁的使用者切回事件）。 */
function _applyTakState(state) {
  const disabled = state === 'disabled';
  _takDisabled = disabled;
  for (const t of TAK_TABS) {
    const tabEl = _el('rtab-' + t);
    if (tabEl) tabEl.style.display = disabled ? 'none' : 'inline-flex';
  }
  if (disabled && TAK_TABS.has(_currentTab)) switchRightTab('events');
}

async function _poll() {
  if (_takDisabled || !isLoggedIn()) return;  // #293：登入閘改吃旗標（token 已進 cookie）
  try {
    const resp = await authFetch(API_BASE + '/api/chat?limit=200', { signal: AbortSignal.timeout(5000) });
    if (!resp.ok) return; // 靜默（含 first-run 423 / 403）；不污染畫面
    const data = await resp.json();
    _chats = Array.isArray(data?.chats) ? data.chats : [];
    _clampLastSeenIfStale(_chats);  // #250 fix：poll 拿到全量 → 夾掉跨 DB 的 stale 高水位
    if (_currentTab === 'chat') {
      _setLastSeen(maxChatId(_chats, _lastSeenId));
    }
    _renderUnread();
    _renderChips();
    if (_currentTab === 'chat') _renderStream();
  } catch (_) { /* 逾時 / 網路 — 下輪重試 */ }
}

function _renderUnread() {
  const badge = _el('chat-unread');
  if (!badge) return;
  // 通聯分頁中＝即時已讀，永遠 0；事件分頁才累計未讀
  const n = _currentTab === 'chat' ? 0 : countUnread(_chats, _lastSeenId);
  badge.textContent = String(n);
  badge.style.display = n > 0 ? 'inline-block' : 'none';
}

function _renderChips() {
  const bar = _el('chat-chips');
  if (!bar) return;
  // b3-1：by-sender 過濾啟用時，顯示可清除的 sender chip（蓋過 room chips，不受 ≤1 房間隱藏影響）。
  if (_activeSender) {
    bar.style.display = 'flex';
    bar.replaceChildren(_senderChip(_activeSender));
    return;
  }
  const rooms = distinctRooms(_chats);
  // 若當前選的 room 已不在資料中（時間窗滾動）→ 回退「全部」。review fix：靜默 reset 後
  // 同步刷新 compose 目標提示，否則提示仍寫「→ <房>」、buildChatBody 卻送廣播（提示與實送不符）。
  if (_activeRoom !== '__all__' && !rooms.includes(_activeRoom)) { _activeRoom = '__all__'; _renderComposeTarget(); }
  bar.replaceChildren();
  // ≤1 房間時篩選無意義（「全部」與該房間同集合）→ 隱藏整條 chips bar，避免與 TAK
  // 內建房間名（如「All Chat Rooms」）視覺撞「全部」。每則仍有 [room] 行內標籤。
  if (rooms.length <= 1) {
    bar.style.display = 'none';
    if (_activeRoom !== '__all__') { _activeRoom = '__all__'; _renderComposeTarget(); }
    return;
  }
  bar.style.display = 'flex';
  bar.appendChild(_chip('全部', '__all__'));
  for (const r of rooms) bar.appendChild(_chip(r, r));
}

function _chip(label, room) {
  const el = document.createElement('span');
  el.className = 'chat-chip' + (room === _activeRoom ? ' active' : '');
  el.dataset.action = 'chatFilterRoom';
  el.dataset.room = room;
  el.textContent = label;
  return el;
}

/** b3-1：by-sender 過濾 chip（顯示單位 callsign + ✕ 清除）。 */
function _senderChip(sender) {
  const el = document.createElement('span');
  el.className = 'chat-chip active';
  el.dataset.action = 'chatClearSender';
  el.textContent = `篩 ${sender.callsign || sender.uid} ✕`;
  el.title = '清除單位過濾';
  return el;
}

/** chip 點擊（main.js data-action 委派進來）。 */
export function chatFilterRoom(room) {
  _activeRoom = room || '__all__';
  _renderChips();
  _renderStream();
  _renderComposeTarget();  // #216：房間切換 → 送出目標跟著變
}

/** 點地圖 TAK marker（map:unitSelected，b3-1）→ by-sender 過濾 + 切到通聯。 */
function _onUnitSelected(detail) {
  if (!detail?.uid || _takDisabled) return;
  _activeSender = { uid: detail.uid, callsign: detail.callsign || null };
  switchRightTab('chat');  // 切到通聯（內部 _renderStream 已吃 _activeSender）
  _renderChips();          // 顯示可清除的 sender chip
  _renderComposeTarget();  // #216：選單位 → 送出變 DM
}

/** 清除 by-sender 過濾（sender chip ✕，main.js data-action）。 */
export function chatClearSender() {
  _activeSender = null;
  _renderChips();
  _renderStream();
  _renderComposeTarget();  // #216：清除單位過濾 → 送出回退廣播/房間
}

// b3-2：訊息**長按**（press-and-hold 600ms，對齊 events.js `_evtCardDown/_evtCardUp`）。
// 按住達門檻 → 派 chat:locateSender（map flyTo + 閃泡泡，持續）；放開 → chat:unlocateSender（收泡泡）。
let _chatLpTimer = null;
let _chatLpActive = false;

/** 訊息列 pointerdown（main.js 全域 pointer 委派）→ 起長按計時。 */
export function chatRowDown(ds) {
  if (!ds?.senderUid) return;
  _chatLpActive = false;
  if (_chatLpTimer) clearTimeout(_chatLpTimer);
  _chatLpTimer = setTimeout(() => {
    _chatLpTimer = null;
    _chatLpActive = true;
    document.dispatchEvent(new CustomEvent('chat:locateSender', {
      detail: {
        uid: ds.senderUid,
        callsign: ds.callsign || null,
        lat: ds.lat !== '' ? Number(ds.lat) : null,
        lon: ds.lon !== '' ? Number(ds.lon) : null,
      },
    }));
  }, 600);
}

/** 訊息列 pointerup/leave（main.js 全域）→ 取消未達門檻的計時；已 highlight → 收泡泡。 */
export function chatRowUp() {
  if (_chatLpTimer) { clearTimeout(_chatLpTimer); _chatLpTimer = null; }
  if (_chatLpActive) {
    document.dispatchEvent(new CustomEvent('chat:unlocateSender'));
    _chatLpActive = false;
  }
}

function _renderStream() {
  const stream = _el('chat-stream');
  if (!stream) return;
  // b3-1：by-sender 過濾優先（蓋過 room）；否則照 room 過濾。
  const rows = _activeSender ? filterChatsBySender(_chats, _activeSender) : filterChatsByRoom(_chats, _activeRoom);
  stream.replaceChildren();
  if (!rows.length) {
    const empty = document.createElement('div');
    empty.className = 'chat-empty';
    empty.textContent = _takDisabled ? 'TAK 未啟用' : (_activeSender ? '此單位無通聯' : '無通聯');
    stream.appendChild(empty);
    return;
  }
  for (const c of rows) stream.appendChild(_row(c));
  stream.scrollTop = stream.scrollHeight; // 單流貼底（最新在下）
}

function _row(c) {
  const row = document.createElement('div');
  row.className = 'chat-row';
  // b3-2：**長按**訊息 → 定位發訊單位（對齊事件卡長按；map.js 按住閃對話泡泡、放開收，
  // 無座標回 fallback by-sender）。長按由 main.js 全域 pointer 監聽認 `.chat-row` + 下列 data。
  row.dataset.senderUid = parseSenderUid(c.sender_uid) || '';
  row.dataset.callsign = c.callsign || '';
  row.dataset.lat = c.lat == null ? '' : String(c.lat);
  row.dataset.lon = c.lon == null ? '' : String(c.lon);

  const head = document.createElement('div');
  head.className = 'chat-row-head';
  const room = document.createElement('span');
  room.className = 'chat-room-tag';
  room.textContent = `[${roomLabel(c.group)}]`;
  const who = document.createElement('span');
  who.className = 'chat-who';
  who.textContent = c.callsign || c.sender_uid || '?';
  const time = document.createElement('span');
  time.className = 'chat-time';
  time.textContent = _shortTime(c.t);
  head.append(room, who, time);

  const body = document.createElement('div');
  body.className = 'chat-msg';
  body.textContent = decodeChatMessage(c.message); // 純文字落 DOM（無 innerHTML）

  row.append(head, body);
  return row;
}

/** ISO 8601（UTC，帶 Z）→ **本地** HH:MM:SS。對齊 header 時鐘（main.js toTimeString=本地），
 *  否則通聯顯示 UTC、比 header 慢一個時區（#216 dogfood 抓出）。CoT/received_at 皆帶 Z（UTC），
 *  new Date 解為 UTC、toTimeString 轉本地。非標準 ISO（無法解析）→ 退回原樣切，不臆測時區。 */
function _shortTime(t) {
  if (typeof t !== 'string') return '';
  const d = new Date(t);
  if (!Number.isNaN(d.getTime())) return d.toTimeString().slice(0, 8);
  const m = t.match(/T(\d{2}:\d{2}:\d{2})/);
  return m ? m[1] : t;
}
