/**
 * map/cop_stream.js — COP entity 即時同步 client（issue #29 PR-E）
 *
 * 與既有 map_config 圖層「疊加」而非取代：cop_entities 以獨立 marker 層即時渲染，
 * 可 toggle 顯示。operator 透過 /api/cop/* per-entity API 建立 / 拖移 / 刪除，
 * server WebSocket 廣播 → 其他 client 不 reload 即時更新。
 *
 * 對應 issue #29 原 plan 的 6 道防護（per-entity 模型下）：
 *  1. _isSaving（in-flight write）：某 uid 寫入飛行中 → 跳過該 uid 的 incoming WS / resync，
 *     避免 stale 覆蓋本地剛送出的值（根除 commit 58bb5d4 的 clobber）。
 *  2. post-fetch double-check：resync GET 回來後逐 uid 再確認非 in-flight 才套用。
 *  3. server 端 atomic write + 樂觀鎖：PR-A/B 已備（PUT/DELETE 帶 If-Match）。
 *  4. 編輯中（_draggingUid）skip：正在拖的 entity 不被遠端更新拉走。
 *  5. last-write-wins by version_clock：merge 單調，重複 / 亂序 / 重連 replay 安全。
 *  6. logout / page-hide cleanup：關 WS、清 marker、停重連。
 *
 * 為可測試，所有外部相依（map / WebSocket / fetch / Marker / token / role）皆注入。
 */

const WS_SUBPROTOCOL = "ics-cop-v1";
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30000;

/**
 * @param {object} deps
 *  - map: MapLibre map（需 getCenter()；marker 用）
 *  - getToken: () => string|null
 *  - authFetch: (url, opts) => Promise<Response>
 *  - canWrite: () => boolean（operator+ 才可建立/拖/刪）
 *  - MarkerCtor: maplibregl.Marker 建構子（注入便於測試）
 *  - WebSocketCtor: WebSocket 建構子（注入便於測試）
 *  - wsUrl: WS 連線 URL（預設依 location 推導）
 *  - apiBase: REST base（預設 ''）
 *  - setTimeoutFn / clearTimeoutFn: 計時器（注入便於測試）
 */
