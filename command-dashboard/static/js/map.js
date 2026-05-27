/**
 * map.js — 指揮情境圖模組（C1-F CSP 模組化）
 *
 * 職責：
 *   - 載入 /static/map_config.json
 *   - 管理站內靜態圖與站外 MapLibre 地圖切換（P1-10b 起，原 Leaflet）
 *   - 渲染基本節點 / 事件 marker
 *   - 提供 main.js 事件委派所需的地圖操作函式
 *
 * 可 import：ws.js + map/maplibre_core.js。不得 import events.js / decisions.js / cop.js / charts.js。
 *
 * P1-10b 進行中（issue #19）：
 *   - 步驟 4 完成：map instance + dblclick coord pin + 長按 popup + view restore（本檔）
 *   - 步驟 5–10 待做：entity layer / SDF icon / draw tools / popup / MGRS grid port
 *   - 過渡期 entity rendering 函式（refreshLeafletMarkers / _renderPolygons / _drawMgrsGrid 等）
 *     已 stub 為 early-return，TODO 標註待 port
 */

import { authFetch, canAccessMapObjects, canCreateEvents } from './ws.js';
import {
  initMaplibre,
  getMap as _getMap,
  resizeMap as _resizeMap,
  getCenter as _mapGetCenter,
  getZoom as _mapGetZoom,
  showCoordPin as _showCoordPinCore,
  clearCoordPin as _clearCoordPinCore,
  getCoordPinLatLng as _getCoordPinLatLng,
  hasCoordPin as _hasCoordPin,
} from './map/maplibre_core.js';
import {
  EntityLayer,
  polygonToFeature,
  polygonLabelToFeature,
  infraToFeature,
  routeToFeature,
  flowToFeature,
  zoneToNodeFeature,
  bakeTextSdf,
  bakeArrowSdf,
} from './map/entity_layer.js';

const API_BASE = location.origin;
const el = id => document.getElementById(id);

let _deps = {};
let _mapConfig = null;
let _currentMap = 'indoor';
// _leafletMap：歷史命名，P1-10b 後實為 maplibregl.Map instance（透過 maplibre_core 取得）。
// 為避免大爆炸 rename，過渡期保留變數名；mapInitialized 旗標更可靠。
let _leafletMap = null;
let _leafletMarkers = [];
// P1-10b 步驟 6/7：原為 Leaflet L.layerGroup，現改持 EntityLayer instance。
let _polygonLayer = null;   // EntityLayer (Polygon fill+stroke)
let _infraLayer = null;     // EntityLayer (Point circle)
let _flowLayer = null;      // EntityLayer (LineString)
let _routeLayer = null;     // EntityLayer (LineString)
let _zoneLayer = null;      // EntityLayer (Point circle) — step 7 階段 1
let _entityLayersInstalled = false;
let _mgrsGridLayer = null;
let _coordPin = null;            // 雙擊放置的藍色十字 marker
let _polyDrawState = null;       // { latlngs, markers, previewPoly }
let _routeDrawState = null;      // { latlngs, markers, previewLine }
let _pendingPolyLatlngs = null;  // _openPolyForm → _savePolygon 暫存
let _pendingRouteLatlngs = null; // _openRouteForm → _saveRoute 暫存
let _pinEditMode = false;
let _coordDisplayMode = 'mgrs';  // 'mgrs' | 'wgs84'
let _mgrsGridVisible = false;
const _layerVis = { zones: true, polygons: true, infra: true, flows: true, routes: true, mgrs: false };

const _HSINCHU_CENTER = [24.8283, 121.0149];
const _HSINCHU_ZOOM = 15;

const _PERM_NODES = [
  { id: 'node_shelter', label: '收容組', node_type: 'shelter', icon: 'pin' },
  { id: 'node_medical', label: '醫療組', node_type: 'medical', icon: 'pin' },
  { id: 'node_command', label: '指揮部', node_type: 'command', icon: 'pin' },
  { id: 'node_forward', label: '前進組', node_type: 'forward', icon: 'pin' },
  { id: 'node_security', label: '安全組', node_type: 'security', icon: 'pin' },
];

const _EVENT_TYPES = {
  explosive: { label: '疑似爆裂物', group: 'security', severity: 'critical' },
  drone: { label: '無人機威脅', group: 'security', severity: 'critical' },
  violent: { label: '暴力事件', group: 'security', severity: 'critical' },
  unknown_person: { label: '不明人士', group: 'security', severity: 'warning' },
  perimeter: { label: '管制區異常', group: 'security', severity: 'warning' },
  crowd: { label: '秩序問題', group: 'security', severity: 'warning' },
  rescue: { label: '受困救援', group: 'rescue', severity: 'warning' },
  qrf: { label: 'QRF 出動', group: 'rescue', severity: 'warning' },
  mci: { label: '大量傷亡', group: 'medical', severity: 'critical' },
  emergency: { label: '緊急病症', group: 'medical', severity: 'critical' },
  infectious: { label: '傳染疑慮', group: 'medical', severity: 'warning' },
  capacity: { label: '量能超載', group: 'care', severity: 'warning' },
  isolation: { label: '隔離事件', group: 'care', severity: 'warning' },
  person_need: { label: '人員狀況', group: 'care', severity: 'info' },
  comm_fail: { label: '通訊異常', group: 'infra', severity: 'warning' },
  facility: { label: '設施異常', group: 'infra', severity: 'info' },
  equipment: { label: '設備故障', group: 'infra', severity: 'info' },
  evacuation: { label: '撤離', group: 'ops', severity: 'warning' },
  resource: { label: '資源調度', group: 'ops', severity: 'info' },
  situation: { label: '現場變化', group: 'ops', severity: 'info' },
  hazard: { label: '危害回報', group: 'ops', severity: 'info' },
  other: { label: '其他', group: 'ops', severity: 'info' },
};

const _EVENT_GROUPS = {
  security: '安全威脅',
  rescue: '搜救行動',
  medical: '醫療緊急',
  care: '收容照護',
  infra: '基礎設施',
  ops: '行動管理',
};

const _NAPSG_GROUP_ABBR = { security: '安', rescue: '救', medical: '醫', care: '護', infra: '設', ops: '行' };
const _NODE_ABBR = { shelter: '收', medical: '醫', forward: '前', security: '安', command: '指' };
const _NODE_COLORS = { shelter: '#f0883e', medical: '#e05555', forward: '#58a6ff', security: '#e3b341', command: '#8b949e' };
const _SEV_COLORS = { critical: '#e05555', warning: '#e3b341', info: '#3a4149' };
const _RAG_COLORS = { ok: '#3fb950', warn: '#e3b341', crit: '#f85149' };

const POLY_TYPES = {
  control:    { label: '管制區', color: '#e05555', dash: true },
  evacuation: { label: '疏散範圍', color: '#e3b341', dash: true },
  assembly:   { label: '集結點', color: '#3fb950', dash: false },
  danger:     { label: '危險區域', color: '#c0392b', dash: false },
  ops:        { label: '作業區', color: '#58a6ff', dash: false },
};

const INFRA_TYPES = {
  hospital: { label: '醫院', color: '#e05555', abbr: 'H' },
  shelter:  { label: '收容所', color: '#e3b341', abbr: 'S' },
  police:   { label: '警察局', color: '#58a6ff', abbr: 'P' },
  fire:     { label: '消防站', color: '#ff7f50', abbr: 'F' },
  utility:  { label: '公用設施', color: '#8b949e', abbr: 'U' },
};

const ROUTE_TYPES = {
  primary:   { label: '主要疏散路線', color: '#56d364', dash: false },
  secondary: { label: '次要路線', color: '#e3b341', dash: true },
  emergency: { label: '緊急通道', color: '#e05555', dash: false },
};

const FLOW_TYPES = {
  casualty:   { label: '傷患後送', color: '#e05555' },
  evacuation: { label: '疏散人員', color: '#e3b341' },
  resource:   { label: '資源調度', color: '#56d364' },
};

export function initMap(deps = {}) {
  _deps = deps;
  return _loadMapConfig();
}

async function _loadMapConfig() {
  try {
    // cache-bust：reset DB / 拖曳 / 新增 marker 後 saveMapConfig 才寫回靜態檔案，
    // 但 /static/map_config.json 預設無 Cache-Control，瀏覽器會用啟發式快取
    // 導致 location.reload() 後仍讀到舊版本（殘留事件 marker）
    const resp = await fetch(API_BASE + '/static/map_config.json?t=' + Date.now(), {
      cache: 'no-store',
    });
    _mapConfig = await resp.json();
  } catch (e) {
    console.warn('[map.js] map_config 載入失敗', e);
    _mapConfig = { maps: { indoor: { zones: [] }, outdoor: { zones: [] } } };
  }
  switchMap(sessionStorage.getItem('_currentMap') || 'indoor');
}

export function getMapConfig() {
  return _mapConfig;
}

export function switchMap(key) {
  if (key === 'osm') key = 'outdoor';
  _currentMap = key || 'indoor';
  sessionStorage.setItem('_currentMap', _currentMap);
  document.querySelectorAll('.map-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.map === _currentMap);
  });

  const isOutdoor = _currentMap === 'outdoor';
  const mapImg = el('map-img');
  const overlay = el('map-overlay');
  const leafletEl = el('leaflet-map');
  if (mapImg) mapImg.style.display = isOutdoor ? 'none' : '';
  if (overlay) overlay.style.display = isOutdoor ? 'none' : '';
  if (leafletEl) leafletEl.classList.toggle('active', isOutdoor);
  const mgrsIsland = el('mgrs-island');
  if (mgrsIsland) mgrsIsland.style.display = isOutdoor ? 'flex' : 'none';

  if (isOutdoor) {
    _initMaplibre();
    refreshLeafletMarkers();
    return;
  }

  const map = _mapConfig?.maps?.[_currentMap];
  if (mapImg && map?.image) {
    mapImg.src = '/static/' + map.image;
    mapImg.style.display = '';
  }
  renderMapOverlay();
}

function _initMaplibre() {
  if (_leafletMap) {
    // 容器尺寸變動補 resize（取代 Leaflet invalidateSize）
    setTimeout(() => _resizeMap(), 0);
    return;
  }

  _leafletMap = initMaplibre('leaflet-map', {
    shouldSuppressInteraction: () => !!(_polyDrawState || _routeDrawState),

    // 單擊：繪製模式時新增頂點
    onClick: ({ lat, lng }) => {
      if (_polyDrawState) { _addPolyVertex(lat, lng); return; }
      if (_routeDrawState) { _addRouteVertex(lat, lng); return; }
    },

    // 雙擊：放置藍色十字座標 pin（doubleClickZoom 在 core 已關閉）
    onDblclick: ({ lat, lng }) => {
      _showCoordPin(lat, lng);
      _refreshCoordPanel();
    },

    // 長按地圖（650ms）→ 開啟事件回報 popup（NAPSG 兩階段選單）
    onLongPress: ({ lat, lng }) => {
      if (canCreateEvents()) _openEventPopup(lat, lng);
    },

    // moveend / zoomend → sessionStorage view save + MGRS placeholder 更新
    onMoveEnd: ({ lat, lng, zoom }) => {
      sessionStorage.setItem('_mapView', JSON.stringify({ lat, lng, zoom }));
      _updateMgrsPlaceholder();
      if (_mgrsGridVisible) _drawMgrsGrid();
    },
  });

  if (!_leafletMap) return;

  _initMapTools();
  _updateMgrsPlaceholder();
}

// ── 站外地圖工具列（☰ 圖層 / ▱ 範圍 / ↗ 路線 / → 流向）──
function _initMapTools() {
  const tools = el('map-tools');
  if (!tools) return;
  tools.dataset.initialised = '1';
  applyMapRoleUiGuards();
}

export function applyMapRoleUiGuards() {
  const tools = el('map-tools');
  if (!tools) return;
  const objectTools = canAccessMapObjects()
    ? `<button class="map-btn" id="btn-poly-draw"  data-action="startPolyDraw"   title="繪製範圍" style="font-size:16px;">▱</button>
     <button class="map-btn" id="btn-route-draw" data-action="startRouteDraw"  title="繪製路線" style="font-size:14px;">↗</button>
     <button class="map-btn" id="btn-flow-add"   data-action="openFlowForm"    title="新增流向" style="font-size:14px;">→</button>`
    : '';
  // CSP 合規：用 data-action 委派，main.js 全域 click handler 會接住
  tools.innerHTML =
    `<button class="map-btn" id="btn-layer-panel" data-action="toggleLayerPanel" title="圖層面板" style="font-size:14px;">☰</button>
     ${objectTools}
     <button class="map-btn" id="btn-mgrs-grid"  data-action="toggleMgrsGrid"  title="MGRS 格線" style="font-size:13px;">⊞</button>`;
}

export function renderMapOverlay() {
  const overlay = el('map-overlay');
  if (!overlay || !_mapConfig) return;
  const map = _mapConfig.maps?.[_currentMap];
  overlay.innerHTML = '';
  if (!map?.zones) return;
  overlay.style.pointerEvents = 'none';

  const data = _deps.getData?.() || {};
  for (const zone of map.zones) {
    let orphanZone = false;
    if (zone.event_id) {
      const ev = (data.events || []).find(item => item.id === zone.event_id);
      if (!ev) orphanZone = true;
      else if (['resolved', 'closed'].includes(ev.status)) continue;
    }
    const marker = document.createElement('button');
    marker.type = 'button';
    marker.className = 'zone-marker';
    marker.style.left = (zone.x_pct || 50) + '%';
    marker.style.top = (zone.y_pct || 50) + '%';
    marker.style.pointerEvents = 'auto';
    marker.dataset.zoneId = zone.id;
    marker.innerHTML = `<div class="zone-dot">${_icon(zone)}</div>
      <div class="zone-info"><div class="zone-label">${zone.event_code || zone.label || zone.id}</div></div>`;
    marker.addEventListener('click', () => {
      if (!canAccessMapObjects()) return;
      if (orphanZone) _showOrphanZoneModal(zone);
      else if (zone.event_id) _deps.showEventProcessModal?.(zone);
      else (_deps.showZoneDetail || showZoneDetail)(zone);
    });
    overlay.appendChild(marker);
  }
}

