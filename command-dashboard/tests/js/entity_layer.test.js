/**
 * entity_layer.test.js — P1-10b 步驟 5 EntityLayer 抽象 unit test
 *
 * 走 mock map（避免 jsdom + WebGL），驗證：
 *   - source + layer 安裝順序正確
 *   - update() 把 features 包成 FeatureCollection 並 setData
 *   - zoneToFeature 對齊 source × affiliation × severity 三維度 schema
 *   - setVisible / setFeatureState / destroy lifecycle 正確
 */

import { describe, test, expect, beforeEach } from 'vitest';
import { EntityLayer, zoneToFeature } from '../../static/js/map/entity_layer.js';

/** Mock MapLibre Map：記錄所有 addSource/addLayer/setData 呼叫 */
function makeMockMap() {
  const sources = new Map();
  const layers = new Map();
  const featureStates = new Map();
  const layoutProps = new Map();

  return {
    sources,
    layers,
    featureStates,
    layoutProps,

    addSource(id, spec) {
      sources.set(id, {
        ...spec,
        _data: spec.data,
        setData(data) { this._data = data; },
        serialize() { return { ...spec, data: this._data }; },
      });
    },
    getSource(id) { return sources.get(id) || null; },
    removeSource(id) { sources.delete(id); },

    addLayer(spec) { layers.set(spec.id, spec); },
    getLayer(id) { return layers.get(id) || null; },
    removeLayer(id) { layers.delete(id); },

    setFeatureState({ source, id }, state) {
      const key = `${source}:${id}`;
      featureStates.set(key, { ...(featureStates.get(key) || {}), ...state });
    },
    getFeatureState({ source, id }) { return featureStates.get(`${source}:${id}`) || {}; },
    removeFeatureState({ source, id }, key) {
      const fk = `${source}:${id}`;
      const s = featureStates.get(fk);
      if (s && key) { delete s[key]; }
      else if (s) { featureStates.delete(fk); }
    },

    setLayoutProperty(layerId, prop, val) {
      layoutProps.set(`${layerId}:${prop}`, val);
    },
    getLayoutProperty(layerId, prop) {
      return layoutProps.get(`${layerId}:${prop}`);
    },
  };
}

