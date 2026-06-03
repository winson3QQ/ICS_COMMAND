/**
 * facilities_layer.test.js — P1-17 永久設施基準層（issue #88）。
 * 測 facilityToFeature 轉換 + facilitiesLayerSpecs 樣式契約（desaturated / 避讓 doctrine）。
 */
import { describe, test, expect } from 'vitest';
import {
  FACILITY_TYPES,
  FACILITY_ICON_SVG,
  FacilitiesLayer,
  facilityToFeature,
  facilitiesLayerSpecs,
  clusterLayerSpecs,
} from '../../static/js/map/facilities_layer.js';

describe('facilityToFeature (P1-17)', () => {
  test('有效設施 → Point feature，帶 type 對映的 color/abbr 與穩定 id', () => {
    const f = facilityToFeature({ type: 'shelter', name: '五峰活動中心', lat: 24.386, lng: 121.073 }, 3);
    expect(f.type).toBe('Feature');
    expect(f.geometry).toEqual({ type: 'Point', coordinates: [121.073, 24.386] });
    expect(f.properties.id).toBe('fac-3');               // promoteId 用
    expect(f.properties.ftype).toBe('shelter');
    expect(f.properties.color).toBe(FACILITY_TYPES.shelter.color);
    expect(f.properties.abbr).toBe('避');
    expect(f.properties.name).toBe('五峰活動中心');
    expect(f.properties.badge).toBe('');                 // shelter 無數字徽 → 顯象形
  });

  test('警察/消防帶 110/119 數字徽', () => {
    expect(facilityToFeature({ type: 'police', name: 'X', lat: 24, lng: 121 }).properties.badge).toBe('110');
    expect(facilityToFeature({ type: 'fire', name: 'X', lat: 24, lng: 121 }).properties.badge).toBe('119');
  });

  test('null / 座標無效 → null（防 NaN 進 source）', () => {
    expect(facilityToFeature(null)).toBeNull();
    expect(facilityToFeature({ type: 'fire', name: 'x', lat: 'abc', lng: 121 })).toBeNull();
    expect(facilityToFeature({ type: 'fire', name: 'x', lat: 24 })).toBeNull();
  });

  test('未知 type → _default 低飽和色 + 「設」', () => {
    const f = facilityToFeature({ type: 'zzz', name: 'Y', lat: 24, lng: 121 }, 0);
    expect(f.properties.color).toBe(FACILITY_TYPES._default.color);
    expect(f.properties.abbr).toBe('設');
    expect(f.properties.ftype).toBe('zzz');
  });

  test('四類設施都有 desaturated 色與中文縮寫', () => {
    for (const k of ['shelter', 'fire', 'police', 'hospital', 'clinic']) {
      expect(FACILITY_TYPES[k].color).toMatch(/^#[0-9a-f]{6}$/i);
      expect(FACILITY_TYPES[k].abbr).toHaveLength(1);
    }
  });
});

describe('facilitiesLayerSpecs (個別點層，排除 cluster)', () => {
  test('彩色圓 + 象形 icon（無 badge）+ 數字徽（110/119），皆排除 cluster', () => {
    const specs = facilitiesLayerSpecs();
    const circle = specs.find((s) => s.id === 'facilities-circle');
    const icon = specs.find((s) => s.id === 'facilities-icon');
    const badge = specs.find((s) => s.id === 'facilities-badge');
    expect(circle.paint['circle-color']).toEqual(['get', 'color']);
    expect(circle.filter).toEqual(['!', ['has', 'point_count']]);            // 不畫 cluster
    // icon：無 badge + 非 cluster
    expect(icon.filter).toEqual(['all', ['!', ['has', 'point_count']], ['==', ['get', 'badge'], '']]);
    expect(JSON.stringify(icon.layout['icon-image'])).toContain('fac-ico-');
    // badge：有 badge + 非 cluster
    expect(badge.filter).toEqual(['all', ['!', ['has', 'point_count']], ['!=', ['get', 'badge'], '']]);
    expect(badge.layout['text-field']).toEqual(['get', 'badge']);
  });
});

describe('clusterLayerSpecs (P1-10f 技術，scope facilities)', () => {
  test('cluster 圓 + 計數，皆 filter has point_count', () => {
    const specs = clusterLayerSpecs();
    const bubble = specs.find((s) => s.id === 'facilities-cluster');
    const count = specs.find((s) => s.id === 'facilities-cluster-count');
    expect(bubble.filter).toEqual(['has', 'point_count']);
    expect(count.filter).toEqual(['has', 'point_count']);
    expect(count.layout['text-field']).toEqual(['get', 'point_count_abbreviated']);
    // 半徑隨點數分級（step expression）
    expect(bubble.paint['circle-radius'][0]).toBe('step');
  });
});

describe('FacilitiesLayer (自管 clustered source)', () => {
  function fakeMap() {
    const sources = {}, layers = {};
    return {
      sources, layers,
      getSource: (id) => sources[id],
      addSource: (id, def) => { sources[id] = def; },
      getLayer: (id) => layers[id],
      addLayer: (spec) => { layers[spec.id] = spec; },
      on: () => {},
      getCanvas: () => ({ style: {} }),
    };
  }
  test('建構 → source cluster:true + 5 個 layer', () => {
    const map = fakeMap();
    new FacilitiesLayer(map);
    expect(map.sources.facilities.cluster).toBe(true);
    expect(map.sources.facilities.promoteId).toBe('id');
    for (const id of FacilitiesLayer.LAYER_IDS) {
      expect(map.layers[id]).toBeTruthy();
      expect(map.layers[id].source).toBe('facilities');
    }
  });
  test('update(features) → setData FeatureCollection', () => {
    const map = fakeMap();
    let set = null;
    const fl = new FacilitiesLayer(map);
    map.sources.facilities.setData = (fc) => { set = fc; };
    fl.update([{ type: 'Feature' }]);
    expect(set.type).toBe('FeatureCollection');
    expect(set.features).toHaveLength(1);
  });
});

describe('FACILITY_ICON_SVG (P1-17 象形)', () => {
  test('無 badge 型別有白色象形 SVG（警察/消防走數字徽不在此）', () => {
    for (const k of ['shelter', 'hospital', 'clinic', '_default']) {
      expect(FACILITY_ICON_SVG[k]).toMatch(/^<svg/);
      expect(FACILITY_ICON_SVG[k]).toContain('#fff');
    }
    expect(FACILITY_ICON_SVG.police).toBeUndefined(); // 警察走 110
    expect(FACILITY_ICON_SVG.fire).toBeUndefined();   // 消防走 119
  });
});