export function refreshLeafletMarkers() {
  if (!_leafletMap || !_mapConfig) return;
  // P1-10b 步驟 6/7：polygons/infra/routes/flows/zones 全 port 到 MapLibre EntityLayer。
  if (typeof window.maplibregl !== 'undefined') {
    _ensureEntityLayers();
    _renderPolygons();
    _renderInfra();
    _renderFlows();
    _renderRoutes();
    _renderZones();
    return;
  }
  _leafletMarkers.forEach(m => m.remove());
  _leafletMarkers = [];
  _renderPolygons();
  _renderInfra();
  _renderFlows();
  _renderRoutes();
  if (!_layerVis.zones) return;
  const map = _mapConfig.maps?.outdoor;
  if (!map?.zones || !window.L) return;
  const data = _deps.getData?.() || {};
  for (const zone of map.zones) {
    if (zone.lat == null || zone.lng == null) continue;
    const isEvent = !!(zone.event_id || zone.event_code);
    let severity = 'warning';
    let isOrphan = false;
    if (isEvent) {
      const ev = (data.events || []).find(item => item.id === zone.event_id);
      if (!ev) { severity = 'info'; isOrphan = true; }
      else if (['resolved', 'closed'].includes(ev.status)) continue;
      else severity = ev.severity || 'warning';
    }

    const nodeOpts = {};
    if (!isEvent && zone.icon === 'pin' && (zone.node_type === 'shelter' || zone.node_type === 'medical')) {
      const calc = data.calc || {};
      const piNode = (data.pi_nodes || []).find(n => n.unit_id === zone.node_type);
      let linkLevel = 'lkp';
      if (piNode?.last_seen_at) {
        const age = Date.now() - new Date(piNode.last_seen_at).getTime();
        linkLevel = age < 30000 ? 'ok' : age < 90000 ? 'warn' : 'crit';
      }
      const snapshot = calc[zone.node_type]?.snapshot;
      if (snapshot) {
        const used = snapshot.bed_used || 0;
        const total = snapshot.bed_total || 1;
        const pct = used / total * 100;
        nodeOpts.rag = pct >= 90 ? 'crit' : pct >= 70 ? 'warn' : 'ok';
        nodeOpts.badge = used;
      } else {
        nodeOpts.rag = 'ok';
        nodeOpts.badge = '—';
      }
      if (linkLevel === 'crit' || linkLevel === 'lkp') nodeOpts.stale = true;
      if (linkLevel === 'warn') nodeOpts.linkWarn = true;
    }

    const marker = L.marker([zone.lat, zone.lng], {
      icon: _napsgIcon(zone.node_type, severity, isEvent, nodeOpts),
      draggable: true,
      title: zone.label || zone.id,
    });
    marker.bindTooltip(
      `<span>${zone.label || zone.id}</span>${zone.event_code ? ` <span style="color:var(--yellow)">${zone.event_code}</span>` : ''}`,
      { className: 'napsg-tooltip', direction: 'top', offset: [0, -16], permanent: false }
    );
    marker.on('click', e => {
      L.DomEvent.stopPropagation(e);
      if (!canAccessMapObjects()) return;
      if (isEvent && isOrphan) _showOrphanZoneModal(zone);
      else if (isEvent) _deps.showEventProcessModal?.(zone);
      else (_deps.showZoneDetail || showZoneDetail)(zone);
    });
    marker.on('dragend', async () => {
      const ll = marker.getLatLng();
      zone.lat = Math.round(ll.lat * 1000000) / 1000000;
      zone.lng = Math.round(ll.lng * 1000000) / 1000000;
      const mgrs = _latlngToMGRS(zone.lat, zone.lng, 5);
      await saveMapConfig();

      // 座標面板顯示確認，2.5 秒後恢復
      const panel = el('map-coord-panel');
      if (panel) {
        panel.style.display = 'flex';
        panel.innerHTML =
          `<span style="color:#8b949e">移動至</span>&nbsp;<b>${mgrs}</b>` +
          `<span style="color:#56d364;margin-left:10px">✓ 已儲存</span>`;
        setTimeout(() => _refreshCoordPanel(), 2500);
      }

      // 事件 marker：把新座標寫入該事件的處置紀錄
      if (zone.event_id) {
        try {
          await authFetch(API_BASE + '/api/events/' + zone.event_id + '/notes', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              text: `地圖位置已移動 → ${mgrs}`,
              operator: _deps.getCurrentOperator?.() || '',
            }),
          });
          _deps.doPoll?.();
        } catch (e) {
          console.warn('[map.js] dragend note failed', e);
        }
      }
    });
    marker.addTo(_leafletMap);
    _leafletMarkers.push(marker);
  }
}

// ══════════════════════════════════════════════════════════════
// MGRS / WGS84 座標轉換（DMA TM 8358.2 規格）
// ══════════════════════════════════════════════════════════════

function _latlngToMGRS(lat, lng, precision) {
  precision = (precision === undefined) ? 5 : precision;
  const zoneNum = Math.floor((lng + 180) / 6) + 1;
  const latBands = 'CDEFGHJKLMNPQRSTUVWX';
  const latBand = latBands[Math.min(Math.floor((lat + 80) / 8), 19)];
  const a = 6378137.0, f = 1 / 298.257223563;
  const b = a * (1 - f), e2 = 1 - (b * b) / (a * a), ep2 = e2 / (1 - e2), k0 = 0.9996;
  const phi = lat * Math.PI / 180, lam = lng * Math.PI / 180;
  const lam0 = ((zoneNum - 1) * 6 - 180 + 3) * Math.PI / 180;
  const sinp = Math.sin(phi), cosp = Math.cos(phi), tanp = Math.tan(phi);
  const N = a / Math.sqrt(1 - e2 * sinp * sinp);
  const T = tanp * tanp, C = ep2 * cosp * cosp, A = cosp * (lam - lam0);
  const M = a * (
    (1 - e2 / 4 - 3 * e2 * e2 / 64 - 5 * e2 * e2 * e2 / 256) * phi
    - (3 * e2 / 8 + 3 * e2 * e2 / 32 + 45 * e2 * e2 * e2 / 1024) * Math.sin(2 * phi)
    + (15 * e2 * e2 / 256 + 45 * e2 * e2 * e2 / 1024) * Math.sin(4 * phi)
    - (35 * e2 * e2 * e2 / 3072) * Math.sin(6 * phi));
  let E = k0 * N * (A + (1 - T + C) * A * A * A / 6 + (5 - 18 * T + T * T + 72 * C - 58 * ep2) * A * A * A * A * A / 120) + 500000;
  let Nn = k0 * (M + N * tanp * (A * A / 2 + (5 - T + 9 * C + 4 * C * C) * A * A * A * A / 24
    + (61 - 58 * T + T * T + 600 * C - 330 * ep2) * A * A * A * A * A * A / 720));
  if (lat < 0) Nn += 10000000;
  const colSets = ['ABCDEFGH', 'JKLMNPQR', 'STUVWXYZ'];
  const rowOdd = 'ABCDEFGHJKLMNPQRSTUV';
  const rowEven = 'FGHJKLMNPQRSTUVABCDE';
  const colIdx = Math.floor(E / 100000) - 1;
  const rowIdx = Math.floor(Nn / 100000) % 20;
  if (colIdx < 0 || colIdx > 7) return `${zoneNum}${latBand} ??`;
  const colLetter = colSets[(zoneNum - 1) % 3][colIdx];
  const rowLetter = (zoneNum % 2 === 1 ? rowOdd : rowEven)[rowIdx];
  const ep = String(Math.round(E % 100000)).padStart(5, '0').substring(0, precision);
  const np = String(Math.round(Nn % 100000)).padStart(5, '0').substring(0, precision);
  return `${zoneNum}${latBand} ${colLetter}${rowLetter} ${ep} ${np}`;
}

function _utmToLatLng(zoneNum, E, N) {
  const a = 6378137.0, f = 1 / 298.257223563;
  const b = a * (1 - f), e2 = 1 - (b * b) / (a * a), ep2 = e2 / (1 - e2), k0 = 0.9996;
  const e1 = (1 - Math.sqrt(1 - e2)) / (1 + Math.sqrt(1 - e2));
  const x = E - 500000;
  const M = N / k0;
  const mu = M / (a * (1 - e2 / 4 - 3 * e2 * e2 / 64 - 5 * e2 * e2 * e2 / 256));
  const phi1 = mu
    + (3 * e1 / 2 - 27 * e1 * e1 * e1 / 32) * Math.sin(2 * mu)
    + (21 * e1 * e1 / 16 - 55 * e1 * e1 * e1 * e1 / 32) * Math.sin(4 * mu)
    + (151 * e1 * e1 * e1 / 96) * Math.sin(6 * mu)
    + (1097 * e1 * e1 * e1 * e1 / 512) * Math.sin(8 * mu);
  const sinp = Math.sin(phi1), cosp = Math.cos(phi1), tanp = Math.tan(phi1);
  const N1 = a / Math.sqrt(1 - e2 * sinp * sinp);
  const T1 = tanp * tanp, C1 = ep2 * cosp * cosp;
  const R1 = a * (1 - e2) / Math.pow(1 - e2 * sinp * sinp, 1.5);
  const D = x / (N1 * k0);
  const latRad = phi1 - (N1 * tanp / R1) * (
    D * D / 2
    - (5 + 3 * T1 + 10 * C1 - 4 * C1 * C1 - 9 * ep2) * D * D * D * D / 24
    + (61 + 90 * T1 + 298 * C1 + 45 * T1 * T1 - 252 * ep2 - 3 * C1 * C1) * D * D * D * D * D * D / 720
  );
  const lngRad = (D
    - (1 + 2 * T1 + C1) * D * D * D / 6
    + (5 - 2 * C1 + 28 * T1 - 3 * C1 * C1 + 8 * ep2 + 24 * T1 * T1) * D * D * D * D * D / 120
  ) / cosp;
  const lam0 = ((zoneNum - 1) * 6 - 180 + 3) * Math.PI / 180;
  return { lat: latRad * 180 / Math.PI, lng: (lam0 + lngRad) * 180 / Math.PI };
}

function _latlngToUtm(lat, lng) {
  const a = 6378137.0, f = 1 / 298.257223563;
  const b = a * (1 - f), e2 = 1 - (b * b) / (a * a), ep2 = e2 / (1 - e2), k0 = 0.9996;
  const zoneNum = Math.floor((lng + 180) / 6) + 1;
  const phi = lat * Math.PI / 180, lam = lng * Math.PI / 180;
  const lam0 = ((zoneNum - 1) * 6 - 180 + 3) * Math.PI / 180;
  const sinp = Math.sin(phi), cosp = Math.cos(phi), tanp = Math.tan(phi);
  const N = a / Math.sqrt(1 - e2 * sinp * sinp);
  const T = tanp * tanp, C = ep2 * cosp * cosp, A = cosp * (lam - lam0);
  const M = a * (
    (1 - e2 / 4 - 3 * e2 * e2 / 64 - 5 * e2 * e2 * e2 / 256) * phi
    - (3 * e2 / 8 + 3 * e2 * e2 / 32 + 45 * e2 * e2 * e2 / 1024) * Math.sin(2 * phi)
    + (15 * e2 * e2 / 256 + 45 * e2 * e2 * e2 / 1024) * Math.sin(4 * phi)
    - (35 * e2 * e2 * e2 / 3072) * Math.sin(6 * phi));
  let E = k0 * N * (A + (1 - T + C) * A * A * A / 6 + (5 - 18 * T + T * T + 72 * C - 58 * ep2) * A * A * A * A * A / 120) + 500000;
  let Nn = k0 * (M + N * tanp * (A * A / 2 + (5 - T + 9 * C + 4 * C * C) * A * A * A * A / 24
    + (61 - 58 * T + T * T + 600 * C - 330 * ep2) * A * A * A * A * A * A / 720));
  if (lat < 0) Nn += 10000000;
  return { zoneNum, easting: E, northing: Nn };
}

function _mgrsToLatLng(mgrsStr) {
  const s = mgrsStr.trim().toUpperCase().replace(/\s+/g, '');
  const m = s.match(/^(\d{1,2})([C-HJ-NP-X])([A-HJ-NP-Z])([A-HJ-NP-V])(\d{2,10})$/);
  if (!m) return null;
  const zoneNum = parseInt(m[1], 10);
  const latBand = m[2];
  const colLtr = m[3];
  const rowLtr = m[4];
  const digits = m[5];
  if (digits.length % 2 !== 0) return null;
  const half = digits.length / 2;
  const scale = Math.pow(10, 5 - half);
  const eOff = parseInt(digits.substring(0, half), 10) * scale;
  const nOff = parseInt(digits.substring(half), 10) * scale;
  const colSets = ['ABCDEFGH', 'JKLMNPQR', 'STUVWXYZ'];
  const colSet = colSets[(zoneNum - 1) % 3];
  const colIdx = colSet.indexOf(colLtr);
  if (colIdx < 0) return null;
  const utmE = (colIdx + 1) * 100000 + eOff;
  const rowOdd = 'ABCDEFGHJKLMNPQRSTUV';
  const rowEven = 'FGHJKLMNPQRSTUVABCDE';
  const rowSet = (zoneNum % 2 === 1) ? rowOdd : rowEven;
  const rowIdx = rowSet.indexOf(rowLtr);
  if (rowIdx < 0) return null;
  const latBands = 'CDEFGHJKLMNPQRSTUVWX';
  const bandIdx = latBands.indexOf(latBand);
  if (bandIdx < 0) return null;
  const approxLat = (bandIdx * 8 - 80) + 4;
  const a = 6378137.0, f = 1 / 298.257223563;
  const b = a * (1 - f), e2 = 1 - (b * b) / (a * a), k0 = 0.9996;
  const phi = approxLat * Math.PI / 180;
  const Mapprox = a * (
    (1 - e2 / 4 - 3 * e2 * e2 / 64 - 5 * e2 * e2 * e2 / 256) * phi
    - (3 * e2 / 8 + 3 * e2 * e2 / 32 + 45 * e2 * e2 * e2 / 1024) * Math.sin(2 * phi)
    + (15 * e2 * e2 / 256 + 45 * e2 * e2 * e2 / 1024) * Math.sin(4 * phi)
    - (35 * e2 * e2 * e2 / 3072) * Math.sin(6 * phi));
  let approxNn = k0 * Mapprox;
  if (approxLat < 0) approxNn += 10000000;
  let nBand = Math.floor(approxNn / 100000);
  const approxRowIdx = nBand % 20;
  let diff = (rowIdx - approxRowIdx + 20) % 20;
  if (diff > 10) diff -= 20;
  nBand += diff;
  const utmN = nBand * 100000 + nOff;
  return _utmToLatLng(zoneNum, utmE, utmN);
}

