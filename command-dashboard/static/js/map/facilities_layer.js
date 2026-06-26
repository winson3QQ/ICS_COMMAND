// SPDX-License-Identifier: LicenseRef-Proprietary
// Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
/**
 * facilities_layer.js — P1-17 永久設施公開資料底圖層（issue #88）。
 *
 * **命名**：`facilities` ＝ UI「公共設施」（本檔，固定常駐、開放資料、唯讀）。**不要與
 * 既有 `infra` / `INFRA_TYPES`（UI「設施」，P1-16 演習中手放的臨時設施）搞混**——兩者
 * 中文都可譯「設施」，故 UI 刻意用「公共設施」vs「設施」區分。
 *
 * 唯讀基準參考層：真實醫院/診所、消防、警局、避難收容處所（學校）等永久公共設施。
 * **完全自管 `facilities` source（含 clustering），不碰既有 zones/infra/polygons/routes
 * 層、也不依賴共用 EntityLayer**（與 P1-14(#89)/P1-16 協調紅線）。資料來自
 * GET /api/facilities（台灣政府開放資料，非中國）。
 *
 * 視覺：基準層維持**低飽和**（POLICY：唯一 saturated 色保留給 MIL affiliation + severity），
 * 但提明度/對比讓基準層可讀（dogfood）。樣式 = 彩色圓 + 白色象形 icon / 110·119 數字徽。
 *
 * **Clustering（P1-10f 同技術，scope 限 facilities）**：8000+ 點低 zoom 會糊成一坨，
 * 故 source `cluster:true`（supercluster）—— 低 zoom 顯數字泡泡、放大裂開成個別點，
 * 點 cluster 自動 zoom 展開（業界標準作法）。戰術層點少不需要，故不開。
 *
 * 象形 icon 複用 entity_layer 的通用 `bakeSvgIcon`（純工具，非改共用渲染邏輯）。
 */

import { bakeSvgIcon } from './entity_layer.js';

const EMPTY_FC = { type: 'FeatureCollection', features: [] };
let _clusterHandlersWired = false; // cluster click/cursor handler 一次性綁（防重複）

// 設施型別 → 低飽和但清楚的色。badge：台灣通用緊急電話（警察 110 / 消防 119，最秒懂）；
// 有 badge 顯數字、無 badge 顯象形 icon（shelter 屋 / hospital 十字…）。
export const FACILITY_TYPES = {
  shelter:  { abbr: '避', color: '#4f9e6e', label: '避難收容處所' },
  fire:     { abbr: '消', color: '#c06a44', label: '消防', badge: '119' },
  police:   { abbr: '警', color: '#4f7bc0', label: '警察', badge: '110' },
  hospital: { abbr: '醫', color: '#c25555', label: '醫院' },
  clinic:   { abbr: '診', color: '#9a86c4', label: '診所' },
  _default: { abbr: '設', color: '#8b95a3', label: '設施' },
};

// 無 badge 型別的白色象形 SVG（疊彩色圓上）。警察/消防走 110/119 文字徽，不在此。
export const FACILITY_ICON_SVG = {
  shelter: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="#fff" d="M12 4 4 11h2v9h5v-5h2v5h5v-9h2z"/></svg>',
  hospital: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="#fff" d="M10 4h4v6h6v4h-6v6h-4v-6H4v-4h6z"/></svg>',
  clinic: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="#fff" d="M10.9 5.5h2.2v5.4H18.5v2.2h-5.4V18.5h-2.2v-5.4H5.5v-2.2h5.4z"/></svg>',
  _default: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><circle cx="12" cy="12" r="4" fill="#fff"/></svg>',
};

/** bake 象形 icon 進 map（id = `fac-ico-<type>`）。複用通用 bakeSvgIcon（防重複 + onerror 退回）。*/
export function bakeFacilityIcons(map) {
  if (!map) return Promise.resolve();
  return Promise.all(
    Object.entries(FACILITY_ICON_SVG).map(([type, svg]) =>
      bakeSvgIcon(map, `fac-ico-${type}`, svg, { size: 48 }),
    ),
  );
}

/** 設施物件 → GeoJSON Point Feature。座標缺值回 null。*/
export function facilityToFeature(f, i = 0) {
  if (f == null) return null;
  const lat = Number(f.lat);
  const lng = Number(f.lng);
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
  const def = FACILITY_TYPES[f.type] || FACILITY_TYPES._default;
  return {
    type: 'Feature',
    geometry: { type: 'Point', coordinates: [lng, lat] },
    properties: {
      id: `fac-${i}`,
      ftype: f.type || 'unknown',
      name: f.name || '',
      color: def.color,
      abbr: def.abbr,
      badge: def.badge || '',
    },
  };
}

// cluster 泡泡 + 計數的 layer spec（neutral slate，讓「聚合」看起來不像某類設施）。
export function clusterLayerSpecs() {
  return [
    {
      id: 'facilities-cluster',
      type: 'circle',
      filter: ['has', 'point_count'],
      paint: {
        'circle-color': '#5b6e8c',
        'circle-opacity': 0.82,
        'circle-stroke-width': 1.5,
        'circle-stroke-color': '#e8edf2',
        'circle-stroke-opacity': 0.85,
        // 半徑隨點數分級放大
        'circle-radius': ['step', ['get', 'point_count'], 13, 50, 17, 200, 21, 1000, 27],
      },
    },
    {
      id: 'facilities-cluster-count',
      type: 'symbol',
      filter: ['has', 'point_count'],
      layout: {
        'text-field': ['get', 'point_count_abbreviated'], // "1.2k"
        'text-font': ['Noto Sans Regular'],
        'text-size': 11,
        'text-allow-overlap': true,
        'text-ignore-placement': true,
      },
      paint: { 'text-color': '#ffffff', 'text-halo-color': '#0b0e14', 'text-halo-width': 0.8 },
    },
  ];
}

