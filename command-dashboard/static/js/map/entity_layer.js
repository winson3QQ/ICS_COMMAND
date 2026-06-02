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

/**
 * SDF icon baking — Canvas → ImageData → map.addImage(id, data, {sdf: true})
 *
 * MapLibre SDF mode 把 image alpha channel 當 mask，用 icon-color 上色。
 * 用 sans-serif system font 渲染 char（browser 本機字體 — 中文需 user 安裝 CJK font）。
 *
 * P1-10b 步驟 7 階段 2：用於 zone abbr 字（收/醫/指/前/安 等）+ event group abbr。
 *
 * @param {maplibregl.Map} map
 * @param {string} idPrefix - addImage 用的 id 前綴（例 'napsg-abbr-'）
 * @param {string[]} chars - 要 bake 的字元清單
 * @param {object} opts - { size: 32, fontSize: 22, weight: '700', font: 'sans-serif' }
 */
export function bakeTextSdf(map, idPrefix, chars, opts = {}) {
  const size = opts.size ?? 32;
  const fontSize = opts.fontSize ?? 22;
  const weight = opts.weight ?? '700';
  const font = opts.font ?? 'sans-serif';
  for (const ch of chars) {
    const id = idPrefix + ch;
    if (map.hasImage?.(id)) continue;
    const canvas = document.createElement('canvas');
    canvas.width = size;
    canvas.height = size;
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, size, size);
    ctx.fillStyle = '#ffffff';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    // 多字元（如 QR / MCI）自動縮字級塞進 canvas，避免裁切（P1-10d 事件 abbr）
    let fs = fontSize;
    ctx.font = `${weight} ${fs}px ${font}`;
    const w = ctx.measureText(ch).width;
    const maxW = size * 0.86;
    if (w > maxW) {
      fs = Math.max(8, Math.floor((fs * maxW) / w));
      ctx.font = `${weight} ${fs}px ${font}`;
    }
    ctx.fillText(ch, size / 2, size / 2);
    map.addImage(id, ctx.getImageData(0, 0, size, size), { sdf: true, pixelRatio: 2 });
  }
}

/**
 * Arrow SDF icon — 三角形，用於 routes/flows 的 symbol-placement: 'line'。
 * 預設指向 12 點鐘方向（MapLibre symbol-on-line 會自動依 line bearing 旋轉）。
 */
export function bakeArrowSdf(map, id, opts = {}) {
  if (map.hasImage?.(id)) return;
  // 預設 14px — 與 route line-width 3 約 4:1 比例（戰術地圖典型 arrow-to-line ratio）。
  // chevron 形狀（>）：**點在右側** — MapLibre symbol-placement: 'line' 將 icon 的
  // positive X 對齊 line forward direction，所以「右指」chevron 才會指向沿線方向。
  // 第一版點在 top 是錯的 — 會橫躺指向 line 法向量（user dogfood 發現）。
  const size = opts.size ?? 14;
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, size, size);
  ctx.strokeStyle = '#ffffff';
  ctx.lineWidth = 2;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  // Chevron（>）：左上 → 右中頂點 → 左下
  ctx.beginPath();
  ctx.moveTo(3, 3);
  ctx.lineTo(size - 2, size / 2);
  ctx.lineTo(3, size - 3);
  ctx.stroke();
  map.addImage(id, ctx.getImageData(0, 0, size, size), { sdf: true });
}

/**
 * Diamond (rhombus) SDF — P1-10d：事件（hazard）的 NAPSG ◆ 形狀。
 * 實心 diamond 當 alpha mask；MapLibre symbol 用 icon-color 填 severity 色、
 * icon-halo-* 給白邊（取代 circle 的 white stroke）。
 */
export function bakeDiamondSdf(map, id, opts = {}) {
  if (map.hasImage?.(id)) return;
  const size = opts.size ?? 44;
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, size, size);
  ctx.fillStyle = '#ffffff';
  const m = size / 2;
  const pad = 4;  // 留白給 icon-halo 白邊
  ctx.beginPath();
  ctx.moveTo(m, pad);          // 上
  ctx.lineTo(size - pad, m);   // 右
  ctx.lineTo(m, size - pad);   // 下
  ctx.lineTo(pad, m);          // 左
  ctx.closePath();
  ctx.fill();
  map.addImage(id, ctx.getImageData(0, 0, size, size), { sdf: true, pixelRatio: 2 });
}