function _parseWgs84(str) {
  const m = str.match(/(-?\d+\.?\d*)[,\s]+(-?\d+\.?\d*)/);
  if (!m) return null;
  const lat = parseFloat(m[1]), lng = parseFloat(m[2]);
  if (isNaN(lat) || isNaN(lng)) return null;
  if (lat < -90 || lat > 90 || lng < -180 || lng > 180) return null;
  return { lat, lng };
}

function _currentMgrsGzd() {
  const c = _mapGetCenter();
  if (!c) return null;
  const full = _latlngToMGRS(c.lat, c.lng, 5);
  const m = full.match(/^(\d+[A-Z])\s+([A-Z]{2})/);
  return m ? m[1] + m[2] : null;
}

function _updateMgrsPlaceholder() {
  const input = el('mgrs-search-input');
  if (!input) return;
  const gzd = _currentMgrsGzd();
  input.placeholder = gzd ? `${gzd.slice(3)} 00000 00000` : 'MGRS 或 lat, lng';
}

// ══════════════════════════════════════════════════════════════
// 座標 pin（雙擊放置的藍色十字）+ 中央浮島
// ══════════════════════════════════════════════════════════════

function _showCoordPin(lat, lng) {
  // P1-10b: 委派 maplibre_core；本地 _coordPin 不再持有 marker 物件，僅記座標
  _showCoordPinCore(lat, lng);
  _coordPin = { lat, lng };  // 保留為 truthy flag，供 panel render 判斷
}

function _clearCoordPin() {
  _clearCoordPinCore();
  _coordPin = null;
  _refreshCoordPanel();
}

function _refreshCoordPanel() {
  const panel = el('map-coord-panel');
  if (!panel) return;
  const ll = _getCoordPinLatLng();
  if (ll) {
    panel.style.display = 'flex';
    panel.style.alignItems = 'center';
    panel.classList.add('clickable');
    panel.innerHTML =
      `<span style="color:#58a6ff;flex-shrink:0;">✛</span>&nbsp;${_coordValueHTML(ll.lat, ll.lng)}${_coordToggleBtn()}`;
    return;
  }
  panel.style.display = 'none';
  panel.classList.remove('clickable');
}

function _coordToggleBtn() {
  const next = _coordDisplayMode === 'mgrs' ? 'WGS84' : 'MGRS';
  // CSP 合規：用 data-action="toggleCoordMode"，main.js 委派處理
  return `<span data-action="toggleCoordMode"
    style="font-size:9px;color:var(--text3);border:1px solid var(--border);border-radius:3px;
    padding:1px 5px;margin-left:8px;cursor:pointer;flex-shrink:0;white-space:nowrap;"
    title="切換座標系統">${next}</span>`;
}

function _coordValueHTML(lat, lng) {
  const lbl = `<span style="color:#8b949e;font-size:9px;flex-shrink:0;">`;
  if (_coordDisplayMode === 'wgs84') {
    return `${lbl}WGS84</span>&nbsp;${lat.toFixed(6)}°N,&nbsp;${lng.toFixed(6)}°E`;
  }
  return `${lbl}MGRS</span>&nbsp;<b>${_latlngToMGRS(lat, lng, 5)}</b>`;
}

// ══════════════════════════════════════════════════════════════
// 圖層面板（layer-panel 內容重建）
// ══════════════════════════════════════════════════════════════

function _rebuildLayerPanel() {
  const panel = el('layer-panel');
  if (!panel) return;
  _layerVis.mgrs = _mgrsGridVisible;
  const layers = [
    { key: 'zones',    icon: '◆', label: '節點' },
    { key: 'polygons', icon: '▱', label: '範圍' },
    { key: 'infra',    icon: '＋', label: '設施' },
    { key: 'flows',    icon: '→', label: '流向' },
    { key: 'routes',   icon: '↗', label: '路線' },
    { key: 'mgrs',     icon: '⊞', label: 'MGRS 格線' },
  ];
  let html = '<h4>圖層</h4>';
  for (const layer of layers) {
    const on = _layerVis[layer.key];
    // CSP 合規：data-action="toggleLayer" data-layer="..."（與 main.js dataset.layer 對齊）
    html += `<div class="layer-row" data-action="toggleLayer" data-layer="${layer.key}">`;
    html += `<div class="layer-check${on ? ' on' : ''}">${on ? '✓' : ''}</div>`;
    html += `<span style="font-size:11px;color:${on ? 'var(--text)' : 'var(--text3)'};">${layer.icon} ${layer.label}</span>`;
    html += `</div>`;
  }
  html += `<div style="border-top:1px solid var(--border);margin:4px 0 2px;padding:4px 12px 2px;font-size:9px;color:var(--text3);letter-spacing:.1em;text-transform:uppercase;">地圖設定</div>`;
  html += `<div class="layer-row" data-action="openInfraForm">
    <span style="font-size:11px;color:var(--text2);">＋ 新增設施</span></div>`;
  panel.innerHTML = html;
}

// ══════════════════════════════════════════════════════════════
// MGRS 格線（簡化版：依像素密度自動決定間距）
// ══════════════════════════════════════════════════════════════

function _mgrsGridSpacing() {
  const MIN_PX = 60;
  const zoom = _mapGetZoom();
  const c = _mapGetCenter();
  if (zoom == null || !c) return 100000;
  const metersPerPx = (40075016.686 / (256 * Math.pow(2, zoom))) / Math.cos(c.lat * Math.PI / 180);
  const minMeters = metersPerPx * MIN_PX;
  const levels = [1, 10, 100, 1000, 10000, 100000];
  return levels.find(s => s >= minMeters) || 100000;
}

function _mgrsGridLabel(val, spacing) {
  // 位數 = log10(100000 / spacing)：10km→1位、1km→2位、100m→3位、10m→4位
  const digits = Math.max(3, Math.round(Math.log10(100000 / spacing)));
  const divisor = Math.pow(10, 5 - digits);
  const v = Math.round(val % 100000);
  return String(Math.floor(v / divisor)).padStart(digits, '0');
}

function _mgrsLabelIconW(spacing) {
  const digits = Math.max(1, Math.round(Math.log10(100000 / spacing)));
  return 16 + digits * 6;
}

function _drawMgrsGrid() {
  // P1-10b: MapLibre 啟用後 L.polyline + addTo(_leafletMap) 會炸；MGRS grid port 留到 P2-05
  //（MIL-STD-2525 同 phase）或步驟 10 收尾。過渡期 toggle MGRS 圖層 no-op。
  // TODO(P1-10b#19): port 到 MapLibre line layer（GeoJSON source + line layer）。
  if (typeof window.maplibregl !== 'undefined') return;
  if (!_leafletMap || !window.L) return;
  if (!_mgrsGridLayer) _mgrsGridLayer = L.layerGroup();
  _mgrsGridLayer.clearLayers();
  if (!_mgrsGridVisible) {
    if (_mgrsGridLayer._map) _mgrsGridLayer.remove();
    return;
  }
  if (!_mgrsGridLayer._map) _mgrsGridLayer.addTo(_leafletMap);
  const sp = _mgrsGridSpacing();
  const b = _leafletMap.getBounds();
  const ctr = _latlngToUtm(b.getCenter().lat, b.getCenter().lng);
  const sw = _latlngToUtm(b.getSouth(), b.getWest());
  const ne = _latlngToUtm(b.getNorth(), b.getEast());
  const zn = ctr.zoneNum;
  const lineOpt = { color: 'rgba(90,150,215,.55)', weight: 1, interactive: false };

  // 將標籤定位於距 viewport 邊緣固定像素的位置（避免被 UI 元件遮蓋）
  const mapW = _leafletMap.getContainer().offsetWidth;
  const mapH = _leafletMap.getContainer().offsetHeight;
  const leftPx = 24;     // Y 標籤距左側
  const bottomPx = 60;   // X 標籤距底部（避開 MGRS 搜尋欄）
  const llLeft   = _leafletMap.containerPointToLatLng(L.point(leftPx,   mapH / 2));
  const llBottom = _leafletMap.containerPointToLatLng(L.point(mapW / 2, mapH - bottomPx));
  const labelE = _latlngToUtm(llLeft.lat,   llLeft.lng).easting;
  const labelN = _latlngToUtm(llBottom.lat, llBottom.lng).northing;
  const labelW = _mgrsLabelIconW(sp);

  // 東西向 northing 線 + 左側 Y 軸標籤
  const n0 = Math.floor(sw.northing / sp) * sp;
  const n1 = Math.ceil(ne.northing / sp) * sp;
  for (let n = n0; n <= n1; n += sp) {
    const p1 = _utmToLatLng(zn, sw.easting - sp, n);
    const p2 = _utmToLatLng(zn, ne.easting + sp, n);
    if (!isFinite(p1.lat) || !isFinite(p2.lat)) continue;
    L.polyline([[p1.lat, p1.lng], [p2.lat, p2.lng]], lineOpt).addTo(_mgrsGridLayer);
    const ll = _utmToLatLng(zn, labelE, n);
    if (isFinite(ll.lat)) {
      L.marker([ll.lat, ll.lng], {
        icon: L.divIcon({
          html: `<div class="mgrs-gl">${_mgrsGridLabel(n, sp)}</div>`,
          className: '', iconSize: [labelW, 14], iconAnchor: [labelW / 2, 7],
        }),
        interactive: false, zIndexOffset: -900,
      }).addTo(_mgrsGridLayer);
    }
  }

  // 南北向 easting 線 + 底部 X 軸標籤
  const e0 = Math.floor(sw.easting / sp) * sp;
  const e1 = Math.ceil(ne.easting / sp) * sp;
  for (let e = e0; e <= e1; e += sp) {
    const p1 = _utmToLatLng(zn, e, sw.northing - sp);
    const p2 = _utmToLatLng(zn, e, ne.northing + sp);
    if (!isFinite(p1.lat) || !isFinite(p2.lat)) continue;
    L.polyline([[p1.lat, p1.lng], [p2.lat, p2.lng]], lineOpt).addTo(_mgrsGridLayer);
    const ll = _utmToLatLng(zn, e, labelN);
    if (isFinite(ll.lat)) {
      L.marker([ll.lat, ll.lng], {
        icon: L.divIcon({
          html: `<div class="mgrs-gl">${_mgrsGridLabel(e, sp)}</div>`,
          className: '', iconSize: [labelW, 14], iconAnchor: [labelW / 2, 7],
        }),
        interactive: false, zIndexOffset: -900,
      }).addTo(_mgrsGridLayer);
    }
  }
}

// ══════════════════════════════════════════════════════════════
// 長按地圖 → 事件回報 popup（NAPSG 兩階段選單）
// 使用 L.DomUtil + L.DomEvent，避免 inline onclick 失效
// ══════════════════════════════════════════════════════════════

let _evPopup = null;
let _evPopupLatLng = null;

function _evPopupBuildGroups(mgrs, reportUnit) {
  const wrap = L.DomUtil.create('div', 'ev-popup-inner');

  const header = L.DomUtil.create('div', 'ev-popup-header', wrap);
  const repWrap = L.DomUtil.create('div', 'ev-popup-reporter-wrap', header);
  const lbl = L.DomUtil.create('label', '', repWrap);
  lbl.textContent = '回報：';
  const sel = L.DomUtil.create('select', '', repWrap);
  sel.id = 'ev-popup-unit';
  [['command', '指揮部'], ['forward', '前進組'], ['security', '安全組'],
   ['shelter', '收容組'], ['medical', '醫療組']].forEach(([v, t]) => {
    const opt = document.createElement('option');
    opt.value = v; opt.textContent = t;
    if (v === reportUnit) opt.selected = true;
    sel.appendChild(opt);
  });
  L.DomEvent.on(sel, 'change', () => {
    const bar = el('place-report-unit');
    if (bar) bar.value = sel.value;
  });

  const mgrsSpan = L.DomUtil.create('span', 'ev-popup-mgrs', header);
  mgrsSpan.textContent = `📍 ${mgrs}`;

  const grid = L.DomUtil.create('div', 'ev-popup-groups', wrap);
  Object.entries(_EVENT_GROUPS).forEach(([k, label]) => {
    const btn = L.DomUtil.create('button', 'ev-popup-group-btn', grid);
    btn.type = 'button';
    btn.textContent = label;
    L.DomEvent.on(btn, 'click', (e) => {
      L.DomEvent.stopPropagation(e);
      _evPopupGroup(k);
    });
  });
  return wrap;
}

function _evPopupBuildTypes(groupKey) {
  const wrap = L.DomUtil.create('div', 'ev-popup-inner');
  const header = L.DomUtil.create('div', 'ev-popup-header', wrap);
  const back = L.DomUtil.create('span', 'ev-popup-back', header);
  back.style.marginBottom = '0';
  back.textContent = '← 返回';
  L.DomEvent.on(back, 'click', (e) => {
    L.DomEvent.stopPropagation(e);
    _evPopupBack();
  });
  const grpSpan = L.DomUtil.create('span', 'ev-popup-mgrs', header);
  grpSpan.textContent = _EVENT_GROUPS[groupKey] || groupKey;

  const list = L.DomUtil.create('div', 'ev-popup-types', wrap);
  Object.entries(_EVENT_TYPES)
    .filter(([, v]) => v.group === groupKey)
    .forEach(([k, v]) => {
      const sev = v.severity || 'info';
      const btn = L.DomUtil.create('button', `ev-popup-type-btn sev-${sev}`, list);
      btn.type = 'button';
      btn.textContent = v.label;
      L.DomEvent.on(btn, 'click', (e) => {
        L.DomEvent.stopPropagation(e);
        _evPopupSubmit(k);
      });
    });
  return wrap;
}

