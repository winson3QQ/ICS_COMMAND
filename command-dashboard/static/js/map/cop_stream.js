// SPDX-License-Identifier: LicenseRef-Proprietary
// Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
/**
 * map/cop_stream.js — COP entity 即時同步 client（issue #29）
 *
 * **純資料層**（PR-H：PR-E 的「自建 marker / ＋標記 MVP」已退役）。cop_stream 只負責
 * 維護 uid → entity 的本地快取與同步邏輯；**所有渲染由 map.js 透過 onChange 訂閱 +
 * EntityLayer 完成**（route/polygon/event 等 attributes.kind 分流渲染）。
 *
 * operator 透過 /api/cop/* per-entity API 建立 / 更新 / 刪除，server WebSocket 廣播 →
 * 其他 client 不 reload 即時更新。
 *
 * 對應 issue #29 原 plan 的 6 道防護（per-entity 模型下）：
 *  1. _isSaving（in-flight write）：某 uid 寫入飛行中 → 跳過該 uid 的 incoming WS / resync，
 *     避免 stale 覆蓋本地剛送出的值（根除 commit 58bb5d4 的 clobber）。
 *  2. post-fetch double-check：resync GET 回來後逐 uid 再確認非 in-flight 才套用。
 *  3. server 端 atomic write + 樂觀鎖：PR-A/B 已備（PUT/DELETE 帶 If-Match）。
 *  4. 編輯中（_draggingUid）skip：正在拖的 entity 不被遠端更新拉走。
 *  5. last-write-wins by version_clock：merge 單調，重複 / 亂序 / 重連 replay 安全。
 *  6. logout / page-hide cleanup：關 WS、清快取、停重連。
 *
 * 為可測試，所有外部相依（WebSocket / fetch / token / role / 計時器）皆注入。
 */

const WS_SUBPROTOCOL = "ics-cop-v1";
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30000;
// 週期對帳間隔（#160/#161 軟 stale）。iTAK 刪除 = 停止重播（無顯式 delete CoT），entity 過
// CoT stale + backend 移除窗口後從列表消失 → 須週期 resync 才會在前端移除（否則要手動 refresh，
// 正是 #161 症狀）。同時每輪 emit 一次讓前端依 entity.stale 重算「過 stale → 變灰」（#160）。
const STALE_REFRESH_MS = 20000;

/**
 * @param {object} deps
 *  - getToken: () => string|null
 *  - authFetch: (url, opts) => Promise<Response>
 *  - canWrite: () => boolean（operator+ 才可建立/拖/刪）
 *  - WebSocketCtor: WebSocket 建構子（注入便於測試）
 *  - wsUrl: WS 連線 URL（預設依 location 推導）
 *  - apiBase: REST base（預設 ''）
 *  - setTimeoutFn / clearTimeoutFn: 計時器（注入便於測試）
 */
