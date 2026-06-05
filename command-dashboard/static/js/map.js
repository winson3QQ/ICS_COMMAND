/**
 * map.js — 指揮情境圖模組（C1-F CSP 模組化）
 *
 * 職責：
 *   - 載入 /api/map_config（P1-13 起，原 /static/map_config.json）
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

import { authFetch, canAccessMapObjects, canCreateEvents, canUseRealModeControls } from './ws.js';
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
  setBasemapTheme as _setBasemapTheme,
  getBasemapTheme as _getBasemapTheme,
} from './map/maplibre_core.js';
import {
  EntityLayer,
  polygonToFeature,
  polygonCentroid,
  polygonLabelToFeature,
  infraToFeature,
  routeToFeature,
  routeMidLngLat,
  routeLabelToFeature,
  zoneToNodeFeature,
  copEntityToRoute,
  copEntityToPolygon,
  copEntityToEventZone,
  copEntityToZone,
  copEntityToInfra,
  bakeTextSdf,
  bakeArrowSdf,
  bakeDiamondSdf,
  bakeSvgIcon,
  pickForeground,
} from './map/entity_layer.js';
import { NAPSG_GLYPH_SVG, hasNapsgGlyph } from './map/napsg_glyphs.js';
import { DrawPreview } from './map/draw_tools.js';
import { LabelMarkerManager } from './map/label_markers.js';
import { EventPopup } from './map/event_popup.js';
import { EventDragManager } from './map/event_drag.js';
// P1-10b 步驟 10：座標工具（UTM / MGRS / WGS84 互轉）+ MGRS grid 渲染統一抽到
// coord_tools.js。本檔保留舊命名作為 import alias，caller 無需改。
import {
  latlngToMgrs as _latlngToMGRS,
  mgrsToLatLng as _mgrsToLatLng,
  latlngToUtm as _latlngToUtm,
  utmToLatLng as _utmToLatLng,
  parseWgs84 as _parseWgs84,
  MgrsGrid,
} from './map/coord_tools.js';
// P1-17（issue #88）永久設施公開資料底圖層 — 獨立 facilities 層，**不碰既有層**。
import {
  FACILITY_TYPES,
  FacilitiesLayer,
  facilityToFeature,
  bakeFacilityIcons,
} from './map/facilities_layer.js';

const API_BASE = location.origin;
const el = id => document.getElementById(id);

// HTML escape — 深度防禦（backend `_validate_map_config_strings` 已擋，這層是 belt-and-braces）
// 用於 map_config-derived 字串注入到 innerHTML / Leaflet bindTooltip(HTML) / openModal title。
// issue #24 security review：α PR 把 map_config 寫入下放給 operator → 既有 sink 變提權路徑。
function _escapeHtml(s) {
  if (s == null) return '';
  return String(s)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;')
    .replaceAll('`', '&#96;');
}

let _deps = {};
let _mapConfig = null;
// issue #29 PR-G1a：route / polygon cutover 進 cop_entities（即時同步）。map.js 不擁有
// cop_stream（生命週期在 main.js），透過 setCopStream() 注入參考；render 從它取資料、
// 編輯器改打 /api/cop/*。zone（節點）/ 事件 / infra / flow 仍走 _mapConfig（事件留 G1b）。
let _copStream = null;
// 預設站外（MapLibre 戶外地圖）+ 網格 on：演習主視圖是戶外態勢圖，開站即看到帶網格的站外圖。
let _currentMap = 'outdoor';
// _leafletMap：歷史命名，P1-10b 後實為 maplibregl.Map instance（透過 maplibre_core 取得）。
// 為避免大爆炸 rename，過渡期保留變數名；mapInitialized 旗標更可靠。
let _leafletMap = null;
let _leafletMarkers = [];
// P1-10b 步驟 6/7：原為 Leaflet L.layerGroup，現改持 EntityLayer instance。
let _polygonLayer = null;   // EntityLayer (Polygon fill+stroke)
let _infraLayer = null;     // EntityLayer (Point circle)
let _routeLayer = null;     // EntityLayer (LineString)
let _zoneLayer = null;      // EntityLayer (Point circle) — step 7 階段 1
let _facilitiesLayer = null;   // P1-17：永久設施唯讀基準層（獨立 facilities source）
let _facilitiesData = null;    // /api/facilities 快取（lazy：首次開圖層才抓）
let _facilitiesPopup = null;   // P1-17：hover tooltip（maplibregl.Popup）
let _facilitiesHoverWired = false;
let _drawPreview = null;       // DrawPreview — step 8（polygon / route 繪製預覽）
let _polyLabelMgr = null;      // LabelMarkerManager (polygons)
let _routeLabelMgr = null;     // LabelMarkerManager (routes)
let _eventPopup = null;        // EventPopup — step 9（長按 → 兩階段事件選單）
let _eventDragMgr = null;      // EventDragManager — 補 step 7 symbol layer 化後事件
                               //   zone 失去的拖曳行為；只服務事件 zone（節點不在 scope）
let _entityLayersInstalled = false;
let _mgrsGrid = null;        // MgrsGrid instance — step 10 port 到 MapLibre symbol/line layer
let _coordPin = null;            // 雙擊放置的藍色十字 marker
let _polyDrawState = null;       // { latlngs, markers, previewPoly }
let _routeDrawState = null;      // { latlngs, markers, previewLine }
let _placeState = null;          // P1-16：{ kind:'zone'|'infra', type } —— on-demand 放置模式（節點/設施共用；單一 state 根除互斥殘留）
let _pendingPolyLatlngs = null;  // _openPolyForm → _savePolygon 暫存
let _pendingRouteLatlngs = null; // _openRouteForm → _saveRoute 暫存
let _pinEditMode = false;
let _coordDisplayMode = 'mgrs';  // 'mgrs' | 'wgs84'
// MGRS 格線開關跨 refresh 保留（issue #24 step 1）：用 sessionStorage 持久化，
// 與既有 _mapView / _currentMap 等狀態的 storage 慣例一致。
// 預設 on：沒按過 → 顯示網格；使用者明確關（存 '0'）才隱藏。跨 refresh 持久化。
let _mgrsGridVisible = sessionStorage.getItem('_mgrsGridVisible') !== '0';
// PR-G1b：events 從 zones 拆出獨立可見性 —— 事件圖釘與永久節點同走 _zoneLayer，但分別
// 由 _layerVis.zones（節點）/ _layerVis.events（事件）控制，feature-level 過濾（取消勾「節點」
// 不再連帶把事件藏掉）。
// P1-17：facilities（永久設施基準層）預設**關**——唯讀參考層，需要才從面板開，避免雜訊。
const _layerVis = { zones: true, events: true, polygons: true, infra: true, routes: true, facilities: false, mgrs: _mgrsGridVisible };

const _HSINCHU_CENTER = [24.8283, 121.0149];
const _HSINCHU_ZOOM = 15;

const _PERM_NODES = [
  { id: 'node_shelter', label: '收容組', node_type: 'shelter', icon: 'pin' },
  { id: 'node_medical', label: '醫療組', node_type: 'medical', icon: 'pin' },
  { id: 'node_command', label: '指揮部', node_type: 'command', icon: 'pin' },
  { id: 'node_forward', label: '前進組', node_type: 'forward', icon: 'pin' },
  { id: 'node_security', label: '安全組', node_type: 'security', icon: 'pin' },
];

// 內建 fallback（runtime SoT = /api/event_taxonomy，由 applyEventTaxonomy 覆蓋）。
// **必含 abbr** —— marker 字 + SDF bake 都讀它；缺 abbr 會讓事件菱形無字（review #70 HIGH）。
const _EVENT_TYPES = {
  explosive: { label: '疑似爆裂物', group: 'security', severity: 'critical', abbr: '爆' },
  drone: { label: '無人機威脅', group: 'security', severity: 'critical', abbr: '機' },
  violent: { label: '暴力事件', group: 'security', severity: 'critical', abbr: '暴' },
  unknown_person: { label: '不明人士', group: 'security', severity: 'warning', abbr: '人' },
  perimeter: { label: '管制區異常', group: 'security', severity: 'warning', abbr: '域' },
  crowd: { label: '秩序問題', group: 'security', severity: 'warning', abbr: '眾' },
  rescue: { label: '受困救援', group: 'rescue', severity: 'warning', abbr: '救' },
  qrf: { label: 'QRF 出動', group: 'rescue', severity: 'warning', abbr: 'QR' },
  mci: { label: '大量傷亡', group: 'medical', severity: 'critical', abbr: 'MCI' },
  emergency: { label: '緊急病症', group: 'medical', severity: 'critical', abbr: '急' },
  infectious: { label: '傳染疑慮', group: 'medical', severity: 'warning', abbr: '疫' },
  capacity: { label: '量能超載', group: 'care', severity: 'warning', abbr: '滿' },
  isolation: { label: '隔離事件', group: 'care', severity: 'warning', abbr: '隔' },
  person_need: { label: '人員狀況', group: 'care', severity: 'info', abbr: '護' },
  comm_fail: { label: '通訊異常', group: 'infra', severity: 'warning', abbr: '訊' },
  facility: { label: '設施異常', group: 'infra', severity: 'info', abbr: '設' },
  equipment: { label: '設備故障', group: 'infra', severity: 'info', abbr: '器' },
  evacuation: { label: '撤離', group: 'ops', severity: 'warning', abbr: '疏' },
  resource: { label: '資源調度', group: 'ops', severity: 'info', abbr: '物' },
  situation: { label: '現場變化', group: 'ops', severity: 'info', abbr: '況' },
  hazard: { label: '危害回報', group: 'ops', severity: 'info', abbr: '危' },
  other: { label: '其他', group: 'ops', severity: 'info', abbr: '他' },
};

const _EVENT_GROUPS = {
  security: '安全威脅',
  rescue: '搜救行動',
  medical: '醫療緊急',
  care: '收容照護',
  infra: '基礎設施',
  ops: '行動管理',
};

// P1-10d 地基（#60/#66）：上面的 _EVENT_TYPES / _EVENT_GROUPS 為**內建 fallback**；
// runtime SoT = /api/event_taxonomy。map.js 受 import boundary 限制不能 import events.js，
// 故由 main.js 載入後呼叫本函式套用（就地 mutate 保 ref；EventPopup 持有的 ref 同步）。
export function applyEventTaxonomy(tax) {
  if (!tax || !Array.isArray(tax.events) || !Array.isArray(tax.groups)) return false;
  const unsafe = (k) => k === '__proto__' || k === 'constructor' || k === 'prototype';
  for (const k of Object.keys(_EVENT_TYPES)) delete _EVENT_TYPES[k];
  for (const ev of tax.events) {
    if (!ev || !ev.key || unsafe(ev.key)) continue;  // 防原型污染（review #69）
    const { key, ...rest } = ev;
    _EVENT_TYPES[key] = rest;
  }
  for (const k of Object.keys(_EVENT_GROUPS)) delete _EVENT_GROUPS[k];
  for (const g of tax.groups) {
    if (g && g.key && !unsafe(g.key)) _EVENT_GROUPS[g.key] = g.label;
  }
  // taxonomy 變更後新 abbr 需 bake（review #70 HIGH：否則 marker 無字）。idempotent；
  // map 未就緒則 defer 到 load。bake 在 marker render 前完成（onEnterDashboard 序 + reloadMapConfig）。
  const _m = _getMap();
  if (_m) { if (_m.isStyleLoaded()) _bakeAbbrs(_m); else _m.once('load', () => _bakeAbbrs(_m)); }
  return true;
}

// P1-10d：bake 節點 abbr + group abbr(fallback) + 所有事件型別 abbr（taxonomy 動態）。
// 讓事件 marker 顯示各自型別字（爆/機/QR/MCI…）。可重複呼叫（bakeTextSdf 內 hasImage 去重），
// 故 taxonomy 變更（#66 admin 編輯）後再呼叫即補 bake 新字。多字元由 bakeTextSdf 自動縮放。
function _bakeAbbrs(map) {
  if (!map) return;
  const set = new Set([
    ...Object.values(_NODE_ABBR),
    ...Object.values(_NAPSG_GROUP_ABBR),
    ...Object.values(_EVENT_TYPES).map((t) => t && t.abbr).filter(Boolean),
  ]);
  bakeTextSdf(map, 'napsg-abbr-', [...set]);
}

// P1-10d 正式 icon：bake vendored NAPSG 象形 glyph（非 SDF 白圖，見 napsg_glyphs.js）。
// SVG raster 非同步 → 全部 bake 完成後重繪一次，讓 _renderZones 重算 fg（象形取代 abbr）。
// glyph 為固定 vendored 集（非 taxonomy 動態），故只需 layer 建立時 bake 一次。
function _bakeGlyphs(map) {
  if (!map) return;
  const tasks = [];
  for (const key of Object.keys(NAPSG_GLYPH_SVG)) {
    if (key === '__proto__' || key === 'constructor' || key === 'prototype') continue;
    tasks.push(bakeSvgIcon(map, 'napsg-glyph-' + key, NAPSG_GLYPH_SVG[key]));
  }
  Promise.all(tasks).then((res) => { if (res.some(Boolean)) refreshLeafletMarkers(); });
}

// P1-16 視覺收尾：bake on-demand 節點的白色象形 icon（shelter 屋 / medical 十字）。
// 仿 _bakeGlyphs：SVG raster 為非同步 → 全部 bake 完成後重繪一次（讓 zones-node-icon 層拿到 image）。
// 固定兩個（非 taxonomy 動態），layer 建立時 bake 一次即可。
function _bakeZoneIcons(map) {
  if (!map) return;
  const tasks = [];
  for (const [type, svg] of Object.entries(ZONE_ICON_SVG)) {
    if (type === '__proto__' || type === 'constructor' || type === 'prototype') continue;
    tasks.push(bakeSvgIcon(map, 'zone-ico-' + type, svg));
  }
  Promise.all(tasks).then((res) => { if (res.some(Boolean)) refreshLeafletMarkers(); });
}

const _NAPSG_GROUP_ABBR = { security: '安', rescue: '救', medical: '醫', care: '護', infra: '設', ops: '行' };
const _NODE_ABBR = { shelter: '收', medical: '醫', forward: '前', security: '安', command: '指' };

// ── 顏色 token 橋接（POLICY hex doctrine：JS 不散寫遊離 hex，統一對齊 ds-tokens.css 調色盤）──
// MapLibre paint 是 WebGL/canvas，不吃 CSS var() → 載入時用 getComputedStyle 解析成具體 hex。
// fallback = 該 token 的標準值，供無 DOM（vitest）/ CSS 未載時兜底，與 token 同值故零分歧。
// 快取：palette token（--red/--green/--accent/--severity-*/--mil-*）為 theme-invariant
// （POLICY：affiliation/severity 色日夜恆定，#95），故首解即可快取。
const _cssVarCache = {};
function cssVar(name, fallback) {
  if (name in _cssVarCache) return _cssVarCache[name];
  let v = fallback;
  if (typeof window !== 'undefined' && window.document?.documentElement) {
    const got = window.getComputedStyle(window.document.documentElement).getPropertyValue(name).trim();
    if (got) v = got;
  }
  _cssVarCache[name] = v;
  return v;
}

