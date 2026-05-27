/**
 * entity_layer.js — MapLibre EntityLayer 抽象
 *
 * P1-10b 步驟 5：取代 Leaflet 的「marker 物件陣列 add/remove」逐筆寫法。
 *
 * 借鏡 mini-taiwan `TrainSymbolLayer.ts:107-270`（commit 公開於 README，BSD-style 學習）：
 *   - 1 個 GeoJSON source per entity-class
 *   - 1 個或多個 MapLibre Layer 疊在同 source 上（base / glow / selected / halo）
 *   - update(featureCollection) 批量替換 source data
 *   - destroy() 清掉 layer + source
 *
 * 修正 mini-taiwan 反例：
 *   - mini-taiwan 用單一 `system` property 同時當 source / affiliation / 視覺分類；
 *     ICS_Command 區分 properties.source (tak/manual/pi-node/waveink) ×
 *     properties.affiliation (friendly/hostile/neutral/unknown) ×
 *     properties.severity (critical/warning/info) 三維度（為 P2-04 鋪路）。
 *   - 預留 setFeatureState hover / symbol-sort-key priority 接點（步驟 7 補 paint expression）。
 *
 * 效能避雷（套 mini-taiwan PERFORMANCE_OPTIMIZATION_PLAN.md）：
 *   - update() 一次 setData 而非逐筆 add；MapLibre 內部 diff 比 marker 陣列效率高
 *   - 不在每幀重建 feature；caller 應在 dirty 時才呼叫 update
 *   - 不在 paint expression 內做重計算；severity / affiliation → color 走 data-driven
 *
 * 不負責（屬之後步驟）：
 *   - SDF icon + addImage（步驟 7 NAPSG / P2-05 MIL-STD-2525 frame）
 *   - 4-layer state stack 完整實作（步驟 7 補 glow / selected / halo layer）
 *   - hover / selected via setFeatureState（步驟 7 接 mouseenter 事件）
 *   - clustering（步驟 11 P1-10f 才開）
 */

const EMPTY_FC = Object.freeze({ type: 'FeatureCollection', features: [] });

/**
 * 一個 EntityLayer 對應 1 個 GeoJSON source + N 個 MapLibre Layer（共用 source）。
 *
 * 使用 pattern：
 *   const zones = new EntityLayer(map, 'zones', {
 *     layers: [
 *       { id: 'zones-base',   type: 'symbol', layout: { 'icon-image': ['get','iconId'] }, paint: { 'icon-color': ['get','color'] } },
 *       { id: 'zones-halo',   type: 'circle', filter: ['==', ['get','severity'], 'critical'], paint: {...} },
 *     ],
 *   });
 *   zones.update(zoneFeatures);  // 餵 GeoJSON Feature 陣列
 *
 * Feature 標準 properties：
 *   { id, source, affiliation, severity, iconId, color, priority, label, status, ... }
 */
export class EntityLayer {
  /**
   * @param {maplibregl.Map} map - MapLibre map instance
   * @param {string} id - source id（同時作為 layer id 前綴）
   * @param {object} opts
   *   - layers: Array<MapLibre layer spec>（必填，至少 1 層；type 為 circle/symbol/fill/line 之一）
   *   - clusterOptions: 之後 P1-10f 用；本步驟不開啟
   */
  constructor(map, id, opts = {}) {
    if (!map) throw new Error('EntityLayer: map required');
    if (!id) throw new Error('EntityLayer: id required');
    const layers = opts.layers || [];
    if (layers.length === 0) throw new Error(`EntityLayer ${id}: 至少需要 1 個 layer spec`);

    this.map = map;
    this.id = id;
    this.layerSpecs = layers;
    this._installed = false;
    this._lastFeatureCount = 0;

    this._install();
  }

  _install() {
    if (this._installed) return;

    // 1. add source（空 FeatureCollection 起手；caller update() 後才有資料）
    if (!this.map.getSource(this.id)) {
      this.map.addSource(this.id, {
        type: 'geojson',
        data: EMPTY_FC,
        // promoteId 讓 setFeatureState 可走 properties.id（步驟 7 hover 機制用）
        promoteId: 'id',
      });
    }

    // 2. add layers
    for (const spec of this.layerSpecs) {
      if (this.map.getLayer(spec.id)) continue;
      // 強制 source 對齊本 EntityLayer，caller 不需要重複寫
      this.map.addLayer({ ...spec, source: this.id });
    }

    this._installed = true;
  }

