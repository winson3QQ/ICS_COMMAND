// SPDX-License-Identifier: LicenseRef-Proprietary
// Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
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

import { parseWgs84 } from './coord_tools.js';

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
    this._lastVisible = true;   // P1-10g：layer 安裝時為 visible
    this._hideTimer = null;
    this._visInit = false;      // 首次 setVisible 瞬時（避免載入時先閃再淡出）

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

  /**
   * 切換整層可見性。P1-10g：可見性「真的改變」時做 200ms opacity 淡入/淡出
   * （取代瞬時 visibility 的「啪」一下）。render 每次都呼叫 setVisible，同值則 no-op，
   * 不會每次資料更新都閃。opacity 原值從 layerSpec.paint 取，忠實還原 hover/selected
   * 等 case expression。
   */
  setVisible(visible) {
    visible = !!visible;
    if (!this._visInit) {
      // 首次：瞬時設定，不動畫（避免載入時先前被關的層閃一下再淡出）
      this._visInit = true;
      this._lastVisible = visible;
      const v = visible ? 'visible' : 'none';
      for (const spec of this.layerSpecs) {
        if (this.map.getLayer(spec.id)) this.map.setLayoutProperty(spec.id, 'visibility', v);
      }
      return;
    }
    if (visible === this._lastVisible) return;  // 無變化 → no-op（避免資料更新誤觸發淡入）
    this._lastVisible = visible;
    if (this._hideTimer) { clearTimeout(this._hideTimer); this._hideTimer = null; }
    const DUR = 200;
    const raf = (typeof requestAnimationFrame !== 'undefined')
      ? requestAnimationFrame : (f) => setTimeout(f, 16);
    for (const spec of this.layerSpecs) {
      if (!this.map.getLayer(spec.id)) continue;
      const props = EntityLayer._opacityProps(spec);
      for (const { prop } of props) {
        try { this.map.setPaintProperty(spec.id, `${prop}-transition`, { duration: DUR }); } catch (e) { /* 該 prop 不存在則略 */ }
      }
      if (visible) {
        this.map.setLayoutProperty(spec.id, 'visibility', 'visible');
        for (const { prop } of props) this.map.setPaintProperty(spec.id, prop, 0);  // 起點 0
        raf(() => {
          if (!this._lastVisible) return;  // 淡入途中又被關掉
          for (const { prop, orig } of props) this.map.setPaintProperty(spec.id, prop, orig);  // → 還原值（transition 內插）
        });
      } else {
        for (const { prop } of props) this.map.setPaintProperty(spec.id, prop, 0);  // 淡出到 0
      }
    }
    if (!visible) {
      this._hideTimer = setTimeout(() => {
        this._hideTimer = null;
        if (this._lastVisible) return;  // 淡出途中又被打開
        for (const spec of this.layerSpecs) {
          if (!this.map.getLayer(spec.id)) continue;
          this.map.setLayoutProperty(spec.id, 'visibility', 'none');
          for (const { prop, orig } of EntityLayer._opacityProps(spec)) {
            this.map.setPaintProperty(spec.id, prop, orig);  // 還原 opacity 供下次顯示
          }
        }
      }, DUR);
    }
  }

  /** 各 layer type 對應的 opacity paint property + 原值（從 spec.paint 取，缺省 1）。 */
  static _opacityProps(spec) {
    const def = (p) => (spec.paint && spec.paint[p] !== undefined) ? spec.paint[p] : 1;
    switch (spec.type) {
      case 'fill': return [{ prop: 'fill-opacity', orig: def('fill-opacity') }];
      case 'line': return [{ prop: 'line-opacity', orig: def('line-opacity') }];
      case 'circle': return [{ prop: 'circle-opacity', orig: def('circle-opacity') }];
      case 'symbol': return [
        { prop: 'icon-opacity', orig: def('icon-opacity') },
        { prop: 'text-opacity', orig: def('text-opacity') },
      ];
      default: return [];
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
  ctx.lineWidth = 2.5;  // route 線細化後加粗箭頭 chevron（user 回饋）
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
 * Triangle (▲) SDF — 乙-2a（#243）：regime='alert' 公眾警報的 NAPSG ▲ 形狀。
 * 與 diamond 同為實心 alpha mask（icon-color 填 severity 色、外框走下層墊大三角）。
 * **形心置中**：頂點朝上的三角形心在 1/3 高處，故依 centroid=(m,m) 反推頂點，確保 icon
 * 錨在格心時 ▲ 視覺壓在點上（非偏移）。zones-event(-outline) 層 icon-image 依 regime 切換。
 */
export function bakeTriangleSdf(map, id, opts = {}) {
  if (map.hasImage?.(id)) return;
  const size = opts.size ?? 44;
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, size, size);
  ctx.fillStyle = '#ffffff';
  const m = size / 2;
  const pad = 2;
  // **等邊三角形**（base = 2w、height = √3·w，比例 0.866，非先前 squat 的 0.67）+ **形心置中**：
  // 依 centroid=(m,m) 反推 apexY/baseY，等邊取最大內接（留 pad），確保壓在點上且比例正確。
  const w = 0.866 * (m - pad);     // 底邊半寬
  const h = 1.732 * w;             // 高 = √3·半寬
  const apexY = m - (2 * h) / 3;   // 頂點（形心上方 2/3 高）
  const baseY = m + h / 3;         // 底邊（形心下方 1/3 高）
  ctx.beginPath();
  ctx.moveTo(m, apexY);           // 上頂點
  ctx.lineTo(m + w, baseY);       // 右下
  ctx.lineTo(m - w, baseY);       // 左下
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
 * MIL-STD-2525 frame bake（P2-05/#110）— milsymbol SIDC → 全彩 RGBA icon → addImage。
 * id = 'mil-' + sidc。milsymbol SVG 已含 affiliation 框色（friend 青框矩形 / hostile
 * 紅菱 / neutral 綠方 / unknown 黃四葉）→ 走 bakeSvgIcon（**非 SDF**，色彩語意由 SIDC
 * 內建，不用 icon-color tint）。window.ms 未載 → resolve(false)（caller fallback）。
 *
 * @param {maplibregl.Map} map
 * @param {string} sidc - 2525C SIDC（mil_symbol.cotToSidc 產出）
 * @param {object} opts - { symbolSize:30（milsymbol 邏輯尺寸）, size:48（bake canvas px）}
 * @returns {Promise<boolean>} true=新 bake 完成
 */
export function bakeMilSymbol(map, sidc, opts = {}) {
  if (!map || !sidc) return Promise.resolve(false);
  const ms = typeof window !== 'undefined' ? window.ms : null;
  if (!ms || !ms.Symbol) return Promise.resolve(false);
  const id = 'mil-' + sidc;
  if (map.hasImage?.(id)) return Promise.resolve(false);
  let svg;
  try {
    const sym = new ms.Symbol(sidc, { size: opts.symbolSize ?? 30 });
    if (!sym.isValid(false)) return Promise.resolve(false);
    svg = sym.asSVG();
  } catch {
    return Promise.resolve(false);
  }
  return bakeSvgIcon(map, id, svg, { size: opts.size ?? 48 });
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
      // 乙-2a（#243）：視覺規制（civil/alert/military），驅動 zones-event 層外框形狀
      // （alert→▲、其餘→◆）。缺省 civil（◆）。
      regime: opts.regime ?? 'civil',
      // 乙-2b（#243）：regime='military' 時的 milsymbol 2525 框 icon id（'mil-<SIDC>'）。
      // zones-event 層 icon-image 對 military 吃此值；非 military / 烤不出 → null（走 ◆/▲）。
      iconId: opts.iconId ?? null,
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
      dotted: !!poly.dotted,   // P2-10：dotted 筆觸（小圓點）
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
      dotted: !!route.dotted,   // P2-10：dotted 筆觸（小圓點）
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
    dotted: !!attrs.dotted,   // P2-10：TAK dotted 筆觸（小圓點），與 dash 互斥
    label_anchor: Array.isArray(attrs.label_anchor) ? attrs.label_anchor : undefined,
  };
}

/** cop_entity（attributes.kind='route'）→ routeToFeature 吃的 route-shape。 */
export function copEntityToRoute(entity) {
  const obj = _copEntityToMapObject(entity, 'route_type', '#58a6ff');
  // #260 C1：route 導航屬性（attributes.link_attr：method/routetype/direction…）暴露供詳情顯示
  // （polygon 無此軸，故只在 route builder 帶）。值來自 TAK detail，顯示端須 escape。
  const la = entity?.attributes?.link_attr;
  if (obj && la && typeof la === 'object' && !Array.isArray(la)) obj.route_attr = la;
  // #260 C2：命名 waypoint（attributes.link 的 b-m-p-w）掛上 shape，供 _renderRoutes 畫 marker+label。
  if (obj) {
    const wps = extractRouteWaypoints(entity);
    if (wps.length) obj.waypoints = wps;
  }
  return obj;
}

/**
 * #260 C2：從 cop_entity 的 attributes.link 萃取「命名 waypoint」。
 *
 * TAK route 的 <link> 序列（真機實證，4 樣本一致）：
 *   - type='b-m-p-w' + 非空 callsign → 命名 waypoint（SP/CP1/TGT…），畫 marker+label
 *   - type='b-m-p-c'（callsign 空）  → control point，只塑線、不畫 marker（已在 vertices）
 * point 格式 "lat,lon,hae"（逗號三段，hae 可為 0.0）→ 轉 [lng,lat]。
 * 純 ICS 自建 route 無 link → 回 []（不顯示 waypoint，符合 scope；命名 = #260 D 編輯 UI）。
 *
 * @returns {Array<{uid: string|null, name: string, lngLat: [number, number]}>}
 */
export function extractRouteWaypoints(entity) {
  let links = entity?.attributes?.link;
  if (links && !Array.isArray(links)) links = [links];  // 單一 <link>（dict）→ 包成 array（防禦）
  if (!Array.isArray(links)) return [];
  const out = [];
  for (const lk of links) {
    if (!lk || lk.type !== 'b-m-p-w') continue;         // 只取 waypoint，跳過 control point
    const name = String(lk.callsign ?? '').trim();
    if (!name) continue;                                 // 未命名 → 不畫
    // point "lat,lon,hae" → parseWgs84 取前兩數（忽略 hae）+ WGS84 邊界驗證（擋畫到地球外的髒座標）
    const c = parseWgs84(lk.point);
    if (!c) continue;
    out.push({ uid: lk.uid ?? null, name, lngLat: [c.lng, c.lat] });
  }
  return out;
}

/**
 * route-shape.waypoints → GeoJSON Point Feature 陣列（kind='waypoint'）。
 * 併進既有 'routes' source（對齊 kind='label' 樣板）；刻意不設 properties.id，
 * 避免撞到 route LineString 的 feature-state（source promoteId='id'）。
 * 座標非法 → 略過該點。
 */
export function routeWaypointsToFeatures(route) {
  if (route == null || !Array.isArray(route.waypoints)) return [];
  const out = [];
  for (const w of route.waypoints) {
    const ll = w?.lngLat;
    if (!Array.isArray(ll) || !Number.isFinite(ll[0]) || !Number.isFinite(ll[1])) continue;
    out.push({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [ll[0], ll[1]] },
      properties: {
        kind: 'waypoint',
        label: w.name ?? '',
        color: route.color ?? '#58a6ff',
        route_id: route.id ?? null,
      },
    });
  }
  return out;
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
 *   - event_id（**頂層**，P2-33b：來源 = `event_markers` junction 權威，非 attributes glue）
 *   - attributes.event_code → event_code（event_id 是與 events 表的連結）
 *   - attributes.event_group（或 back-compat 舊 node_type）→ event_group（事件類別，解撞名 #66 PR-B）
 *   - icon 固定 'event'
 *
 * 缺 event_id（非事件 entity / 資料殘缺）→ 回 null（下游已用 event_id 判斷是否為事件）。
 */
export function copEntityToEventZone(entity) {
  if (entity == null) return null;
  const attrs = entity.attributes || {};
  // P2-33b：event_id 改吃**頂層**（junction 權威），不再讀 attributes.event_id glue。
  // 甲-1b（#240 刀0）：kind 'event'→'sighting' 降級。**同時認兩者**（m027 遷移前後皆不掉視覺；
  // 'event'=舊/未遷、'sighting'=新建/已遷）。判事件性靠 event_id（junction），非 kind 字串。
  if ((attrs.kind !== 'event' && attrs.kind !== 'sighting') || !entity.event_id) return null;
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
    event_id: entity.event_id,
    event_code: attrs.event_code ?? null,
    // 甲-1（#240 刀0）：marker 自帶觀察型別 → render 依此推 regime/abbr（不再查 linked event 借）。
    // 舊 marker 無此欄 → null，render fallback 查 event（back-compat）。
    event_type: attrs.event_type ?? null,
  };
}