// 節點色 → 標準調色盤（#110/§8 對齊：shelter/medical 由遊離 hex 收斂到 --orange/--red）。
const _NODE_COLORS = {
  shelter: cssVar('--orange', '#d29922'),
  medical: cssVar('--red', '#f85149'),
  forward: cssVar('--accent', '#58a6ff'),
  security: cssVar('--yellow', '#e3b341'),
  command: cssVar('--text-secondary', '#8b949e'),
};
// P1-16 視覺收尾：on-demand 節點（kind='zone'）的白色象形 icon（疊在彩色圓上）。
// shelter＝屋頂（沿用 facilities_layer.js 的「避難收容處所」屋頂 path，與公共設施層一致）；
// medical＝白十字（疊紅圓上＝紅十字醫療標誌）。command/forward/security 不入此表，維持 abbr（指/前/安）。
// viewBox 0 0 24 24、fill #fff，bake 後 id = 'zone-ico-<node_type>'。
const ZONE_ICON_SVG = {
  shelter: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="#fff" d="M12 4 4 11h2v9h5v-5h2v5h5v-9h2z"/></svg>',
  medical: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="#fff" d="M10 4h4v6h6v4h-6v6h-4v-6H4v-4h6z"/></svg>',
};
// P1-10d：severity 色 = NAPSG Incident Symbology 標準（critical Red / warning Orange / info Blue）。
// 讀 ds-tokens --severity-*（值即 NAPSG hex，零視覺變化）。見 docs/design/event-symbology-mapping.md。
const _SEV_COLORS = {
  critical: cssVar('--severity-critical', '#FF181E'),
  warning: cssVar('--severity-warning', '#FF8918'),
  info: cssVar('--severity-info', '#237ACF'),
};
const _RAG_COLORS = { ok: cssVar('--green', '#3fb950'), warn: cssVar('--yellow', '#e3b341'), crit: cssVar('--red', '#f85149') };

// 線/面/設施色 → 標準調色盤（#110/§8：遊離 hex control/danger/hospital/emergency→--red、
// primary/assembly→--green、fire/shelter-node→--orange、ops/police→--accent、utility→--text-secondary）。
const POLY_TYPES = {
  control:    { label: '管制區', color: cssVar('--red', '#f85149'), dash: true },
  evacuation: { label: '疏散範圍', color: cssVar('--yellow', '#e3b341'), dash: true },
  assembly:   { label: '集結點', color: cssVar('--green', '#3fb950'), dash: false },
  danger:     { label: '危險區域', color: cssVar('--red', '#f85149'), dash: false },
  ops:        { label: '作業區', color: cssVar('--accent', '#58a6ff'), dash: false },
};

const INFRA_TYPES = {
  hospital: { label: '醫院', color: cssVar('--red', '#f85149'), abbr: 'H' },
  shelter:  { label: '收容所', color: cssVar('--yellow', '#e3b341'), abbr: 'S' },
  police:   { label: '警察局', color: cssVar('--accent', '#58a6ff'), abbr: 'P' },
  fire:     { label: '消防站', color: cssVar('--orange', '#d29922'), abbr: 'F' },
  utility:  { label: '公用設施', color: cssVar('--text-secondary', '#8b949e'), abbr: 'U' },
};

const ROUTE_TYPES = {
  primary:   { label: '主要疏散路線', color: cssVar('--green', '#3fb950'), dash: false },
  secondary: { label: '次要路線', color: cssVar('--yellow', '#e3b341'), dash: true },
  emergency: { label: '緊急通道', color: cssVar('--red', '#f85149'), dash: false },
};

// issue #29 PR-G1a cutover：route / polygon 寫進 cop_entities 時帶的 CoT 相容 type
// （為 P2-04 TAK 雙向預留 —— backend type 為自由字串，TAK 真正落地時再精修）。
//   route  → b-m-r   CoT「route」（含 link 頂點的多段線）
//   polygon→ u-d-f   CoT/TAK drawing「free-form」封閉圖形
// kind 另存 attributes.kind（getEntitiesByKind 過濾用），與 type 解耦。
const ROUTE_COT_TYPE = 'b-m-r';
const POLY_COT_TYPE = 'u-d-f';
// 事件位置圖釘 cutover（PR-G1b）：暫用 a-u-G（atoms-unknown-ground，未識別陸上目標）。
// 事件嚴重度/狀態仍由 events 表掌管，CoT type 的 affiliation/dimension 細分留 P2-04 TAK。
const EVENT_COT_TYPE = 'a-u-G';

export function initMap(deps = {}) {
  _deps = deps;
  return _loadMapConfig();
}

async function _loadMapConfig() {
  // P1-13（issue #27）：改打 GET /api/map_config（取代直讀 /static/map_config.json）。
  // - 後端從 data/map_config.json 讀，不存在則 fallback static/map_config.seed.json
  // - Response 帶 Cache-Control: no-store，瀏覽器不再需要 ?t= cache-bust
  // - 走 authFetch 跟 POST 同一條 auth chain（雖然 GET 目前未掛 require_role，
  //   未來收緊也可用）
  //
  // **不要 fallback 空殼**（issue #24 code-review 衍生）：若 _mapConfig 在 fetch 失敗時
  // 被設成空殼 + user 後續觸發 saveMapConfig → 把空殼寫上 disk → 演習資料全失。改為
  // fetch 失敗時 _mapConfig=null，靠 saveMapConfig 的 null guard 護住 disk。
  try {
    const resp = await authFetch(API_BASE + '/api/map_config');
    if (!resp.ok) {
      console.warn(`[map.js] map_config 載入失敗（HTTP ${resp.status}），_mapConfig 保持 null，本地動作不會洗 disk`);
    } else {
      _mapConfig = await resp.json();
    }
  } catch (e) {
    console.warn('[map.js] map_config 載入錯誤，_mapConfig 保持 null', e);
  }
  // switchMap 不論載入成功與否都跑（map tab UI / Leaflet container 要初始化）；
  // 載入失敗時 _mapConfig=null，switchMap 內部 `_mapConfig?.maps?.[..]` optional chaining
  // 已保護，不會 NPE，但畫面上 zones / markers 就會空白 — user 可手動 reload 救回。
  switchMap(sessionStorage.getItem('_currentMap') || 'outdoor');
}

export function getMapConfig() {
  return _mapConfig;
}