  /**
   * 批量替換 source 資料。caller 給整份 feature array，本 method 包成 FeatureCollection。
   * 一次 setData 取代逐筆 add/remove（mini-taiwan PERFORMANCE_OPTIMIZATION_PLAN 反例 #6）。
   *
   * @param {Array<GeoJSON.Feature>} features
   */
  update(features) {
    const source = this.map.getSource(this.id);
    if (!source) return;
    const fc = {
      type: 'FeatureCollection',
      features: Array.isArray(features) ? features : [],
    };
    source.setData(fc);
    this._lastFeatureCount = fc.features.length;
  }

  /** 清空（同 update([])） */
  clear() {
    this.update([]);
  }

  /** 切換整層可見性（layer-level visibility，比 update([]) 輕量） */
  setVisible(visible) {
    const visValue = visible ? 'visible' : 'none';
    for (const spec of this.layerSpecs) {
      if (this.map.getLayer(spec.id)) {
        this.map.setLayoutProperty(spec.id, 'visibility', visValue);
      }
    }
  }

  /**
   * setFeatureState helper（步驟 7 hover/selected 用）。
   * 走 promoteId='id'，caller 給 feature.properties.id 即可。
   */
  setFeatureState(featureId, state) {
    if (featureId == null) return;
    this.map.setFeatureState({ source: this.id, id: featureId }, state);
  }

  removeFeatureState(featureId, key) {
    if (featureId == null) return;
    this.map.removeFeatureState({ source: this.id, id: featureId }, key);
  }

  /** 拆掉 layer + source（map switch / 重 init 時用） */
  destroy() {
    if (!this._installed) return;
    for (const spec of this.layerSpecs) {
      if (this.map.getLayer(spec.id)) this.map.removeLayer(spec.id);
    }
    if (this.map.getSource(this.id)) this.map.removeSource(this.id);
    this._installed = false;
    this._lastFeatureCount = 0;
  }

  /** 給 unit test / debug 用 */
  getFeatureCount() {
    return this._lastFeatureCount;
  }

  isInstalled() {
    return this._installed;
  }
}

/** 內部：座標 [lat, lng] → [lng, lat]（MapLibre 順序） */
function _llToLngLat(pair) {
  if (!Array.isArray(pair) || pair.length < 2) return null;
  const lat = Number(pair[0]);
  const lng = Number(pair[1]);
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
  return [lng, lat];
}

/**
 * polygon-shaped object → GeoJSON Polygon Feature。
 * map_config.maps.outdoor.polygons schema：{ id, latlngs: [[lat,lng],...], color, poly_type, label, label_anchor?, dash? }
 *
 * 不合法（< 3 頂點 / 任一座標 NaN）→ 回 null
 */
export function polygonToFeature(poly) {
  if (poly == null || !Array.isArray(poly.latlngs)) return null;
  const ring = poly.latlngs.map(_llToLngLat).filter(Boolean);
  if (ring.length < 3) return null;
  // GeoJSON Polygon 須閉合（首尾相同點）
  const closed = (ring[0][0] === ring[ring.length - 1][0] && ring[0][1] === ring[ring.length - 1][1])
    ? ring : [...ring, ring[0]];
  return {
    type: 'Feature',
    geometry: { type: 'Polygon', coordinates: [closed] },
    properties: {
      id: poly.id ?? null,
      poly_type: poly.poly_type ?? null,
      color: poly.color ?? '#888888',
      label: poly.label ?? '',
      dash: !!poly.dash,
    },
  };
}

/**
 * route-shaped object → GeoJSON LineString Feature。
 * map_config.maps.outdoor.routes schema：{ id, latlngs: [[lat,lng],...], color, route_type, label?, dash? }
 *
 * 不合法（< 2 頂點 / 任一座標 NaN）→ 回 null
 */
export function routeToFeature(route) {
  if (route == null || !Array.isArray(route.latlngs)) return null;
  const coords = route.latlngs.map(_llToLngLat).filter(Boolean);
  if (coords.length < 2) return null;
  return {
    type: 'Feature',
    geometry: { type: 'LineString', coordinates: coords },
    properties: {
      id: route.id ?? null,
      route_type: route.route_type ?? null,
      color: route.color ?? '#58a6ff',
      label: route.label ?? '',
      dash: !!route.dash,
    },
  };
}

