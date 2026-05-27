/**
 * event_drag.js — 事件 marker 拖曳 + 點擊轉派
 *
 * P1-10b step 9 後新增：補事件 zone 在 MapLibre symbol layer 化後失去的拖曳行為。
 *
 * 沿用 step 8 label_markers.js 的 hybrid B 模式：
 *   - 視覺：MapLibre symbol layer 的 zones-base circle + zones-abbr SDF 不動
 *   - 拖曳：每個事件 zone 上方蓋一個透明 maplibregl.Marker（HTML element），
 *           draggable:true，dragend 寫回 zone.lat/lng + saveMapConfig + PATCH /api/events
 *   - 點擊：handle 蓋住 zones-base 的 click handler，所以必須轉派 onClick → caller
 *           的 _onZoneClick（不然事件點不開 modal）
 *
 * 與 label_markers.js 的差異（為什麼不直接重用）：
 *   - 不需要 setFeatureState({dragging:true}) — 事件 zone 拖曳時 circle 留原處作為
 *     參考（沒有 ghost label 的概念）；用 feature-state 會增加 paint expression 複雜度
 *   - 不需要 ghost text — 事件只有 SDF abbr，視覺重點是 circle 位置而非文字
 *   - 純粹 forward click → 不像 label 點了沒事，事件點 = 開 modal，必須轉派
 *
 * 範圍：**只服務事件 zone**（caller 自己 filter `z.event_id || z.event_code`）。
 *       節點 zone（指揮/前進/...）不在 scope，要動就走「帳號管理 → 重新設定節點」
 *       或之後另開 sub-item。
 */

// 事件 zone circle 直徑：zones-base `circle-radius` 在 event entity 時為 13 →
// 直徑 26。Handle 對齊到 26+2px buffer = 28，搭配 border-radius:50% 後形成
// 與內圓緊貼的圓形 hover 虛線框。
const HANDLE_SIZE = 28;

export class EventDragManager {
  /**
   * @param {maplibregl.Map} map
   * @param {object} maplibreglNs - window.maplibregl global
   */
  constructor(map, maplibreglNs) {
    if (!map) throw new Error('EventDragManager: map required');
    if (!maplibreglNs?.Marker) throw new Error('EventDragManager: maplibregl.Marker required');
    this.map = map;
    this.maplibregl = maplibreglNs;
    this.markers = new Map();   // featureId → maplibregl.Marker
  }

  /**
   * Sync 事件 zones → drag handles。caller 在每次 _renderZones 完成後呼叫。
   *
   * @param {Array<{id, lat, lng, ...}>} eventZones - 已 filter 過的事件 zone 列表
   * @param {(zoneId, latlng: {lat, lng}, fromLatlng: {lat, lng}) => void} onDragEnd
   *        drop 後寫 DB / map_config；fromLatlng = drag 開始前的座標（caller 用來
   *        在 note 內顯示「移動 from → to」）
   * @param {(zoneId) => void} [onClick] - 短按 handle 觸發；undefined → 短按 noop
   * @param {(zoneId, latlng: {lat, lng}) => void} [onDrag] - drag 過程每幀 fire；
   *        caller 用來即時更新 zones source 讓 GPU circle 跟手（不持久化）。
   *        undefined → drag 中 GPU 不跟手，等 dragend 才跳新位置。
   */
  sync(eventZones, onDragEnd, onClick, onDrag) {
    const seenIds = new Set();
    for (const z of eventZones) {
      if (!z?.id) continue;
      const lat = Number(z.lat);
      const lng = Number(z.lng);
      if (!Number.isFinite(lat) || !Number.isFinite(lng)) continue;
      seenIds.add(z.id);
      this._upsert(z, [lng, lat], onDragEnd, onClick, onDrag);
    }
    for (const [id, m] of this.markers) {
      if (!seenIds.has(id)) {
        m.remove();
        this.markers.delete(id);
      }
    }
  }