// 登入後重抓 map_config：boot 時（登入前）_loadMapConfig 的 GET /api/map_config 會 401
// （無 session token）→ _mapConfig=null → 地圖空白。登入成功後由 onEnterDashboard 呼叫本
// 函式重抓，帶上 token → 200 → 正常 render。issue：每次登入要 cmd-shift-R 才出現節點/網格。
export function reloadMapConfig() {
  return _loadMapConfig();
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
    shouldSuppressInteraction: () => !!(_polyDrawState || _routeDrawState || _placeState),

    // 單擊：繪製模式時新增頂點 / 放置節點 / 放置設施
    onClick: ({ lat, lng }) => {
      if (_placeState) { _placeAt(lat, lng); return; }
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

    // moveend / zoomend → sessionStorage view save + MGRS placeholder + grid redraw
    onMoveEnd: ({ lat, lng, zoom }) => {
      sessionStorage.setItem('_mapView', JSON.stringify({ lat, lng, zoom }));
      _updateMgrsPlaceholder();
      // step 10：MGRS grid 改走 MapLibre source/layer，redraw() 內部會處理「未啟用就跳過」
      if (_mgrsGrid && _mgrsGridVisible) _mgrsGrid.redraw();
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
     <button class="map-btn" id="btn-route-draw" data-action="startRouteDraw"  title="繪製路線" style="font-size:14px;">↗</button>`
    : '';
  // CSP 合規：用 data-action 委派，main.js 全域 click handler 會接住
  tools.innerHTML =
    `<button class="map-btn" id="btn-layer-panel" data-action="toggleLayerPanel" title="圖層面板" style="font-size:14px;">☰</button>
     ${objectTools}
     <button class="map-btn" id="btn-mgrs-grid"  data-action="toggleMgrsGrid"  title="MGRS 格線" style="font-size:13px;">⊞</button>
     <button class="map-btn${_getBasemapTheme() === 'muted-day' ? ' active' : ''}" id="btn-theme-day"   data-action="setBasemapTheme" data-theme="muted-day" title="白天底圖（淺灰）" style="font-size:13px;">☀</button>
     <button class="map-btn${_getBasemapTheme() === 'dark' ? ' active' : ''}" id="btn-theme-night" data-action="setBasemapTheme" data-theme="dark"      title="夜間底圖（深）"   style="font-size:13px;">☾</button>`;
}

/** 同步日/夜分段鈕高亮（segmented，active 標目前主題；對齊站內/站外 tab 慣例）。*/
function _syncThemeButtons(theme) {
  el('btn-theme-day')?.classList.toggle('active', theme === 'muted-day');
  el('btn-theme-night')?.classList.toggle('active', theme === 'dark');
}

/**
 * 選擇 basemap 主題（segmented：☀ muted-day / ☾ dark，直接選非 toggle）。
 * 走 maplibre_core.setBasemapTheme 的「只抽換底圖層」路徑，overlay（節點/範圍/路線/
 * 事件/MGRS）完全不動。
 */
export function setBasemapTheme(theme) {
  if (theme !== 'dark' && theme !== 'muted-day') return;
  if (_getBasemapTheme() === theme) { _syncThemeButtons(theme); return; }
  _setBasemapTheme(theme).then((ok) => {
    if (!ok) return;
    _syncThemeButtons(theme);
    _mgrsGrid?.applyTheme(theme);   // grid 配色跟著底圖主題走（淺底改深色，避免淺藍糊掉）
    _applyOverlayThemeContrast(theme);  // marker 外框對比跟主題（figure-ground：白天深、夜間白）
  });
}

// 白天 overlay 對比（業界 figure-ground：底圖去飽和當 ground、overlay 自己拉對比當 figure；
// 對齊 Carto Positron / Esri Light Gray Canvas 慣例 + TAK/2525 顯示哲學）。
// marker 外框隨主題翻：夜間深底 → 白外框（跳）；muted-day 淺底 → 白外框會消失，改深外框定義邊緣。
// **只調外框對比、不改戰術語意色**（severity/affiliation 兩主題恆定 → 2525 符號日夜一致、不違 doctrine）。
// label halo 維持深色（亮/白字在兩主題都以深 halo 當外框；翻白會讓白字 label 消失）。
// setBasemapTheme 走「只抽換底圖層、overlay 不動」路徑，故 setPaintProperty 值持久。
function _applyOverlayThemeContrast(theme) {
  const map = _getMap();
  if (!map?.getLayer) return;
  const stroke = theme === 'muted-day' ? '#0d1117' : '#ffffff';  // marker 外框：白天深、夜間白
  const set = (id, prop, val) => {
    if (map.getLayer(id)) {
      try { map.setPaintProperty(id, prop, val); } catch (e) { /* layer 未就緒，忽略 */ }
    }
  };
  set('zones-base', 'circle-stroke-color', stroke);       // 節點圓外框
  set('zones-event-outline', 'icon-color', stroke);       // 事件 ◆ 菱形外框（下層墊大菱形）
  set('infra-circle', 'circle-stroke-color', stroke);     // 設施圓外框
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
    // _icon(zone) 內部產 SVG（由 _NAPSG_GROUP_ABBR 對照表查 abbr，安全）；
    // event_code / label / id 是 user-controlled → escape（issue #24 XSS hardening）
    marker.innerHTML = `<div class="zone-dot">${_icon(zone)}</div>
      <div class="zone-info"><div class="zone-label">${_escapeHtml(zone.event_code || zone.label || zone.id)}</div></div>`;
    marker.addEventListener('click', () => {
      if (!canAccessMapObjects()) return;
      if (orphanZone) _showOrphanZoneModal(zone);
      else if (zone.event_id) _deps.showEventProcessModal?.(zone);
      else (_deps.showZoneDetail || showZoneDetail)(zone);
    });
    overlay.appendChild(marker);
  }
}

/**
 * 注入 cop_stream 參考（main.js _initCopStream 建好後呼叫）。
 *
 * 訂閱 onChange → cop_entities 任何變更（本地寫 / WS 廣播 / resync）都即時重繪
 * route + polygon 兩層；同時 onChange 讓 cop_stream 進入 kind-aware 委派模式
 * （route/polygon 不自建 marker，交由本檔 EntityLayer 渲染）。
 *
 * 對所有角色都訂閱（含 observer）：canWrite 只管編輯，讀 / 即時同步人人有份。
 */
export function setCopStream(stream) {
  _copStream = stream;
  if (!stream) return;
  // onChange 在「每一筆」entity upsert/remove 都觸發；resync N 筆會連發 N 次。用 rAF
  // 合併成「每幀最多一次」重繪兩層，避免 N 次 getEntitiesByKind 全掃 + setData。
  stream.onChange(_scheduleCopRender);
  // 注入時可能 cop_stream 已有資料（先 connect 後 setCopStream）→ 補繪一次
  _scheduleCopRender();
}

let _copRenderScheduled = false;
function _scheduleCopRender() {
  if (_copRenderScheduled) return;
  _copRenderScheduled = true;
  const run = () => {
    _copRenderScheduled = false;
    _renderRoutes();
    _renderPolygons();
    _renderZones(); // PR-G1b：事件位置圖釘也在 cop_entities，即時重繪
    _renderInfra(); // P1-16 PR-2：設施（kind='infra'）cutover 進 cop_entities，即時重繪
  };
  if (typeof requestAnimationFrame !== 'undefined') requestAnimationFrame(run);
  else setTimeout(run, 0);
}

export function refreshLeafletMarkers() {
  // P1-10b 步驟 11（清死碼）：Leaflet legacy fallback path（原 line 370-470 區段）
  // 完整移除。MapLibre 必載入（main.js boot 不再 fallback）。函式名稱保留為
  // public API alias（cop.js 等舊呼叫者不需動），內部一律走 MapLibre EntityLayer。
  if (!_leafletMap || !_mapConfig) return;
  _ensureEntityLayers();
  _renderPolygons();
  _renderInfra();
  _renderRoutes();
  _renderZones();
  _renderFacilities();  // P1-17：永久設施基準層（lazy + 預設關）
}

// P1-17：渲染永久設施基準層。lazy —— 預設關，首次開圖層才抓 /api/facilities（8000+ 點，
// 不必要時不載）。資料抓回後快取，之後切換只 setVisible（淡入/淡出）。獨立於 map_config。
async function _renderFacilities() {
  if (!_facilitiesLayer) return;
  _facilitiesLayer.setVisible(_layerVis.facilities);
  if (!_layerVis.facilities) {
    _facilitiesPopup?.remove();               // 關閉時收掉 hover tooltip（防游標停點上殘留）
    return;
  }
  if (_facilitiesData !== null) return;       // 已載入：可見性已套用，不重抓
  _facilitiesData = [];                        // 佔位，避免並發重抓（成功保留陣列、失敗回 null 可重試）
  const map = _getMap();
  if (map) await bakeFacilityIcons(map);       // 白色象形 icon（idempotent；icon-image 需先存在）
  let data = null;
  try {
    const resp = await authFetch(API_BASE + '/api/facilities');
    if (resp.ok) {
      const body = await resp.json();
      data = Array.isArray(body?.facilities) ? body.facilities : [];
    }
  } catch {
    data = null;
  }
  if (data === null) {
    _facilitiesData = null;   // 失敗 → 重置，下次開圖層可重試（不卡成永久空白）
    return;
  }
  _facilitiesData = data;
  _facilitiesLayer.update(data.map((f, i) => facilityToFeature(f, i)).filter(Boolean));
}

// P1-17：hover 設施 → 顯示名稱 + 類型 tooltip。一次性 wire（handler 掛 map，layer 後建也有效）。
function _wireFacilitiesHover(map) {
  if (_facilitiesHoverWired || !map || !window.maplibregl) return;
  _facilitiesHoverWired = true;
  _facilitiesPopup = new window.maplibregl.Popup({
    closeButton: false, closeOnClick: false, offset: 12, className: 'facility-tip',
  });
  const show = (e) => {
    const f = e.features?.[0];
    if (!f) return;
    map.getCanvas().style.cursor = 'pointer';
    const p = f.properties || {};
    const typeLabel = (FACILITY_TYPES[p.ftype] || FACILITY_TYPES._default).label;
    const html = `<div style="font-size:12px;line-height:1.4;">`
      + `<b>${_escapeHtml(p.name)}</b><br>`
      + `<span style="color:var(--text3);font-size:11px;">${_escapeHtml(typeLabel)}</span></div>`;
    // anchor 在設施點座標（非游標），tooltip 穩定貼在圓上
    _facilitiesPopup.setLngLat(f.geometry.coordinates).setHTML(html).addTo(map);
  };
  map.on('mouseenter', 'facilities-circle', show);
  map.on('mousemove', 'facilities-circle', show);
  map.on('mouseleave', 'facilities-circle', () => {
    map.getCanvas().style.cursor = '';
    _facilitiesPopup?.remove();
  });
}

// ══════════════════════════════════════════════════════════════
// MGRS / WGS84 / UTM 工具：步驟 10 已抽出到 map/coord_tools.js。
// 本檔僅留 _latlngToMGRS / _mgrsToLatLng / _latlngToUtm / _utmToLatLng /
// _parseWgs84 為 import alias（見檔首），caller 不需改。
// ══════════════════════════════════════════════════════════════

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
    { key: 'events',   icon: '▲', label: '事件' },
    { key: 'polygons', icon: '▱', label: '範圍' },
    { key: 'infra',    icon: '＋', label: '設施' },
    { key: 'routes',   icon: '↗', label: '路線' },
    { key: 'facilities', icon: '⊕', label: '公共設施' },  // P1-17：永久設施基準層（唯讀）
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
  // P1-16 PR-2：on-demand 放置設施入口（限指揮層；operator/observer 不顯示，mirror 放置節點）
  if (canUseRealModeControls()) {
    html += `<div class="layer-row" data-action="openInfraForm">
      <span style="font-size:11px;color:var(--text2);">＋ 新增設施</span></div>`;
  }
  // P1-16：on-demand 放置節點入口（限指揮層；operator/observer 不顯示）
  if (canUseRealModeControls()) {
    html += `<div class="layer-row" data-action="openNodePlace">
      <span style="font-size:11px;color:var(--text2);">⊙ 放置節點</span></div>`;
  }
  panel.innerHTML = html;
}

// ══════════════════════════════════════════════════════════════
// MGRS 格線：步驟 10 已 port 到 map/coord_tools.js 的 MgrsGrid class。
// 渲染走 MapLibre GeoJSON source + line/symbol layer，redraw() 在 moveend 觸發。
// 本檔僅留 _drawMgrsGrid 作為 toggle 的 thin wrapper（向後相容 caller）。
// ══════════════════════════════════════════════════════════════

function _drawMgrsGrid() {
  if (!_mgrsGrid) return;     // _ensureEntityLayers 未跑（map 未 init），跳過
  _mgrsGrid.setVisible(_mgrsGridVisible);
}

// ══════════════════════════════════════════════════════════════
// 長按地圖 → 事件回報 popup（NAPSG 兩階段選單）
// 使用 L.DomUtil + L.DomEvent，避免 inline onclick 失效
// ══════════════════════════════════════════════════════════════

// P1-10b 步驟 9：DOM 建構與 popup lifecycle 移至 EventPopup（map/event_popup.js），
// 本檔僅保留 _openEventPopup（thin wrapper + 權限判定）+ _evPopupSubmit（資料層：
// authFetch /api/events + 寫 map_config + 觸發 poll）。
//
// EventPopup instance 在 _ensureEntityLayers 末段 lazy 建立（與 DrawPreview / LabelMarkerManager 同層）。

function _openEventPopup(lat, lng) {
  if (!canCreateEvents()) return;
  // P1-10b 步驟 11：Leaflet undefined guard 移除（MapLibre 必載入）。
  if (!_eventPopup) return;   // _ensureEntityLayers 尚未跑（style not loaded），略
  _eventPopup.open(lat, lng);
}

async function _evPopupSubmit(typeKey, ctx) {
  if (!canCreateEvents()) return;
  if (!ctx) return;
  const evDef = _EVENT_TYPES[typeKey];
  if (!evDef) return;

  const reportedBy = ctx.reporter || el('place-report-unit')?.value || 'command';
  const { lat, lng } = ctx;

  const id = 'evt_' + Date.now();
  const mgrs = _latlngToMGRS(lat, lng, 5);
  const operator = _deps.getCurrentOperator?.() || '';
  // P1-14 PR-2：session_type 退役（後端自動依 active exercise scope，建立事件不再送）。

  const evGroup = evDef.group || 'ops';  // 解撞名：事件「類別 group」≠ ICS 組織 node_type
  const roundedLat = Math.round(lat * 1000000) / 1000000;
  const roundedLng = Math.round(lng * 1000000) / 1000000;

  // 1. 建立事件記錄（events 表）—— 嚴重度/狀態/處置流程的 SoT，不變。
  let eventId = null;
  let eventCode = null;
  try {
    const resp = await authFetch(API_BASE + '/api/events', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        reported_by_unit: reportedBy,
        event_type: typeKey,
        severity: evDef.severity || 'warning',
        description: evDef.label,
        assigned_unit: evDef.defaultAssigned || null,  // #66：新事件預填 taxonomy 預設處理組
        operator_name: operator,
        location_zone_id: id,   // 記錄用 client id；consumer 主要靠 event_id 連結
        location_desc: mgrs,
      }),
    });
    if (resp.ok) {
      const data = await resp.json();
      eventId = data.id;
      eventCode = data.event_code;
    } else {
      console.warn('[map.js] 事件建立失敗', resp.status);
    }
  } catch (e) {
    console.error('[map.js] _evPopupSubmit', e);
  }
  if (!eventId) return; // 事件沒建起來就不放圖釘

  // 2. PR-G1b cutover：事件「位置圖釘」改建為 cop_entity（即時同步），取代 push 進 map_config。
  //    event_id 連回 events 表；event_group=NAPSG 事件類別（解撞名，非 ICS node_type）；label→callsign。
  if (!_copStream) { _flashMapMsg('✗ 即時同步未就緒，事件已建立但圖釘未放，請重整'); _deps.doPoll?.(); return; }
  const created = await _copStream.createEntity({
    type: EVENT_COT_TYPE,
    lat: roundedLat,
    lon: roundedLng,
    callsign: evDef.label,
    severity: evDef.severity || 'warning',
    attributes: { kind: 'event', event_id: eventId, event_code: eventCode, event_group: evGroup },
  });
  if (!created) {
    // 事件已落 DB 但圖釘沒建起來 = orphan event（與舊 rollback 行為對齊：提示重試）
    _flashMapMsg(`✗ ${eventCode || ''} 圖釘建立失敗，事件已建立但地圖未放，請重試`);
    _deps.doPoll?.();
    return;
  }
  // 放置確認（createEntity 內部 upsert + onChange 已即時重繪 zones）
  const panel = el('map-coord-panel');
  if (panel) {
    panel.style.display = 'flex';
    panel.innerHTML =
      `<span style="color:#3fb950">✓ 放置</span>&nbsp;<b>${_escapeHtml(eventCode || '')}</b>` +
      `<span style="color:#8b949e;margin-left:8px">MGRS</span>&nbsp;${_escapeHtml(mgrs)}`;
    setTimeout(() => _refreshCoordPanel(), 3000);
  }
  _deps.doPoll?.(); // 右側事件追蹤欄即時更新
}

// P1-10b 步驟 11：_napsgIcon dead code 移除（L.divIcon 工廠，原為 Leaflet legacy fallback
// 唯一 caller。MapLibre zone rendering 走 _renderZones 內 SDF + abbr text-field 直繪，
// 無需 L.divIcon。NAPSG marker 行為已 port 進 entity_layer.js 步驟 7 階段 1/2/3a。

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
  if (!eventId) return null;
  // PR-G1b：事件 zone 已 cutover 進 cop_entities，先查 cop（adapter 還原 zone shape）。
  for (const ent of (_copStream?.getEntitiesByKind('event') || [])) {
    if (ent.attributes?.event_id === eventId) return copEntityToEventZone(ent);
  }
  // 退路：map_config 殘留（節點不帶 event_id，但保險用）。
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
  // 注意：此為 fallback；live 路徑是 events.js showZoneDetail → _renderZoneModal（含刪除節點鈕）。
  // _onZoneClick 走 (_deps.showZoneDetail || showZoneDetail)，_deps 恆被注入故此分支實務不命中。
  const body = `<div style="font-size:12px;line-height:1.7;">
    <div>類型：${_escapeHtml(zone.node_type || '—')}</div>
    <div>座標：${zone.lat != null ? _coordValueHTML(zone.lat, zone.lng) : '站內相對位置'}</div>
  </div>`;
  _deps.openModal?.(zone.label || zone.id || '節點', body);
}

export async function _deleteNode(id) {
  if (!canUseRealModeControls()) return;  // 限指揮層（與放置一致）
  if (!_copStream || !id) return;
  const ok = await _copStream.deleteEntity(id);
  _deps.closeModal?.();
  if (!ok) _flashMapMsg('✗ 節點刪除失敗，請重試');
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
  // 防 silent fail：authFetch 只攔 401，403/5xx 會 silently 流過。
  // **失敗必須 throw** — 不能 silently return，否則 caller 的 await chain 跑下去會把後續
  // side effects（PATCH /api/events / alert 成功 / UI ✓ 已儲存 / orphan event push）
  // 全部執行，造成 DB / disk / in-memory 三方分歧（issue #24 code-review 5 findings）。
  let resp;
  try {
    resp = await authFetch(API_BASE + '/api/map_config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(_mapConfig),
    });
  } catch (e) {
    console.warn('[map.js] saveMapConfig 網路錯誤，map_config 未上 disk', e);
    throw e;
  }
  if (!resp.ok) {
    const msg = `saveMapConfig 失敗（HTTP ${resp.status}），本地變動未上 disk，refresh 會消失`;
    console.warn('[map.js]', msg);
    throw new Error(msg);
  }
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
    if (def.deleted) continue;  // #66：soft-delete 的型別不出現在建立事件下拉（保留供既有事件渲染）
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
    // style 未載完就 defer 建 layer；建完必須補一次 refreshLeafletMarkers，否則當下觸發 defer
    // 的那次 _render*（layer 還是 null）全 bail → 節點/grid 不出現，要手動 reload 才好。
    map.once('load', () => { _ensureEntityLayers(); refreshLeafletMarkers(); });
    return;
  }

  // P1-17：永久設施基準層 —— **先建**（draw order 最底，退到戰術 entity 之後）。
  // 自管 clustered facilities source，不碰既有層、不依賴 EntityLayer。lazy 由 _renderFacilities 抓。
  _facilitiesLayer = new FacilitiesLayer(map);
  _wireFacilitiesHover(map);   // P1-17：滑鼠移上去顯示設施名稱 tooltip

  // Polygons — fill + stroke + label（dash/solid 拆兩 layer + filter，因 MapLibre v4
  // line-dasharray 不支援 data-driven expression）
  _polygonLayer = new EntityLayer(map, 'polygons', {
    layers: [
      {
        id: 'polygons-fill', type: 'fill',
        // P1-10e：hover 時 fill opacity 微升（0.12→0.22）。case 走 feature-state，
        // 不碰 color（顏色仍是 affiliation/severity 語意，hover 只動 opacity/width）。
        paint: {
          'fill-color': ['get', 'color'],
          'fill-opacity': ['case', ['boolean', ['feature-state', 'hover'], false], 0.22, 0.12],
        },
      },
      {
        id: 'polygons-stroke-solid', type: 'line',
        filter: ['!', ['coalesce', ['get', 'dash'], false]],
        // P1-10e：hover 時 outline 加粗（2→3.5）
        paint: {
          'line-color': ['get', 'color'],
          'line-width': ['case', ['boolean', ['feature-state', 'hover'], false], 3.5, 2],
        },
      },
      {
        id: 'polygons-stroke-dash', type: 'line',
        filter: ['==', ['coalesce', ['get', 'dash'], false], true],
        paint: {
          'line-color': ['get', 'color'],
          'line-width': ['case', ['boolean', ['feature-state', 'hover'], false], 3.5, 2],
          'line-dasharray': [2, 1.5],
        },
      },
      {
        // P1-10e：selected 虛線外框（疊在 stroke 之上）。只渲染 feature-state.selected
        // 的那一個（其餘 line-opacity=0）；sel_pulse（RAF 餵）做 opacity 呼吸動畫——
        // 動 opacity 不重算 dash tessellation（比動 width/dasharray 便宜）。色仍走 color，
        // 不搶語意。
        id: 'polygons-selected', type: 'line',
        paint: {
          'line-color': ['get', 'color'],
          'line-width': 4,
          'line-dasharray': [2, 2],
          'line-opacity': ['case',
            ['boolean', ['feature-state', 'selected'], false],
            ['coalesce', ['feature-state', 'sel_pulse'], 1], 0],
        },
      },
      {
        // Polygon label：用 Point geometry（caller 算 centroid 加進 source）。
        // 解 MapLibre 對 Polygon symbol-placement:'point' 跨 tile 算多次 centroid
        // → zoom 拉大 polygon 跨多 tile → 多個 label 的 bug。
        // filter geometry-type=Point 確保只渲染 caller 加的 centroid Point feature，
        // 同 source 的 Polygon feature 不被本 layer render。
        // text-opacity：拖曳期間 (feature-state.dragging=true) 隱藏，讓 HTML drag handle
        // 的 ghost label 接手視覺 — LabelMarkerManager 控制（step 8 hybrid B）。
        id: 'polygons-label', type: 'symbol',
        // ⚠️ 不用 geometry-type filter — MapLibre 4.7.1 render pipeline 對該
        // expression 有 bug（symbol layer cull 掉所有 features），改用
        // polygonLabelToFeature 注入的 properties.kind='label' 分流。
        filter: ['all',
          ['has', 'label'],
          ['==', ['get', 'kind'], 'label'],
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
          'text-opacity': [
            'case', ['boolean', ['feature-state', 'dragging'], false], 0, 1,
          ],
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
          'circle-stroke-width': 1.5, 'circle-stroke-color': '#ffffff', 'circle-opacity': 0.92,
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
  _bakeAbbrs(map);
  _bakeGlyphs(map);  // P1-10d 正式 icon：NAPSG 象形（非同步 SVG raster，完成後自重繪）
  _bakeZoneIcons(map);  // P1-16 視覺收尾：節點白色象形（shelter 屋 / medical 十字，非同步 bake 完自重繪）
  bakeArrowSdf(map, 'route-arrow');
  bakeDiamondSdf(map, 'zone-diamond');  // P1-10d：事件 ◆ hazard 形狀

  // Routes — line（solid/dash 拆兩 layer）+ arrow symbol-on-line（step 7 階段 3a）
  _routeLayer = new EntityLayer(map, 'routes', {
    layers: [
      {
        id: 'routes-line-solid', type: 'line',
        filter: ['!', ['coalesce', ['get', 'dash'], false]],
        // P1-10e：hover 時加粗（2→3）。route 線細（base 2），讓箭頭相對更顯眼。
        paint: {
          'line-color': ['get', 'color'],
          'line-width': ['case', ['boolean', ['feature-state', 'hover'], false], 3, 2],
          'line-opacity': 0.9,
        },
      },
      {
        id: 'routes-line-dash', type: 'line',
        filter: ['==', ['coalesce', ['get', 'dash'], false], true],
        paint: {
          'line-color': ['get', 'color'],
          'line-width': ['case', ['boolean', ['feature-state', 'hover'], false], 4.5, 3],
          'line-opacity': 0.9,
          'line-dasharray': [2, 1.5],
        },
      },
      {
        // P1-10e：selected 虛線外框（同 polygons-selected 機制）。filter LineString
        // 避開 routes source 內的 Point label feature。
        id: 'routes-selected', type: 'line',
        filter: ['==', ['geometry-type'], 'LineString'],
        paint: {
          'line-color': ['get', 'color'],
          'line-width': 4,
          'line-dasharray': [2, 2],
          'line-opacity': ['case',
            ['boolean', ['feature-state', 'selected'], false],
            ['coalesce', ['feature-state', 'sel_pulse'], 1], 0],
        },
      },
      {
        // 顯式 LineString filter — routes source 在 step 8 起含 Point label feature，
        // 防 line-placement 套到 Point 干擾整 source 的 symbol rendering（user 撞到 label 消失）
        id: 'routes-arrow', type: 'symbol',
        filter: ['==', ['geometry-type'], 'LineString'],
        layout: {
          'symbol-placement': 'line',
          'symbol-spacing': 90,
          'icon-image': 'route-arrow',
          'icon-size': 1.4,
          'icon-allow-overlap': true,
          'icon-ignore-placement': true,
          'icon-rotation-alignment': 'map',
        },
        paint: { 'icon-color': ['get', 'color'], 'icon-opacity': 0.9 },
      },
      {
        // Route label：用 Point geometry（caller 算 midpoint/label_anchor 加進 source）。
        // 解 symbol-placement:'line-center' 不認 label_anchor 的問題 — drag 後 anchor
        // 寫進 route.label_anchor，re-render emit 新 Point feature，label 跟到新位置。
        // filter geometry-type=Point 確保只渲染 caller 加的 Point feature。
        // text-opacity 拖曳期間隱藏（feature-state.dragging），讓 HTML handle ghost 接手。
        // text-allow-overlap / text-ignore-placement: true — 避免 routes-arrow chevron
        // 在線中點附近時把 label 推開（user 撞到的「滑鼠落差」bug）。
        id: 'routes-label', type: 'symbol',
        // 同 polygons-label — 改用 properties.kind='label' filter（geometry-type
        // expression 在 MapLibre 4.7.1 render pipeline 不穩定）。
        filter: ['all',
          ['has', 'label'],
          ['==', ['get', 'kind'], 'label'],
        ],
        layout: {
          'text-field': ['get', 'label'],
          'text-font': ['Noto Sans Regular'],
          'text-size': 11,
          'symbol-placement': 'point',
          'text-allow-overlap': true,
          'text-ignore-placement': true,
        },
        paint: {
          'text-color': ['get', 'color'],
          'text-halo-color': '#0d1117',
          'text-halo-width': 2,
          'text-opacity': [
            'case', ['boolean', ['feature-state', 'dragging'], false], 0, 1,
          ],
        },
      },
    ],
  });

  // PR-H：flows EntityLayer 已移除（流向功能退役 —— 與 route 重疊，且 G1b 後連不到事件）。

  // Zones — step 7 階段 1：circle marker（NAPSG SVG SDF 留階段 2；
  // 用三層 stack 預留 hover/selected/halo 接點，目前 selected/halo 給 0 opacity）
  // MapLibre case 條件需顯式 boolean expression（不接受 ['get','xxx'] 直接當 truthy），
  // 用 ['==', ..., true] 確保通過 style 驗證。
  _zoneLayer = new EntityLayer(map, 'zones', {
    layers: [
      // P1-10d：critical 事件脈動光暈（獨立層，全域 RAF 動 radius/opacity；
      // 與既有 zones-halo 的 highlighted/feature-state 邏輯不衝突）。draw 最底。
      {
        id: 'zones-crit-pulse', type: 'circle',
        filter: ['all',
          ['==', ['get', 'severity'], 'critical'],
          ['==', ['coalesce', ['get', 'is_event'], false], true],
        ],
        paint: {
          'circle-color': ['get', 'color'],
          'circle-radius': 18,       // RAF 每幀覆寫
          'circle-opacity': 0,       // 初始 0；_updateCritPulse 有 critical 時才由 RAF 拉起
          'circle-blur': 0.5,
          'circle-opacity-transition': { duration: 0 },
        },
      },
      // halo（給 critical entity / right-panel 長按 highlight 用）
      // 三狀態優先序：
      //   dimmed=true   → halo 隱（focus 模式下其他 zone 整個暗，halo 跟著消）
      //   highlighted=true → 綠色泛光（右側事件欄長按 target 的視覺回饋；
      //                      呼應舊 Leaflet zone-marker 周圍綠光效果）
      //   severity=critical → 紅色淡 halo（baseline，永遠開）
      //   其他 → 隱
      {
        id: 'zones-halo', type: 'circle',
        paint: {
          // highlighted radius / opacity 從 feature-state 拿（RAF pulse loop 每幀更新），
          // 沒 pulse 值（剛 highlight 還沒第一幀）時 coalesce 預設值。
          'circle-radius': [
            'case',
            ['boolean', ['feature-state', 'highlighted'], false],
            ['coalesce', ['feature-state', 'pulse_radius'], 28],
            22,
          ],
          'circle-color': [
            'case',
            // 綠色加強（原 #3fb950 → #4ade80 更鮮亮）
            ['boolean', ['feature-state', 'highlighted'], false], '#4ade80',
            ['get', 'color'],
          ],
          'circle-opacity': [
            'case',
            ['boolean', ['feature-state', 'dimmed'], false], 0,
            ['boolean', ['feature-state', 'highlighted'], false],
            ['coalesce', ['feature-state', 'pulse_opacity'], 0.6],
            ['==', ['get', 'severity'], 'critical'], 0.18,
            0,
          ],
          'circle-blur': 0.6,
        },
      },
      // base circle
      // dimmed=true：opacity 0.15（與舊 Leaflet .zone-marker.dimmed CSS 對齊），
      // **stroke 也要 0.15** — 否則白圈仍然顯眼（issue #24 dogfood UX 反饋）。
      {
        id: 'zones-base', type: 'circle',
        // P1-10d：只有節點用圓；事件（hazard）改走 ◆ diamond（zones-event 層）。
        filter: ['!=', ['coalesce', ['get', 'is_event'], false], true],
        paint: {
          'circle-radius': [
            'case', ['==', ['coalesce', ['get', 'is_event'], false], true], 13, 9,
          ],
          'circle-color': ['get', 'color'],
          'circle-stroke-color': '#ffffff',
          'circle-stroke-width': 1.5,   // 與設施(infra)外框一致（以設施為準）
          'circle-stroke-opacity': [
            'case',
            ['boolean', ['feature-state', 'dimmed'], false], 0.15,
            1,
          ],
          'circle-opacity': [
            'case',
            ['boolean', ['feature-state', 'dimmed'], false], 0.15,
            ['==', ['coalesce', ['get', 'stale'], false], true], 0.55,
            0.92,
          ],
        },
      },
      // 事件 ◆ 外框：墊一個稍大的菱形在 severity 菱形「下面」（取代失效的 SDF icon-halo —
      // bakeDiamondSdf 是實心 alpha、非真 distance-field，halo 無法加寬）。icon-color = 主題外框色
      // （白天深 / 夜間白，由 _applyOverlayThemeContrast 翻）。外露的 size 差 = 框粗，目視對齊圓的 1.5 stroke。
      {
        id: 'zones-event-outline', type: 'symbol',
        filter: ['==', ['coalesce', ['get', 'is_event'], false], true],
        layout: {
          'icon-image': 'zone-diamond',
          'icon-size': 1.32,   // > zones-event 1.1：外露一圈即外框（差越大框越粗）
          'icon-allow-overlap': true,
          'icon-ignore-placement': true,
          'symbol-sort-key': ['case', ['==', ['get', 'severity'], 'critical'], 0, 1],
        },
        paint: {
          'icon-color': '#ffffff',   // 由 _applyOverlayThemeContrast 翻主題（白天深 / 夜間白）
          'icon-opacity': [
            'case',
            ['boolean', ['feature-state', 'dimmed'], false], 0.15,
            ['==', ['coalesce', ['get', 'stale'], false], true], 0.55,
            0.95,
          ],
        },
      },
      // P1-10d：事件 ◆ diamond（NAPSG hazard 形狀）。icon-color = severity 色。只 render is_event=true。
      {
        id: 'zones-event', type: 'symbol',
        filter: ['==', ['coalesce', ['get', 'is_event'], false], true],
        layout: {
          'icon-image': 'zone-diamond',
          'icon-size': 1.1,   // 事件(hazard)為焦點：比節點圓更醒目（外框由下層 zones-event-outline 提供）
          'icon-allow-overlap': true,
          'icon-ignore-placement': true,
          'symbol-sort-key': [
            'case', ['==', ['get', 'severity'], 'critical'], 0, 1,  // critical 優先放置
          ],
        },
        paint: {
          'icon-color': ['get', 'color'],
          // 外框改由下層 zones-event-outline 墊大菱形提供（icon-halo 對實心 alpha SDF 無法加寬，已棄用）。
          'icon-opacity': [
            'case',
            ['boolean', ['feature-state', 'dimmed'], false], 0.15,
            ['==', ['coalesce', ['get', 'stale'], false], true], 0.55,
            0.95,
          ],
        },
      },
      // P1-10b 步驟 7 階段 2：abbr 字（白色 SDF 字浮在 circle 上）
      // icon-image 動態組 'napsg-abbr-' + properties.abbr；caller 須確認 abbr 已 bake。
      // SDF + icon-color 白 → 任何 base color 上都可見。
      {
        id: 'zones-abbr', type: 'symbol',
        // P1-16 視覺收尾：shelter/medical 的「節點」改用白色象形（zones-node-icon 層），
        // 故此處 abbr 字要抑制，否則象形跟字疊一起。只排除「節點」（is_event!=true）的
        // shelter/medical；事件（is_event=true，event abbr/glyph 走自己的 fg）不受影響，
        // command/forward/security 節點（仍顯 指/前/安）也不受影響。
        filter: ['!',
          ['all',
            ['!=', ['coalesce', ['get', 'is_event'], false], true],
            ['in', ['get', 'node_type'], ['literal', ['shelter', 'medical']]],
          ],
        ],
        layout: {
          // P1-10d 正式 icon：fg = NAPSG 象形（'napsg-glyph-*'）或 abbr（'napsg-abbr-*'）。
          // coalesce 防禦（fg 理論上恆有值）。glyph 框內加大：影像 48px（abbr 32px），
          // icon-size 0.72 → glyph 明顯大於 abbr（review #75：0.62 太小、形同 abbr，未兌現「加大」）。
          'icon-image': ['coalesce', ['get', 'fg'], ['concat', 'napsg-abbr-', ['get', 'abbr']]],
          'icon-size': ['case', ['==', ['coalesce', ['get', 'fg_glyph'], false], true], 0.72, 0.9],
          'icon-allow-overlap': true,
          'icon-ignore-placement': true,
        },
        paint: {
          'icon-color': '#ffffff',
          'icon-opacity': [
            'case', ['boolean', ['feature-state', 'dimmed'], false], 0.15, 0.95,
          ],
        },
      },
      // P1-16 視覺收尾：on-demand 節點的白色象形 icon（shelter 屋 / medical 十字），疊在
      // zones-base 圓之上、取代 zones-abbr 字。icon-image 動態組 'zone-ico-' + node_type
      // （只 bake 了 shelter/medical 兩張，見 _bakeZoneIcons）。filter：只對「節點」
      // （is_event!=true）且 node_type ∈ {shelter,medical} 顯示——事件不顯（事件走 ◆ + 自己的 fg）。
      // icon-color 不設（image 本身已是白色 fill，非 SDF）；圓的 RAG 著色保留在 zones-base。
      {
        id: 'zones-node-icon', type: 'symbol',
        filter: ['all',
          ['!=', ['coalesce', ['get', 'is_event'], false], true],
          ['in', ['get', 'node_type'], ['literal', ['shelter', 'medical']]],
        ],
        layout: {
          'icon-image': ['concat', 'zone-ico-', ['get', 'node_type']],
          'icon-size': 0.55,
          'icon-allow-overlap': true,
          'icon-ignore-placement': true,
        },
        paint: {
          'icon-opacity': [
            'case', ['boolean', ['feature-state', 'dimmed'], false], 0.15, 0.95,
          ],
        },
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
          'text-opacity': [
            'case', ['boolean', ['feature-state', 'dimmed'], false], 0.15, 1,
          ],
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
  map.on('click', 'zones-base', (e) => _onZoneClick(e));
  map.on('click', 'zones-abbr', (e) => _onZoneClick(e));  // abbr 字也可點，跟 base 同 handler

  // P1-10e：點到空白（游標下無 polygon/route）→ 取消選中。layer-specific click 先觸發
  // （已 _setSelection），此 general handler 後觸發；queryRenderedFeatures 有命中就不清。
  map.on('click', (e) => {
    const hit = map.queryRenderedFeatures(e.point, {
      layers: ['polygons-fill', 'routes-line-solid', 'routes-line-dash'],
    });
    if (!hit.length) _clearSelection();
  });

  // Cursor 變 pointer 提示可點 — 用 counter 追進入多少 clickable layer，
  // 為 0 時還原 MapLibre 預設 'grab'（不能 reset 成 '' 否則拖曳 cursor 卡住）。
  let _hoverCount = 0;
  const _setHoverCursor = () => { map.getCanvas().style.cursor = 'pointer'; };
  const _resetHoverCursor = () => { map.getCanvas().style.cursor = ''; };  // '' = 回 MapLibre 自己管
  [
    'polygons-fill', 'infra-circle',
    'routes-line-solid', 'routes-line-dash',
    'zones-base', 'zones-abbr',
  ].forEach((id) => {
    map.on('mouseenter', id, () => { _hoverCount += 1; _setHoverCursor(); });
    map.on('mouseleave', id, () => {
      _hoverCount = Math.max(0, _hoverCount - 1);
      if (_hoverCount === 0) _resetHoverCursor();
    });
  });

  // P1-10e：polygon / route hover 視覺回饋（outline 加粗 + fill opacity 微升）。
  // paint case expression 已在 layer spec 備好（feature-state.hover）；此處只負責
  // 在 mousemove 把 hover state 設到游標下 feature、離開時清掉。promoteId='id' →
  // e.features[0].id 即 properties.id。hover 與既有 dimmed/highlighted/dragging
  // 是各自獨立的 state key，不互相覆蓋。
  let _hoveredFeat = null;  // { source, id }
  const _clearHover = () => {
    if (_hoveredFeat) {
      map.removeFeatureState({ source: _hoveredFeat.source, id: _hoveredFeat.id }, 'hover');
      _hoveredFeat = null;
    }
  };
  const _hoverOn = (source, fid) => {
    if (fid == null) return;
    if (_hoveredFeat && _hoveredFeat.source === source && _hoveredFeat.id === fid) return;
    _clearHover();
    map.setFeatureState({ source, id: fid }, { hover: true });
    _hoveredFeat = { source, id: fid };
  };
  [
    ['polygons-fill', 'polygons'],
    ['routes-line-solid', 'routes'],
    ['routes-line-dash', 'routes'],
  ].forEach(([layerId, source]) => {
    map.on('mousemove', layerId, (e) => {
      if (e.features && e.features.length) _hoverOn(source, e.features[0].id);
    });
    map.on('mouseleave', layerId, _clearHover);
  });

  // Step 8：DrawPreview（polygon/route 繪製預覽）— 共用 'draw-vertices' + 'draw-shape'
  // 兩個 source，lazy install 直到 _startPolyDraw / _startRouteDraw 第一次呼叫。
  _drawPreview = new DrawPreview(map);

  // Step 8：LabelMarkerManager — 透明 HTML drag handle 蓋在 SDF symbol layer text 上方，
  // 提供 polygon/route label 拖曳重定位能力（取代 Leaflet 原 L.marker draggable）。
  _polyLabelMgr = new LabelMarkerManager(map, window.maplibregl, 'polygons');
  _routeLabelMgr = new LabelMarkerManager(map, window.maplibregl, 'routes');

  // Step 9 後新增：EventDragManager — 補 step 7 zone symbol layer 化後事件 zone
  // 失去的拖曳行為。沿用 step 8 hybrid B（透明 HTML handle 蓋 SDF circle）；
  // 只服務事件 zone，handle click 轉派 _onZoneClick 開事件 modal。
  _eventDragMgr = new EventDragManager(map, window.maplibregl);

  // Step 10：MGRS grid — 透過 MgrsGrid 抽象走 MapLibre GeoJSON source + line/symbol
  // layer。Toggle 走 setVisible()，redraw() 在 moveend 自動 trigger。
  _mgrsGrid = new MgrsGrid(map);
  _mgrsGrid.applyTheme(_getBasemapTheme());   // 依目前底圖主題定 grid 配色（淺底用深色，對比）
  _applyOverlayThemeContrast(_getBasemapTheme());  // 依目前主題定 marker 外框對比（figure-ground）
  // 跨 refresh 持久化（issue #24 step 1）：sessionStorage 載到的 _mgrsGridVisible
  // 若是 true，map style ready 後立刻 restore 視覺 — 用 _drawMgrsGrid 統一路徑
  // 同步 button .active class 給 toolbar 顯示對的狀態。
  if (_mgrsGridVisible) {
    _layerVis.mgrs = true;
    document.getElementById('btn-mgrs-grid')?.classList.add('active');
    _drawMgrsGrid();
  }

  // Step 9：EventPopup — 長按事件回報 popup（取代 Leaflet 的 L.popup + L.DomUtil/DomEvent）。
  // 兩階段選單：group 按鈕 → type 按鈕；submit 走 _evPopupSubmit 寫 /api/events。
  _eventPopup = new EventPopup(map, window.maplibregl, {
    groups: _EVENT_GROUPS,
    types: _EVENT_TYPES,
    reporterOptions: [
      ['command', '指揮部'], ['forward', '前進組'], ['security', '安全組'],
      ['shelter', '收容組'], ['medical', '醫療組'],
    ],
    getReporter: () => el('place-report-unit')?.value || 'command',
    onReporterChange: (v) => { const bar = el('place-report-unit'); if (bar) bar.value = v; },
    latlngToMgrs: (lat, lng) => _latlngToMGRS(lat, lng, 5),
    onSubmit: (typeKey, ctx) => _evPopupSubmit(typeKey, ctx),
  });

  // 右側事件欄長按 → 地圖 highlight 該事件 + 其他暗化（events.js dispatch
  // map:highlightEvent / map:unhighlightEvent CustomEvent）
  // 機制：zones source `dimmed` feature-state + paint expression 控 opacity；
  //       event_drag handle 走 inline style opacity / pointerEvents。
  document.addEventListener('map:highlightEvent', (e) => {
    _highlightEvent(e.detail?.eventId);
  });
  document.addEventListener('map:unhighlightEvent', () => {
    _unhighlightEvent();
  });

  _updateCritPulse();
  _entityLayersInstalled = true;
}

// P1-10d：critical 事件脈動。單一全域 RAF，每幀對 zones-crit-pulse 層下 2 個 setPaintProperty
// （sin wave 動 radius/opacity）。不用 @keyframes（WebGL 層非 DOM）、不用 per-feature
// feature-state（避免與 highlight 衝突）。
//
// review #70 修：RAF 只在「有 critical 事件 且 非 focus(dim) 模式」時跑，且 tab 隱藏時跳過
// 重繪——否則永久 RAF 會每幀強制全圖 GL 重繪（Pi 不友善），且 focus 模式下非 target 的
// critical 仍全亮（與 dim 矛盾）。
let _critPulseRaf = null;
let _critPulseHas = false;    // 畫面上有 critical 事件
let _critPulseFocus = false;  // focus（右側長按 dim）模式中
function _updateCritPulse() {
  const map = _getMap();
  if (!map) return;
  const want = _critPulseHas && !_critPulseFocus;
  if (want && !_critPulseRaf && typeof requestAnimationFrame !== 'undefined') {
    const loop = () => {
      if (!map.getLayer('zones-crit-pulse')) { _critPulseRaf = null; return; }
      if (typeof document !== 'undefined' && document.hidden) {
        _critPulseRaf = requestAnimationFrame(loop); return;  // tab 隱藏不強制重繪
      }
      const k = (Math.sin(performance.now() / 1000 * 3.2) + 1) / 2;  // 0..1
      map.setPaintProperty('zones-crit-pulse', 'circle-radius', 16 + k * 11);
      map.setPaintProperty('zones-crit-pulse', 'circle-opacity', 0.10 + k * 0.22);
      _critPulseRaf = requestAnimationFrame(loop);
    };
    _critPulseRaf = requestAnimationFrame(loop);
  } else if (!want && _critPulseRaf) {
    cancelAnimationFrame(_critPulseRaf);
    _critPulseRaf = null;
    if (map.getLayer('zones-crit-pulse')) map.setPaintProperty('zones-crit-pulse', 'circle-opacity', 0);
  }
}

/**
 * 對所有 zones 設 feature-state.dimmed=true，target event 的 zone 設 false。
 * zones-halo / zones-base / zones-abbr / zones-label 四個 layer 的 paint
 * expression 都已對應，視覺一致地暗化。event_drag handle 也同步 dim。
 *
 * UX 強化（issue #24 dogfood 反饋）：
 *   1. flyTo target — 找事件不用 user 自己捲動地圖
 *   2. RAF pulse loop — halo radius / opacity 用 sin wave 呼吸（28→34, 0.45→0.80）
 *   3. base circle stroke 跟著 dim（已在 zones-base paint expression 處理）
 */
let _highlightPulseRaf = null;
let _highlightPulseZoneId = null;
const _PULSE_PERIOD_MS = 1100;
const _PULSE_R_MIN = 28;
const _PULSE_R_MAX = 34;
const _PULSE_O_MIN = 0.45;
const _PULSE_O_MAX = 0.80;

function _stopHighlightPulse(map) {
  if (_highlightPulseRaf) {
    cancelAnimationFrame(_highlightPulseRaf);
    _highlightPulseRaf = null;
  }
  if (_highlightPulseZoneId && map) {
    map.setFeatureState(
      { source: 'zones', id: _highlightPulseZoneId },
      { pulse_radius: null, pulse_opacity: null },
    );
  }
  _highlightPulseZoneId = null;
}

function _startHighlightPulse(map, zoneId) {
  _stopHighlightPulse(map);
  _highlightPulseZoneId = zoneId;
  const start = performance.now();
  const loop = (t) => {
    const phase = (Math.sin(((t - start) / _PULSE_PERIOD_MS) * Math.PI * 2) + 1) / 2;
    const r = _PULSE_R_MIN + (_PULSE_R_MAX - _PULSE_R_MIN) * phase;
    const o = _PULSE_O_MIN + (_PULSE_O_MAX - _PULSE_O_MIN) * phase;
    map.setFeatureState(
      { source: 'zones', id: zoneId },
      { highlighted: true, pulse_radius: r, pulse_opacity: o },
    );
    _highlightPulseRaf = requestAnimationFrame(loop);
  };
  _highlightPulseRaf = requestAnimationFrame(loop);
}

/**
 * 'zones' EntityLayer 目前渲染的全部 zone（PR-G1b hybrid 唯一真相來源）：
 *   - 永久節點：map_config（icon='pin'，排除任何殘留 event zone）
 *   - 事件圖釘：cop_entities（attributes.kind='event'）→ adapter 還原 zone shape，id=cop uid
 * _renderZones / _highlightEvent / _unhighlightEvent / _syncEventDragHandles 共用此來源，
 * 確保 setFeatureState 用的 id 與實際渲染的 feature id 一致（highlight 才點得到事件）。
 */
function _allRenderedZones() {
  // P1-16 cutover：節點不再從 map_config 讀，改 on-demand cop_entities（attributes.kind='zone'）。
  const nodeZones = (_copStream?.getEntitiesByKind('zone') || []).map(copEntityToZone).filter(Boolean);
  const eventZones = (_copStream?.getEntitiesByKind('event') || [])
    .map(copEntityToEventZone)
    .filter(Boolean);
  return { nodeZones, eventZones, all: [...nodeZones, ...eventZones] };
}

function _highlightEvent(eventId) {
  if (!eventId) return;
  const map = _getMap();
  if (!map) return;
  const zones = _allRenderedZones().all; // 事件已 cutover 進 cop_entities（PR-G1b）
  const target = zones.find((z) => z.event_id === eventId);
  if (!target) return;
  for (const z of zones) {
    if (!z?.id) continue;
    const isTarget = z.id === target.id;
    // target → highlighted=true (綠光 halo) + dimmed=false
    // others → dimmed=true (整個暗化)
    map.setFeatureState(
      { source: 'zones', id: z.id },
      { dimmed: !isTarget, highlighted: isTarget },
    );
  }
  _eventDragMgr?.dimAllExcept(target.id);
  // (1) flyTo target — 找事件不用 user 自己捲
  if (typeof target.lat === 'number' && typeof target.lng === 'number') {
    map.flyTo({ center: [target.lng, target.lat], duration: 600, essential: true });
  }
  // (2) 啟動 pulse
  _startHighlightPulse(map, target.id);
  // focus 模式：暫停 critical 脈動（否則非 target 的 critical 仍全亮，與 dim 矛盾，review #70）
  _critPulseFocus = true;
  _updateCritPulse();
}

function _unhighlightEvent() {
  const map = _getMap();
  if (!map) return;
  _stopHighlightPulse(map);
  const zones = _allRenderedZones().all;
  for (const z of zones) {
    if (!z?.id) continue;
    map.setFeatureState({ source: 'zones', id: z.id }, { dimmed: false, highlighted: false });
  }
  _eventDragMgr?.undimAll();
  // 離開 focus 模式：恢復 critical 脈動（若畫面有 critical 事件）
  _critPulseFocus = false;
  _updateCritPulse();
}


function _findById(arr, id) {
  return Array.isArray(arr) ? arr.find((x) => x?.id === id) : null;
}

// P1-10e：polygon / route 選中態。單一全域 RAF 對 selected feature 餵 sel_pulse
// （opacity 呼吸），複用 crit-pulse 的「只在需要時跑 + tab 隱藏跳過重繪」紀律。
// 選中走 feature-state.selected（paint case 已備）；setData 會清 feature-state，
// 故 _renderPolygons / _renderRoutes 後呼叫 _reapplySelection 重套。
let _selectedObj = null;       // { source, id }
let _selPulseRaf = null;
function _stopSelPulse() {
  if (_selPulseRaf) { cancelAnimationFrame(_selPulseRaf); _selPulseRaf = null; }
}
function _startSelPulse() {
  const map = _getMap();
  if (!map || _selPulseRaf || typeof requestAnimationFrame === 'undefined') return;
  const loop = () => {
    if (!_selectedObj) { _selPulseRaf = null; return; }
    if (typeof document !== 'undefined' && document.hidden) { _selPulseRaf = requestAnimationFrame(loop); return; }
    const t = (typeof performance !== 'undefined' ? performance.now() : 0) / 1000;
    const p = 0.55 + 0.45 * (0.5 + 0.5 * Math.sin(t * 3));  // 0.55..1.0 呼吸
    map.setFeatureState(_selectedObj, { sel_pulse: p });
    _selPulseRaf = requestAnimationFrame(loop);
  };
  _selPulseRaf = requestAnimationFrame(loop);
}
function _clearSelection() {
  const map = _getMap();
  if (map && _selectedObj) {
    map.removeFeatureState(_selectedObj, 'selected');
    map.removeFeatureState(_selectedObj, 'sel_pulse');
  }
  _selectedObj = null;
  _stopSelPulse();
}
function _setSelection(source, id) {
  const map = _getMap();
  if (!map || id == null) return;
  if (_selectedObj && (_selectedObj.source !== source || _selectedObj.id !== id)) _clearSelection();
  _selectedObj = { source, id };
  map.setFeatureState(_selectedObj, { selected: true });
  _startSelPulse();
}
// setData 後 feature-state 遺失 → 重套目前選中（若仍存在則視覺接回）。
function _reapplySelection() {
  const map = _getMap();
  if (map && _selectedObj) map.setFeatureState(_selectedObj, { selected: true });
}

function _onPolygonClick(e) {
  if (!canAccessMapObjects()) return;
  const id = e.features?.[0]?.properties?.id;
  // PR-G1a：polygon 已 cutover 進 cop_entities，從 cop_stream 回查（id = entity uid）。
  const poly = copEntityToPolygon(_copStream?.getEntity(id));
  if (!poly) return;
  _setSelection('polygons', id);  // P1-10e
  const typeLabel = POLY_TYPES[poly.poly_type]?.label || poly.poly_type;
  const desc = `${typeLabel}　${poly.latlngs.length} 個頂點`;
  _deps.openModal?.(`▱ ${poly.label || '範圍'}`,
    _featureInfo(desc, 'deletePolygon', poly.id,
      poly.label_anchor ? { resetAnchorAction: 'resetPolyLabelAnchor' } : {}));
}

function _onInfraClick(e) {
  if (!canAccessMapObjects()) return;
  const id = e.features?.[0]?.properties?.id;
  // P1-16 PR-2 cutover：設施已搬進 cop_entities（id=uid），從 cop_stream 回查。
  const item = copEntityToInfra(_copStream?.getEntity(id));
  if (!item) return;
  const def = INFRA_TYPES[item.infra_type] || INFRA_TYPES.utility;
  const desc = `${def.label}　${item.lat != null ? _coordValueHTML(item.lat, item.lng) : ''}`;
  // 刪除設施限指揮層（與放置一致）；operator/observer 只看 detail。
  let body = `<div style="font-size:12px;line-height:1.7;color:var(--text2);margin-bottom:12px;">${desc}</div>`;
  if (canUseRealModeControls() && item.id) {
    body += `<button data-action="deleteInfra" data-id="${_escapeHtml(String(item.id))}"
      style="width:100%;padding:8px;background:var(--red);color:#fff;border:none;border-radius:6px;
      font-weight:700;cursor:pointer;font-family:var(--mono);font-size:12px;">🗑 刪除設施</button>`;
  }
  _deps.openModal?.(`${def.abbr} ${item.label || def.label}`, body);
}

function _onRouteClick(e) {
  if (!canAccessMapObjects()) return;
  const id = e.features?.[0]?.properties?.id;
  // PR-G1a：route 已 cutover 進 cop_entities，從 cop_stream 回查（id = entity uid）。
  const route = copEntityToRoute(_copStream?.getEntity(id));
  if (!route) return;
  _setSelection('routes', id);  // P1-10e
  const typeLabel = ROUTE_TYPES[route.route_type]?.label || route.route_type;
  const desc = `${typeLabel}　${route.latlngs.length} 個節點`;
  _deps.openModal?.(`↗ ${route.label || '路線'}`,
    _featureInfo(desc, 'deleteRoute', route.id,
      route.label_anchor ? { resetAnchorAction: 'resetRouteLabelAnchor' } : {}));
}

function _onZoneClick(e) {
  if (!canAccessMapObjects()) return;
  const id = e.features?.[0]?.properties?.id;
  // PR-G1b/P1-16：事件 zone 與節點 zone 都在 cop_entities（id=uid）。先查 cop 事件，再退節點。
  const copEnt = _copStream?.getEntity(id);
  const zone = copEntityToEventZone(copEnt) || copEntityToZone(copEnt) || _findById(_mapConfig?.maps?.outdoor?.zones, id);
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
  // P1-10b 步驟 11：Leaflet legacy path 完整移除。MapLibre EntityLayer 為唯一渲染路徑。
  if (!_polygonLayer) return;
  _polygonLayer.setVisible(_layerVis.polygons);
  if (!_layerVis.polygons) {
    _polygonLayer.clear();
    _polyLabelMgr?.clear();
    return;
  }
  // PR-G1a cutover：資料來源從 _mapConfig 改為 cop_entities（attributes.kind='polygon'），
  // 經 copEntityToPolygon adapter 轉成 polygonToFeature 吃的 shape，渲染管線不變。
  const polys = (_copStream?.getEntitiesByKind('polygon') || [])
    .map(copEntityToPolygon)
    .filter(Boolean);
  // 每個 polygon 產出 2 個 feature 餵同 source：
  //   1. Polygon geometry（fill / stroke layer 渲染）
  //   2. Point geometry（centroid，label layer 渲染 — 解 MapLibre 跨 tile 多
  //      centroid 導致 label 重複的 bug，filter geometry-type=Point）
  const features = [
    ...polys.map(polygonToFeature).filter(Boolean),
    ...polys.map(polygonLabelToFeature).filter(Boolean),
  ];
  _polygonLayer.update(features);
  _reapplySelection();  // P1-10e：setData 清掉 feature-state，重套選中
  // Step 8：sync HTML drag handles 給有 label 的 polygon。label_anchor 改動 → PUT cop
  // （attributes 整包覆寫，故先取現值合併）。WS 廣播回來 → onChange 自動重繪。
  if (_polyLabelMgr) {
    _polyLabelMgr.sync(polys, (id, latlng) => {
      _putCopLabelAnchor(id, [latlng.lat, latlng.lng]);
    }, polygonCentroid);
  }
}

/**
 * 改寫某 cop map 物件（route/polygon）的 label_anchor → PUT /api/cop（帶 If-Match）。
 * backend PUT 對 attributes 是整包覆寫，故先讀現值再合併（anchor=null → 刪除該欄位）。
 * 回傳 Promise<boolean>（true=成功）；caller（reset 走 modal）可據此提示失敗。
 */
function _putCopLabelAnchor(uid, anchor) {
  if (!_copStream) return Promise.resolve(false);
  const ent = _copStream.getEntity(uid);
  if (!ent) return Promise.resolve(false);
  const attrs = { ...(ent.attributes || {}) };
  if (anchor) attrs.label_anchor = anchor;
  else delete attrs.label_anchor;
  return _copStream.updateEntity(uid, { attributes: attrs });
}

/**
 * 地圖操作失敗時的即時提示（沿用 _evPopupSubmit 的 coord-panel ✗ 樣式）。
 * cop 寫入（建立/刪除/標籤）失敗不再 silent —— 至少讓 operator 知道要重試。
 */
function _flashMapMsg(text, ms = 4000) {
  const panel = el('map-coord-panel');
  if (!panel) return;
  panel.style.display = 'flex';
  panel.innerHTML = `<span style="color:#f85149">${_escapeHtml(text)}</span>`;
  setTimeout(() => _refreshCoordPanel(), ms);
}

/** 在表單 modal 內顯示錯誤（沿用 flow-err 樣式）。textContent → 無 XSS。 */
function _showFormErr(id, text) {
  const e = el(id);
  if (!e) return;
  e.textContent = text;
  e.style.display = 'block';
}

function _polyCentroid(latlngs) {
  const lat = latlngs.reduce((sum, point) => sum + point[0], 0) / latlngs.length;
  const lng = latlngs.reduce((sum, point) => sum + point[1], 0) / latlngs.length;
  return [lat, lng];
}

// P1-10b 步驟 11：_infraIcon dead code 移除（L.divIcon 工廠，原為 Leaflet _renderInfra
// legacy 唯一 caller。MapLibre 走 _renderInfra → infraToFeature → entity_layer
// circle layer，無需 L.divIcon）。

function _renderInfra() {
  // P1-10b 步驟 11：Leaflet legacy 移除。
  if (!_infraLayer) return;
  _infraLayer.setVisible(_layerVis.infra);
  if (!_layerVis.infra) { _infraLayer.clear(); return; }
  // P1-16 PR-2 cutover：資料來源從 _mapConfig.infrastructure 改為 cop_entities
  // （attributes.kind='infra'），經 copEntityToInfra adapter 轉成 infraToFeature 吃的 shape，
  // 渲染管線不變。color / abbr 由 INFRA_TYPES 對映補上。
  const features = (_copStream?.getEntitiesByKind('infra') || [])
    .map(copEntityToInfra)
    .filter(Boolean)
    .map((item) => {
      const def = INFRA_TYPES[item.infra_type] || INFRA_TYPES.utility;
      return infraToFeature({ ...item, color: def.color, abbr: def.abbr });
    })
    .filter(Boolean);
  _infraLayer.update(features);
}

function _bearing(lat1, lng1, lat2, lng2) {
  const rad = Math.PI / 180;
  const dLng = (lng2 - lng1) * rad;
  const y = Math.sin(dLng) * Math.cos(lat2 * rad);
  const x = Math.cos(lat1 * rad) * Math.sin(lat2 * rad) -
    Math.sin(lat1 * rad) * Math.cos(lat2 * rad) * Math.cos(dLng);
  return ((Math.atan2(y, x) * 180 / Math.PI) + 360) % 360;
}

// P1-10b 步驟 11：_arrowIcon dead code 移除（L.divIcon 工廠，原為 Leaflet _renderRoutes/
// _renderFlows legacy 唯一 caller。MapLibre chevron arrow 走 entity_layer.js 步驟 7
// 階段 2/3a 的 symbol layer + icon-image，無需 L.divIcon）。

function _renderRoutes() {
  // P1-10b 步驟 11：Leaflet legacy 移除。
  if (!_routeLayer) return;
  _routeLayer.setVisible(_layerVis.routes);
  if (!_layerVis.routes) {
    _routeLayer.clear();
    _routeLabelMgr?.clear();
    return;
  }
  // PR-G1a cutover：資料來源從 _mapConfig 改為 cop_entities（attributes.kind='route'）。
  const routes = (_copStream?.getEntitiesByKind('route') || [])
    .map(copEntityToRoute)
    .filter(Boolean);
  // 每個 route 產出 LineString（line/arrow layer 渲染）+ Point（label layer 渲染，
  // 支援 label_anchor override 給 drag handle 移動後的新位置）。
  const features = [
    ...routes.map(routeToFeature).filter(Boolean),
    ...routes.map(routeLabelToFeature).filter(Boolean),
  ];
  _routeLayer.update(features);
  _reapplySelection();  // P1-10e：setData 清掉 feature-state，重套選中
  // Step 8：sync route label drag handles。label_anchor 改動 → PUT cop（同 polygon）。
  if (_routeLabelMgr) {
    _routeLabelMgr.sync(routes, (id, latlng) => {
      _putCopLabelAnchor(id, [latlng.lat, latlng.lng]);
    }, routeMidLngLat);
  }
}

// PR-H：_resolveRef / _renderFlows 已移除（流向功能退役）。

// P1-10b 步驟 7 階段 1：zone marker port — circle only（NAPSG SVG SDF + abbr text
// 留階段 2，需 addImage + glyphs source）。Drag-to-reposition 留 step 8 draw_tools.js。
function _renderZones(opts = {}) {
  // P1-10b 步驟 11：Leaflet legacy 已刪，window.maplibregl guard 也不再需要。
  if (!_zoneLayer) return;
  // PR-G1b：節點 / 事件分別由 _layerVis.zones / _layerVis.events 控制（同一 _zoneLayer，
  // feature-level 過濾）。任一開即顯示圖層；兩者皆關才清空。
  const showNodes = _layerVis.zones;
  const showEvents = _layerVis.events;
  _zoneLayer.setVisible(showNodes || showEvents);
  if (!showNodes && !showEvents) { _zoneLayer.clear(); return; }
  const data = _deps.getData?.() || {};
  const rendered = _allRenderedZones(); // PR-G1b hybrid：節點 map_config + 事件 cop_entities
  const zones = [
    ...(showNodes ? rendered.nodeZones : []),
    ...(showEvents ? rendered.eventZones : []),
  ];
  const features = [];
  for (const zone of zones) {
    if (zone.lat == null || zone.lng == null) continue;
    const isEvent = !!(zone.event_id || zone.event_code);
    let severity = 'warning';
    let isOrphan = false;
    let evType = null;
    if (isEvent) {
      const ev = (data.events || []).find((item) => item.id === zone.event_id);
      if (!ev) { severity = 'info'; isOrphan = true; }
      else if (['resolved', 'closed'].includes(ev.status)) continue;
      else { severity = ev.severity || 'warning'; evType = ev.event_type; }
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

    // NAPSG abbr 對映（P1-10d 事件資料模型）：
    // **事件** marker 字用「**事件型別自己的 abbr**」（爆/機/暴…，來自 taxonomy _EVENT_TYPES,
    // runtime SoT）——不再用群組 abbr，否則同群組事件圖上分不出（見
    // docs/design/event-symbology-mapping.md：符號講 WHAT）。orphan / 查不到型別 → 退群組 abbr。
    // **節點** 仍用 _NODE_ABBR（收/醫/指/前/安）。NAPSG 象形 glyph 為後續正式版（PR-2b-3）。
    // 事件 group 權威來源 = event_type 經 taxonomy 推得；zone.event_group 為孤兒/back-compat fallback
    // （解撞名：不再用 node_type 裝事件類別）。節點仍用 node_type。
    const evGroup = (evType && _EVENT_TYPES[evType]?.group) || zone.event_group;
    const abbr = isEvent
      ? ((evType && _EVENT_TYPES[evType]?.abbr) || _NAPSG_GROUP_ABBR[evGroup] || '?')
      : (_NODE_ABBR[zone.node_type] || '?');

    // P1-10d 正式 icon：有 vendored NAPSG 象形且已 bake → 用 glyph，否則退 abbr（見 napsg_glyphs.js）。
    const _map = _getMap();
    const _hasGlyph = !!(isEvent && hasNapsgGlyph(evType)
      && _map?.hasImage?.('napsg-glyph-' + evType));
    const { fg, fg_glyph } = pickForeground({ isEvent, evType, abbr, hasGlyph: _hasGlyph });

    const feat = zoneToNodeFeature(zone, { color, abbr, severity, stale, is_orphan: isOrphan, fg, fg_glyph });
    if (feat) features.push(feat);
  }
  _zoneLayer.update(features);
  // P1-10d：只有 critical 事件會有 severity==='critical'（節點預設 'warning'）→ 用來 gate 脈動 RAF。
  _critPulseHas = features.some((f) => f.properties && f.properties.severity === 'critical');
  _updateCritPulse();
  // 帶入避免重複全掃；事件隱藏時不掛事件拖曳 handle。
  // P1-16：節點 zone（cop entity，無 event_id）也可拖，與事件 zone 共用同一 drag manager。
  if (!opts.skipHandleSync) {
    _syncEventDragHandles(
      showEvents ? rendered.eventZones : [],
      showNodes ? rendered.nodeZones : [],
    );
  }
}

/**
 * Sync 事件 zone 的拖曳 handle（在每次 _renderZones 完成後呼叫）。
 *
 * - 只服務事件 zone（event_id / event_code 非空）
 * - drag（每幀）：更新 zone.lat/lng（in-memory）+ re-render zones source 讓 GPU
 *   circle 跟著 handle 走；**不**存 map_config、**不** PATCH（這些放 dragend）。
 *   skipHandleSync=true 避免 sync() 對正在被拖的 marker setLngLat 干擾 drag。
 * - dragend：寫 zone.lat/lng → saveMapConfig → PATCH /api/events/{id} location_desc=新MGRS
 *   → refreshLeafletMarkers re-render（會再次走到本函式更新 handle 位置，但因 sync()
 *   內部走 setLngLat 不重建，無無限遞迴風險）
 * - click：轉派 _onZoneClick（不然 handle 蓋住 zones-base，事件 modal 開不起來）
 */
function _syncEventDragHandles(eventZonesArg, nodeZonesArg) {
  if (!_eventDragMgr) return;
  // 同步 _renderZones 的過濾邏輯：只 sync 還在「open / in_progress」狀態的事件
  // zone，避免事件結案後 GPU circle 已消失、handle 還掛在原地（dogfood 撞到）。
  const data = _deps.getData?.() || {};
  // PR-G1b：事件 zone 來源改 cop_entities（共用 _allRenderedZones，id=cop uid）；只 sync 仍
  // open/in_progress 的事件，避免結案後 handle 殘留。eventZonesArg 由 _renderZones 帶入
  // 避免重複 getEntitiesByKind 全掃（standalone 呼叫則自行取）。
  const eventZones = (eventZonesArg || _allRenderedZones().eventZones).filter((z) => {
    const ev = (data.events || []).find((e) => e.id === z.event_id);
    return !(ev && ['resolved', 'closed'].includes(ev.status));
  });
  // P1-16：節點 zone（cop entity，無 event_id）也可拖。無「結案」概念，全數 sync。
  // dragend / onDrag / onClick callback 用 entity 有無 event_id 區分行為（見下）。
  const nodeZones = nodeZonesArg || _allRenderedZones().nodeZones;
  // P1-16 follow-up：設施(kind='infra')也可拖。infra 在獨立 _infraLayer，_renderZones 不帶它，
  // 故此處自取；圖層隱藏（_layerVis.infra=false）時不掛 handle。callback 按 kind 分支（見下）。
  const infraZones = _layerVis.infra
    ? (_copStream?.getEntitiesByKind('infra') || []).map(copEntityToInfra).filter(Boolean)
    : [];
  _eventDragMgr.sync(
    [...eventZones, ...nodeZones, ...infraZones],
    async (id, latlng, from) => {
      // dragend：① cop 落地位置（PUT + If-Match，會清 dragging）。失敗（409/網路）則 cop 已
      // 採 server 現值（pin 彈回），**不可**再寫 events 表，否則 location_desc 與 pin 分歧。
      const ent = _copStream?.getEntity(id);
      const eventId = ent?.attributes?.event_id;
      const ok = await _copStream?.updateEntity(id, { lat: latlng.lat, lon: latlng.lng });
      if (!ok) { const _k = ent?.attributes?.kind; _flashMapMsg('✗ ' + (eventId ? '事件' : _k === 'infra' ? '設施' : '節點') + '位置儲存失敗（可能被他人同時修改），請重試'); return; }
      if (eventId) {
        const newMgrs = _latlngToMGRS(latlng.lat, latlng.lng, 5);
        // 1. PATCH location_desc — events table 同步（僅在 cop 落地成功後）
        try {
          await authFetch(API_BASE + '/api/events/' + eventId, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ location_desc: newMgrs }),
          });
        } catch (e) {
          console.warn('[map.js] event location PATCH failed', e);
        }
        // 2. POST 處置紀錄條目 — from → to 一條 note，不每幀寫
        try {
          const fromMgrs = from ? _latlngToMGRS(from.lat, from.lng, 5) : null;
          const noteText = fromMgrs
            ? `地圖位置已移動 ${fromMgrs} → ${newMgrs}`
            : `地圖位置已移動 → ${newMgrs}`;
          await authFetch(API_BASE + '/api/events/' + eventId + '/notes', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              text: noteText,
              operator: _deps.getCurrentOperator?.() || '',
            }),
          });
          _deps.doPoll?.();
        } catch (e) {
          console.warn('[map.js] event drag note POST failed', e);
        }
      }
      // updateEntity 成功 → onChange 已即時重繪；此處不需手動 render。
    },
    (id) => {
      // 仿真 layer click 結構；設施 → _onInfraClick、其餘（事件/節點）→ _onZoneClick。
      const evt = { features: [{ properties: { id } }] };
      if (_copStream?.getEntity(id)?.attributes?.kind === 'infra') { _onInfraClick(evt); return; }
      _onZoneClick(evt);
    },
    (id, latlng) => {
      // drag per-frame：cop 本地樂觀位移（不 POST）。設施重畫 _infraLayer、其餘重畫 zones
      // （skipHandleSync 避免動到正在被拖的 handle 自己）。落地在 dragend 的 updateEntity。
      _copStream?.dragLocal(id, latlng.lat, latlng.lng);
      if (_copStream?.getEntity(id)?.attributes?.kind === 'infra') _renderInfra();
      else _renderZones({ skipHandleSync: true });
    },
  );
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
  // 持久化到 sessionStorage — refresh 後 _ensureEntityLayers restore（issue #24 step 1）
  sessionStorage.setItem('_mgrsGridVisible', _mgrsGridVisible ? '1' : '0');
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
  // 深度防禦：cop callsign/attributes 已過 backend validate_no_unsafe_strings，這層 escape
  // 與其他 modal sink 一致（issue #24 belt-and-braces），避免日後驗證鬆動成提權路徑。
  const code = _escapeHtml(zone.event_code || zone.id || '?');
  const desc = `此事件標記在地圖上仍存在，但對應的事件紀錄已不在資料庫（可能已被清除或重設）。`
    + `<br><br><span style="color:var(--text3);font-size:11px;">標記：${code}　·　類型：${_escapeHtml(zone.label || zone.event_group || '—')}</span>`;
  _deps.openModal?.(`⚠ 孤兒事件標記`,
    _featureInfo(desc, 'deleteEventZone', zone.id));
}