describe('EntityLayer', () => {
  let map;
  beforeEach(() => { map = makeMockMap(); });

  test('constructor 拒絕缺 map / id / layers', () => {
    expect(() => new EntityLayer(null, 'x', { layers: [{ id: 'l', type: 'circle' }] }))
      .toThrow(/map required/);
    expect(() => new EntityLayer(map, '', { layers: [{ id: 'l', type: 'circle' }] }))
      .toThrow(/id required/);
    expect(() => new EntityLayer(map, 'x', { layers: [] }))
      .toThrow(/至少需要 1 個 layer spec/);
  });

  test('install 建 source + layer，promoteId=id', () => {
    const el = new EntityLayer(map, 'zones', {
      layers: [
        { id: 'zones-base', type: 'circle', paint: { 'circle-radius': 6 } },
        { id: 'zones-halo', type: 'circle', paint: { 'circle-radius': 12 } },
      ],
    });
    expect(el.isInstalled()).toBe(true);
    expect(map.getSource('zones')).toBeTruthy();
    expect(map.getSource('zones').promoteId).toBe('id');
    expect(map.getLayer('zones-base')).toBeTruthy();
    expect(map.getLayer('zones-halo')).toBeTruthy();
    // layer 自動帶 source: 'zones'，caller 不需手填
    expect(map.getLayer('zones-base').source).toBe('zones');
  });

  test('update 包成 FeatureCollection + 計數', () => {
    const el = new EntityLayer(map, 'zones', {
      layers: [{ id: 'zones-base', type: 'circle' }],
    });
    const feats = [
      { type: 'Feature', geometry: { type: 'Point', coordinates: [121, 24] }, properties: { id: 'a' } },
      { type: 'Feature', geometry: { type: 'Point', coordinates: [122, 25] }, properties: { id: 'b' } },
    ];
    el.update(feats);
    expect(el.getFeatureCount()).toBe(2);
    const data = map.getSource('zones').serialize().data;
    expect(data.type).toBe('FeatureCollection');
    expect(data.features).toEqual(feats);
  });

  test('update 拒非 array（包成空 FC）', () => {
    const el = new EntityLayer(map, 'zones', { layers: [{ id: 'zones-base', type: 'circle' }] });
    el.update(null);
    expect(el.getFeatureCount()).toBe(0);
    el.update('not-array');
    expect(el.getFeatureCount()).toBe(0);
  });

  test('clear() 等同 update([])', () => {
    const el = new EntityLayer(map, 'zones', { layers: [{ id: 'zones-base', type: 'circle' }] });
    el.update([{ type: 'Feature', geometry: { type: 'Point', coordinates: [0, 0] }, properties: {} }]);
    expect(el.getFeatureCount()).toBe(1);
    el.clear();
    expect(el.getFeatureCount()).toBe(0);
    expect(map.getSource('zones').serialize().data.features).toEqual([]);
  });

  test('setVisible 切 layer visibility', () => {
    const el = new EntityLayer(map, 'zones', {
      layers: [{ id: 'zones-base', type: 'circle' }, { id: 'zones-halo', type: 'circle' }],
    });
    el.setVisible(false);
    expect(map.getLayoutProperty('zones-base', 'visibility')).toBe('none');
    expect(map.getLayoutProperty('zones-halo', 'visibility')).toBe('none');
    el.setVisible(true);
    expect(map.getLayoutProperty('zones-base', 'visibility')).toBe('visible');
  });

  test('setFeatureState / removeFeatureState 走 promoteId', () => {
    const el = new EntityLayer(map, 'zones', { layers: [{ id: 'zones-base', type: 'circle' }] });
    el.setFeatureState('z1', { hover: true });
    expect(map.getFeatureState({ source: 'zones', id: 'z1' })).toEqual({ hover: true });
    el.setFeatureState('z1', { selected: true });
    expect(map.getFeatureState({ source: 'zones', id: 'z1' })).toEqual({ hover: true, selected: true });
    el.removeFeatureState('z1', 'hover');
    expect(map.getFeatureState({ source: 'zones', id: 'z1' })).toEqual({ selected: true });
    // null id no-op（不炸）
    expect(() => el.setFeatureState(null, { x: 1 })).not.toThrow();
  });

  test('destroy 拆 layer + source，可重複呼叫', () => {
    const el = new EntityLayer(map, 'zones', {
      layers: [{ id: 'zones-base', type: 'circle' }, { id: 'zones-halo', type: 'circle' }],
    });
    el.destroy();
    expect(el.isInstalled()).toBe(false);
    expect(map.getLayer('zones-base')).toBeNull();
    expect(map.getLayer('zones-halo')).toBeNull();
    expect(map.getSource('zones')).toBeNull();
    expect(() => el.destroy()).not.toThrow();
  });

  test('構造後重複 source / layer id 不重複建', () => {
    // mock map 已有同名 source，constructor 應跳過 addSource（避免 MapLibre 報錯）
    map.addSource('zones', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
    const el = new EntityLayer(map, 'zones', { layers: [{ id: 'zones-base', type: 'circle' }] });
    expect(el.isInstalled()).toBe(true);
    expect(map.sources.size).toBe(1);  // 沒被覆蓋
  });
});

describe('zoneToFeature', () => {
  test('合法 zone → Feature with 三維度 properties', () => {
    const f = zoneToFeature({
      id: 'z1', lat: 24.8283, lng: 121.0149,
      source: 'tak', affiliation: 'friendly', severity: 'warning',
      label: 'CP-1', node_type: 'command', event_id: 'e7',
    });
    expect(f).not.toBeNull();
    expect(f.type).toBe('Feature');
    expect(f.geometry).toEqual({ type: 'Point', coordinates: [121.0149, 24.8283] });
    expect(f.properties.id).toBe('z1');
    expect(f.properties.source).toBe('tak');
    expect(f.properties.affiliation).toBe('friendly');
    expect(f.properties.severity).toBe('warning');
    expect(f.properties.label).toBe('CP-1');
    expect(f.properties.node_type).toBe('command');
    expect(f.properties.event_id).toBe('e7');
  });

  test('預設值：source=manual / severity=info / affiliation=null', () => {
    const f = zoneToFeature({ id: 'z2', lat: 24, lng: 121 });
    expect(f.properties.source).toBe('manual');
    expect(f.properties.severity).toBe('info');
    expect(f.properties.affiliation).toBeNull();
  });

  test('label fallback 順序：label → event_code → id → ""', () => {
    expect(zoneToFeature({ id: 'x', lat: 0, lng: 0, label: 'L' }).properties.label).toBe('L');
    expect(zoneToFeature({ id: 'x', lat: 0, lng: 0, event_code: 'EC' }).properties.label).toBe('EC');
    expect(zoneToFeature({ id: 'x', lat: 0, lng: 0 }).properties.label).toBe('x');
    expect(zoneToFeature({ lat: 0, lng: 0 }).properties.label).toBe('');
  });

  test('id fallback：id → uid → null', () => {
    expect(zoneToFeature({ uid: 'cot-u', lat: 0, lng: 0 }).properties.id).toBe('cot-u');
    expect(zoneToFeature({ lat: 0, lng: 0 }).properties.id).toBeNull();
  });

  test('座標無效 → null（NaN / undefined / 非數字字串）', () => {
    expect(zoneToFeature(null)).toBeNull();
    expect(zoneToFeature(undefined)).toBeNull();
    expect(zoneToFeature({})).toBeNull();
    expect(zoneToFeature({ lat: NaN, lng: 0 })).toBeNull();
    expect(zoneToFeature({ lat: 0, lng: NaN })).toBeNull();
    expect(zoneToFeature({ lat: 'abc', lng: 0 })).toBeNull();
  });

  test('priority 預設 0（symbol-sort-key 步驟 7 用）', () => {
    expect(zoneToFeature({ lat: 0, lng: 0 }).properties.priority).toBe(0);
    expect(zoneToFeature({ lat: 0, lng: 0, priority: 99 }).properties.priority).toBe(99);
  });
});
