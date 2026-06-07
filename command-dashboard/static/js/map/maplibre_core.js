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

// Fallback empty dark style — pmtiles.js 未載入時退而求其次（底圖不顯示，僅深色背景）。
// glyphs URL：P1-10b 步驟 7 階段 3b vendor Noto Sans Regular（OFL，~6.8MB / 256 pbf）
// 為了 polygon / route / flow / infra 的 text-field labels 可顯示中文。
const EMPTY_DARK_STYLE = {
  version: 8,
  glyphs: '/static/fonts/glyphs/{fontstack}/{range}.pbf',
  sources: {},
  layers: [
    {
      id: 'background',
      type: 'background',
      paint: { 'background-color': '#0d1117' },
    },
  ],
};

// P1-10c：兩套 Protomaps basemap style（dark=black flavor / muted-day=grayscale flavor）。
// style.json 由 @protomaps/basemaps v5.7.2 產生，source 走 pmtiles:///tiles/pmtiles/taiwan.pmtiles，
// 對齊 taiwan.pmtiles schema（source-layer 已驗證對得上，見 docs/design/POLICY.md）。
const BASEMAP_STYLES = {
  dark: '/static/styles/basemap-dark.json',
  'muted-day': '/static/styles/basemap-muted-day.json',
};
const DEFAULT_BASEMAP_THEME = 'dark';
let _basemapTheme = sessionStorage.getItem('_basemapTheme') || DEFAULT_BASEMAP_THEME;
let _pmtilesProtocolRegistered = false;
// 目前 basemap 佔用的 layer id（用於主題切換時精準移除底圖層，不動 overlay 層）。
let _basemapLayerIds = [];
// 主題切換 in-flight 旗標（防快速連點重入 / 重複 fetch）。
let _themeSwitching = false;

/** 註冊 MapLibre 的 pmtiles:// protocol（idempotent）。回傳是否成功（pmtiles.js 是否已載入）。*/
function _ensurePmtilesProtocol() {
  if (_pmtilesProtocolRegistered) return true;
  if (!window.pmtiles || !window.maplibregl) return false;
  const protocol = new window.pmtiles.Protocol();
  window.maplibregl.addProtocol('pmtiles', protocol.tile);
  _pmtilesProtocolRegistered = true;
  return true;
}

/** 取得指定主題的 basemap style URL（未知主題 fallback 預設）。*/
export function basemapStyleUrl(theme) {
  return BASEMAP_STYLES[theme] || BASEMAP_STYLES[DEFAULT_BASEMAP_THEME];
}

/** 目前 basemap 主題（'dark' | 'muted-day'）。*/
export function getBasemapTheme() {
  return _basemapTheme;
}

/**
 * 切換 basemap 主題（dark ↔ muted-day）。
 *
 * 設計（不走 map.setStyle）：setStyle 會丟棄所有 runtime overlay（EntityLayers /
 * MGRS grid / draw tools / event popup / drag handles）+ 其 listener，重建成本高且
 * 易漏。改為**只抽換底圖層**——移除目前 basemap layer、把另一主題的 basemap layer
 * 重新加在 overlay 之下（beforeId = 最底 overlay），overlay 完全不動。
 *
 * 兩主題共用同一 pmtiles source（同檔），layer id 由 @protomaps/basemaps 決定，
 * 結構相同、僅 paint 不同；以實際加入的 id 追蹤，跨主題差異也安全。
 *
 * @param {string} theme 'dark' | 'muted-day'
 * @returns {Promise<boolean>} 是否切換成功
 */