export async function _deleteEventZone(id) {
  // PR-G1b：事件 zone 已 cutover 進 cop_entities（id=uid）→ 走 cop soft-delete，WS 即時同步。
  if (!_copStream || !id) return;
  const ok = await _copStream.deleteEntity(id);
  _deps.closeModal?.();
  if (!ok) _flashMapMsg('✗ 事件標記刪除失敗，請重試');
  // 成功 → onChange 即時重繪移除。
}

// ══════════════════════════════════════════════════════════════
// 繪製範圍（Polygon）
// ══════════════════════════════════════════════════════════════

export function _startPolyDraw() {
  if (!canAccessMapObjects()) return;
  if (_polyDrawState) _cancelPolyDraw();
  if (_routeDrawState) _cancelRouteDraw();
  if (_currentMap !== 'outdoor') switchMap('outdoor');
  // _polyDrawState 仍用為 truthy flag（onClick callback / shouldSuppressInteraction 查它）
  // 實際 latlngs / preview state 改由 _drawPreview 管理
  _polyDrawState = { active: true };
  if (_drawPreview) _drawPreview.start('polygon');
  const banner = el('poly-draw-banner');
  if (banner) banner.style.display = 'flex';
  if (el('map-coord-panel')) el('map-coord-panel').style.display = 'none';
  // canvas cursor crosshair（hover system 在 click 期間不會搶 — hover 只 mouseenter/leave 觸發）
  if (_leafletMap) _leafletMap.getCanvas().style.cursor = 'crosshair';
  document.getElementById('btn-poly-draw')?.classList.add('active');
}