/**
 * SVG → 白色 RGBA icon（**非 SDF**）— P1-10d 事件 NAPSG 象形 glyph。
 *
 * 為何非 SDF：象形固定白色（貼在 severity 色 ◆ 上），不需 icon-color tint；且 SDF shader
 * 以 alpha 當距離場會侵蝕細線（星爆光芒、訊號電波）。直接白色 RGBA 最 crisp、與 pilot 一致。
 * 與共層的 abbr（SDF）混用無礙：icon-color 只作用在 SDF image，對非 SDF glyph 無效。
 *
 * SVG raster 為非同步（Image.onload）→ 回傳 Promise<boolean>（true=新 bake 完成）。
 * caller 於 Promise.all 後重繪一次，讓 _renderZones 重算 fg（glyph 取代 abbr）。
 *
 * @param {maplibregl.Map} map
 * @param {string} id - addImage id（例 'napsg-glyph-explosive'）
 * @param {string} svgStr - 完整 SVG 字串（需含 fill 與 width/height，見 napsg_glyphs.js）
 * @param {object} opts - { size: 48 }（canvas px；pixelRatio 2 → 邏輯 size/2）
 * @returns {Promise<boolean>}
 */
export function bakeSvgIcon(map, id, svgStr, opts = {}) {
  if (!map || !svgStr) return Promise.resolve(false);
  if (map.hasImage?.(id)) return Promise.resolve(false);
  const size = opts.size ?? 48;
  return new Promise((resolve) => {
    const img = document.createElement('img');
    img.onload = () => {
      try {
        if (map.hasImage?.(id)) { resolve(false); return; }  // race：重入時別重複 add
        const canvas = document.createElement('canvas');
        canvas.width = size;
        canvas.height = size;
        const ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, size, size);
        ctx.drawImage(img, 0, 0, size, size);
        map.addImage(id, ctx.getImageData(0, 0, size, size), { pixelRatio: 2 });
        resolve(true);
      } catch {
        resolve(false);
      }
    };
    // onerror 才會被 CSP img-src 不含 data: / SVG 解析失敗觸發 → 退 abbr。
    // 加 warn 讓「靜默退回 abbr」可被 debug（review #75 MED；現行 CSP 已含 data: blob:）。
    img.onerror = () => { console.warn('[entity_layer] NAPSG glyph SVG raster 失敗:', id); resolve(false); };
    img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svgStr);
  });
}

/**
 * 把 zone-shaped object（cop_entities 或 map_config.maps.outdoor.zones）
 * 轉成「節點圖示」用 GeoJSON Point Feature。
 *
 * 不同於 zoneToFeature（一般 entity）：caller 透過 _NODE_COLORS / _SEV_COLORS / _RAG_COLORS
 * 對映後餵 color / abbr 進來，本 helper 不耦合業務常數。
 *
 * 設計（step 7 階段 1）：
 *   - 用 circle layer 渲染（NAPSG 完整 SVG SDF + 形狀變化留階段 2）
 *   - color 為事件 severity > RAG > node_type 三者依優先序 caller 解出
 *   - stale flag 透過 properties.stale 帶（caller 看 link age 決定）
 *   - is_event flag 區分節點 vs 事件 zone（影響 click handler 走向）
 */
export function zoneToNodeFeature(zone, opts = {}) {
  if (zone == null) return null;
  const lat = Number(zone.lat);
  const lng = Number(zone.lng);
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
  return {
    type: 'Feature',
    geometry: { type: 'Point', coordinates: [lng, lat] },
    properties: {
      id: zone.id ?? null,
      node_type: zone.node_type ?? null,
      label: zone.label ?? zone.event_code ?? zone.id ?? '',
      color: opts.color ?? '#8b949e',
      abbr: opts.abbr ?? '?',
      is_event: !!(zone.event_id || zone.event_code),
      is_orphan: !!opts.is_orphan,
      severity: opts.severity ?? 'info',
      stale: !!opts.stale,
      // P1-10d 正式 icon：前景圖示 id（有 NAPSG 象形用 glyph，否則 abbr）+ 是否為 glyph（控 icon-size）。
      fg: opts.fg ?? ('napsg-abbr-' + (opts.abbr ?? '?')),
      fg_glyph: !!opts.fg_glyph,
    },
  };
}

/**
 * 決定事件/節點 marker 的前景圖示（NAPSG 象形 vs abbr 字）。純函式，便於單測。
 * 象形屬「細節層」，僅「有 vendored NAPSG glyph 且已 bake」的事件型別用；其餘退 abbr。
 * @param {{isEvent:boolean, evType:?string, abbr:string, hasGlyph:boolean}} a
 * @returns {{fg:string, fg_glyph:boolean}}
 */