// 未叢集（個別點）的 layer spec：彩色圓 + 象形 icon（無 badge）+ 110/119 數字徽。
// 每層都 AND `['!',['has','point_count']]` 排除 cluster。
const _notCluster = ['!', ['has', 'point_count']];
export function facilitiesLayerSpecs() {
  return [
    {
      id: 'facilities-circle',
      type: 'circle',
      filter: _notCluster,
      paint: {
        'circle-radius': 8, // 容得下 3 位數字徽（110/119）
        'circle-color': ['get', 'color'],
        'circle-stroke-width': 1.5,
        'circle-stroke-color': '#e8edf2',
        'circle-opacity': 0.85,
        'circle-stroke-opacity': 0.9,
      },
    },
    {
      id: 'facilities-icon',
      type: 'symbol',
      minzoom: 11,
      filter: ['all', _notCluster, ['==', ['get', 'badge'], '']],
      layout: {
        'icon-image': [
          'coalesce',
          ['image', ['concat', 'fac-ico-', ['get', 'ftype']]],
          ['image', 'fac-ico-_default'],
        ],
        'icon-size': 0.58,
        'icon-allow-overlap': true,
        'icon-ignore-placement': true,
      },
    },
    {
      id: 'facilities-badge',
      type: 'symbol',
      minzoom: 11,
      filter: ['all', _notCluster, ['!=', ['get', 'badge'], '']],
      layout: {
        'text-field': ['get', 'badge'],
        'text-font': ['Noto Sans Regular'],
        'text-size': 8.5, // dogfood：110/119 縮小
        'text-anchor': 'center',
        'text-allow-overlap': true,
        'text-ignore-placement': true,
      },
      paint: { 'text-color': '#ffffff', 'text-halo-color': '#0b0e14', 'text-halo-width': 0.6 },
    },
  ];
}

/**
 * FacilitiesLayer — 自管 `facilities` clustered source + 5 個 layer（cluster 圓/計數 +
 * 個別圓/icon/數字徽）。對外介面 update(features) / setVisible(v) 與舊用法相容。
 */
export class FacilitiesLayer {
  static SOURCE_ID = 'facilities';
  static LAYER_IDS = [
    'facilities-cluster', 'facilities-cluster-count',
    'facilities-circle', 'facilities-icon', 'facilities-badge',
  ];

  constructor(map) {
    if (!map) throw new Error('FacilitiesLayer: map required');
    this.map = map;
    this._install();
  }

  _install() {
    const map = this.map;
    if (!map.getSource(FacilitiesLayer.SOURCE_ID)) {
      map.addSource(FacilitiesLayer.SOURCE_ID, {
        type: 'geojson',
        data: EMPTY_FC,
        promoteId: 'id',
        cluster: true,
        clusterRadius: 50,
        clusterMaxZoom: 13, // 此 zoom 以上散成個別點
      });
    }
    for (const spec of [...clusterLayerSpecs(), ...facilitiesLayerSpecs()]) {
      if (!map.getLayer(spec.id)) {
        map.addLayer({ ...spec, source: FacilitiesLayer.SOURCE_ID });
      }
    }
    // 點 cluster → 自動展開 zoom（業界標準 UX）。handler 一次性綁（module guard），避免
    // class 被重建時重複綁（source/layer 有 getX guard，handler 也要對稱）。
    if (!_clusterHandlersWired) {
      _clusterHandlersWired = true;
      map.on('click', 'facilities-cluster', (e) => {
        const feats = map.queryRenderedFeatures(e.point, { layers: ['facilities-cluster'] });
        const clusterId = feats[0]?.properties?.cluster_id;
        if (clusterId == null) return;
        // MapLibre v4：getClusterExpansionZoom 回 Promise（舊版才是 callback）。Promise.resolve
        // 兼容兩者。
        Promise.resolve(map.getSource(FacilitiesLayer.SOURCE_ID).getClusterExpansionZoom(clusterId))
          .then((zoom) => {
            if (zoom == null) return;
            map.easeTo({ center: feats[0].geometry.coordinates, zoom });
          })
          .catch(() => {});
      });
      map.on('mouseenter', 'facilities-cluster', () => { map.getCanvas().style.cursor = 'pointer'; });
      map.on('mouseleave', 'facilities-cluster', () => { map.getCanvas().style.cursor = ''; });
    }
  }

  update(features) {
    const src = this.map.getSource(FacilitiesLayer.SOURCE_ID);
    if (src) src.setData({ type: 'FeatureCollection', features: Array.isArray(features) ? features : [] });
  }

  setVisible(visible) {
    const v = visible ? 'visible' : 'none';
    for (const id of FacilitiesLayer.LAYER_IDS) {
      if (this.map.getLayer(id)) this.map.setLayoutProperty(id, 'visibility', v);
    }
  }
}
