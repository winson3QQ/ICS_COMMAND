// aar_map.js — AAR 回放頁的地圖層（P2-20(B) B1，issue #201）
//
// 自帶 map 實例：直接 import maplibre_core（initMaplibre 建獨立實例 + pmtiles 底圖），
// **不經 map.js / EntityLayer**——回放是唯讀視圖，單位以 circle+callsign 簡繪（2525 符號留 v2），
// 與 P2-33b/c、part-3 的 live 渲染領地零重疊。

import { initMaplibre } from '../map/maplibre_core.js';
import { positionsToGeoJSON } from './replay_engine.js';

const SRC = 'aar-units';
let _map = null;
let _ready = false;
let _pending = null; // map 未 ready 前最後一次 setPositions 的資料（ready 後補渲染）

/** 建 source + 兩層（circle + callsign label）。style 未就緒時 addSource 會 throw →
 *  回 false 讓 caller 改掛事件重試。idempotent（_ready 守門）。 */
function _ensureLayers() {
  if (_ready || !_map) return _ready;
  try {
    _map.addSource(SRC, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
    _map.addLayer({
      id: 'aar-units-dot',
      type: 'circle',
      source: SRC,
      paint: {
        'circle-radius': 7,
        'circle-color': '#3fb950',
        'circle-stroke-width': 2,
        'circle-stroke-color': '#0d1117',
      },
    });
    _map.addLayer({
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
  } catch {
    return false; // style 未就緒（addSource/addLayer throw）→ caller 重試
  }
  _ready = true;
  if (_pending) {
    setPositions(_pending);
    _pending = null;
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
  if (src) src.setData(positionsToGeoJSON(posMap));
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