export function pickForeground({ isEvent, evType, abbr, hasGlyph }) {
  if (isEvent && evType && hasGlyph) {
    return { fg: 'napsg-glyph-' + evType, fg_glyph: true };
  }
  return { fg: 'napsg-abbr-' + (abbr ?? '?'), fg_glyph: false };
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
 * 算 polygon 簡單算術中心（用 outer ring 點平均）。
 * 對 convex polygon 接近 visual center；concave 可能落在 polygon 外（接受 trade-off
 * 為簡單性，呼叫端可指定 label_anchor override）。
 * @returns {[number, number] | null} [lng, lat] 或 null
 */
export function polygonCentroid(poly) {
  if (poly == null || !Array.isArray(poly.latlngs)) return null;
  if (Array.isArray(poly.label_anchor) && poly.label_anchor.length === 2) {
    const [lat, lng] = poly.label_anchor.map(Number);
    if (Number.isFinite(lat) && Number.isFinite(lng)) return [lng, lat];
  }
  const valid = poly.latlngs
    .map((p) => (Array.isArray(p) && p.length >= 2 ? [Number(p[0]), Number(p[1])] : null))
    .filter((p) => p && Number.isFinite(p[0]) && Number.isFinite(p[1]));
  if (valid.length === 0) return null;
  const sumLat = valid.reduce((a, [lat]) => a + lat, 0);
  const sumLng = valid.reduce((a, [, lng]) => a + lng, 0);
  return [sumLng / valid.length, sumLat / valid.length];
}

/**
 * polygon 的 label 用 Point Feature 代替（解 MapLibre 對 Polygon symbol-placement:'point'
 * 跨 tile 算多個 centroid 導致 label 重複的 bug）。同 source 加 Point feature，
 * caller layer 用 `['==', ['get', 'kind'], 'label']` filter 取出。
 *
 * ⚠️ 為什麼不用 `['geometry-type']` filter（前一輪寫法）：MapLibre 4.7.1 render
 * pipeline 對該 expression 不穩定（querySourceFeatures 用 OK、symbol layer 渲染
 * 階段卻 cull 掉，dogfood 撞到），改用 explicit `properties.kind` 標籤 + `get`
 * 對映 filter 規避。
 */
export function polygonLabelToFeature(poly) {
  if (poly == null) return null;
  const ctr = polygonCentroid(poly);
  if (!ctr) return null;
  return {
    type: 'Feature',
    geometry: { type: 'Point', coordinates: ctr },
    properties: {
      id: poly.id ?? null,
      color: poly.color ?? '#888888',
      label: poly.label ?? '',
      kind: 'label',
    },
  };
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
 * 算 route 中點（label 預設位置）— 取中間 index 頂點，支援 label_anchor override。
 * @returns {[number, number] | null} [lng, lat] 或 null
 */
export function routeMidLngLat(route) {
  if (route == null) return null;
  if (Array.isArray(route.label_anchor) && route.label_anchor.length === 2) {
    const lat = Number(route.label_anchor[0]);
    const lng = Number(route.label_anchor[1]);
    if (Number.isFinite(lat) && Number.isFinite(lng)) return [lng, lat];
  }
  if (!Array.isArray(route.latlngs) || route.latlngs.length < 2) return null;
  const valid = route.latlngs
    .map((p) => (Array.isArray(p) && p.length >= 2 ? [Number(p[0]), Number(p[1])] : null))
    .filter((p) => p && Number.isFinite(p[0]) && Number.isFinite(p[1]));
  if (valid.length < 2) return null;
  const mid = Math.floor(valid.length / 2);
  const [lat, lng] = valid[mid];
  return [lng, lat];
}

/**
 * route 的 label 用 Point Feature 代替（解 symbol-placement:'line-center' 不認
 * label_anchor 的問題）。同 source 加 Point feature，caller layer 用
 * `['==', ['get', 'kind'], 'label']` filter 取出。
 * （ geometry-type filter 在 MapLibre 4.7.1 render pipeline 有 bug — 見
 *   polygonLabelToFeature 同樣的 rationale ）
 */
export function routeLabelToFeature(route) {
  if (route == null) return null;
  const ll = routeMidLngLat(route);
  if (!ll) return null;
  return {
    type: 'Feature',
    geometry: { type: 'Point', coordinates: ll },
    properties: {
      kind: 'label',
      id: route.id ?? null,
      color: route.color ?? '#58a6ff',
      label: route.label ?? '',
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

// ── cop_entity → route/polygon shape adapter（issue #29 PR-G1a cutover）──────
//
// cutover 後 route/polygon 不再讀 map_config，改從 cop_entities 取（即時同步）。
// 但渲染複用既有 routeToFeature / polygonToFeature —— 它們吃 map_config-shape
// （{ id, latlngs, color, route_type|poly_type, label, dash, label_anchor }）。
// 本 adapter 把 cop_entity 轉成那個 shape，換資料來源不換渲染：
//   - uid               → id（feature.properties.id；click / drag 用它回查 cop entity）
//   - attributes.vertices → latlngs（[[lat,lng],...]；繪製時存進去的頂點）
//   - attributes.color / route_type|poly_type / dash / label_anchor → 同名欄位
//   - callsign          → label（cutover 時 label 存 callsign，對齊 CoT contact）
//
// vertices 缺 / 非陣列 → 回 null（下游 routeToFeature/polygonToFeature 也會再擋一次）。

/**
 * 共用：cop_entity → route/polygon 共通 shape（兩者只差「型別欄位名」與預設色）。
 * @param {string} typeField 'route_type' | 'poly_type'
 * @param {string} defaultColor 缺 color 時的預設
 */
function _copEntityToMapObject(entity, typeField, defaultColor) {
  if (entity == null) return null;
  const attrs = entity.attributes || {};
  if (!Array.isArray(attrs.vertices)) return null;
  return {
    id: entity.uid ?? null,
    latlngs: attrs.vertices,
    color: attrs.color ?? defaultColor,
    [typeField]: attrs[typeField] ?? null,
    label: entity.callsign ?? '',
    dash: !!attrs.dash,
    label_anchor: Array.isArray(attrs.label_anchor) ? attrs.label_anchor : undefined,
  };
}

/** cop_entity（attributes.kind='route'）→ routeToFeature 吃的 route-shape。 */
export function copEntityToRoute(entity) {
  return _copEntityToMapObject(entity, 'route_type', '#58a6ff');
}

/** cop_entity（attributes.kind='polygon'）→ polygonToFeature 吃的 polygon-shape。 */
export function copEntityToPolygon(entity) {
  return _copEntityToMapObject(entity, 'poly_type', '#888888');
}

/**
 * cop_entity（attributes.kind='event'）→ 既有「事件 zone」shape（issue #29 PR-G1b cutover）。
 *
 * 事件位置圖釘從 map_config.zones 搬進 cop_entities，但 render（_renderZones）與所有
 * consumer（showEventProcessModal / findZoneByEventId / _onZoneClick / orphan）仍吃舊 zone
 * shape `{ id, lat, lng, label, node_type, icon, event_id, event_code }`。本 adapter 還原它：
 *   - uid              → id（cop 主鍵；刪除 / 回查用）
 *   - lat / lon        → lat / lng（注意：cop 用 lon，zone 用 lng）
 *   - callsign         → label（事件類型中文名）
 *   - attributes.{event_id, event_code} → 同名（event_id 是與 events 表的連結）
 *   - attributes.event_group（或 back-compat 舊 node_type）→ event_group（事件類別，解撞名 #66 PR-B）
 *   - icon 固定 'event'
 *
 * 缺 event_id（非事件 entity / 資料殘缺）→ 回 null（下游已用 event_id 判斷是否為事件）。
 */
export function copEntityToEventZone(entity) {
  if (entity == null) return null;
  const attrs = entity.attributes || {};
  if (attrs.kind !== 'event' || !attrs.event_id) return null;
  const lat = Number(entity.lat);
  const lng = Number(entity.lon);
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
  return {
    id: entity.uid ?? null,
    lat,
    lng,
    label: entity.callsign ?? '',
    // 解撞名（#66 PR-B）：事件「類別 group」用 event_group，不再借 node_type（ICS 組織單位）。
    // back-compat：舊 cop entity attributes 用 node_type 存 group → fallback 讀回。
    event_group: attrs.event_group ?? attrs.node_type ?? 'ops',
    icon: 'event',
    event_id: attrs.event_id,
    event_code: attrs.event_code ?? null,
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

// PR-H：flowToFeature 已移除（流向功能退役 —— 與 routeToFeature 重疊）。

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
