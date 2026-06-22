// aar_map.js — AAR 回放頁的地圖層（P2-20(B)，issue #201 / #4）
//
// 自帶 map 實例：直接 import maplibre_core（initMaplibre 建獨立實例 + pmtiles 底圖）。
// #4：單位改用 live 同款 MIL-STD-2525 符號（milsymbol，via cotToSidc + bakeMilSymbol）；
//     無 sidc 的單位（手動標記等）退回敵我配色點；尾跡依敵我態配色。**仍不經 map.js**——
//     符號/敵我函式皆來自可 import 的共用模組（mil_symbol.js / entity_layer.js）。

import { initMaplibre } from '../map/maplibre_core.js';
import { cotToSidc, affiliationFromCot } from '../map/mil_symbol.js';
import { bakeMilSymbol, bakeDiamondSdf, bakeTriangleSdf, bakeTextSdf, bakeSvgIcon } from '../map/entity_layer.js';
import { NAPSG_GLYPH_SVG } from '../map/napsg_glyphs.js'; // #3：重用 live 的 NAPSG 象形圖庫

const SRC = 'aar-units';
const SRC_TRAILS = 'aar-trails';
const SRC_ZONES = 'aar-zones'; // #338：區域（polygon fill+outline / route line）
const SRC_EVENTS = 'aar-events'; // #339：事件（畫在關聯標記位置）
let _map = null;
let _ready = false;
let _pending = null; // map 未 ready 前最後一次 setPositions 的資料（ready 後補渲染）
let _pendingTrails = null; // 同上（B2 尾跡）
let _pendingZones = null; // 同上（#338 區域）
let _pendingEvents = null; // 同上（#339 事件）

// #339/#3：事件 severity → 色（對齊 live `_SEV_COLORS`，POLICY 日夜恆定）。◆ 用此 tint。
const SEVERITY_COLOR = [
  'match', ['get', 'severity'],
  'critical', '#FF181E', 'warning', '#FF8918', 'info', '#237ACF',
  /* 其他 */ '#8b949e',
];
let _evBakeSeq = 0; // 事件象形 async bake seq guard
let _renderSeq = 0; // bake async seq guard（舊輪 .then 不蓋新位置）

// 敵我態 → 顏色（fallback 點 + 尾跡線）。對齊 2525 慣例：友藍 / 敵紅 / 中綠 / 不明黃。
const AFFIL_COLOR = [
  'match', ['get', 'affiliation'],
  'friendly', '#3b82f6', 'hostile', '#ef4444', 'neutral', '#22c55e',
  /* unknown / 其他 */ '#eab308',
];

/** 折疊位置 Map → units GeoJSON（含 iconId='mil-'+sidc / affiliation）。回 {fc, sidcs}。
 *  軌跡不存歷史敵我態 → 用該 uid 現值 cot_type（v1 限制，#4 issue 記）。 */
function _unitsToGeoJSON(posMap) {
  const features = [];
  const sidcs = new Set();
  for (const p of posMap.values()) {
    const sidc = p.cot_type ? cotToSidc(p.cot_type) : null;
    const props = {
      uid: p.uid, callsign: p.actor, t: p.t,
      affiliation: p.cot_type ? affiliationFromCot(p.cot_type) : 'unknown',
    };
    if (sidc) { props.iconId = 'mil-' + sidc; sidcs.add(sidc); }
    features.push({ type: 'Feature', geometry: { type: 'Point', coordinates: [p.lon, p.lat] }, properties: props });
  }
  return { fc: { type: 'FeatureCollection', features }, sidcs };
}

/** 建 source + 兩層（circle + callsign label）。style 未就緒時 addSource 會 throw →
 *  回 false 讓 caller 改掛事件重試。idempotent（_ready 守門）。 */