export function _cancelPolyDraw() {
  if (!_polyDrawState) return;
  _polyDrawState = null;
  if (_drawPreview) _drawPreview.cancel();
  const banner = el('poly-draw-banner');
  if (banner) banner.style.display = 'none';
  if (_leafletMap) _leafletMap.getCanvas().style.cursor = '';
  document.getElementById('btn-poly-draw')?.classList.remove('active');
}

function _addPolyVertex(lat, lng) {
  if (!_polyDrawState || !_drawPreview) return;
  _drawPreview.addVertex(lat, lng);
  const finBtn = document.getElementById('poly-finish-btn');
  if (finBtn) finBtn.disabled = !_drawPreview.canFinish();
}

export function _finishPolyDraw() {
  if (!_polyDrawState || !_drawPreview || !_drawPreview.canFinish()) return;
  const latlngs = _drawPreview.getLatlngs();
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
  html += `<div id="poly-err" style="display:none;font-size:11px;color:var(--red);margin-bottom:10px;"></div>`;
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
  if (!name || !latlngs || !_copStream) return;
  const def = POLY_TYPES[typeKey];
  // PR-G1a cutover：建立 cop_entity（attributes.kind='polygon'）取代 push 進 _mapConfig。
  // centroid 當 entity lat/lon（地圖物件位置），頂點存 attributes.vertices，label→callsign。
  const [lat, lng] = _polyCentroid(latlngs);
  const created = await _copStream.createEntity({
    type: POLY_COT_TYPE,
    lat,
    lon: lng,
    callsign: name,
    attributes: { kind: 'polygon', vertices: latlngs, color: def.color, poly_type: typeKey, dash: def.dash },
  });
  if (!created) {
    // 失敗不 silent：保留 modal + _pendingPolyLatlngs 讓 operator 直接重試
    _showFormErr('poly-err', '儲存失敗（權限或連線問題），請重試');
    return;
  }
  _pendingPolyLatlngs = null;
  _deps.closeModal?.();
  // 建立後 createEntity 內部 upsert + onChange 已重繪；此處不需再手動 render。
}