/**
 * infra-shaped object → GeoJSON Point Feature。
 * map_config.maps.outdoor.infrastructure schema：{ id, lat, lng, infra_type, label }
 *
 * 步驟 6 用 circle + text symbol 渲染；步驟 7 升級成 SDF icon。
 */
export function infraToFeature(infra) {
  if (infra == null) return null;
  const lat = Number(infra.lat);
  const lng = Number(infra.lng);
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
  return {
    type: 'Feature',
    geometry: { type: 'Point', coordinates: [lng, lat] },
    properties: {
      id: infra.id ?? null,
      infra_type: infra.infra_type ?? 'utility',
      label: infra.label ?? '',
      // color / abbr 由 caller 透過 INFRA_TYPES 對映後寫進來，避免 entity_layer 耦合業務常數
      color: infra.color ?? '#888888',
      abbr: infra.abbr ?? '?',
    },
  };
}

/**
 * flow-shaped object + resolveRef 函式 → GeoJSON LineString Feature。
 * map_config.maps.outdoor.flows schema：{ id, from_ref|from_zone_id, to_ref|to_zone_id, flow_type, color?, label? }
 *
 * caller 必須提供 resolveRef(ref) → { lat, lng, label? } | null
 * （map.js 已有 _resolveRef 可重用；entity_layer 不知道 zones / infrastructure 在哪）
 *
 * @param {object} flow
 * @param {(ref: string | null) => ({lat: number, lng: number, label?: string} | null)} resolveRef
 */
export function flowToFeature(flow, resolveRef) {
  if (flow == null || typeof resolveRef !== 'function') return null;
  const fromRef = flow.from_ref || (flow.from_zone_id ? `zone:${flow.from_zone_id}` : null);
  const toRef   = flow.to_ref   || (flow.to_zone_id   ? `zone:${flow.to_zone_id}`   : null);
  const from = resolveRef(fromRef);
  const to   = resolveRef(toRef);
  if (!from || !to) return null;
  const fLng = Number(from.lng), fLat = Number(from.lat);
  const tLng = Number(to.lng),   tLat = Number(to.lat);
  if (![fLng, fLat, tLng, tLat].every(Number.isFinite)) return null;
  return {
    type: 'Feature',
    geometry: { type: 'LineString', coordinates: [[fLng, fLat], [tLng, tLat]] },
    properties: {
      id: flow.id ?? null,
      flow_type: flow.flow_type ?? null,
      color: flow.color ?? '#888888',
      label: flow.label ?? '',
      from_label: from.label ?? '',
      to_label: to.label ?? '',
    },
  };
}

/**
 * 標準化 helper：把 zone-shaped object（cop_entities 或 map_config.maps.outdoor.zones）
 * 轉成 GeoJSON Feature with ICS_Command 三維度 properties。
 *
 * 對 P2-04 鋪路：source × affiliation × severity 拆三 property，
 * 不像 mini-taiwan 用單一 `system` 同時擔三責。
 *
 * @param {object} zone - { id, lat, lng, source?, affiliation?, severity?, label?, ... }
 * @returns {GeoJSON.Feature | null}  座標缺值回 null
 */
export function zoneToFeature(zone) {
  if (zone == null) return null;
  const lat = Number(zone.lat);
  const lng = Number(zone.lng);
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
  return {
    type: 'Feature',
    geometry: { type: 'Point', coordinates: [lng, lat] },
    properties: {
      id: zone.id ?? zone.uid ?? null,
      source: zone.source ?? 'manual',         // {manual, pi-node, tak, waveink}
      affiliation: zone.affiliation ?? null,   // {friendly, hostile, neutral, unknown}（P2-04 起填）
      severity: zone.severity ?? 'info',       // {info, warning, critical}
      label: zone.label ?? zone.event_code ?? zone.id ?? '',
      node_type: zone.node_type ?? null,
      event_id: zone.event_id ?? null,
      event_code: zone.event_code ?? null,
      icon: zone.icon ?? null,
      priority: zone.priority ?? 0,            // symbol-sort-key 用（步驟 7）
      status: zone.status ?? null,
    },
  };
}