function _openEventPopup(lat, lng) {
  if (!canCreateEvents()) return;
  if (!window.L || !_leafletMap) return;
  // P1-10b 步驟 9 才 port event_popup.js（L.popup → maplibregl.Popup + DOM 建構保留）。
  // 過渡期 long press 觸發但 popup 不開（silent，不阻塞 step 4 verify）。
  // TODO(P1-10b#19 step 9): port 到 maplibregl.Popup + setLngLat + setDOMContent。
  if (typeof window.maplibregl !== 'undefined') {
    console.info('[map] _openEventPopup 暫停用，等 P1-10b 步驟 9 port');
    return;
  }
  if (_evPopup) { _leafletMap.closePopup(_evPopup); _evPopup = null; }
  _evPopupLatLng = { lat, lng };
  const mgrs = _latlngToMGRS(lat, lng, 5);
  const reportUnit = el('place-report-unit')?.value || 'command';
  _evPopup = L.popup({
    className: 'ev-popup',
    closeButton: true,
    autoClose: false,
    closeOnClick: false,
    maxWidth: 280,
    offset: [0, -6],
  })
    .setLatLng([lat, lng])
    .setContent(_evPopupBuildGroups(mgrs, reportUnit))
    .openOn(_leafletMap);
  _evPopup.on('remove', () => { _evPopup = null; });
}

function _evPopupGroup(groupKey) {
  if (!_evPopup || !_evPopupLatLng) return;
  _evPopup.setContent(_evPopupBuildTypes(groupKey));
}

function _evPopupBack() {
  if (!_evPopupLatLng) return;
  const { lat, lng } = _evPopupLatLng;
  _openEventPopup(lat, lng);
}

async function _evPopupSubmit(typeKey) {
  if (!canCreateEvents()) return;
  if (!_evPopupLatLng) return;
  const evDef = _EVENT_TYPES[typeKey];
  if (!evDef) return;

  const popupSel = document.getElementById('ev-popup-unit');
  const reportedBy = popupSel ? popupSel.value : (el('place-report-unit')?.value || 'command');
  const { lat, lng } = _evPopupLatLng;
  _evPopupLatLng = null;
  if (_evPopup) { _leafletMap.closePopup(_evPopup); _evPopup = null; }

  const id = 'evt_' + Date.now();
  const mgrs = _latlngToMGRS(lat, lng, 5);
  const operator = _deps.getCurrentOperator?.() || '';
  const sessionType = (() => {
    try { return window.__sessionType || 'real'; } catch { return 'real'; }
  })();

  const zone = {
    id,
    label: evDef.label,
    sub: '',
    lat: Math.round(lat * 1000000) / 1000000,
    lng: Math.round(lng * 1000000) / 1000000,
    node_type: evDef.group || 'ops',
    icon: 'event',
  };

  try {
    const resp = await authFetch(API_BASE + '/api/events', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        reported_by_unit: reportedBy,
        event_type: typeKey,
        severity: evDef.severity || 'warning',
        description: evDef.label,
        operator_name: operator,
        location_zone_id: id,
        location_desc: mgrs,
        session_type: sessionType,
      }),
    });
    if (resp.ok) {
      const data = await resp.json();
      zone.event_id = data.id;
      zone.event_code = data.event_code;
    } else {
      console.warn('[map.js] 事件建立失敗', resp.status);
    }
  } catch (e) {
    console.error('[map.js] _evPopupSubmit', e);
  }

  if (zone.event_id) {
    if (!_mapConfig.maps.outdoor.zones) _mapConfig.maps.outdoor.zones = [];
    _mapConfig.maps.outdoor.zones.push(zone);
    saveMapConfig();
    refreshLeafletMarkers();
    // 顯示放置確認
    const panel = el('map-coord-panel');
    if (panel) {
      panel.style.display = 'flex';
      panel.innerHTML =
        `<span style="color:#3fb950">✓ 放置</span>&nbsp;<b>${zone.event_code}</b>` +
        `<span style="color:#8b949e;margin-left:8px">MGRS</span>&nbsp;${mgrs}`;
      setTimeout(() => _refreshCoordPanel(), 3000);
    }
    // 觸發 poll 讓右側事件追蹤欄即時更新
    _deps.doPoll?.();
  }
}