export function createCopStream(deps) {
  const {
    map,
    getToken,
    authFetch,
    canWrite = () => false,
    MarkerCtor,
    WebSocketCtor = (typeof WebSocket !== "undefined" ? WebSocket : null),
    wsUrl = _defaultWsUrl(),
    apiBase = "",
    setTimeoutFn = (typeof setTimeout !== "undefined" ? setTimeout : null),
    clearTimeoutFn = (typeof clearTimeout !== "undefined" ? clearTimeout : null),
    confirmFn = (typeof confirm !== "undefined" ? confirm : () => false),
  } = deps;

  // uid → { entity, marker }
  const _byUid = new Map();
  // 寫入飛行中的 uid（防護 1）
  const _isSaving = new Set();
  // 正在拖的 uid（防護 4）
  let _draggingUid = null;
  let _ws = null;
  let _reconnectAttempt = 0;
  let _reconnectTimer = null;
  let _visible = true;
  let _stopped = false;
  // 渲染委派（PR-G1）：map.js 註冊 onChange 後 → cop_stream 不再自建 marker，
  // 由 map.js 用 EntityLayer 渲染；未註冊（PR-E standalone）則 fallback 自建 marker。
  let _onChange = null;
  let _renderDelegated = false;

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
    _renderUpsert(entity);
    return true;
  }

  /** 套用一筆遠端刪除（soft-delete：stale 過期 → 從畫面移除）。 */
  function _applyDelete(uid, versionClock) {
    if (_isSaving.has(uid) || _draggingUid === uid) return false;
    const existing = _byUid.get(uid);
    // 遲到的舊刪除（version 比現值小）忽略
    if (existing && versionClock != null && versionClock < existing.entity.version_clock) return false;
    _renderRemove(uid);
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
      default:
        break;
    }
  }

  // ── 渲染（marker）──────────────────────────────────────────────────────

  function _renderUpsert(entity) {
    const existing = _byUid.get(entity.uid);
    if (existing) {
      existing.entity = entity;
      if (existing.marker) existing.marker.setLngLat([entity.lon, entity.lat]);
      _emitChange();
      return;
    }
    // 委派模式：只存資料、不自建 marker（map.js 用 EntityLayer 渲染）
    let marker = null;
    if (!_renderDelegated) {
      marker = new MarkerCtor({ draggable: canWrite() });
      marker.setLngLat([entity.lon, entity.lat]);
      if (marker.addTo && _visible) marker.addTo(map);
    }
    const rec = { entity, marker };
    _byUid.set(entity.uid, rec);
    if (marker) _wireMarkerDrag(rec);
    _emitChange();
  }

  function _renderRemove(uid) {
    const rec = _byUid.get(uid);
    if (!rec) return;
    if (rec.marker && rec.marker.remove) rec.marker.remove();
    _byUid.delete(uid);
    _emitChange();
  }

  function _wireMarkerDrag(rec) {
    const m = rec.marker;
    if (!canWrite() || !m.on) return;
    m.on("dragstart", () => {
      _draggingUid = rec.entity.uid;
    });
    m.on("dragend", async () => {
      const uid = rec.entity.uid;
      const ll = m.getLngLat();
      _draggingUid = null;
      await _putEntity(uid, { lat: ll.lat, lon: ll.lng });
    });
    // 雙擊 marker → 確認後 soft-delete（dblclick 避免誤刪、不與單擊/拖衝突）
    const el = m.getElement && m.getElement();
    if (el && el.addEventListener) {
      el.addEventListener("dblclick", (ev) => {
        ev.stopPropagation();
        if (confirmFn(`刪除 COP 標記 ${rec.entity.callsign || rec.entity.uid}？`)) {
          deleteEntity(rec.entity.uid);
        }
      });
    }
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

  /** 在地圖中心放一顆新 COP 標記（POST）。 */
  async function placeAtCenter(fields = {}) {
    if (!canWrite()) return null;
    const c = map.getCenter();
    const body = { type: "a-f-G-U-C", lat: c.lat, lon: c.lng, ...fields };
    const resp = await authFetch(`${apiBase}/api/cop/entities`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) return null;
    const entity = await resp.json();
    // 本地立即 upsert（WS 廣播也會到，version_clock LWW 冪等不重複）
    _renderUpsert(entity);
    return entity;
  }

  /** 更新一顆 entity（PUT + If-Match）。409 → 採 server 現值。 */
  async function _putEntity(uid, patch) {
    const rec = _byUid.get(uid);
    if (!rec || !canWrite()) return;
    const expected = rec.entity.version_clock;
    return _withSaving(uid, async () => {
      const resp = await authFetch(`${apiBase}/api/cop/entities/${encodeURIComponent(uid)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json", "If-Match": String(expected) },
        body: JSON.stringify(patch),
      });
      if (resp.ok) {
        _renderUpsert(await resp.json());
      } else if (resp.status === 409) {
        const data = await resp.json();
        if (data && data.server_entity) _renderUpsert(data.server_entity); // 對齊 server，避免本地漂位
      }
    });
  }

  /** 刪除一顆 entity（DELETE + If-Match，soft-delete）。 */
  async function deleteEntity(uid) {
    const rec = _byUid.get(uid);
    if (!rec || !canWrite()) return;
    const expected = rec.entity.version_clock;
    return _withSaving(uid, async () => {
      const resp = await authFetch(`${apiBase}/api/cop/entities/${encodeURIComponent(uid)}`, {
        method: "DELETE",
        headers: { "If-Match": String(expected) },
      });
      if (resp.ok) {
        _renderRemove(uid);
      } else if (resp.status === 409) {
        const data = await resp.json();
        if (data && data.server_entity) _renderUpsert(data.server_entity);
      }
    });
  }

  // ── 全量 resync（連線 / 重連時）───────────────────────────────────────────

  async function resync() {
    // 進入時先快照「已知 uid」。removal 只考慮這批 —— resync 飛行中（GET await 期間）
    // 新建的 entity（如同時 placeAtCenter POST 落地）不在快照內，不會被誤刪。
    const known = new Set(_byUid.keys());
    const resp = await authFetch(`${apiBase}/api/cop/entities`);
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
      if (!seen.has(uid) && !_isSaving.has(uid) && _draggingUid !== uid) _renderRemove(uid);
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
    try {
      ws = new WebSocketCtor(wsUrl, [WS_SUBPROTOCOL, `ics.session.${token}`]);
    } catch {
      _scheduleReconnect();
      return;
    }
    _ws = ws;
    ws.onopen = () => {
      _reconnectAttempt = 0;
      // 連上先全量 resync（version_clock merge 冪等，補上斷線期間漏掉的）
      resync();
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
      if (_ws === ws) _ws = null;
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

  /** 防護 6：停止（logout / page-hide）—關 WS、停重連、清 marker。 */
  function stop() {
    _stopped = true;
    if (_reconnectTimer && clearTimeoutFn) clearTimeoutFn(_reconnectTimer);
    _reconnectTimer = null;
    if (_ws) {
      try {
        _ws.close();
      } catch {
        /* noop */
      }
      _ws = null;
    }
    for (const uid of [..._byUid.keys()]) _renderRemove(uid);
    _isSaving.clear();
    _draggingUid = null;
  }

  // ── 顯示切換 ───────────────────────────────────────────────────────────

  function setVisible(visible) {
    _visible = visible;
    // 委派模式下 marker 為 null（由 map.js 的 EntityLayer 控制顯示）→ 跳過
    for (const { marker } of _byUid.values()) {
      if (!marker) continue;
      if (visible) {
        if (marker.addTo) marker.addTo(map);
      } else if (marker.remove) {
        marker.remove();
      }
    }
  }

  function toggleVisible() {
    setVisible(!_visible);
    return _visible;
  }

  // ── 測試 / 外部查詢 ───────────────────────────────────────────────────────
  function _debugState() {
    return {
      count: _byUid.size,
      saving: [..._isSaving],
      dragging: _draggingUid,
      connected: !!_ws,
      visible: _visible,
    };
  }

  /** map.js 註冊重繪 callback → 進入委派模式（cop_stream 不再自建 marker）。 */
  function onChange(cb) {
    _onChange = cb;
    _renderDelegated = true;
  }

  /** 取某 kind 的 entity 陣列（map.js 渲染用）。kind 取自 attributes.kind。 */
  function getEntitiesByKind(kind) {
    const out = [];
    for (const { entity } of _byUid.values()) {
      if (entity.attributes && entity.attributes.kind === kind) out.push(entity);
    }
    return out;
  }

  /** 取單顆 entity（編輯器讀 version_clock 用）。 */
  function getEntity(uid) {
    const rec = _byUid.get(uid);
    return rec ? rec.entity : null;
  }

  return {
    connect,
    stop,
    resync,
    placeAtCenter,
    deleteEntity,
    setVisible,
    toggleVisible,
    onChange,
    getEntitiesByKind,
    getEntity,
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
