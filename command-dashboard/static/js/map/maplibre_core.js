/**
 * maplibre_core.js — MapLibre GL JS 地圖實例 lifecycle + 互動事件 wiring
 *
 * P1-10b 步驟 4：取代原 map.js._initLeaflet
 *
 * 職責：
 *   - 建立 MapLibre map instance（暫時無底圖，P1-10c 接 PMTiles）
 *   - wiring：click / dblclick / 長按 / moveend-zoomend / dragstart
 *   - 座標 pin（雙擊放置藍色十字）：showCoordPin / clearCoordPin / getCoordPinLatLng
 *   - sessionStorage view restore
 *
 * 不負責（屬之後步驟）：
 *   - entity rendering（zone / polygon / infra / route / flow / MGRS grid）→ 步驟 5–10
 *   - PMTiles 底圖載入 → P1-10c
 *   - SDF icon / 4-layer state stack / hover state → 步驟 5–7
 *
 * 借鏡 mini-taiwan TrainSymbolLayer.ts lifecycle（initialize / destroy）但精簡化。
 */

const HSINCHU_CENTER = [121.0149, 24.8283];  // [lng, lat]（MapLibre 順序）
const HSINCHU_ZOOM = 15;
const LONG_PRESS_MS = 650;

// 暫時的 empty dark style — P1-10c 才接真實 PMTiles
const EMPTY_DARK_STYLE = {
  version: 8,
  sources: {},
  layers: [
    {
      id: 'background',
      type: 'background',
      paint: { 'background-color': '#0d1117' },
    },
  ],
};

let _map = null;
let _coordPin = null;
let _coordPinMarker = null;

/**
 * 初始化 MapLibre map instance。
 *
 * @param {string} containerId DOM element id（沿用 `leaflet-map` 為 alias）
 * @param {object} callbacks  { onClick(latlng), onDblclick(latlng), onLongPress(latlng), onMoveEnd({lat,lng,zoom}), shouldSuppressInteraction() }
 * @returns {maplibregl.Map} map instance
 */