function _napsgIcon(nodeType, severity, isEvent, opts = {}) {
  if (isEvent) {
    const color = _SEV_COLORS[severity] || '#8b949e';
    const abbr = _NAPSG_GROUP_ABBR[nodeType] || _NODE_ABBR[nodeType] || '?';
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="26" height="26" viewBox="0 0 26 26">
      <polygon points="13,1 25,13 13,25 1,13" fill="${color}" stroke="#fff" stroke-width="2"/>
      <text x="13" y="17" text-anchor="middle" font-size="9" font-weight="700" fill="#fff"
            font-family="sans-serif">${abbr}</text>
    </svg>`;
    return L.divIcon({ html: svg, className: '', iconSize: [26, 26], iconAnchor: [13, 13] });
  }

  const abbr = _NODE_ABBR[nodeType] || '?';
  let color = _NODE_COLORS[nodeType] || '#8b949e';
  if ((nodeType === 'shelter' || nodeType === 'medical') && opts.rag) {
    color = _RAG_COLORS[opts.rag] || color;
  }
  const ragCls = opts.rag ? ' ' + opts.rag : '';
  const staleCls = opts.stale ? ' napsg-stale' : '';
  const linkWarnCls = opts.linkWarn ? ' link-warn' : '';
  let badgeHtml = '';
  if (opts.stale && (nodeType === 'shelter' || nodeType === 'medical')) {
    badgeHtml = '<div class="napsg-badge off">OFF</div>';
  } else if (opts.badge != null && opts.badge !== '') {
    const badgeClass = opts.rag === 'crit' ? 'crit' : opts.rag === 'warn' ? 'warn' : '';
    badgeHtml = `<div class="napsg-badge ${badgeClass}">${opts.badge}</div>`;
  }

  if (nodeType === 'forward' || nodeType === 'security') {
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">
      <rect x="1" y="1" width="22" height="22" rx="2" fill="${color}" stroke="#fff" stroke-width="2"/>
      <text x="12" y="16" text-anchor="middle" font-size="9" font-weight="700" fill="#fff"
            font-family="sans-serif">${abbr}</text>
    </svg>`;
    return L.divIcon({
      html: `<div class="napsg-wrap${ragCls}${staleCls}${linkWarnCls}">${svg}${badgeHtml}</div>`,
      className: '', iconSize: [24, 24], iconAnchor: [12, 12],
    });
  }

  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 28 28">
    <circle cx="14" cy="14" r="12" fill="${color}" stroke="#fff" stroke-width="2"/>
    <text x="14" y="18" text-anchor="middle" font-size="10" font-weight="700" fill="#fff"
          font-family="sans-serif">${abbr}</text>
  </svg>`;
  return L.divIcon({
    html: `<div class="napsg-wrap${ragCls}${staleCls}${linkWarnCls}">${svg}${badgeHtml}</div>`,
    className: '', iconSize: [28, 28], iconAnchor: [14, 14],
  });
}

function _icon(zone) {
  if (zone.icon === 'pin') return '●';
  if (zone.event_id || zone.event_code) return '!';
  return '◆';
}

// events.js 透過 dynamic import 取用此函式更新 modal-title icon
// 簡化版：以單字表示（pin=●、事件=▲、其他=◆）— legacy SVG icon 集若需可擴充
export function renderIcon(icon) {
  if (icon === 'pin' || icon === 'pin_shelter' || icon === 'pin_medical') return '●';
  if (icon === 'event' || icon === 'shield' || icon === 'explosive'
      || icon === 'drone' || icon === 'eye' || icon === 'run'
      || icon === 'handshake' || icon === 'person' || icon === 'threat') return '▲';
  return '◆';
}

export function findZoneByEventId(eventId) {
  if (!_mapConfig) return null;
  for (const map of Object.values(_mapConfig.maps || {})) {
    const found = (map.zones || []).find(z => z.event_id === eventId || z.id === eventId);
    if (found) return found;
  }
  return null;
}

export function showZoneDetail(zone) {
  if (!canAccessMapObjects()) return;
  if (!zone) return;
  const body = `<div style="font-size:12px;line-height:1.7;">
    <div>類型：${zone.node_type || '—'}</div>
    <div>座標：${zone.lat != null ? _coordValueHTML(zone.lat, zone.lng) : '站內相對位置'}</div>
  </div>`;
  _deps.openModal?.(zone.label || zone.id || '節點', body);
}

export function openMapConfigPanel() {
  const currentImage = _mapConfig?.maps?.indoor?.image || '—';
  const rows = _PERM_NODES.map(n => `<div style="display:flex;justify-content:space-between;gap:8px;padding:4px 0;border-bottom:1px solid var(--border);">
    <span>${n.label}</span><button data-action="closeModal" class="adm-btn">關閉</button></div>`).join('');
  _deps.openModal?.('地圖設定', `<div style="font-size:11px;color:var(--text2);margin-bottom:8px;">站內圖：${currentImage}</div>${rows}`);
}

export function closeMapConfigPanel() {
  el('map-config-overlay')?.style.setProperty('display', 'none');
}

export async function saveMapConfig() {
  if (!_mapConfig) return;
  await authFetch(API_BASE + '/api/map_config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(_mapConfig),
  });
}

export function togglePinEditMode() {
  _pinEditMode = !_pinEditMode;
  const btn = el('pin-edit-btn');
  if (btn) btn.textContent = _pinEditMode ? '調整中...' : '調整據點';
  renderMapOverlay();
}

export function cancelPlaceMode() {
  const panel = el('map-panel');
  panel?.classList.remove('placing');
}

export function _populateNapsgCsel() {
  const panel = el('place-type-panel');
  if (!panel) return;
  panel.innerHTML = '';
  let group = null;
  for (const [key, def] of Object.entries(_EVENT_TYPES)) {
    if (def.group !== group) {
      group = def.group;
      const h = document.createElement('div');
      h.className = 'csel-group-header';
      h.textContent = _EVENT_GROUPS[group] || group;
      panel.appendChild(h);
    }
    const item = document.createElement('button');
    item.type = 'button';
    item.className = 'csel-opt';
    item.dataset.value = key;
    item.textContent = def.label;
    item.addEventListener('click', () => {
      el('place-type-label').textContent = def.label;
      panel.classList.remove('open');
      onPlaceTypeChange();
    });
    panel.appendChild(item);
  }
}

export function toggleCsel() {
  el('place-type-panel')?.classList.toggle('open');
}

export function onPlaceTypeChange() {
  el('map-panel')?.classList.add('placing');
}

export function _mgrsSearch() {
  const input = el('mgrs-search-input');
  if (!input || !_leafletMap) return;
  let val = (input.value || '').trim().toUpperCase();
  if (!val) return;
  input.classList.remove('error');
  // 三段式自動補前綴
  if (/^[\d\s]+$/.test(val)) {
    const gzd = _currentMgrsGzd();
    if (gzd) val = gzd + val.replace(/\s/g, '');
  } else if (/^[A-Z]{2}[\d\s]+$/.test(val)) {
    const gzd = _currentMgrsGzd();
    if (gzd) val = gzd.slice(0, 3) + val.replace(/\s/g, '');
  }
  const ll = _mgrsToLatLng(val) || _parseWgs84(val);
  if (!ll) {
    input.classList.add('error');
    return;
  }
  // P1-10b: MapLibre 用 flyTo + [lng, lat]（Leaflet 的 setView 已不存在）
  _leafletMap.flyTo({ center: [ll.lng, ll.lat], zoom: Math.max(_leafletMap.getZoom(), 16) });
  _showCoordPin(ll.lat, ll.lng);
  _refreshCoordPanel();
  input.value = '';
}

export function _toggleCoordMode(event) {
  event?.stopPropagation?.();
  event?.preventDefault?.();
  _coordDisplayMode = _coordDisplayMode === 'mgrs' ? 'wgs84' : 'mgrs';
  _refreshCoordPanel();
}

export function _panToCoordTarget(event) {
  // 點擊浮島本體 → pan 到 crosshair；點到內部的 toggle 按鈕則略過（由 toggleCoordMode 處理）
  if (event?.target?.closest?.('[data-action="toggleCoordMode"]')) return;
  event?.stopPropagation?.();
  event?.preventDefault?.();
  // P1-10b: _coordPin 現為 plain {lat, lng}（步驟 4 委派 maplibre_core 後改），
  // 不再有 Leaflet marker.getLatLng()；用 core helper + MapLibre panTo([lng, lat])
  const ll = _getCoordPinLatLng();
  if (!_leafletMap || !ll) return;
  _leafletMap.panTo([ll.lng, ll.lat]);
}

// P1-10b 步驟 6：MapLibre EntityLayer 實例化（一次建好，refreshLeafletMarkers 重複叫只 update data）
// 步驟 7 補：zone NAPSG SVG addImage + 4-layer state stack；arrow marker for route/flow；label 顯示
// 步驟 8 補：label drag-to-reposition（draw_tools）
function _ensureEntityLayers() {
  if (_entityLayersInstalled) return;
  const map = _getMap();
  if (!map) return;
  if (!map.isStyleLoaded()) {
    map.once('load', () => _ensureEntityLayers());
    return;
  }

  // Polygons — fill + stroke + label（dash/solid 拆兩 layer + filter，因 MapLibre v4
  // line-dasharray 不支援 data-driven expression）
  _polygonLayer = new EntityLayer(map, 'polygons', {
    layers: [
      {
        id: 'polygons-fill', type: 'fill',
        paint: { 'fill-color': ['get', 'color'], 'fill-opacity': 0.12 },
      },
      {
        id: 'polygons-stroke-solid', type: 'line',
        filter: ['!', ['coalesce', ['get', 'dash'], false]],
        paint: { 'line-color': ['get', 'color'], 'line-width': 2 },
      },
      {
        id: 'polygons-stroke-dash', type: 'line',
        filter: ['==', ['coalesce', ['get', 'dash'], false], true],
        paint: { 'line-color': ['get', 'color'], 'line-width': 2, 'line-dasharray': [2, 1.5] },
      },
      {
        // Polygon label：用 Point geometry（caller 算 centroid 加進 source）。
        // 解 MapLibre 對 Polygon symbol-placement:'point' 跨 tile 算多次 centroid
        // → zoom 拉大 polygon 跨多 tile → 多個 label 的 bug。
        // filter geometry-type=Point 確保只渲染 caller 加的 centroid Point feature，
        // 同 source 的 Polygon feature 不被本 layer render。
        id: 'polygons-label', type: 'symbol',
        filter: ['all',
          ['has', 'label'],
          ['==', ['geometry-type'], 'Point'],
        ],
        layout: {
          'text-field': ['get', 'label'],
          'text-font': ['Noto Sans Regular'],
          'text-size': 11,
          'symbol-placement': 'point',
          'text-allow-overlap': false,
        },
        paint: {
          'text-color': ['get', 'color'],
          'text-halo-color': '#0d1117',
          'text-halo-width': 2,
        },
      },
    ],
  });

  // Infra — circle + abbr text label（P1-10b 步驟 7 階段 3b：glyphs source 已 vendor）
  _infraLayer = new EntityLayer(map, 'infra', {
    layers: [
      {
        id: 'infra-circle', type: 'circle',
        paint: {
          'circle-radius': 12, 'circle-color': ['get', 'color'],
          'circle-stroke-width': 2, 'circle-stroke-color': '#ffffff', 'circle-opacity': 0.92,
        },
      },
      {
        id: 'infra-label', type: 'symbol',
        layout: {
          'text-field': ['get', 'abbr'],
          'text-font': ['Noto Sans Regular'],
          'text-size': 12,
          'text-anchor': 'center',
          'text-allow-overlap': true,
          'text-ignore-placement': true,
        },
        paint: {
          'text-color': '#ffffff',
          'text-halo-color': '#000000',
          'text-halo-width': 0.5,
        },
      },
    ],
  });

  // P1-10b 步驟 7 階段 2/3a：bake SDF icons（zone abbr 字 + arrow 三角形）
  // 必須在新 EntityLayer 建 symbol layer 之前 addImage，否則 layer 找不到 icon-image。
  // 不重複 bake — bakeTextSdf/bakeArrowSdf 內部 hasImage 判斷。
  bakeTextSdf(map, 'napsg-abbr-', ['收', '醫', '指', '前', '安', '救', '護', '設', '行']);
  bakeArrowSdf(map, 'route-arrow');

  // Routes — line（solid/dash 拆兩 layer）+ arrow symbol-on-line（step 7 階段 3a）
  _routeLayer = new EntityLayer(map, 'routes', {
    layers: [
      {
        id: 'routes-line-solid', type: 'line',
        filter: ['!', ['coalesce', ['get', 'dash'], false]],
        paint: { 'line-color': ['get', 'color'], 'line-width': 3, 'line-opacity': 0.9 },
      },
      {
        id: 'routes-line-dash', type: 'line',
        filter: ['==', ['coalesce', ['get', 'dash'], false], true],
        paint: { 'line-color': ['get', 'color'], 'line-width': 3, 'line-opacity': 0.9, 'line-dasharray': [2, 1.5] },
      },
      {
        id: 'routes-arrow', type: 'symbol',
        layout: {
          'symbol-placement': 'line',
          'symbol-spacing': 90,
          'icon-image': 'route-arrow',
          'icon-size': 1.0,
          'icon-allow-overlap': true,
          'icon-ignore-placement': true,
          'icon-rotation-alignment': 'map',
        },
        paint: { 'icon-color': ['get', 'color'], 'icon-opacity': 0.9 },
      },
      {
        // Route label：放在線中點，水平閱讀
        id: 'routes-label', type: 'symbol',
        filter: ['has', 'label'],
        layout: {
          'text-field': ['get', 'label'],
          'text-font': ['Noto Sans Regular'],
          'text-size': 11,
          'symbol-placement': 'line-center',
          'text-rotation-alignment': 'viewport',  // 文字永遠水平，不跟線斜
          'text-allow-overlap': false,
        },
        paint: {
          'text-color': ['get', 'color'],
          'text-halo-color': '#0d1117',
          'text-halo-width': 2,
        },
      },
    ],
  });

  // Flows — line + arrow（同 routes 模式，方向感更重要因為 flow 本身 = 流向）
  _flowLayer = new EntityLayer(map, 'flows', {
    layers: [
      {
        id: 'flows-line', type: 'line',
        paint: { 'line-color': ['get', 'color'], 'line-width': 2.5, 'line-opacity': 0.85 },
      },
      {
        id: 'flows-arrow', type: 'symbol',
        layout: {
          'symbol-placement': 'line',
          'symbol-spacing': 80,
          'icon-image': 'route-arrow',
          'icon-size': 1.05,
          'icon-allow-overlap': true,
          'icon-ignore-placement': true,
          'icon-rotation-alignment': 'map',
        },
        paint: { 'icon-color': ['get', 'color'], 'icon-opacity': 0.95 },
      },
      {
        // Flow label：放在線中點
        id: 'flows-label', type: 'symbol',
        filter: ['has', 'label'],
        layout: {
          'text-field': ['get', 'label'],
          'text-font': ['Noto Sans Regular'],
          'text-size': 11,
          'symbol-placement': 'line-center',
          'text-rotation-alignment': 'viewport',
          'text-allow-overlap': false,
        },
        paint: {
          'text-color': ['get', 'color'],
          'text-halo-color': '#0d1117',
          'text-halo-width': 2,
        },
      },
    ],
  });

  // Zones — step 7 階段 1：circle marker（NAPSG SVG SDF 留階段 2；
  // 用三層 stack 預留 hover/selected/halo 接點，目前 selected/halo 給 0 opacity）
  // MapLibre case 條件需顯式 boolean expression（不接受 ['get','xxx'] 直接當 truthy），
  // 用 ['==', ..., true] 確保通過 style 驗證。
  _zoneLayer = new EntityLayer(map, 'zones', {
    layers: [
      // halo（為 critical/selected entity 預留，目前 hidden）
      {
        id: 'zones-halo', type: 'circle',
        paint: {
          'circle-radius': 22,
          'circle-color': ['get', 'color'],
          'circle-opacity': [
            'case', ['==', ['get', 'severity'], 'critical'], 0.18, 0,
          ],
          'circle-blur': 0.6,
        },
      },
      // base circle
      {
        id: 'zones-base', type: 'circle',
        paint: {
          'circle-radius': [
            'case', ['==', ['coalesce', ['get', 'is_event'], false], true], 13, 14,
          ],
          'circle-color': ['get', 'color'],
          'circle-stroke-color': '#ffffff',
          'circle-stroke-width': 2,
          'circle-opacity': [
            'case', ['==', ['coalesce', ['get', 'stale'], false], true], 0.55, 0.92,
          ],
        },
      },
      // P1-10b 步驟 7 階段 2：abbr 字（白色 SDF 字浮在 circle 上）
      // icon-image 動態組 'napsg-abbr-' + properties.abbr；caller 須確認 abbr 已 bake。
      // SDF + icon-color 白 → 任何 base color 上都可見。
      {
        id: 'zones-abbr', type: 'symbol',
        layout: {
          'icon-image': ['concat', 'napsg-abbr-', ['get', 'abbr']],
          'icon-size': 0.9,
          'icon-allow-overlap': true,
          'icon-ignore-placement': true,
        },
        paint: { 'icon-color': '#ffffff', 'icon-opacity': 0.95 },
      },
      // P1-10b 步驟 7 階段 3b：zone full label（收容組/醫療組/...）放圓圈下方
      {
        id: 'zones-label', type: 'symbol',
        filter: ['has', 'label'],
        layout: {
          'text-field': ['get', 'label'],
          'text-font': ['Noto Sans Regular'],
          'text-size': 11,
          'text-anchor': 'top',
          'text-offset': [0, 1.4],   // circle 半徑 ~14 / text-size 11 → offset 1.4 em
          'text-allow-overlap': false,
        },
        paint: {
          'text-color': '#e6edf3',
          'text-halo-color': '#0d1117',
          'text-halo-width': 2,
        },
      },
    ],
  });

  // Click handlers — 統一走 map.on('click', layerId, ...) 委派
  // routes/polygons-stroke 拆兩 layer（solid/dash），各自掛
  map.on('click', 'polygons-fill', (e) => _onPolygonClick(e));
  map.on('click', 'infra-circle', (e) => _onInfraClick(e));
  map.on('click', 'routes-line-solid', (e) => _onRouteClick(e));
  map.on('click', 'routes-line-dash', (e) => _onRouteClick(e));
  map.on('click', 'flows-line', (e) => _onFlowClick(e));
  map.on('click', 'zones-base', (e) => _onZoneClick(e));
  map.on('click', 'zones-abbr', (e) => _onZoneClick(e));  // abbr 字也可點，跟 base 同 handler

  // Cursor 變 pointer 提示可點 — 用 counter 追進入多少 clickable layer，
  // 為 0 時還原 MapLibre 預設 'grab'（不能 reset 成 '' 否則拖曳 cursor 卡住）。
  let _hoverCount = 0;
  const _setHoverCursor = () => { map.getCanvas().style.cursor = 'pointer'; };
  const _resetHoverCursor = () => { map.getCanvas().style.cursor = ''; };  // '' = 回 MapLibre 自己管
  [
    'polygons-fill', 'infra-circle',
    'routes-line-solid', 'routes-line-dash', 'flows-line',
    'zones-base', 'zones-abbr',
  ].forEach((id) => {
    map.on('mouseenter', id, () => { _hoverCount += 1; _setHoverCursor(); });
    map.on('mouseleave', id, () => {
      _hoverCount = Math.max(0, _hoverCount - 1);
      if (_hoverCount === 0) _resetHoverCursor();
    });
  });

  _entityLayersInstalled = true;
}

function _findById(arr, id) {
  return Array.isArray(arr) ? arr.find((x) => x?.id === id) : null;
}

function _onPolygonClick(e) {
  if (!canAccessMapObjects()) return;
  const id = e.features?.[0]?.properties?.id;
  const poly = _findById(_mapConfig?.maps?.outdoor?.polygons, id);
  if (!poly) return;
  const typeLabel = POLY_TYPES[poly.poly_type]?.label || poly.poly_type;
  const desc = `${typeLabel}　${poly.latlngs.length} 個頂點`;
  _deps.openModal?.(`▱ ${poly.label || '範圍'}`,
    _featureInfo(desc, 'deletePolygon', poly.id,
      poly.label_anchor ? { resetAnchorAction: 'resetPolyLabelAnchor' } : {}));
}

function _onInfraClick(e) {
  if (!canAccessMapObjects()) return;
  const id = e.features?.[0]?.properties?.id;
  const item = _findById(_mapConfig?.maps?.outdoor?.infrastructure, id);
  if (!item) return;
  const def = INFRA_TYPES[item.infra_type] || INFRA_TYPES.utility;
  _deps.openModal?.(`${def.abbr} ${item.label}`, _featureInfo(def.label, 'deleteInfra', item.id));
}

function _onRouteClick(e) {
  if (!canAccessMapObjects()) return;
  const id = e.features?.[0]?.properties?.id;
  const route = _findById(_mapConfig?.maps?.outdoor?.routes, id);
  if (!route) return;
  const typeLabel = ROUTE_TYPES[route.route_type]?.label || route.route_type;
  const desc = `${typeLabel}　${route.latlngs.length} 個節點`;
  _deps.openModal?.(`↗ ${route.label || '路線'}`,
    _featureInfo(desc, 'deleteRoute', route.id,
      route.label_anchor ? { resetAnchorAction: 'resetRouteLabelAnchor' } : {}));
}

function _onFlowClick(e) {
  if (!canAccessMapObjects()) return;
  const id = e.features?.[0]?.properties?.id;
  const flow = _findById(_mapConfig?.maps?.outdoor?.flows, id);
  if (!flow) return;
  const def = FLOW_TYPES[flow.flow_type] || FLOW_TYPES.casualty;
  const props = e.features?.[0]?.properties || {};
  const desc = `${def.label || flow.flow_type}　${props.from_label || '?'} → ${props.to_label || '?'}`;
  _deps.openModal?.(`→ ${flow.label || def.label || '流向'}`,
    _featureInfo(desc, 'deleteFlow', flow.id));
}

function _onZoneClick(e) {
  if (!canAccessMapObjects()) return;
  const id = e.features?.[0]?.properties?.id;
  const zone = _findById(_mapConfig?.maps?.outdoor?.zones, id);
  if (!zone) return;
  const isEvent = !!(zone.event_id || zone.event_code);
  if (isEvent) {
    // 看事件存在否，決定 orphan 路徑（與既有 Leaflet path 相同邏輯）
    const data = _deps.getData?.() || {};
    const ev = (data.events || []).find((item) => item.id === zone.event_id);
    if (!ev) _showOrphanZoneModal(zone);
    else _deps.showEventProcessModal?.(zone);
    return;
  }
  (_deps.showZoneDetail || showZoneDetail)(zone);
}

function _renderPolygons() {
  if (typeof window.maplibregl !== 'undefined') {
    if (!_polygonLayer) return;
    _polygonLayer.setVisible(_layerVis.polygons);
    if (!_layerVis.polygons) { _polygonLayer.clear(); return; }
    // 每個 polygon 產出 2 個 feature 餵同 source：
    //   1. Polygon geometry（fill / stroke layer 渲染）
    //   2. Point geometry（centroid，label layer 渲染 — 解 MapLibre 跨 tile 多
    //      centroid 導致 label 重複的 bug，filter geometry-type=Point）
    const polys = _mapConfig?.maps?.outdoor?.polygons || [];
    const features = [
      ...polys.map(polygonToFeature).filter(Boolean),
      ...polys.map(polygonLabelToFeature).filter(Boolean),
    ];
    _polygonLayer.update(features);
    return;
  }
  // Leaflet legacy path（不會跑到，refreshLeafletMarkers 已分流；保留供 fallback / unit test 用）
  if (!_leafletMap) return;
  if (!_polygonLayer) _polygonLayer = L.layerGroup().addTo(_leafletMap);
  _polygonLayer.clearLayers();
  if (!_layerVis.polygons) return;
  const polygons = _mapConfig?.maps?.outdoor?.polygons || [];
  for (const poly of polygons) {
    const dash = poly.dash ? '8 5' : null;
    const layer = L.polygon(poly.latlngs, {
      color: poly.color, weight: 2, dashArray: dash,
      fillColor: poly.color, fillOpacity: 0.12, interactive: true,
    }).addTo(_polygonLayer);
    layer.on('click', () => {
      if (!canAccessMapObjects()) return;
      const typeLabel = POLY_TYPES[poly.poly_type]?.label || poly.poly_type;
      const desc = `${typeLabel}　${poly.latlngs.length} 個頂點`;
      _deps.openModal?.(`▱ ${poly.label || '範圍'}`,
        _featureInfo(desc, 'deletePolygon', poly.id,
          poly.label_anchor ? { resetAnchorAction: 'resetPolyLabelAnchor' } : {}));
    });
  }
}

function _polyCentroid(latlngs) {
  const lat = latlngs.reduce((sum, point) => sum + point[0], 0) / latlngs.length;
  const lng = latlngs.reduce((sum, point) => sum + point[1], 0) / latlngs.length;
  return [lat, lng];
}

function _infraIcon(infraType) {
  const def = INFRA_TYPES[infraType] || INFRA_TYPES.utility;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 28 28">
    <circle cx="14" cy="14" r="12" fill="${def.color}" stroke="#fff" stroke-width="2" opacity=".92"/>
    <text x="14" y="18.5" text-anchor="middle" font-size="11" font-weight="700"
          font-family="sans-serif" fill="#fff">${def.abbr}</text>
  </svg>`;
  return L.divIcon({ html: svg, className: '', iconSize: [28, 28], iconAnchor: [14, 14] });
}