export function createCopStream(deps) {
  const {
    getToken,
    authFetch,
    canWrite = () => false,
    WebSocketCtor = (typeof WebSocket !== "undefined" ? WebSocket : null),
    wsUrl = _defaultWsUrl(),
    // #267 常駐層疊看：true（限指揮層，main.js 注入）→ WS `?standing=1` + resync `?include_standing=1`，
    // 讓 active 場連線也收 NULL 常駐 entity（後端 gate；非指揮層即使 true 也被擋）。
    includeStanding = false,
    apiBase = "",
    setTimeoutFn = (typeof setTimeout !== "undefined" ? setTimeout : null),
    clearTimeoutFn = (typeof clearTimeout !== "undefined" ? clearTimeout : null),
  } = deps;

  // uid → { entity }
  const _byUid = new Map();
  // 寫入飛行中的 uid（防護 1）
  const _isSaving = new Set();
  // 正在拖的 uid（防護 4）
  let _draggingUid = null;
  let _ws = null;
  let _reconnectAttempt = 0;
  let _reconnectTimer = null;
  let _refreshTimer = null;
  let _stopped = false;
  // map.js 訂閱的重繪 callback（資料任何變動都觸發；map.js 自行從 getEntitiesByKind 取資料畫）
  let _onChange = null;

  function _emitChange() {
    if (_onChange) {
      try {
        _onChange();
      } catch {
        /* 訂閱者重繪錯誤不影響 merge */
      }
    }
  }

  // ── merge 核心（純邏輯，可單測）──────────────────────────────────────────

  /** 套用一筆遠端 entity（create/update）。回傳是否真的套用。 */
  function _applyEntity(entity) {
    const uid = entity.uid;
    // 防護 1 & 4：本地正在寫 / 拖這顆 → 不被遠端覆蓋
    if (_isSaving.has(uid) || _draggingUid === uid) return false;
    const existing = _byUid.get(uid);
    // 防護 5：last-write-wins by version_clock（單調；舊的/同的丟棄）
    if (existing && entity.version_clock <= existing.entity.version_clock) return false;
    _upsert(entity);
    return true;
  }

  /** 套用一筆遠端刪除（soft-delete：stale 過期 → 從快取移除）。 */
  function _applyDelete(uid, versionClock) {
    if (_isSaving.has(uid) || _draggingUid === uid) return false;
    const existing = _byUid.get(uid);
    // 遲到的舊刪除（version 比現值小）忽略
    if (existing && versionClock != null && versionClock < existing.entity.version_clock) return false;
    _remove(uid);
    return true;
  }

  /** WS 訊息分派。 */
  function _onMessage(msg) {
    switch (msg.op) {
      case "hello":
        break; // 連線確認；entity 由 resync GET 帶入
      case "create":
      case "update":
        if (msg.entity) _applyEntity(msg.entity);
        break;
      case "delete":
        _applyDelete(msg.uid, msg.version_clock);
        break;
      case "resync":
        // server 端批次清空（admin reset）等不走 per-entity delete 的變動 → 全量對帳，
        // 移除 server 已無者（防護 2 / in-flight 保留邏輯都在 resync 內）。
        resync();
        break;
      case "exercise_switched":
        // P1-14：他人 activate/archive 了演習 → active scope 變了。本 session 須重新依新 scope
        // 對帳（map 圖釘 / 面板 / chip）。WS 連線的 scope 是 connect 當下定的、已過時 → 交給
        // main.js 重連 cop_stream（重讀 active scope）+ poll + 更新 chip（dispatch DOM 事件解耦）。
        if (typeof document !== "undefined") {
          document.dispatchEvent(new CustomEvent("exercise:switched"));
        }
        break;
      case "chat":
        // #213 b2：通聯即時推播。**不進 entity store**（非作戰圖物件）→ 派 DOM 事件給
        // chat_panel.js 消費（同 exercise_switched 的解耦模式，避免 cop_stream 依賴 chat 模組）。
        if (msg.chat && typeof document !== "undefined") {
          document.dispatchEvent(new CustomEvent("chat:new", { detail: msg.chat }));
        }
        break;
      default:
        break;
    }
  }

  // ── 本地快取 upsert / remove（純資料；變動觸發 onChange 讓 map.js 重繪）──────

  function _upsert(entity) {
    const existing = _byUid.get(entity.uid);
    if (existing) existing.entity = entity;
    else _byUid.set(entity.uid, { entity });
    _emitChange();
  }

  function _remove(uid) {
    if (!_byUid.has(uid)) return;
    _byUid.delete(uid);
    _emitChange();
  }

  // ── REST 寫入（帶 If-Match + in-flight guard）────────────────────────────

  async function _withSaving(uid, fn) {
    _isSaving.add(uid);
    try {
      return await fn();
    } finally {
      // 防護 1：寫完才解 flag，期間 incoming update 被跳過；解後下一筆 WS 廣播（含本次結果）會校正
      _isSaving.delete(uid);
    }
  }

  /**
   * 建立一顆 entity（POST）。body 至少需 { type, lat, lon }，可帶 callsign / attributes。
   * 回傳建立後的 entity（含 server 兜底的 uid / version_clock）；失敗回 null。
   * 本地立即 upsert（WS 廣播也會到，version_clock LWW 冪等不重複）。
   * map 物件（route/polygon/event）走這條：attributes.kind + vertices 等由 caller 帶；
   * event 圖釘的 event_id 走**頂層 first-class**（P2-33b，後端建 junction，非 attributes glue）。
   */
  async function createEntity(body = {}) {
    if (!canWrite()) return null;
    const resp = await authFetch(`${apiBase}/api/cop/entities`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) return null;
    const entity = await resp.json();
    _upsert(entity);
    return entity;
  }

  /** 更新一顆 entity（PUT + If-Match）。回傳 true=成功，false=失敗（含 409）。
   *  409 → 採 server 現值（回 false，caller 可提示衝突 / 失敗）。 */
  async function _putEntity(uid, patch) {
    const rec = _byUid.get(uid);
    if (!rec || !canWrite()) return false;
    // dragLocal 期間以 _draggingUid 擋遠端覆蓋；提交（PUT）即代表拖曳結束，於此交棒給
    // _isSaving（防護 1）並清掉 dragging，否則 dragend 後該 uid 永遠不收遠端更新。
    if (_draggingUid === uid) _draggingUid = null;
    const expected = rec.entity.version_clock;
    return _withSaving(uid, async () => {
      const resp = await authFetch(`${apiBase}/api/cop/entities/${encodeURIComponent(uid)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json", "If-Match": String(expected) },
        body: JSON.stringify(patch),
      });
      if (resp.ok) {
        _upsert(await resp.json());
        return true;
      }
      if (resp.status === 409) {
        const data = await resp.json();
        if (data && data.server_entity) _upsert(data.server_entity); // 對齊 server，避免本地漂位
      }
      return false;
    });
  }

  /** 刪除一顆 entity（DELETE + If-Match，soft-delete）。回傳 true=成功，false=失敗。 */
  async function deleteEntity(uid) {
    const rec = _byUid.get(uid);
    if (!rec || !canWrite()) return false;
    const expected = rec.entity.version_clock;
    return _withSaving(uid, async () => {
      const resp = await authFetch(`${apiBase}/api/cop/entities/${encodeURIComponent(uid)}`, {
        method: "DELETE",
        headers: { "If-Match": String(expected) },
      });
      if (resp.ok) {
        _remove(uid);
        return true;
      }
      if (resp.status === 409) {
        const data = await resp.json();
        if (data && data.server_entity) _upsert(data.server_entity);
      }
      return false;
    });
  }

  // ── 全量 resync（連線 / 重連 / admin reset 時）─────────────────────────────

  async function resync() {
    // 進入時先快照「已知 uid」。removal 只考慮這批 —— resync 飛行中（GET await 期間）
    // 新建的 entity（如同時 createEntity POST 落地）不在快照內，不會被誤刪。
    const known = new Set(_byUid.keys());
    // #267：疊看時 resync 也帶 include_standing，與 WS 對等（否則每輪 resync 抹掉 WS 推來的常駐＝鬼影）
    const resp = await authFetch(`${apiBase}/api/cop/entities${includeStanding ? "?include_standing=1" : ""}`);
    if (!resp.ok) return;
    const data = await resp.json();
    const seen = new Set();
    for (const entity of data.entities || []) {
      seen.add(entity.uid);
      // 防護 2：post-fetch double-check（_applyEntity 內已查 _isSaving / _draggingUid / LWW）
      _applyEntity(entity);
    }
    // 只清「進 resync 前就在、server 已無、且本地非編輯中」的 → 移除
    for (const uid of known) {
      if (!seen.has(uid) && !_isSaving.has(uid) && _draggingUid !== uid) _remove(uid);
    }
  }

  // ── WebSocket 生命週期 ────────────────────────────────────────────────────

  function connect() {
    _stopped = false;
    // 已連線 / 連線中 → 不重複開（防 onEnterDashboard + onAuthChange('login') 雙呼叫疊 socket）
    if (_ws && (_ws.readyState === 0 || _ws.readyState === 1)) return;
    const token = getToken && getToken();
    if (!token || !WebSocketCtor) return;
    let ws;
    // #267：疊看時 WS 帶 ?standing=1（後端限 COMMAND）。append 而非改 wsUrl（保持注入值不可變）。
    const url = includeStanding ? `${wsUrl}${wsUrl.includes("?") ? "&" : "?"}standing=1` : wsUrl;
    try {
      ws = new WebSocketCtor(url, [WS_SUBPROTOCOL, `ics.session.${token}`]);
    } catch {
      _scheduleReconnect();
      return;
    }
    _ws = ws;
    ws.onopen = () => {
      _reconnectAttempt = 0;
      // 連上先全量 resync（version_clock merge 冪等，補上斷線期間漏掉的）
      resync();
      // 啟動週期對帳（#160/#161）。self-rescheduling 迴圈 → _refreshTimer 在 tick 間恆非 null；
      // 故此 guard 確保重連 onopen 不會疊第二個迴圈（stop() 會清回 null）。
      if (_refreshTimer == null) _scheduleRefresh();
    };
    ws.onmessage = (ev) => {
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      _onMessage(msg);
    };
    ws.onclose = () => {
      // identity guard（#265）：只有「當前」socket 的關閉才安排重連。被取代的舊 socket
      // （例：切換演習舊路徑、或 onerror→close 後已換新 socket）其 onclose 非同步遲到，
      // 此時 _ws 已是新 socket、_stopped 可能已被 connect() 重設為 false——若不擋，會在
      // 健康連線上誤排重連並誤加 _reconnectAttempt（backoff 漂掉）。
      if (_ws !== ws) return;
      _ws = null;
      if (!_stopped) _scheduleReconnect();
    };
    ws.onerror = () => {
      try {
        ws.close();
      } catch {
        /* noop */
      }
    };
  }

  function _scheduleReconnect() {
    if (_stopped || !setTimeoutFn) return;
    const delay = Math.min(RECONNECT_BASE_MS * 2 ** _reconnectAttempt, RECONNECT_MAX_MS);
    _reconnectAttempt += 1;
    _reconnectTimer = setTimeoutFn(() => {
      if (!_stopped) connect();
    }, delay);
  }

  /** 週期對帳迴圈（#160/#161 軟 stale）：resync 移除 backend 已 drop 的過窗口 entity，並
   *  每輪 emit 讓前端依 entity.stale 重算變灰。self-reschedules 直到 stop()。 */
  function _scheduleRefresh() {
    if (_stopped || !setTimeoutFn) return;
    _refreshTimer = setTimeoutFn(async () => {
      if (_stopped) return;
      try {
        await resync();
      } catch {
        /* 對帳失敗：下輪再試（WS 仍會即時補正） */
      }
      _emitChange(); // 即使無資料變動也重繪：過 stale 的 entity 需轉灰
      _scheduleRefresh();
    }, STALE_REFRESH_MS);
  }

  /** 防護 6：停止（logout / page-hide）—關 WS、停重連、清快取。 */
  function stop() {
    _stopped = true;
    if (_reconnectTimer && clearTimeoutFn) clearTimeoutFn(_reconnectTimer);
    _reconnectTimer = null;
    _reconnectAttempt = 0; // #265：重置 backoff，避免下次 connect() 的重連從漂掉的指數階開始
    if (_refreshTimer && clearTimeoutFn) clearTimeoutFn(_refreshTimer);
    _refreshTimer = null;
    if (_ws) {
      try {
        _ws.close();
      } catch {
        /* noop */
      }
      _ws = null;
    }
    for (const uid of [..._byUid.keys()]) _remove(uid);
    _isSaving.clear();
    _draggingUid = null;
  }

  // ── 外部查詢 / 渲染 seam ───────────────────────────────────────────────────

  /** map.js 註冊重繪 callback。資料任何變動都會觸發。 */
  function onChange(cb) {
    _onChange = cb;
  }

  /** 取某 kind 的 entity 陣列（map.js 渲染用）。kind 取自 attributes.kind。 */
  function getEntitiesByKind(kind) {
    const out = [];
    for (const { entity } of _byUid.values()) {
      if (entity.attributes && entity.attributes.kind === kind) out.push(entity);
    }
    return out;
  }

  /** 依 source 取 entity（P2-05：TAK 單位無 attributes.kind，改用 source 過濾）。 */
  function getEntitiesBySource(source) {
    const out = [];
    for (const { entity } of _byUid.values()) {
      if (entity.source === source) out.push(entity);
    }
    return out;
  }

  /** 取單顆 entity（編輯器讀 version_clock / click 回查用）。 */
  function getEntity(uid) {
    const rec = _byUid.get(uid);
    return rec ? rec.entity : null;
  }

  /**
   * 拖曳中的本地樂觀位移（不 POST、不 emit）：標 _draggingUid（防遠端覆蓋，防護 4）、
   * 就地改 lat/lon。供 map.js 每幀平滑移動 GPU symbol —— **不 _emitChange**，由 map.js
   * 自行 _renderZones({skipHandleSync}) 重畫（避免重繪時 re-sync 干擾正在拖的 handle）。
   * dragend 才由 caller 呼 updateEntity 落地（PUT + If-Match，會清 dragging）。
   * 回傳是否套用（uid 不存在 → false）。
   */
  function dragLocal(uid, lat, lon) {
    const rec = _byUid.get(uid);
    if (!rec) return false;
    _draggingUid = uid;
    rec.entity.lat = lat;
    rec.entity.lon = lon;
    return true;
  }

  function _debugState() {
    return {
      count: _byUid.size,
      saving: [..._isSaving],
      dragging: _draggingUid,
      connected: !!_ws,
    };
  }

  return {
    connect,
    stop,
    resync,
    createEntity,
    updateEntity: _putEntity,
    deleteEntity,
    onChange,
    getEntitiesByKind,
    getEntitiesBySource,
    getEntity,
    dragLocal,
    // 測試 hook
    _onMessage,
    _applyEntity,
    _applyDelete,
    _byUid,
    _isSaving,
    _setDragging: (uid) => {
      _draggingUid = uid;
    },
    _debugState,
  };
}

function _defaultWsUrl() {
  if (typeof location === "undefined") return "";
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}/api/cop/ws/updates`;
}