/**
 * cop_entity（attributes.kind='zone'）→ 既有「節點 zone」shape（P1-16 on-demand 節點放置）。
 *
 * 5 個 ICS 編組節點（收容/醫療/指揮/前進/安全）改成 on-demand：admin 從工具列放置，
 * 存成 cop_entity（attributes.kind='zone'）。render（zoneToNodeFeature）與所有 consumer
 * （showZoneDetail / _onZoneClick / _highlightEvent）仍吃舊 node-zone shape：
 *   { id, lat, lng, label, node_type, icon }
 *
 *   - uid              → id（cop 主鍵；刪除 / 回查用）
 *   - lat / lon        → lat / lng（注意：cop 用 lon，zone 用 lng）
 *   - callsign         → label（節點中文名）
 *   - attributes.node_type → node_type（ICS 組織單位，缺則預設 'command'）
 *   - icon 固定 'pin'
 *
 * guard：attributes.kind 非 'zone' → 回 null；lat/lon 非有限數 → 回 null。
 */
export function copEntityToZone(entity) {
  if (entity == null) return null;
  const attrs = entity.attributes || {};
  if (attrs.kind !== 'zone') return null;
  const lat = Number(entity.lat);
  const lng = Number(entity.lon);
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
  return {
    id: entity.uid ?? null,
    lat,
    lng,
    label: entity.callsign ?? '',
    node_type: attrs.node_type ?? 'command',
    icon: 'pin',
  };
}