function _renderInfra() {
  if (typeof window.maplibregl !== 'undefined') {
    if (!_infraLayer) return;
    _infraLayer.setVisible(_layerVis.infra);
    if (!_layerVis.infra) { _infraLayer.clear(); return; }
    const features = (_mapConfig?.maps?.outdoor?.infrastructure || [])
      .map((item) => {
        const def = INFRA_TYPES[item.infra_type] || INFRA_TYPES.utility;
        return infraToFeature({ ...item, color: def.color, abbr: def.abbr });
      })
      .filter(Boolean);
    _infraLayer.update(features);
    return;
  }
  // Leaflet legacy
  if (!_leafletMap) return;
  if (!_infraLayer) _infraLayer = L.layerGroup().addTo(_leafletMap);
  _infraLayer.clearLayers();
  if (!_layerVis.infra) return;
  const items = _mapConfig?.maps?.outdoor?.infrastructure || [];
  for (const item of items) {
    const def = INFRA_TYPES[item.infra_type] || INFRA_TYPES.utility;
    const marker = L.marker([item.lat, item.lng], {
      icon: _infraIcon(item.infra_type), title: item.label, zIndexOffset: -100,
    });
    marker.on('click', () => {
      if (!canAccessMapObjects()) return;
      _deps.openModal?.(`${def.abbr} ${item.label}`, _featureInfo(def.label, 'deleteInfra', item.id));
    });
    marker.addTo(_infraLayer);
  }
}

function _bearing(lat1, lng1, lat2, lng2) {
  const rad = Math.PI / 180;
  const dLng = (lng2 - lng1) * rad;
  const y = Math.sin(dLng) * Math.cos(lat2 * rad);
  const x = Math.cos(lat1 * rad) * Math.sin(lat2 * rad) -
    Math.sin(lat1 * rad) * Math.cos(lat2 * rad) * Math.cos(dLng);
  return ((Math.atan2(y, x) * 180 / Math.PI) + 360) % 360;
}

function _arrowIcon(angle, color) {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="-11 -11 22 22">
    <polygon points="0,-9 5,4 0,1 -5,4" fill="${color}" stroke="rgba(14,22,29,.6)" stroke-width="1"
      transform="rotate(${angle})"/>
  </svg>`;
  return L.divIcon({ html: svg, className: '', iconSize: [22, 22], iconAnchor: [11, 11] });
}

function _renderRoutes() {
  if (typeof window.maplibregl !== 'undefined') {
    if (!_routeLayer) return;
    _routeLayer.setVisible(_layerVis.routes);
    if (!_layerVis.routes) { _routeLayer.clear(); return; }
    const features = (_mapConfig?.maps?.outdoor?.routes || [])
      .map(routeToFeature).filter(Boolean);
    _routeLayer.update(features);
    return;
  }
  // Leaflet legacy
  if (!_leafletMap) return;
  if (!_routeLayer) _routeLayer = L.layerGroup().addTo(_leafletMap);
  _routeLayer.clearLayers();
  if (!_layerVis.routes) return;
  const routes = _mapConfig?.maps?.outdoor?.routes || [];
  for (const route of routes) {
    const line = L.polyline(route.latlngs, {
      color: route.color, weight: 3,
      dashArray: route.dash ? '8 5' : null, opacity: 0.9,
    }).addTo(_routeLayer);
    line.on('click', () => {
      if (!canAccessMapObjects()) return;
      const typeLabel = ROUTE_TYPES[route.route_type]?.label || route.route_type;
      const desc = `${typeLabel}　${route.latlngs.length} 個節點`;
      _deps.openModal?.(`↗ ${route.label || '路線'}`,
        _featureInfo(desc, 'deleteRoute', route.id,
          route.label_anchor ? { resetAnchorAction: 'resetRouteLabelAnchor' } : {}));
    });
  }
}

function _resolveRef(ref, flow) {
  if (!ref) {
    const zoneId = flow?.from_zone_id || flow?.to_zone_id;
    if (!zoneId) return null;
    ref = `zone:${zoneId}`;
  }
  const sep = ref.indexOf(':');
  const type = sep > 0 ? ref.slice(0, sep) : 'zone';
  const id = sep > 0 ? ref.slice(sep + 1) : ref;
  if (type === 'infra') {
    const item = (_mapConfig?.maps?.outdoor?.infrastructure || []).find(i => i.id === id);
    return item ? { lat: item.lat, lng: item.lng, label: item.label } : null;
  }
  const zone = (_mapConfig?.maps?.outdoor?.zones || []).find(z => z.id === id);
  return zone ? { lat: zone.lat, lng: zone.lng, label: zone.label } : null;
}

function _renderFlows() {
  if (typeof window.maplibregl !== 'undefined') {
    if (!_flowLayer) return;
    _flowLayer.setVisible(_layerVis.flows);
    if (!_layerVis.flows) { _flowLayer.clear(); return; }
    const features = (_mapConfig?.maps?.outdoor?.flows || [])
      .map((flow) => {
        const def = FLOW_TYPES[flow.flow_type] || FLOW_TYPES.casualty;
        return flowToFeature({ ...flow, color: flow.color || def.color }, _resolveRef);
      })
      .filter(Boolean);
    _flowLayer.update(features);
    return;
  }
  // Leaflet legacy
  if (!_leafletMap) return;
  if (!_flowLayer) _flowLayer = L.layerGroup().addTo(_leafletMap);
  _flowLayer.clearLayers();
  if (!_layerVis.flows) return;
  const flows = _mapConfig?.maps?.outdoor?.flows || [];
  for (const flow of flows) {
    const fromRef = flow.from_ref || (flow.from_zone_id ? `zone:${flow.from_zone_id}` : null);
    const toRef = flow.to_ref || (flow.to_zone_id ? `zone:${flow.to_zone_id}` : null);
    const from = _resolveRef(fromRef);
    const to = _resolveRef(toRef);
    if (!from?.lat || !to?.lat) continue;
    const def = FLOW_TYPES[flow.flow_type] || FLOW_TYPES.casualty;
    const line = L.polyline([[from.lat, from.lng], [to.lat, to.lng]], {
      color: def.color, weight: 2.5, opacity: 0.85,
    }).addTo(_flowLayer);
    line.on('click', () => {
      if (!canAccessMapObjects()) return;
      const desc = `${def.label || flow.flow_type}　${from.label || '?'} → ${to.label || '?'}`;
      _deps.openModal?.(`→ ${flow.label || def.label || '流向'}`,
        _featureInfo(desc, 'deleteFlow', flow.id));
    });
  }
}

// P1-10b 步驟 7 階段 1：zone marker port — circle only（NAPSG SVG SDF + abbr text
// 留階段 2，需 addImage + glyphs source）。Drag-to-reposition 留 step 8 draw_tools.js。
function _renderZones() {
  if (typeof window.maplibregl === 'undefined') return;  // Leaflet legacy 走原 refreshLeafletMarkers
  if (!_zoneLayer) return;
  _zoneLayer.setVisible(_layerVis.zones);
  if (!_layerVis.zones) { _zoneLayer.clear(); return; }
  const map = _mapConfig?.maps?.outdoor;
  if (!map?.zones) { _zoneLayer.clear(); return; }
  const data = _deps.getData?.() || {};
  const features = [];
  for (const zone of map.zones) {
    if (zone.lat == null || zone.lng == null) continue;
    const isEvent = !!(zone.event_id || zone.event_code);
    let severity = 'warning';
    let isOrphan = false;
    if (isEvent) {
      const ev = (data.events || []).find((item) => item.id === zone.event_id);
      if (!ev) { severity = 'info'; isOrphan = true; }
      else if (['resolved', 'closed'].includes(ev.status)) continue;
      else severity = ev.severity || 'warning';
    }

    // 顏色解析：事件 → SEV；節點 → NODE base，shelter/medical 受 RAG 蓋過
    let color = isEvent
      ? (_SEV_COLORS[severity] || '#8b949e')
      : (_NODE_COLORS[zone.node_type] || '#8b949e');
    let stale = false;
    if (!isEvent && zone.icon === 'pin' && (zone.node_type === 'shelter' || zone.node_type === 'medical')) {
      const calc = data.calc || {};
      const piNode = (data.pi_nodes || []).find((n) => n.unit_id === zone.node_type);
      let linkLevel = 'lkp';
      if (piNode?.last_seen_at) {
        const age = Date.now() - new Date(piNode.last_seen_at).getTime();
        linkLevel = age < 30000 ? 'ok' : age < 90000 ? 'warn' : 'crit';
      }
      const snapshot = calc[zone.node_type]?.snapshot;
      if (snapshot) {
        const used = snapshot.bed_used || 0;
        const total = snapshot.bed_total || 1;
        const pct = (used / total) * 100;
        const rag = pct >= 90 ? 'crit' : pct >= 70 ? 'warn' : 'ok';
        color = _RAG_COLORS[rag] || color;
      }
      if (linkLevel === 'crit' || linkLevel === 'lkp') stale = true;
    }

    // NAPSG group abbr（事件 zone 走 event group；節點 zone 走 node abbr）
    let abbr = '?';
    if (isEvent) {
      const evDef = _EVENT_TYPES[zone.event_code] || {};
      abbr = _NAPSG_GROUP_ABBR[evDef.group] || _NODE_ABBR[zone.node_type] || '?';
    } else {
      abbr = _NODE_ABBR[zone.node_type] || '?';
    }

    const feat = zoneToNodeFeature(zone, { color, abbr, severity, stale, is_orphan: isOrphan });
    if (feat) features.push(feat);
  }
  _zoneLayer.update(features);
}

function _simpleInfo(text) {
  return `<div style="font-size:12px;line-height:1.7;color:var(--text2);">${text || ''}</div>`;
}

/**
 * 範圍 / 路線 / 流向 / 設施的詳情 modal body
 * 含「刪除」（紅）與選用的「重設標籤位置」（中性）按鈕
 * @param {string} desc 主說明文字
 * @param {string} action 對應 main.js 的 data-action（如 deletePolygon）
 * @param {string} id     對象 id（用於 data-id）
 * @param {object} extra  選用：{ resetAnchorAction: 'resetPolyLabelAnchor' } 顯示重設標籤鈕
 */
function _featureInfo(desc, action, id, extra = {}) {
  let html = `<div style="font-size:12px;line-height:1.7;color:var(--text2);margin-bottom:12px;">${desc || ''}</div>`;
  if (extra.resetAnchorAction) {
    html += `<button data-action="${extra.resetAnchorAction}" data-id="${id}"
      style="width:100%;padding:7px;background:transparent;border:1px solid var(--border);color:var(--text2);
      border-radius:6px;cursor:pointer;margin-bottom:8px;font-family:var(--mono);font-size:11px;">↺ 重設標籤至自動位置</button>`;
  }
  html += `<button data-action="${action}" data-id="${id}"
    style="width:100%;padding:8px;background:var(--red);color:#fff;border:none;border-radius:6px;
    font-weight:700;cursor:pointer;font-family:var(--mono);font-size:12px;">🗑 刪除</button>`;
  return html;
}

export function _toggleMgrsGrid() {
  _mgrsGridVisible = !_mgrsGridVisible;
  _layerVis.mgrs = _mgrsGridVisible;
  document.getElementById('btn-mgrs-grid')?.classList.toggle('active', _mgrsGridVisible);
  _drawMgrsGrid();
}
export function _toggleLayerPanel() {
  const panel = el('layer-panel');
  if (!panel) return;
  if (panel.style.display !== 'none' && panel.style.display !== '') {
    _closeLayerPanel();
  } else {
    _rebuildLayerPanel();
    panel.style.display = 'block';
    document.getElementById('btn-layer-panel')?.classList.add('active');
  }
}
export function _closeLayerPanel() {
  const panel = el('layer-panel');
  if (panel) panel.style.display = 'none';
  document.getElementById('btn-layer-panel')?.classList.remove('active');
}
export function _toggleLayer(key) {
  if (!key) return;
  if (key === 'mgrs') {
    _toggleMgrsGrid();
    _rebuildLayerPanel();
    return;
  }
  if (!(key in _layerVis)) return;
  _layerVis[key] = !_layerVis[key];
  refreshLeafletMarkers();
  _rebuildLayerPanel();
}
// ══════════════════════════════════════════════════════════════
// 孤兒事件 zone（map_config 裡有 zone 但 _data.events 找不到）
// ══════════════════════════════════════════════════════════════

function _showOrphanZoneModal(zone) {
  if (!canAccessMapObjects()) return;
  const code = zone.event_code || zone.id || '?';
  const desc = `此事件標記在地圖上仍存在，但對應的事件紀錄已不在資料庫（可能已被清除或重設）。`
    + `<br><br><span style="color:var(--text3);font-size:11px;">標記：${code}　·　類型：${zone.label || zone.node_type || '—'}</span>`;
  _deps.openModal?.(`⚠ 孤兒事件標記`,
    _featureInfo(desc, 'deleteEventZone', zone.id));
}