export async function setBasemapTheme(theme) {
  if (!_map || !BASEMAP_STYLES[theme]) return false;
  if (_themeSwitching) return false;              // in-flight 防重入（review #62 MED）
  if (!_ensurePmtilesProtocol()) return false;    // 無 pmtiles 無法載底圖
  _themeSwitching = true;
  const map = _map;
  try {
    const style = await fetch(basemapStyleUrl(theme)).then((r) => r.json());
    const newIds = (style.layers || []).map((l) => l.id);
    // 要移除的現有底圖層 id：已追蹤則用追蹤值；首次切換（'load' 尚未填追蹤值）退而用
    // **新 style 的 layer id**（兩主題 layer id 結構相同 → 等同目前底圖層）。
    // **絕不**用 getStyle().layers（含 overlay）當 fallback——否則會誤刪 overlay
    //（review 後快速連點實測抓到的 regression）。
    const removeIds = _basemapLayerIds.length ? _basemapLayerIds : newIds;
    for (const id of removeIds) {
      if (map.getLayer(id)) map.removeLayer(id);
    }
    // 2. 確保底圖 source 在（pmtiles source 共用；缺才補）
    for (const [sid, sdef] of Object.entries(style.sources || {})) {
      if (!map.getSource(sid)) map.addSource(sid, sdef);
    }
    // 3. 插入點 = 目前最底層（即最底 overlay），讓底圖層落在所有 overlay 之下
    const remaining = map.getStyle().layers;
    const beforeId = remaining.length ? remaining[0].id : undefined;
    // 4. 依序加回另一主題的底圖層（在 beforeId 之下，保持底圖在最底）
    _basemapLayerIds = [];
    for (const layer of style.layers || []) {
      if (map.getLayer(layer.id)) map.removeLayer(layer.id);  // 防護：id 已存在先移除（race/重入）
      map.addLayer(layer, beforeId);
      _basemapLayerIds.push(layer.id);
    }
    _basemapTheme = theme;
    sessionStorage.setItem('_basemapTheme', theme);
    return true;
  } catch (e) {
    console.error('[maplibre_core] 切換 basemap 主題失敗', e);
    return false;
  } finally {
    _themeSwitching = false;
  }
}

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

  // P1-10c：先註冊 pmtiles protocol（style 的 source url 用 pmtiles://），再建 map。
  // pmtiles.js 未載入時 fallback empty dark style（底圖不顯示，但 entity layer 仍可運作）。
  const havePmtiles = _ensurePmtilesProtocol();
  if (!havePmtiles) {
    console.warn('[maplibre_core] pmtiles.js 未載入，basemap fallback empty dark style（底圖不顯示）');
  }
  const initialStyle = havePmtiles ? basemapStyleUrl(_basemapTheme) : EMPTY_DARK_STYLE;

  _map = new window.maplibregl.Map({
    container: containerId,
    style: initialStyle,
    center: HSINCHU_CENTER,
    zoom: HSINCHU_ZOOM,
    // ODbL：basemap source 的 attribution（© OpenStreetMap contributors）由 style 提供，
    // compact 控制項顯示於右下角（i 圖示展開）。
    attributionControl: { compact: true },
    doubleClickZoom: false,           // 雙擊放 coord pin，不縮放
    pitchWithRotate: false,
    dragRotate: false,                // 戰術 2D，不旋轉
    touchPitch: false,
  });

  // 不掛 NavigationControl — 既有 toolbar (#map-tools) 已占 top-right，重疊；
  // zoom 走滑鼠滾輪 / 觸控板 pinch 即可。指北、傾斜 P1 都不啟用。

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
  // touch 進行中旗標：阻止 touch 結束後瀏覽器補發的 synthetic mousedown 重啟計時器，
  // 避免 lpTimer/lpMoved 在 mouse/touch 兩條路徑間互相干擾（#165 code-review fix）
  let _touchActive = false;
  _map.on('mousedown', (e) => {
    if (_touchActive) return;
    if (e.originalEvent && e.originalEvent.button !== 0) return;
    // 短路：mousedown target 在 maplibregl.Marker（drag handle / coord pin / 未來
    // 任何 HTML marker）內 → 不啟動長按計時器。否則使用者在 event circle 上按住
    // 想拖時，map 自己的長按 listener 也會 fire，650ms 後再彈出一個新的事件 popup
    // （dogfood 撞到的 regression）。drag handle 自己 stopPropagation 只攔 click，
    // mousedown 仍會冒泡到 map container 觸發 MapLibre 的 mousedown 事件。
    if (e.originalEvent?.target?.closest?.('.maplibregl-marker')) return;
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

  // 觸控長按（touch device 上與 mouse 長按對等）
  // 10px 位移門檻，避免 fat-finger 微抖誤取消
  const TOUCH_SLOP = 10;
  let lpTouchOrigin = null;
  _map.getCanvas().addEventListener('touchstart', (ev) => {
    _touchActive = true;
    if (ev.touches.length !== 1) return;
    if (isSuppressed()) return;
    if (ev.target.closest?.('.maplibregl-marker')) return;
    const t = ev.touches[0];
    const rect = _map.getCanvas().getBoundingClientRect();
    const ll = _map.unproject([t.clientX - rect.left, t.clientY - rect.top]);
    lpTouchOrigin = { x: t.clientX, y: t.clientY };
    lpMoved = false;
    if (lpTimer) { clearTimeout(lpTimer); lpTimer = null; }
    lpTimer = setTimeout(() => {
      lpTimer = null;
      if (!lpMoved && callbacks.onLongPress) callbacks.onLongPress({ lat: ll.lat, lng: ll.lng });
    }, LONG_PRESS_MS);
  }, { passive: true });
  _map.getCanvas().addEventListener('touchmove', (ev) => {
    if (!lpTouchOrigin || !ev.touches.length) return;
    const t = ev.touches[0];
    const dx = t.clientX - lpTouchOrigin.x;
    const dy = t.clientY - lpTouchOrigin.y;
    if (dx * dx + dy * dy > TOUCH_SLOP * TOUCH_SLOP) {
      lpMoved = true;
      if (lpTimer) { clearTimeout(lpTimer); lpTimer = null; }
    }
  }, { passive: true });
  _map.getCanvas().addEventListener('touchend', () => {
    if (lpTimer) { clearTimeout(lpTimer); lpTimer = null; }
    lpTouchOrigin = null;
    // 延遲清旗標，讓 synthetic mousedown（touchend 後約 300ms 補發）仍被攔截
    setTimeout(() => { _touchActive = false; }, 400);
  }, { passive: true });
  _map.getCanvas().addEventListener('touchcancel', () => {
    lpMoved = true;
    if (lpTimer) { clearTimeout(lpTimer); lpTimer = null; }
    lpTouchOrigin = null;
    setTimeout(() => { _touchActive = false; }, 400);
  }, { passive: true });

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

  // 覆蓋層點擊穿透：MapLibre 的 map.on('click') 只對 map render 區域內的 click 觸發；
  // banners / islands / panels 是 DOM siblings（不在 #leaflet-map 內），click 不會洩漏到 MapLibre。
  // 不需要像 Leaflet 那樣手動 stopPropagation。
  // （Leaflet 的 L.DomEvent.disableClickPropagation 只擋 Leaflet 內部 event system，
  //  我先前用 e.stopPropagation() 取代是錯的 — 會連 document-level data-action 委派一起擋掉，
  //  導致 toggleCoordMode / toggleMgrsGrid 等 UI 按鈕全部失效。）

  // 容器尺寸變動補 resize（取代 Leaflet invalidateSize）
  setTimeout(() => _map.resize(), 0);

  // P1-10c 主題切換：初始 style 載入後，從 live map 記錄 basemap 佔用的 layer id
  // （供 setBasemapTheme 精準移除，不誤刪 overlay）。此 once('load') 在 map.js
  // _ensureEntityLayers 的 load handler 之前註冊（initMaplibre 先跑），故此刻 overlay
  // 尚未加入，getStyle().layers 恰為純 basemap 層。
  // 改從 live map 取（取代先前獨立 fetch）— 消除「fetch 未回前就切主題 → addLayer
  // 撞重複 id」的 race（review #62 HIGH）。
  if (havePmtiles) {
    _map.once('load', () => {
      _basemapLayerIds = _map.getStyle().layers.map((l) => l.id);
    });
  }

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