export async function _deletePolygon(id) {
  if (!_copStream || !id) return;
  const ok = await _copStream.deleteEntity(id);
  _deps.closeModal?.();
  if (!ok) _flashMapMsg('✗ 範圍刪除失敗，請重試');
}

export async function _resetPolyLabelAnchor(id) {
  const ok = await _putCopLabelAnchor(id, null);
  _deps.closeModal?.();
  if (!ok) _flashMapMsg('✗ 重設標籤位置失敗，請重試');
}

// ══════════════════════════════════════════════════════════════
// P1-16 PR-2：on-demand 放置設施（kind='infra'）
//   mirror 節點放置流程：開類型選擇 modal → 點地圖 → 建 cop_entity（attributes.kind='infra'）。
//   後端零新工：POST /api/cop/entities + P1-14 自動蓋 active exercise（或實戰 NULL）。
//   v1 = 放 + 刪；拖移留 follow-up（OUT of scope）。
// ══════════════════════════════════════════════════════════════

// 圖層面板「＋ 新增設施」→ 開類型選擇 modal（5 類設施）。
export function _openInfraForm() {
  // 放置/管理設施限指揮層（sysadmin/commander）；operator/observer 不可開。
  if (!canUseRealModeControls()) return;
  const BTN = 'display:block;width:100%;padding:10px;margin-bottom:8px;background:var(--surface);border:1px solid var(--border);color:var(--text);border-radius:6px;cursor:pointer;font-family:var(--mono);font-size:13px;text-align:left;';
  let html = '<div style="font-size:11px;color:var(--text3);margin-bottom:12px;">選擇設施類型，接著點地圖放置：</div>';
  for (const [type, def] of Object.entries(INFRA_TYPES)) {
    if (type === '__proto__' || type === 'constructor' || type === 'prototype') continue;
    html += `<button data-action="startInfraPlace" data-infra-type="${_escapeHtml(type)}" style="${BTN}">${_escapeHtml(def.label)}</button>`;
  }
  _deps.openModal?.('＋ 新增設施', html);
}