export function initMaplibre(containerId, callbacks = {}) {
  if (!window.maplibregl) {
    console.error('[maplibre_core] maplibregl global 未載入');
    return null;
  }
  const container = document.getElementById(containerId);
  if (!container) return null;
  if (_map) return _map;

  _map = new window.maplibregl.Map({
    container: containerId,
    style: EMPTY_DARK_STYLE,
    center: HSINCHU_CENTER,
    zoom: HSINCHU_ZOOM,
    attributionControl: false,        // 之後 P1-10c 加 Protomaps attribution
    doubleClickZoom: false,           // 雙擊放 coord pin，不縮放
    pitchWithRotate: false,
    dragRotate: false,                // 戰術 2D，不旋轉
    touchPitch: false,
  });

  // 內建 zoom 控制（右上）
  _map.addControl(new window.maplibregl.NavigationControl({
    showCompass: false,               // 不旋轉故不需指北
    visualizePitch: false,
  }), 'top-right');

  // ── 事件 wiring（callback 模式，邏輯留 caller） ──
  const isSuppressed = () => callbacks.shouldSuppressInteraction?.() ?? false;

  _map.on('click', (e) => {
    if (callbacks.onClick) callbacks.onClick({ lat: e.lngLat.lat, lng: e.lngLat.lng });
  });

  _map.on('dblclick', (e) => {
    if (isSuppressed()) return;
    if (callbacks.onDblclick) callbacks.onDblclick({ lat: e.lngLat.lat, lng: e.lngLat.lng });
  });

  // 長按 650ms → onLongPress（取代 Leaflet 的 mousedown/mousemove/mouseup 組合）
  let lpTimer = null;
  let lpMoved = false;
  _map.on('mousedown', (e) => {
    if (e.originalEvent && e.originalEvent.button !== 0) return;
    if (isSuppressed()) return;
    lpMoved = false;
    if (lpTimer) { clearTimeout(lpTimer); lpTimer = null; }
    const latlng = { lat: e.lngLat.lat, lng: e.lngLat.lng };
    lpTimer = setTimeout(() => {
      lpTimer = null;
      if (!lpMoved && callbacks.onLongPress) callbacks.onLongPress(latlng);
    }, LONG_PRESS_MS);
  });
  const cancelLp = () => {
    lpMoved = true;
    if (lpTimer) { clearTimeout(lpTimer); lpTimer = null; }
  };
  _map.on('mousemove', cancelLp);
  _map.on('mouseup', () => { if (lpTimer) { clearTimeout(lpTimer); lpTimer = null; } });
  _map.on('dragstart', cancelLp);

  // ── sessionStorage view restore ──
  const savedView = sessionStorage.getItem('_mapView');
  if (savedView) {
    try {
      const { lat, lng, zoom } = JSON.parse(savedView);
      _map.jumpTo({ center: [lng, lat], zoom });
    } catch (e) {
      // 解析失敗保留預設 center/zoom
    }
  }

  _map.on('moveend', () => {
    if (!callbacks.onMoveEnd) return;
    const c = _map.getCenter();
    callbacks.onMoveEnd({
      lat: Math.round(c.lat * 100000) / 100000,
      lng: Math.round(c.lng * 100000) / 100000,
      zoom: _map.getZoom(),
    });
  });

  // 防止覆蓋層點擊穿透到地圖（取代 Leaflet 的 DomEvent.disableClickPropagation）
  [
    'poly-draw-banner', 'route-draw-banner',
    'node-place-banner', 'event-pin-banner',
    'mgrs-island', 'layer-panel', 'map-coord-panel',
  ].forEach((id) => {
    const node = document.getElementById(id);
    if (!node) return;
    node.addEventListener('click', (e) => e.stopPropagation());
    node.addEventListener('mousedown', (e) => e.stopPropagation());
    node.addEventListener('dblclick', (e) => e.stopPropagation());
    node.addEventListener('wheel', (e) => e.stopPropagation(), { passive: true });
  });

  // 容器尺寸變動補 resize（取代 Leaflet invalidateSize）
  setTimeout(() => _map.resize(), 0);

  return _map;
}

export function getMap() {
  return _map;
}

export function resizeMap() {
  if (_map) _map.resize();
}

export function getCenter() {
  if (!_map) return null;
  const c = _map.getCenter();
  return { lat: c.lat, lng: c.lng };
}

export function getZoom() {
  return _map ? _map.getZoom() : null;
}

// ── 座標 pin（雙擊放置的藍色十字） ──

const COORD_PIN_SVG =
  '<svg width="24" height="24" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">' +
  '<line x1="12" y1="1" x2="12" y2="23" stroke="#58a6ff" stroke-width="1.5" opacity=".9"/>' +
  '<line x1="1" y1="12" x2="23" y2="12" stroke="#58a6ff" stroke-width="1.5" opacity=".9"/>' +
  '<circle cx="12" cy="12" r="3.5" stroke="#58a6ff" stroke-width="1.5" fill="rgba(88,166,255,.18)"/>' +
  '</svg>';

export function showCoordPin(lat, lng) {
  if (!_map || !window.maplibregl) return;
  clearCoordPin();
  const el = document.createElement('div');
  el.innerHTML = COORD_PIN_SVG;
  el.style.pointerEvents = 'none';   // 不攔截後續 dblclick
  _coordPinMarker = new window.maplibregl.Marker({ element: el, anchor: 'center' })
    .setLngLat([lng, lat])
    .addTo(_map);
  _coordPin = { lat, lng };
}

export function clearCoordPin() {
  if (_coordPinMarker) {
    _coordPinMarker.remove();
    _coordPinMarker = null;
  }
  _coordPin = null;
}

export function getCoordPinLatLng() {
  return _coordPin ? { ...{ lat: _coordPin.lat, lng: _coordPin.lng } } : null;
}

export function hasCoordPin() {
  return _coordPin !== null;
}