/**
 * cop_entity（attributes.kind='infra'）→ 既有「設施 infra」shape（P1-16 on-demand 設施放置）。
 *
 * 5 類設施（醫院/收容/警局/消防/維生）改成 on-demand：admin 從工具列/圖層面板放置，
 * 存成 cop_entity（attributes.kind='infra'）。render（infraToFeature）吃舊 infra shape：
 *   { id, lat, lng, infra_type, label }
 *
 *   - uid                  → id（cop 主鍵；刪除 / 回查用）
 *   - lat / lon            → lat / lng（注意：cop 用 lon，infra 用 lng）
 *   - callsign             → label（設施中文名）
 *   - attributes.infra_type → infra_type（設施類型，缺則預設 'utility'）
 *
 * color / abbr 由 caller（_renderInfra）用 INFRA_TYPES 補上（infraToFeature 已是此設計）。
 *
 * guard：attributes.kind 非 'infra' → 回 null；lat/lon 非有限數 → 回 null。
 */
export function copEntityToInfra(entity) {
  if (entity == null) return null;
  const attrs = entity.attributes || {};
  if (attrs.kind !== 'infra') return null;
  const lat = Number(entity.lat);
  const lng = Number(entity.lon);
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
  return {
    id: entity.uid ?? null,
    lat,
    lng,
    infra_type: attrs.infra_type ?? 'utility',
    label: entity.callsign ?? '',
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