  _upsert(zone, lnglat, onDragEnd, onClick, onDrag) {
    const existing = this.markers.get(zone.id);
    if (existing) {
      existing.setLngLat(lnglat);
      return;
    }
    const el = document.createElement('div');
    el.className = 'mlb-event-drag-handle';
    el.dataset.eventId = zone.id;
    // **不可用 `el.style.cssText = ...`** — maplibregl.Marker 自己把 `transform`
    // 寫在 inline style 上控制螢幕位置，覆寫整包 cssText 會擦掉 transform，導致
    // 下個 frame 才補回，視覺上 marker 會閃一下又歸位（regression 留痕）。
    // 改用個別 property 設定，不碰其他 inline style。
    el.style.width = `${HANDLE_SIZE}px`;
    el.style.height = `${HANDLE_SIZE}px`;
    el.style.boxSizing = 'border-box';
    el.style.borderRadius = '50%';        // 圓形 — 配合 border 才能跟事件 circle 貼合
    el.style.background = 'transparent';
    el.style.cursor = 'grab';
    el.style.userSelect = 'none';
    // border 1.5px transparent 是 hover 切換用的「位置占位」— 沒這行的話
    // hover 時加 border 會讓 box 多 3px 整個跳，破壞與 SDF circle 對齊。
    el.style.border = '1.5px dashed transparent';

    el.addEventListener('mouseenter', () => {
      el.style.borderColor = 'rgba(88, 166, 255, 0.7)';
      el.style.background = 'rgba(88, 166, 255, 0.08)';
    });
    el.addEventListener('mouseleave', () => {
      el.style.borderColor = 'transparent';
      el.style.background = 'transparent';
    });

    // 區分 click vs drag：marker dragstart 設 didDrag，drag 完瀏覽器 fire 的 click
    // 被 swallow + reset，純 click 才轉派。**不**掛 mousedown listener — 早期實驗
    // 證實會干擾 maplibregl.Marker 內部 drag 啟動 listener（regression 留痕）。
    let didDrag = false;
    let fromLatlng = null;   // dragstart 時捕獲的原座標，dragend 帶給 caller 寫 note
    el.addEventListener('click', (e) => {
      e.stopPropagation();    // 不穿透到 map（避免 draw mode 誤判為新 vertex）
      if (didDrag) { didDrag = false; return; }
      if (typeof onClick === 'function') onClick(zone.id);
    });

    const marker = new this.maplibregl.Marker({
      element: el,
      draggable: true,
      anchor: 'center',
    })
      .setLngLat(lnglat)
      .addTo(this.map);

    marker.on('dragstart', () => {
      didDrag = true;
      el.style.cursor = 'grabbing';
      const ll = marker.getLngLat();
      fromLatlng = { lat: ll.lat, lng: ll.lng };
    });
    // drag 每幀 fire（mousemove during drag）— caller 即時更新 zones source GeoJSON
    // 座標，讓 GPU 渲染的 circle 跟著 handle 走。**不**寫 map_config / 不 PATCH，
    // 那些放在 dragend 一次處理。
    marker.on('drag', () => {
      if (typeof onDrag !== 'function') return;
      const ll = marker.getLngLat();
      onDrag(zone.id, { lat: ll.lat, lng: ll.lng });
    });
    marker.on('dragend', () => {
      el.style.cursor = 'grab';
      const ll = marker.getLngLat();
      const lat = Math.round(ll.lat * 1000000) / 1000000;
      const lng = Math.round(ll.lng * 1000000) / 1000000;
      const from = fromLatlng ? {
        lat: Math.round(fromLatlng.lat * 1000000) / 1000000,
        lng: Math.round(fromLatlng.lng * 1000000) / 1000000,
      } : null;
      fromLatlng = null;
      if (typeof onDragEnd === 'function') onDragEnd(zone.id, { lat, lng }, from);
    });

    this.markers.set(zone.id, marker);
  }

  clear() {
    for (const m of this.markers.values()) m.remove();
    this.markers.clear();
  }

  getMarkerCount() {
    return this.markers.size;
  }

  /**
   * 進入「focus 模式」：activeId 之外的 handle 全部 dim（opacity 0.15）且不能互動。
   * 與 zones GPU layer 的 feature-state.dimmed paint expression 對應，視覺一致。
   * 給 right panel 長按事件 → 地圖 highlight 該事件、暗化其他 用。
   */
  dimAllExcept(activeId) {
    for (const [id, marker] of this.markers) {
      const el = marker.getElement();
      const active = id === activeId;
      el.style.opacity = active ? '1' : '0.15';
      el.style.pointerEvents = active ? 'auto' : 'none';
    }
  }

  /** 退出 focus 模式：所有 handle 回正常 */
  undimAll() {
    for (const marker of this.markers.values()) {
      const el = marker.getElement();
      el.style.opacity = '1';
      el.style.pointerEvents = 'auto';
    }
  }
}