export async function _deleteEventZone(id) {
  if (!_mapConfig?.maps || !id) return;
  let removed = false;
  for (const m of Object.values(_mapConfig.maps)) {
    if (!m.zones) continue;
    const before = m.zones.length;
    m.zones = m.zones.filter(z => z.id !== id);
    if (m.zones.length !== before) removed = true;
    // 同時清掉指向此 zone 的 flow（避免另一種孤兒）
    if (m.flows) {
      m.flows = m.flows.filter(f =>
        f.from_ref !== `zone:${id}` && f.to_ref !== `zone:${id}` &&
        f.from_zone_id !== id && f.to_zone_id !== id
      );
    }
  }
  if (!removed) return;
  await saveMapConfig();
  _deps.closeModal?.();
  if (_currentMap === 'outdoor') refreshLeafletMarkers();
  else renderMapOverlay();
}

// ══════════════════════════════════════════════════════════════
// 繪製範圍（Polygon）
// ══════════════════════════════════════════════════════════════

export function _startPolyDraw() {
  if (!canAccessMapObjects()) return;
  if (_polyDrawState) _cancelPolyDraw();
  if (_routeDrawState) _cancelRouteDraw();
  if (_currentMap !== 'outdoor') switchMap('outdoor');
  _polyDrawState = { latlngs: [], markers: [], previewPoly: null };
  const banner = el('poly-draw-banner');
  if (banner) banner.style.display = 'flex';
  if (el('map-coord-panel')) el('map-coord-panel').style.display = 'none';
  if (_leafletMap) _leafletMap.getContainer().style.cursor = 'crosshair';
  document.getElementById('btn-poly-draw')?.classList.add('active');
}

export function _cancelPolyDraw() {
  if (!_polyDrawState) return;
  _polyDrawState.markers.forEach(m => m.remove());
  if (_polyDrawState.previewPoly) _polyDrawState.previewPoly.remove();
  _polyDrawState = null;
  const banner = el('poly-draw-banner');
  if (banner) banner.style.display = 'none';
  if (_leafletMap) _leafletMap.getContainer().style.removeProperty('cursor');
  document.getElementById('btn-poly-draw')?.classList.remove('active');
}

function _addPolyVertex(lat, lng) {
  if (!_polyDrawState || !window.L || !_leafletMap) return;
  _polyDrawState.latlngs.push([lat, lng]);
  const m = L.circleMarker([lat, lng], {
    radius: 4, color: '#fff', weight: 2, fillColor: '#58a6ff', fillOpacity: 1, interactive: false,
  }).addTo(_leafletMap);
  _polyDrawState.markers.push(m);
  if (_polyDrawState.previewPoly) _polyDrawState.previewPoly.remove();
  if (_polyDrawState.latlngs.length >= 2) {
    _polyDrawState.previewPoly = L.polygon(_polyDrawState.latlngs, {
      color: '#58a6ff', weight: 1.5, dashArray: '6 3', fillOpacity: 0.08, interactive: false,
    }).addTo(_leafletMap);
  }
  const finBtn = document.getElementById('poly-finish-btn');
  if (finBtn) finBtn.disabled = _polyDrawState.latlngs.length < 3;
}

export function _finishPolyDraw() {
  if (!_polyDrawState || _polyDrawState.latlngs.length < 3) return;
  const latlngs = [..._polyDrawState.latlngs];
  _cancelPolyDraw();
  _openPolyForm(latlngs);
}

export function _openPolyForm(latlngs) {
  if (!latlngs || latlngs.length < 3) return;
  _pendingPolyLatlngs = latlngs;
  const typeOpts = Object.entries(POLY_TYPES).map(([k, v]) =>
    `<option value="${k}">${v.label}</option>`).join('');
  const SEL = 'width:100%;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:6px 8px;border-radius:4px;font-size:12px;';
  let html = '';
  html += `<div style="margin-bottom:12px;"><label style="font-size:11px;color:var(--text3);display:block;margin-bottom:4px;">範圍名稱</label>`;
  html += `<input id="poly-name" placeholder="例：北側管制區" autocomplete="off" style="${SEL}"></div>`;
  html += `<div style="margin-bottom:16px;"><label style="font-size:11px;color:var(--text3);display:block;margin-bottom:4px;">類型</label>`;
  html += `<select id="poly-type" style="${SEL}">${typeOpts}</select></div>`;
  html += `<div style="font-size:10px;color:var(--text3);margin-bottom:16px;">${latlngs.length} 個頂點</div>`;
  html += `<div style="display:flex;gap:8px;">`;
  html += `<button data-action="closeModal" style="flex:1;padding:8px;background:transparent;border:1px solid var(--border);color:var(--text2);border-radius:6px;cursor:pointer;font-family:var(--mono);">取消</button>`;
  html += `<button data-action="savePolygon" style="flex:2;padding:8px;background:var(--green);color:#fff;border:none;border-radius:6px;font-weight:700;cursor:pointer;font-family:var(--mono);">儲存</button>`;
  html += `</div>`;
  _deps.openModal?.('✏ 新增範圍', html);
}

export async function _savePolygon() {
  if (!canAccessMapObjects()) return;
  const name = (document.getElementById('poly-name')?.value || '').trim();
  const typeKey = document.getElementById('poly-type')?.value || 'ops';
  const latlngs = _pendingPolyLatlngs;
  if (!name || !latlngs) return;
  const def = POLY_TYPES[typeKey];
  const poly = {
    id: 'poly_' + Date.now(),
    label: name,
    poly_type: typeKey,
    color: def.color,
    dash: def.dash,
    latlngs,
  };
  if (!_mapConfig.maps.outdoor.polygons) _mapConfig.maps.outdoor.polygons = [];
  _mapConfig.maps.outdoor.polygons.push(poly);
  _pendingPolyLatlngs = null;
  await saveMapConfig();
  _deps.closeModal?.();
  _renderPolygons();
}

export async function _deletePolygon(id) {
  if (!_mapConfig?.maps?.outdoor?.polygons || !id) return;
  _mapConfig.maps.outdoor.polygons = _mapConfig.maps.outdoor.polygons.filter(p => p.id !== id);
  await saveMapConfig();
  _deps.closeModal?.();
  _renderPolygons();
}

export async function _resetPolyLabelAnchor(id) {
  const poly = (_mapConfig?.maps?.outdoor?.polygons || []).find(p => p.id === id);
  if (!poly) return;
  delete poly.label_anchor;
  await saveMapConfig();
  _deps.closeModal?.();
  _renderPolygons();
}

// 設施新增（_openInfraForm / _startInfraPlace / _saveInfraPosition）— 暫未實作
// 目前用「圖層面板 → 新增設施」進入點，待後續補上
export function _openInfraForm() {}
export function _startInfraPlace() {}

export async function _deleteInfra(id) {
  if (!_mapConfig?.maps?.outdoor?.infrastructure || !id) return;
  _mapConfig.maps.outdoor.infrastructure = _mapConfig.maps.outdoor.infrastructure.filter(i => i.id !== id);
  await saveMapConfig();
  _deps.closeModal?.();
  _renderInfra();
}

export function _saveInfraPosition() {}
// ══════════════════════════════════════════════════════════════
// 流向（Flow）表單
// ══════════════════════════════════════════════════════════════

export function _openFlowForm() {
  if (!canAccessMapObjects()) return;
  const zones = (_mapConfig?.maps?.outdoor?.zones || []).filter(z => z.lat != null);
  const infras = _mapConfig?.maps?.outdoor?.infrastructure || [];
  if (zones.length + infras.length < 2) {
    _deps.openModal?.('● → ● 新增調度指示',
      `<div style="color:var(--text2);font-size:12px;padding:12px 0;">需要至少兩個已定位的節點（或設施）才能建立流向。</div>
       <button data-action="closeModal" style="width:100%;padding:8px;background:var(--surface2);border:1px solid var(--border);color:var(--text);border-radius:6px;cursor:pointer;font-family:var(--mono);">關閉</button>`);
    return;
  }
  const SEL = 'width:100%;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:6px 8px;border-radius:4px;font-size:12px;';
  const nodeZones = zones.filter(z => !(z.event_id || z.event_code));
  const eventZones = zones.filter(z => !!(z.event_id || z.event_code));
  const endpointOpts =
    (nodeZones.length ? `<optgroup label="── ICS 節點">` +
      nodeZones.map(z => `<option value="zone:${z.id}">${z.label}</option>`).join('') +
      `</optgroup>` : '') +
    (eventZones.length ? `<optgroup label="── 事件標記">` +
      eventZones.map(z => {
        const typeName = _EVENT_TYPES[z.node_type]?.label || z.label;
        const code = z.event_code ? ` · ${z.event_code.replace(/-\d{4}-/, '-')}` : '';
        return `<option value="zone:${z.id}">${typeName}${code}</option>`;
      }).join('') + `</optgroup>` : '') +
    (infras.length ? `<optgroup label="── 基礎設施">` + infras.map(i => {
      const def = INFRA_TYPES[i.infra_type] || {};
      return `<option value="infra:${i.id}">${def.abbr || '+'} ${i.label}</option>`;
    }).join('') + `</optgroup>` : '');
  const typeOpts = Object.entries(FLOW_TYPES).map(([k, v]) =>
    `<option value="${k}">${v.label}</option>`).join('');
  let html = '';
  html += `<div style="margin-bottom:10px;"><label style="font-size:11px;color:var(--text3);display:block;margin-bottom:4px;">流向類型</label>`;
  html += `<select id="flow-type-sel" style="${SEL}">${typeOpts}</select></div>`;
  html += `<div style="margin-bottom:10px;"><label style="font-size:11px;color:var(--text3);display:block;margin-bottom:4px;">起點</label>`;
  html += `<select id="flow-from-sel" style="${SEL}">${endpointOpts}</select></div>`;
  html += `<div style="margin-bottom:10px;"><label style="font-size:11px;color:var(--text3);display:block;margin-bottom:4px;">終點</label>`;
  html += `<select id="flow-to-sel" style="${SEL}">${endpointOpts}</select></div>`;
  html += `<div style="margin-bottom:16px;"><label style="font-size:11px;color:var(--text3);display:block;margin-bottom:4px;">標籤（可選）</label>`;
  html += `<input id="flow-label" placeholder="例：傷患後送路徑" autocomplete="off" style="${SEL}"></div>`;
  html += `<div style="display:flex;gap:8px;">`;
  html += `<button data-action="closeModal" style="flex:1;padding:8px;background:transparent;border:1px solid var(--border);color:var(--text2);border-radius:6px;cursor:pointer;font-family:var(--mono);">取消</button>`;
  html += `<button data-action="saveFlow" style="flex:2;padding:8px;background:var(--green);color:#fff;border:none;border-radius:6px;font-weight:700;cursor:pointer;font-family:var(--mono);">儲存</button>`;
  html += `</div>`;
  _deps.openModal?.('● → ● 新增調度指示', html);
}

export async function _saveFlow() {
  if (!canAccessMapObjects()) return;
  const typeVal = document.getElementById('flow-type-sel')?.value || 'casualty';
  const fromRef = document.getElementById('flow-from-sel')?.value || '';
  const toRef = document.getElementById('flow-to-sel')?.value || '';
  const labelVal = (document.getElementById('flow-label')?.value || '').trim();
  if (!fromRef || !toRef || fromRef === toRef) return;
  const flow = {
    id: 'flow_' + Date.now(),
    flow_type: typeVal,
    from_ref: fromRef,
    to_ref: toRef,
    label: labelVal,
  };
  if (!_mapConfig.maps.outdoor.flows) _mapConfig.maps.outdoor.flows = [];
  _mapConfig.maps.outdoor.flows.push(flow);
  await saveMapConfig();
  _deps.closeModal?.();
  _renderFlows();
}

export async function _deleteFlow(id) {
  if (!_mapConfig?.maps?.outdoor?.flows || !id) return;
  _mapConfig.maps.outdoor.flows = _mapConfig.maps.outdoor.flows.filter(f => f.id !== id);
  await saveMapConfig();
  _deps.closeModal?.();
  _renderFlows();
}

// ══════════════════════════════════════════════════════════════
// 繪製路線（Route）
// ══════════════════════════════════════════════════════════════

export function _startRouteDraw() {
  if (!canAccessMapObjects()) return;
  if (_routeDrawState) _cancelRouteDraw();
  if (_polyDrawState) _cancelPolyDraw();
  if (_currentMap !== 'outdoor') switchMap('outdoor');
  _routeDrawState = { latlngs: [], markers: [], previewLine: null };
  const banner = el('route-draw-banner');
  if (banner) banner.style.display = 'flex';
  if (el('map-coord-panel')) el('map-coord-panel').style.display = 'none';
  if (_leafletMap) _leafletMap.getContainer().style.cursor = 'crosshair';
  document.getElementById('btn-route-draw')?.classList.add('active');
}

export function _cancelRouteDraw() {
  if (!_routeDrawState) return;
  _routeDrawState.markers.forEach(m => m.remove());
  if (_routeDrawState.previewLine) _routeDrawState.previewLine.remove();
  _routeDrawState = null;
  const banner = el('route-draw-banner');
  if (banner) banner.style.display = 'none';
  if (_leafletMap) _leafletMap.getContainer().style.removeProperty('cursor');
  document.getElementById('btn-route-draw')?.classList.remove('active');
}

function _addRouteVertex(lat, lng) {
  if (!_routeDrawState || !window.L || !_leafletMap) return;
  _routeDrawState.latlngs.push([lat, lng]);
  const m = L.circleMarker([lat, lng], {
    radius: 4, color: '#fff', weight: 2, fillColor: '#56d364', fillOpacity: 1, interactive: false,
  }).addTo(_leafletMap);
  _routeDrawState.markers.push(m);
  if (_routeDrawState.previewLine) _routeDrawState.previewLine.remove();
  if (_routeDrawState.latlngs.length >= 2) {
    _routeDrawState.previewLine = L.polyline(_routeDrawState.latlngs, {
      color: '#56d364', weight: 2, dashArray: '6 3', opacity: 0.7, interactive: false,
    }).addTo(_leafletMap);
  }
  const finBtn = document.getElementById('route-finish-btn');
  if (finBtn) finBtn.disabled = _routeDrawState.latlngs.length < 2;
}

export function _finishRouteDraw() {
  if (!_routeDrawState || _routeDrawState.latlngs.length < 2) return;
  const latlngs = [..._routeDrawState.latlngs];
  _cancelRouteDraw();
  _openRouteForm(latlngs);
}