// thin wrapper（共用實作 _startPlace / _cancelPlace / _placeAt 見節點區）。
export function _startInfraPlace(infraType) { _startPlace('infra', INFRA_TYPES[infraType] ? infraType : 'utility'); }
export function _cancelInfraPlace() { _cancelPlace(); }

export async function _deleteInfra(id) {
  if (!canUseRealModeControls()) return;  // 限指揮層（與放置一致）
  if (!_copStream || !id) return;
  const ok = await _copStream.deleteEntity(id);
  _deps.closeModal?.();
  if (!ok) _flashMapMsg('✗ 設施刪除失敗，請重試');
}

// PR-H：流向（Flow）功能整組退役 —— 與「繪製路線」重疊（route 是有向多段線、對齊
// CoT b-m-r，已即時同步），且 cutover 後 flow 連不到事件。_openFlowForm / _saveFlow /
// _deleteFlow / _renderFlows / _resolveRef / FLOW_TYPES / flows 圖層皆移除。

// ══════════════════════════════════════════════════════════════
// 繪製路線（Route）
// ══════════════════════════════════════════════════════════════

export function _startRouteDraw() {
  if (!canAccessMapObjects()) return;
  if (_routeDrawState) _cancelRouteDraw();
  if (_polyDrawState) _cancelPolyDraw();
  if (_currentMap !== 'outdoor') switchMap('outdoor');
  _routeDrawState = { active: true };
  if (_drawPreview) _drawPreview.start('route');
  const banner = el('route-draw-banner');
  if (banner) banner.style.display = 'flex';
  if (el('map-coord-panel')) el('map-coord-panel').style.display = 'none';
  if (_leafletMap) _leafletMap.getCanvas().style.cursor = 'crosshair';
  document.getElementById('btn-route-draw')?.classList.add('active');
}