function _ensureLayers() {
  if (_ready || !_map) return _ready;
  try {
    // 每步都有 idempotent guard（review #201-B2-1）：style 競態下可能「部分加入後 throw」，
    // retry 若重 addSource 會撞 'already exists' → 永遠卡在 not-ready。guard 後 retry 只補缺的。
    // #338：區域層最先加（在最底——尾跡/單位之下，當背景）。polygon 給 fill+outline、route 給 line。
    if (!_map.getSource(SRC_ZONES)) {
      _map.addSource(SRC_ZONES, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
    }
    if (!_map.getLayer('aar-zones-fill')) {
      _map.addLayer({
        id: 'aar-zones-fill',
        type: 'fill',
        source: SRC_ZONES,
        filter: ['==', ['get', 'kind'], 'polygon'], // 只有面填色；route 不填
        paint: { 'fill-color': ['get', 'color'], 'fill-opacity': 0.15 },
      });
    }
    if (!_map.getLayer('aar-zones-line')) {
      _map.addLayer({
        id: 'aar-zones-line',
        type: 'line',
        source: SRC_ZONES, // polygon 外框 + route 線（同層，maplibre 對 Polygon 畫外環）
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': ['get', 'color'], 'line-width': 2, 'line-opacity': 0.85 },
      });
    }
    // 尾跡層次加（線在點之下）
    if (!_map.getSource(SRC_TRAILS)) {
      _map.addSource(SRC_TRAILS, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
    }
    if (!_map.getLayer('aar-trails-line')) {
      _map.addLayer({
        id: 'aar-trails-line',
        type: 'line',
        source: SRC_TRAILS,
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: {
          'line-color': AFFIL_COLOR, // #4：依敵我態配色
          'line-width': 2.5,
          'line-opacity': 0.5,
        },
      });
    }
    if (!_map.getSource(SRC)) {
      _map.addSource(SRC, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
    }
    // #4：milsymbol 2525 圖示（有 sidc 才有 iconId；未 bake 完 icon-image 找不到 → 該幀不顯，
    // bake 完 setPositions re-setData 即現）。
    if (!_map.getLayer('aar-units-icon')) _map.addLayer({
      id: 'aar-units-icon',
      type: 'symbol',
      source: SRC,
      filter: ['has', 'iconId'],
      layout: {
        'icon-image': ['get', 'iconId'],
        'icon-allow-overlap': true,
        'icon-ignore-placement': true,
      },
    });
    // fallback：無 sidc 的單位（非 atom / 缺 type）→ 敵我配色點。
    if (!_map.getLayer('aar-units-dot')) _map.addLayer({
      id: 'aar-units-dot',
      type: 'circle',
      source: SRC,
      filter: ['!', ['has', 'iconId']],
      paint: {
        'circle-radius': 7,
        'circle-color': AFFIL_COLOR,
        'circle-stroke-width': 2,
        'circle-stroke-color': '#0d1117',
      },
    });
    if (!_map.getLayer('aar-units-label')) _map.addLayer({
      id: 'aar-units-label',
      type: 'symbol',
      source: SRC,
      layout: {
        'text-field': ['get', 'callsign'],
        'text-font': ['Noto Sans Regular'], // vendored glyphs（maplibre_core 同款，P1-10b 3b）
        'text-size': 12,
        'text-offset': [0, 1.2],
        'text-anchor': 'top',
      },
      paint: {
        'text-color': '#e6edf3',
        'text-halo-color': '#0d1117',
        'text-halo-width': 1.5,
      },
    });
    // #339/#3：事件層（最上層）——**重用 live COP 原本的事件符號**：severity ◆ + NAPSG 象形/abbr 前景。
    // 形狀 SDF（zone-diamond/zone-triangle）在此 bake（同 live `bakeDiamondSdf`）；abbr/glyph 前景在
    // setEvents 依資料 lazy bake。三屬性對齊 live zones-event 層：severity（tint ◆）、fg、fg_glyph。
    bakeDiamondSdf(_map, 'zone-diamond'); // 事件 ◆
    bakeTriangleSdf(_map, 'zone-triangle'); // alert ▲（事件預設 civil，備用）
    if (!_map.getSource(SRC_EVENTS)) {
      _map.addSource(SRC_EVENTS, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
    }
    // civil/alert：severity ◆/▲（military 走下面 milsymbol 層，排除於此）。
    if (!_map.getLayer('aar-events-shape')) _map.addLayer({
      id: 'aar-events-shape',
      type: 'symbol',
      source: SRC_EVENTS,
      filter: ['!=', ['get', 'regime'], 'military'],
      layout: {
        'icon-image': ['match', ['get', 'regime'], 'alert', 'zone-triangle', 'zone-diamond'],
        'icon-size': 1.1,
        'icon-allow-overlap': true,
        'icon-ignore-placement': true,
        'symbol-sort-key': ['case', ['==', ['get', 'severity'], 'critical'], 0, 1],
      },
      paint: { 'icon-color': SEVERITY_COLOR }, // ◆ tint = severity 色（同 live）
    });
    // military：milsymbol 2525 框（drone 紅機/QRF 藍方/不明黃）。**不套 icon-color**（全彩自帶），
    // icon-image=iconId（'mil-<SIDC>'，setEvents async 烤完才現）。同 live zones-military-icon。
    if (!_map.getLayer('aar-events-mil')) _map.addLayer({
      id: 'aar-events-mil',
      type: 'symbol',
      source: SRC_EVENTS,
      filter: ['all', ['==', ['get', 'regime'], 'military'], ['has', 'iconId']],
      layout: {
        'icon-image': ['get', 'iconId'],
        'icon-size': 1.1,
        'icon-allow-overlap': true,
        'icon-ignore-placement': true,
        'symbol-sort-key': ['case', ['==', ['get', 'severity'], 'critical'], 0, 1],
      },
    });
    // fg 字/象形：只 civil/alert（military 2525 框自帶敵我語意、不疊 abbr，同 live）。
    if (!_map.getLayer('aar-events-fg')) _map.addLayer({
      id: 'aar-events-fg',
      type: 'symbol',
      source: SRC_EVENTS,
      filter: ['!=', ['get', 'regime'], 'military'],
      layout: {
        // fg = napsg-glyph-<type>（6 種象形）或 napsg-abbr-<abbr>（其餘）。
        'icon-image': ['coalesce', ['get', 'fg'], ['concat', 'napsg-abbr-', ['get', 'abbr']]],
        'icon-size': ['case', ['==', ['coalesce', ['get', 'fg_glyph'], false], true], 0.72, 0.9],
        'icon-allow-overlap': true,
        'icon-ignore-placement': true,
      },
      paint: { 'icon-color': '#ffffff' }, // 白色前景（同 live zones-abbr）
    });
    if (!_map.getLayer('aar-events-label')) _map.addLayer({
      id: 'aar-events-label',
      type: 'symbol',
      source: SRC_EVENTS,
      layout: {
        'text-field': ['get', 'label'], // #1：人類描述（無人機威脅…），非 event_code
        'text-font': ['Noto Sans Regular'],
        'text-size': 11,
        'text-offset': [0, 1.4],
        'text-anchor': 'top',
      },
      paint: { 'text-color': '#e6edf3', 'text-halo-color': '#0d1117', 'text-halo-width': 1.5 },
    });
  } catch {
    return false; // style 未就緒（addSource/addLayer throw）→ caller 重試
  }
  _ready = true;
  if (_pending) {
    setPositions(_pending);
    _pending = null;
  }
  if (_pendingTrails) {
    setTrails(_pendingTrails);
    _pendingTrails = null;
  }
  if (_pendingZones) {
    setZones(_pendingZones);
    _pendingZones = null;
  }
  if (_pendingEvents) {
    setEvents(_pendingEvents);
    _pendingEvents = null;
  }
  return true;
}

/** 初始化回放地圖（idempotent）。回 map 或 null（lib 未載）。
 *  layer 建立時序（dogfood 實測）：pmtiles 底圖 tile 在載時 'load' 可能遲遲不 fire →
 *  比照 map.js 慣例「能加就先加，不行才等事件」：立即試 _ensureLayers()，失敗再掛
 *  once('load') ＋ 'styledata'（每次 style 變動重試，_ready 守門防重複）。 */
export function initAarMap(containerId) {
  _map = initMaplibre(containerId, {}); // 唯讀：不掛 click/longpress callback
  if (!_map) return null;
  if (!_ensureLayers()) {
    _map.once('load', _ensureLayers);
    _map.on('styledata', _ensureLayers);
  }
  return _map;
}

/** 把折疊結果（Map uid→pos）寫進地圖 source。map 未 ready → 暫存待補。 */
export function setPositions(posMap) {
  if (!_ready) {
    _pending = posMap;
    return;
  }
  const src = _map?.getSource(SRC);
  if (!src) return;
  const { fc, sidcs } = _unitsToGeoJSON(posMap);
  src.setData(fc);
  // #4：bake milsymbol（async；已 baked 的 sidc 為 no-op）。本輪有「新」icon 被 bake 出來 →
  // re-setData 讓符號現出。seq guard：播放每幀 setPositions，只有最新一輪的 .then 才套用。
  if (sidcs.size) {
    const seq = ++_renderSeq;
    Promise.all([...sidcs].map(s => bakeMilSymbol(_map, s))).then(rs => {
      if (rs.some(Boolean) && seq === _renderSeq && _map?.getSource(SRC)) _map.getSource(SRC).setData(fc);
    });
  }
}

/** B2 尾跡（GeoJSON LineString FC）。map 未 ready → 暫存待補。#4：補 affiliation 供 line-color match。 */
export function setTrails(geojson) {
  if (!_ready) {
    _pendingTrails = geojson;
    return;
  }
  const src = _map?.getSource(SRC_TRAILS);
  if (!src) return;
  const fc = {
    type: 'FeatureCollection',
    features: (geojson?.features || []).map(f => ({
      ...f,
      properties: {
        ...f.properties,
        affiliation: f.properties?.cot_type ? affiliationFromCot(f.properties.cot_type) : 'unknown',
      },
    })),
  };
  src.setData(fc);
}

/** #338：區域 GeoJSON（polygon/route FC，已由 replay_engine.zonesToGeoJSON 折疊+轉好）。
 *  map 未 ready → 暫存待補。fill 層 filter polygon、line 層含 polygon 外框 + route。 */
export function setZones(geojson) {
  if (!_ready) {
    _pendingZones = geojson;
    return;
  }
  const src = _map?.getSource(SRC_ZONES);
  if (src) src.setData(geojson || { type: 'FeatureCollection', features: [] });
}

/** #339：事件 GeoJSON（replay_engine.eventsToGeoJSON 折疊+定位好，props 對齊 live）。map 未
 *  ready → 暫存待補。前景圖示 lazy bake：abbr 字（SDF，同步）+ NAPSG 象形（SVG raster，非同步
 *  → bake 完 re-setData 才現，seq guard 防舊輪蓋新位置），與 live `_bakeAbbrs`/glyph bake 同源。 */
export function setEvents(geojson) {
  if (!_ready) {
    _pendingEvents = geojson;
    return;
  }
  const src = _map?.getSource(SRC_EVENTS);
  if (!src) return;
  const fc = geojson || { type: 'FeatureCollection', features: [] };
  src.setData(fc);
  const feats = fc.features || [];
  // 收集需要烤的前景：abbr 字（非象形）/ NAPSG 象形 / milsymbol 2525（military）。
  const abbrs = new Set();
  const glyphTypes = new Set();
  const sidcs = new Set();
  for (const f of feats) {
    const pr = f.properties || {};
    if (pr.regime === 'military' && pr.iconId) sidcs.add(pr.iconId.replace(/^mil-/, ''));
    else if (pr.fg_glyph && pr.event_type && NAPSG_GLYPH_SVG[pr.event_type]) glyphTypes.add(pr.event_type);
    else if (pr.abbr) abbrs.add(pr.abbr);
  }
  if (abbrs.size) bakeTextSdf(_map, 'napsg-abbr-', [...abbrs]); // SDF 同步，立即可用
  // 象形 + milsymbol 皆 async（SVG/canvas raster）→ 全部烤完 re-setData 才現；seq guard 防舊輪蓋新位置。
  const asyncBakes = [
    ...[...glyphTypes].map(t => bakeSvgIcon(_map, 'napsg-glyph-' + t, NAPSG_GLYPH_SVG[t])),
    ...[...sidcs].map(s => bakeMilSymbol(_map, s)),
  ];
  if (asyncBakes.length) {
    const seq = ++_evBakeSeq;
    Promise.all(asyncBakes).then(() => {
      if (seq === _evBakeSeq && _map?.getSource(SRC_EVENTS)) _map.getSource(SRC_EVENTS).setData(fc);
    });
  }
}

/** 首次載入時把視野收到所有單位的範圍（無單位 → 不動，沿用預設中心）。 */
export function fitToPositions(posMap) {
  if (!_map || posMap.size === 0) return;
  let minLat = 90, maxLat = -90, minLon = 180, maxLon = -180;
  for (const p of posMap.values()) {
    if (p.lat < minLat) minLat = p.lat;
    if (p.lat > maxLat) maxLat = p.lat;
    if (p.lon < minLon) minLon = p.lon;
    if (p.lon > maxLon) maxLon = p.lon;
  }
  _map.fitBounds([[minLon, minLat], [maxLon, maxLat]], { padding: 80, maxZoom: 15, duration: 0 });
}