export function _openRouteForm(latlngs) {
  if (!latlngs || latlngs.length < 2) return;
  _pendingRouteLatlngs = latlngs;
  const typeOpts = Object.entries(ROUTE_TYPES).map(([k, v]) =>
    `<option value="${k}">${v.label}</option>`).join('');
  const SEL = 'width:100%;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:6px 8px;border-radius:4px;font-size:12px;';
  let html = '';
  html += `<div style="margin-bottom:12px;"><label style="font-size:11px;color:var(--text3);display:block;margin-bottom:4px;">路線名稱</label>`;
  html += `<input id="route-name" placeholder="例：北側主疏散路線" autocomplete="off" style="${SEL}"></div>`;
  html += `<div style="margin-bottom:16px;"><label style="font-size:11px;color:var(--text3);display:block;margin-bottom:4px;">類型</label>`;
  html += `<select id="route-type-sel" style="${SEL}">${typeOpts}</select></div>`;
  html += `<div style="font-size:10px;color:var(--text3);margin-bottom:16px;">${latlngs.length} 個節點</div>`;
  html += `<div style="display:flex;gap:8px;">`;
  html += `<button data-action="closeModal" style="flex:1;padding:8px;background:transparent;border:1px solid var(--border);color:var(--text2);border-radius:6px;cursor:pointer;font-family:var(--mono);">取消</button>`;
  html += `<button data-action="saveRoute" style="flex:2;padding:8px;background:var(--green);color:#fff;border:none;border-radius:6px;font-weight:700;cursor:pointer;font-family:var(--mono);">儲存</button>`;
  html += `</div>`;
  _deps.openModal?.('↗ 新增路線', html);
}

export async function _saveRoute() {
  if (!canAccessMapObjects()) return;
  const name = (document.getElementById('route-name')?.value || '').trim();
  const typeKey = document.getElementById('route-type-sel')?.value || 'primary';
  const latlngs = _pendingRouteLatlngs;
  if (!name || !latlngs) return;
  const def = ROUTE_TYPES[typeKey];
  const route = {
    id: 'route_' + Date.now(),
    route_type: typeKey,
    label: name,
    color: def.color,
    dash: def.dash,
    latlngs,
  };
  if (!_mapConfig.maps.outdoor.routes) _mapConfig.maps.outdoor.routes = [];
  _mapConfig.maps.outdoor.routes.push(route);
  _pendingRouteLatlngs = null;
  await saveMapConfig();
  _deps.closeModal?.();
  _renderRoutes();
}

export async function _deleteRoute(id) {
  if (!_mapConfig?.maps?.outdoor?.routes || !id) return;
  _mapConfig.maps.outdoor.routes = _mapConfig.maps.outdoor.routes.filter(r => r.id !== id);
  await saveMapConfig();
  _deps.closeModal?.();
  _renderRoutes();
}

export async function _resetRouteLabelAnchor(id) {
  const route = (_mapConfig?.maps?.outdoor?.routes || []).find(r => r.id === id);
  if (!route) return;
  delete route.label_anchor;
  await saveMapConfig();
  _deps.closeModal?.();
  _renderRoutes();
}
export function _cancelNodePlace() {}
export function _cancelEventPin() {}
export function admUploadMapImage() {}
export function admRemoveMapImage() {}
export function l3SubTab(tabId, activeId) {
  if (!tabId) return;
  sessionStorage.setItem('_l3SubTab', JSON.stringify({ tabId, activeId }));
  const bar = document.getElementById(tabId + '_bar');
  if (!bar) return;
  const wrap = bar.parentElement;
  wrap?.querySelectorAll(`[id^="${tabId}_panel_"]`).forEach(p => { p.style.display = 'none'; });
  bar.querySelectorAll(`[id^="${tabId}_btn_"]`).forEach(b => {
    b.style.background = 'var(--surface)';
    b.style.color = 'var(--text2)';
    b.style.fontWeight = '400';
  });
  const panel = document.getElementById(`${tabId}_panel_${activeId}`);
  if (panel) panel.style.display = 'block';
  const btn = document.getElementById(`${tabId}_btn_${activeId}`);
  if (btn) {
    btn.style.background = 'var(--accent)';
    btn.style.color = '#fff';
    btn.style.fontWeight = '700';
  }
}

let _l3Data = null;

export async function loadL3Records(unitId) {
  const container = document.getElementById('l3-container');
  if (!container) return;
  container.innerHTML = '<div style="color:var(--text3);font-size:11px;">載入中...</div>';
  try {
    const resp = await authFetch(API_BASE + `/api/pi-data/${unitId}/list`);
    if (!resp.ok) {
      container.innerHTML = '<div style="color:var(--red);font-size:11px;">載入失敗</div>';
      return;
    }
    const data = await resp.json();
    _l3Data = data;
    if (data.offline) {
      container.innerHTML = '<div style="color:var(--yellow);font-size:11px;">⚠ Pi 節點離線，無即時資料</div>';
      return;
    }

    const grouped = data.grouped || {};
    const tableLabels = { persons: '收容人員', resources: '物資', incidents: '組內事件', shifts: '值班', patients: '傷患' };
    let html = '';

    // 醫療：傷患階段摘要表
    if (unitId === 'medical' && grouped.patients) {
      const pts = grouped.patients.map(r => r.record || {});
      const colors = ['red', 'yellow', 'green', 'black'];
      const colorLabels = { red: '紅', yellow: '黃', green: '綠', black: '黑' };
      const colorBg = { red: '#cc2a2a', yellow: '#c8a82a', green: '#2a8c2a', black: '#333' };
      const isActive = p => p.current_zone !== '已離區' && p.disposition !== '離開' && p.disposition !== '死亡';
      const stages = [
        { label: '待評估', fn: p => (p.care_status || 'triaged') === 'triaged' && p.disposition !== '後送' && isActive(p) },
        { label: '治療中', fn: p => p.care_status === 'assessed' && p.disposition !== '後送' && isActive(p) },
        { label: '留觀中', fn: p => p.care_status === 'monitoring' && p.disposition !== '後送' && isActive(p) },
        { label: '等待後送', fn: p => p.disposition === '後送' && isActive(p) },
        { label: '已後送／離區', fn: p => !isActive(p) },
      ];
      html += `<table style="width:100%;border-collapse:collapse;font-size:11px;margin-bottom:10px;text-align:center;">`;
      html += `<tr style="background:var(--surface2);"><td></td>${stages.map(s => `<td style="padding:3px 4px;font-weight:700;color:var(--text2);font-size:10px;">${s.label}</td>`).join('')}<td style="padding:3px 4px;font-weight:700;color:var(--text);font-size:10px;">合計</td></tr>`;
      let grandTotal = 0;
      for (const c of colors) {
        const cPts = pts.filter(p => p.triage_color === c);
        const counts = stages.map(s => cPts.filter(s.fn).length);
        const rowTotal = counts.reduce((a, b) => a + b, 0);
        grandTotal += rowTotal;
        html += `<tr><td style="padding:3px 8px;background:${colorBg[c]};color:#fff;font-weight:700;border-radius:2px;">${colorLabels[c]}</td>`;
        html += counts.map(v => `<td style="padding:3px;color:${v > 0 ? 'var(--text)' : 'var(--text3)'};">${v || '—'}</td>`).join('');
        html += `<td style="padding:3px;font-weight:700;">${rowTotal}</td></tr>`;
      }
      html += `<tr style="border-top:1px solid var(--border);"><td style="padding:3px 8px;font-weight:700;">合計</td><td colspan="${stages.length}"></td><td style="padding:3px;font-weight:900;">${grandTotal}</td></tr></table>`;
    }

    // 收容：人員/床位摘要
    if (unitId === 'shelter' && grouped.persons) {
      const prs = grouped.persons.map(r => r.record || {});
      const totalBeds = (grouped.beds || []).length || Math.max(prs.filter(p => p.status === '已安置').length + 2, 12);
      const usedBeds = prs.filter(p => p.status === '已安置').length;
      const capPct = Math.round(usedBeds / totalBeds * 100);
      const capColor = capPct >= 90 ? 'var(--red)' : capPct >= 70 ? 'var(--yellow)' : 'var(--green)';
      html += `<div style="display:flex;align-items:baseline;gap:8px;margin-bottom:6px;">`;
      html += `<span style="font-size:28px;font-weight:800;">${usedBeds}</span>`;
      html += `<span style="font-size:12px;color:var(--text3);">/ ${totalBeds} 床</span>`;
      html += `<span style="font-size:22px;font-weight:700;color:${capColor};margin-left:auto;">${capPct}%</span>`;
      html += `</div>`;
      html += `<div style="height:4px;background:var(--surface2);border-radius:2px;margin-bottom:10px;"><div style="height:100%;width:${capPct}%;background:${capColor};border-radius:2px;"></div></div>`;
    }

    // 物資
    if (grouped.resources) {
      const resList = grouped.resources.map(r => r.record || {}).filter(r => !r.disabled);
      if (resList.length > 0) {
        html += `<div style="font-size:10px;font-weight:700;margin:6px 0 4px;border-bottom:1px solid var(--border);padding-bottom:2px;">物資</div>`;
        for (const r of resList) {
          const cur = r.qty_current ?? 0;
          const max = r.qty_initial || cur || 1;
          const pct = Math.round(cur / max * 100);
          const c = pct <= 20 ? 'var(--red)' : pct <= 40 ? 'var(--yellow)' : 'var(--green)';
          html += `<div style="display:flex;justify-content:space-between;font-size:11px;margin:2px 0;"><span>${r.name || '?'}</span><span style="color:${c};font-weight:600;">${cur}/${max}</span></div>`;
          html += `<div style="height:3px;background:var(--surface2);border-radius:2px;margin-bottom:3px;"><div style="height:100%;width:${pct}%;background:${c};border-radius:2px;"></div></div>`;
        }
      }
    }

    // 通用記錄列表
    const triageColorDot = { red: '🔴', yellow: '🟡', green: '🟢', black: '⚫' };
    for (const tableName of ['patients', 'persons', 'incidents', 'shifts']) {
      const records = grouped[tableName];
      if (!records || records.length === 0) continue;
      const label = tableLabels[tableName] || tableName;
      html += `<div style="font-size:10px;font-weight:700;margin:8px 0 4px;border-bottom:1px solid var(--border);padding-bottom:2px;">${label}（${records.length}）</div>`;
      records.forEach((r, i) => {
        const rec = r.record || {};
        const did = rec.display_id || rec._id || rec.id || r.record_id || '?';
        let extra = '';
        if (tableName === 'patients') {
          const dot = triageColorDot[rec.triage_color] || '';
          const chief = rec.chief_issue ? ` — ${rec.chief_issue.slice(0, 20)}` : '';
          extra = `${dot} <b>${did}</b>${chief}`;
        } else if (tableName === 'persons') {
          extra = `<b>${did}</b> · ${rec.status || ''}`;
        } else if (tableName === 'incidents') {
          const incLabels = { security_threat: '安全威脅', infectious_risk: '傳染疑慮', resource_shortage: '物資短缺', capacity_overload: '量能超載', medication_mgmt: '藥品管理', language_assist: '語言協助', other: '其他' };
          extra = `${incLabels[rec.type] || rec.type} · ${rec.severity || ''}`;
        } else {
          extra = did;
        }
        html += `<div data-action="openL4Detail" data-unit="${unitId}" data-table="${tableName}" data-index="${i}" style="padding:5px 8px;margin:2px 0;background:var(--surface);border-radius:3px;cursor:pointer;font-size:11px;">${extra}</div>`;
      });
    }

    if (!html) html = '<div style="color:var(--text3);font-size:11px;">無資料</div>';
    container.innerHTML = html;
  } catch (e) {
    container.innerHTML = '<div style="color:var(--red);font-size:11px;">網路錯誤：' + e.message + '</div>';
  }
}

export function openL4Detail(unitId, tableName, index) {
  if (!canAccessMapObjects()) return;
  if (!_l3Data || !_l3Data.grouped) return;
  const records = _l3Data.grouped[tableName];
  if (!records || !records[index]) return;
  const r = records[index];
  const rec = r.record || {};
  const container = document.getElementById('l3-container');
  if (!container) return;
  if (!container.dataset.prevHtml) container.dataset.prevHtml = container.innerHTML;
  const tableLabels = { persons: '收容人員', incidents: '組內事件', patients: '傷患', shifts: '值班', resources: '物資' };
  const label = tableLabels[tableName] || tableName;
  const name = rec.display_id || rec.name || rec._id || rec.id || r.record_id || '?';
  let fields = '';
  for (const [k, v] of Object.entries(rec)) {
    if (k === '_id' || k === '_enc') continue;
    let val = typeof v === 'object' ? JSON.stringify(v) : v;
    if (typeof val === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(val)) {
      const d = new Date(val);
      if (!isNaN(d)) {
        const p = n => String(n).padStart(2, '0');
        val = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
      }
    }
    fields += `<div style="display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid var(--border);font-size:11px;"><span style="color:var(--text3);">${k}</span><span style="font-weight:600;text-align:right;max-width:60%;">${val ?? '—'}</span></div>`;
  }
  container.innerHTML = `
    <div style="margin-bottom:8px;">
      <button data-action="backToL3" style="padding:3px 10px;background:var(--surface2);border:1px solid var(--border);color:var(--text);border-radius:3px;font-size:10px;cursor:pointer;font-family:var(--mono);">← 返回列表</button>
      <span style="font-size:11px;font-weight:700;margin-left:8px;">${label}：${name}</span>
    </div>
    <div style="background:var(--surface);border-radius:5px;padding:10px;font-size:11px;">${fields}</div>
  `;
}

export function backToL3() {
  sessionStorage.removeItem('_openL4');
  const container = document.getElementById('l3-container');
  if (container && container.dataset.prevHtml) {
    container.innerHTML = container.dataset.prevHtml;
    delete container.dataset.prevHtml;
  }
}

export async function _loadPwaIncidents(unitId) {
  // events.js 已 export 同名函式，此處保留 stub 以維持 main.js 介面相容
  // 實際載入由 events.js _renderZoneModal 觸發
  void unitId;
}

export let _zoneModalTab = 'events';
export function setZoneModalTab(tab) { _zoneModalTab = tab; }
export function _renderZoneModal() {}

// 監聽 events.js 派發的 L3 載入請求
document.addEventListener('map:loadL3Records', (e) => {
  const unit = e.detail?.unit;
  if (unit) loadL3Records(unit);
});