export function _cancelRouteDraw() {
  if (!_routeDrawState) return;
  _routeDrawState = null;
  if (_drawPreview) _drawPreview.cancel();
  const banner = el('route-draw-banner');
  if (banner) banner.style.display = 'none';
  if (_leafletMap) _leafletMap.getCanvas().style.cursor = '';
  document.getElementById('btn-route-draw')?.classList.remove('active');
}

function _addRouteVertex(lat, lng) {
  if (!_routeDrawState || !_drawPreview) return;
  _drawPreview.addVertex(lat, lng);
  const finBtn = document.getElementById('route-finish-btn');
  if (finBtn) finBtn.disabled = !_drawPreview.canFinish();
}

export function _finishRouteDraw() {
  if (!_routeDrawState || !_drawPreview || !_drawPreview.canFinish()) return;
  const latlngs = _drawPreview.getLatlngs();
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
  html += `<div id="route-err" style="display:none;font-size:11px;color:var(--red);margin-bottom:10px;"></div>`;
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
  if (!name || !latlngs || !_copStream) return;
  const def = ROUTE_TYPES[typeKey];
  // PR-G1a cutover：建立 cop_entity（attributes.kind='route'）。中點當 entity lat/lon。
  const mid = routeMidLngLat({ latlngs }) || [latlngs[0][1], latlngs[0][0]]; // [lng,lat]
  const created = await _copStream.createEntity({
    type: ROUTE_COT_TYPE,
    lat: mid[1],
    lon: mid[0],
    callsign: name,
    attributes: { kind: 'route', vertices: latlngs, color: def.color, route_type: typeKey, dash: def.dash },
  });
  if (!created) {
    _showFormErr('route-err', '儲存失敗（權限或連線問題），請重試');
    return;
  }
  _pendingRouteLatlngs = null;
  _deps.closeModal?.();
}

export async function _deleteRoute(id) {
  if (!_copStream || !id) return;
  const ok = await _copStream.deleteEntity(id);
  _deps.closeModal?.();
  if (!ok) _flashMapMsg('✗ 路線刪除失敗，請重試');
}

export async function _resetRouteLabelAnchor(id) {
  const ok = await _putCopLabelAnchor(id, null);
  _deps.closeModal?.();
  if (!ok) _flashMapMsg('✗ 重設標籤位置失敗，請重試');
}

// ══════════════════════════════════════════════════════════════
// P1-16：on-demand 放置節點（kind='zone'）
//   clone route draw 模式：進入放置模式 → 點地圖 → 建 cop_entity（attributes.kind='zone'）。
//   後端零新工：POST /api/cop/entities + P1-14 自動蓋 active exercise（或實戰 NULL）。
// ══════════════════════════════════════════════════════════════

// 工具列「⊙ 放置節點」→ 開類型選擇 modal（5 個 ICS 編組）。
export function _openNodePlacePicker() {
  // P1-16：放置/管理節點限指揮層（sysadmin/commander）；operator/observer 不可開。
  if (!canUseRealModeControls()) return;
  const BTN = 'display:block;width:100%;padding:10px;margin-bottom:8px;background:var(--surface);border:1px solid var(--border);color:var(--text);border-radius:6px;cursor:pointer;font-family:var(--mono);font-size:13px;text-align:left;';
  let html = '<div style="font-size:11px;color:var(--text3);margin-bottom:12px;">選擇節點類型，接著點地圖放置：</div>';
  for (const n of _PERM_NODES) {
    html += `<button data-action="startNodePlace" data-node-type="${_escapeHtml(n.node_type)}" style="${BTN}">${_escapeHtml(n.label)}</button>`;
  }
  _deps.openModal?.('⊙ 放置節點', html);
}

// thin wrapper（保留 export 給 main.js dispatch；實作見下方 _startPlace / _cancelPlace / _placeAt）。
export function _startNodePlace(nodeType) { _startPlace('zone', nodeType || 'command'); }
export function _cancelNodePlace() { _cancelPlace(); }

// P1-16 follow-up：節點/設施放置共用實作（取代原 _startNodePlace/_startInfraPlace、
// _cancelNodePlace/_cancelInfraPlace、_placeNodeAt/_placeInfraAt 的近重複）。單一 `_placeState`
// 同時根除「兩個 state 殘留互斥」隱患（原 MED-1）。kind='zone'→_PERM_NODES/node_type；
// 'infra'→INFRA_TYPES/infra_type。CoT type 與 createEntity 流程兩者一致（a-f-G-I + P1-14 綁 scope）。
function _startPlace(kind, type) {
  if (!canUseRealModeControls()) return;  // 限指揮層
  if (_routeDrawState) _cancelRouteDraw();
  if (_polyDrawState) _cancelPolyDraw();
  if (_currentMap !== 'outdoor') switchMap('outdoor');
  _placeState = { kind, type };
  const banner = el('node-place-banner');
  if (banner) banner.style.display = 'flex';
  if (el('map-coord-panel')) el('map-coord-panel').style.display = 'none';
  if (_leafletMap) _leafletMap.getCanvas().style.cursor = 'crosshair';
}

function _cancelPlace() {
  if (!_placeState) return;
  _placeState = null;
  const banner = el('node-place-banner');
  if (banner) banner.style.display = 'none';
  if (_leafletMap) _leafletMap.getCanvas().style.cursor = '';
}

async function _placeAt(lat, lng) {
  if (!_placeState || !_copStream) return;
  const { kind, type } = _placeState;
  let label, attributes;
  if (kind === 'zone') {
    label = _PERM_NODES.find((n) => n.node_type === type)?.label || '';
    attributes = { kind: 'zone', node_type: type };
  } else {
    label = (INFRA_TYPES[type] || INFRA_TYPES.utility).label;
    attributes = { kind: 'infra', infra_type: type };
  }
  const created = await _copStream.createEntity({
    type: 'a-f-G-I',
    lat: +(+lat).toFixed(6),
    lon: +(+lng).toFixed(6),
    callsign: label,
    attributes,
  });
  if (!created) {
    _flashMapMsg('✗ ' + (kind === 'zone' ? '節點' : '設施') + '放置失敗，請重試');
    return;
  }
  // createEntity 內部 upsert + onChange 自動重繪（_scheduleCopRender），不必手動 render。
  _cancelPlace();
}
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
