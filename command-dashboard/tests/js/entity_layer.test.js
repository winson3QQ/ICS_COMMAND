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
import {
  EntityLayer,
  zoneToFeature,
  zoneToNodeFeature,
  polygonToFeature,
  polygonLabelToFeature,
  routeToFeature,
  routeLabelToFeature,
  infraToFeature,
  copEntityToRoute,
  copEntityToPolygon,
  copEntityToEventZone,
  copEntityToZone,
  copEntityToInfra,
} from '../../static/js/map/entity_layer.js';

/** Mock MapLibre Map：記錄所有 addSource/addLayer/setData 呼叫 */
function makeMockMap() {
  const sources = new Map();
  const layers = new Map();
  const featureStates = new Map();
  const layoutProps = new Map();
  const paintProps = new Map();

  return {
    sources,
    layers,
    featureStates,
    layoutProps,
    paintProps,

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
    setPaintProperty(layerId, prop, val) {
      paintProps.set(`${layerId}:${prop}`, val);
    },
    getPaintProperty(layerId, prop) {
      return paintProps.get(`${layerId}:${prop}`);
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

  test('setVisible 首次瞬時、之後 opacity 淡入淡出 + 同值 no-op (P1-10g)', () => {
    const el = new EntityLayer(map, 'polygons', {
      layers: [{ id: 'p-fill', type: 'fill', paint: { 'fill-opacity': 0.12 } }],
    });
    // 首次：瞬時，不設 opacity transition（避免載入時閃）
    el.setVisible(true);
    expect(map.getLayoutProperty('p-fill', 'visibility')).toBe('visible');
    expect(map.getPaintProperty('p-fill', 'fill-opacity-transition')).toBeUndefined();
    // 真正改變（visible→hidden）→ 設 200ms transition + opacity 走 0（淡出）
    el.setVisible(false);
    expect(map.getPaintProperty('p-fill', 'fill-opacity-transition')).toEqual({ duration: 200 });
    expect(map.getPaintProperty('p-fill', 'fill-opacity')).toBe(0);
    // 同值再呼叫 → no-op（不炸）
    expect(() => el.setVisible(false)).not.toThrow();
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

describe('polygonToFeature', () => {
  test('合法 polygon → 閉合 Polygon Feature', () => {
    const f = polygonToFeature({
      id: 'p1', poly_type: 'control', color: '#e05555', dash: true,
      latlngs: [[24.825, 121.012], [24.832, 121.012], [24.832, 121.020], [24.825, 121.020]],
    });
    expect(f).not.toBeNull();
    expect(f.type).toBe('Feature');
    expect(f.geometry.type).toBe('Polygon');
    // 自動閉合（首尾相同）
    expect(f.geometry.coordinates[0][0]).toEqual(f.geometry.coordinates[0][4]);
    expect(f.geometry.coordinates[0]).toHaveLength(5);
    // 座標順序 [lng, lat]（MapLibre 順序）
    expect(f.geometry.coordinates[0][0]).toEqual([121.012, 24.825]);
    expect(f.properties.color).toBe('#e05555');
    expect(f.properties.dash).toBe(true);
    expect(f.properties.poly_type).toBe('control');
  });

  test('已閉合 polygon 不重複加點', () => {
    const f = polygonToFeature({
      latlngs: [[0, 0], [1, 0], [1, 1], [0, 0]],
    });
    expect(f.geometry.coordinates[0]).toHaveLength(4);
  });

  test('< 3 頂點 / 無 latlngs / null → null', () => {
    expect(polygonToFeature(null)).toBeNull();
    expect(polygonToFeature({})).toBeNull();
    expect(polygonToFeature({ latlngs: [[0, 0], [1, 1]] })).toBeNull();
    expect(polygonToFeature({ latlngs: 'not-array' })).toBeNull();
  });

  test('NaN 座標頂點被濾掉，剩下 < 3 → null', () => {
    expect(polygonToFeature({ latlngs: [[NaN, 0], [1, 0], [1, 1]] })).toBeNull();
  });

  test('預設 color = #888888（防 paint expression 抓不到）', () => {
    const f = polygonToFeature({ latlngs: [[0, 0], [1, 0], [1, 1]] });
    expect(f.properties.color).toBe('#888888');
    expect(f.properties.dash).toBe(false);
  });
});

describe('routeToFeature', () => {
  test('合法 route → LineString Feature，座標 [lng, lat]', () => {
    const f = routeToFeature({
      id: 'r1', route_type: 'primary', color: '#56d364',
      latlngs: [[24.826, 121.014], [24.829, 121.016], [24.831, 121.019]],
    });
    expect(f).not.toBeNull();
    expect(f.geometry.type).toBe('LineString');
    expect(f.geometry.coordinates).toHaveLength(3);
    expect(f.geometry.coordinates[0]).toEqual([121.014, 24.826]);
    expect(f.properties.route_type).toBe('primary');
  });

  test('< 2 頂點 → null', () => {
    expect(routeToFeature(null)).toBeNull();
    expect(routeToFeature({ latlngs: [[0, 0]] })).toBeNull();
  });

  test('預設 color = #58a6ff', () => {
    const f = routeToFeature({ latlngs: [[0, 0], [1, 1]] });
    expect(f.properties.color).toBe('#58a6ff');
  });
});

describe('infraToFeature', () => {
  test('合法 infra → Point Feature with color/abbr', () => {
    const f = infraToFeature({ id: 'i1', lat: 24.828, lng: 121.015, infra_type: 'hospital', label: '醫院', color: '#e05555', abbr: 'H' });
    expect(f).not.toBeNull();
    expect(f.geometry).toEqual({ type: 'Point', coordinates: [121.015, 24.828] });
    expect(f.properties.color).toBe('#e05555');
    expect(f.properties.abbr).toBe('H');
    expect(f.properties.infra_type).toBe('hospital');
  });

  test('座標無效 → null', () => {
    expect(infraToFeature(null)).toBeNull();
    expect(infraToFeature({ lat: NaN, lng: 0 })).toBeNull();
  });

  test('預設值：infra_type=utility / color=#888 / abbr=?', () => {
    const f = infraToFeature({ lat: 0, lng: 0 });
    expect(f.properties.infra_type).toBe('utility');
    expect(f.properties.color).toBe('#888888');
    expect(f.properties.abbr).toBe('?');
  });
});

describe('zoneToNodeFeature', () => {
  test('合法 zone → Point with color/abbr/is_event/stale opts', () => {
    const f = zoneToNodeFeature(
      { id: 'z1', lat: 24.82, lng: 121.01, node_type: 'shelter', label: '收容組' },
      { color: '#f0883e', abbr: '收', severity: 'info', stale: false }
    );
    expect(f).not.toBeNull();
    expect(f.geometry.type).toBe('Point');
    expect(f.geometry.coordinates).toEqual([121.01, 24.82]);
    expect(f.properties.id).toBe('z1');
    expect(f.properties.node_type).toBe('shelter');
    expect(f.properties.color).toBe('#f0883e');
    expect(f.properties.abbr).toBe('收');
    expect(f.properties.is_event).toBe(false);
    expect(f.properties.stale).toBe(false);
    expect(f.properties.severity).toBe('info');
  });

  test('event zone → is_event=true', () => {
    const f = zoneToNodeFeature(
      { id: 'ze', lat: 24.82, lng: 121.01, event_id: 'e7', event_code: 'TEST' },
      { color: '#e05555', severity: 'critical' }
    );
    expect(f.properties.is_event).toBe(true);
    expect(f.properties.severity).toBe('critical');
  });

  test('opts.stale 為 truthy → properties.stale=true（boolean coerce）', () => {
    expect(zoneToNodeFeature({ lat: 0, lng: 0 }, { stale: 1 }).properties.stale).toBe(true);
    expect(zoneToNodeFeature({ lat: 0, lng: 0 }, { stale: 0 }).properties.stale).toBe(false);
    expect(zoneToNodeFeature({ lat: 0, lng: 0 }, {}).properties.stale).toBe(false);
  });

  test('座標無效 → null', () => {
    expect(zoneToNodeFeature(null, {})).toBeNull();
    expect(zoneToNodeFeature({}, {})).toBeNull();
    expect(zoneToNodeFeature({ lat: NaN, lng: 0 }, {})).toBeNull();
  });

  test('預設值：color=#888 / abbr=? / severity=info / regime=civil', () => {
    const f = zoneToNodeFeature({ lat: 0, lng: 0 });
    expect(f.properties.color).toBe('#8b949e');
    expect(f.properties.abbr).toBe('?');
    expect(f.properties.severity).toBe('info');
    expect(f.properties.regime).toBe('civil');  // 乙-2a（#243）：缺省 civil（◆）
  });

  test('乙-2a（#243）：opts.regime 帶上 properties（驅動 ◆/▲ 外框）', () => {
    expect(zoneToNodeFeature({ lat: 0, lng: 0 }, { regime: 'alert' }).properties.regime).toBe('alert');
    expect(zoneToNodeFeature({ lat: 0, lng: 0 }, { regime: 'military' }).properties.regime).toBe('military');
    expect(zoneToNodeFeature({ lat: 0, lng: 0 }, {}).properties.regime).toBe('civil');
  });

  test('乙-2b（#243）：opts.iconId 帶上 properties（military milsymbol 2525 框 id）', () => {
    expect(zoneToNodeFeature({ lat: 0, lng: 0 }, { iconId: 'mil-SHAP-----------' }).properties.iconId).toBe('mil-SHAP-----------');
    expect(zoneToNodeFeature({ lat: 0, lng: 0 }, {}).properties.iconId).toBeNull();  // 非 military → null（走 ◆/▲）
  });

  test('label fallback：label → event_code → id → ""', () => {
    expect(zoneToNodeFeature({ lat: 0, lng: 0, label: 'L' }).properties.label).toBe('L');
    expect(zoneToNodeFeature({ lat: 0, lng: 0, event_code: 'EC' }).properties.label).toBe('EC');
    expect(zoneToNodeFeature({ lat: 0, lng: 0, id: 'x' }).properties.label).toBe('x');
    expect(zoneToNodeFeature({ lat: 0, lng: 0 }).properties.label).toBe('');
  });
});


// MapLibre 4.7.1 render pipeline 對 ['geometry-type'] expression 在 symbol layer
// 內 cull 掉所有 features（dogfood 發現）。Label feature 必須帶 properties.kind='label'
// 才能讓 layer 用 ['==', ['get', 'kind'], 'label'] filter 通過，rendering 才正常。
// 本 group lock 這個 contract — 之後 refactor 不會誤刪 kind property。
describe('polygonLabelToFeature / routeLabelToFeature kind="label" 標記', () => {
  test('polygonLabelToFeature 回傳 feature 帶 kind="label"', () => {
    const f = polygonLabelToFeature({
      id: 'p1', label: 'A', color: '#fff',
      latlngs: [[24, 121], [24, 122], [25, 121.5]],
    });
    expect(f).not.toBeNull();
    expect(f.properties.kind).toBe('label');
    expect(f.properties.label).toBe('A');
    expect(f.geometry.type).toBe('Point');
  });

  test('routeLabelToFeature 回傳 feature 帶 kind="label"', () => {
    const f = routeLabelToFeature({
      id: 'r1', label: 'B', color: '#0f0',
      latlngs: [[24, 121], [25, 122]],
    });
    expect(f).not.toBeNull();
    expect(f.properties.kind).toBe('label');
    expect(f.properties.label).toBe('B');
    expect(f.geometry.type).toBe('Point');
  });

  test('polygonToFeature (本體 Polygon) 不帶 kind property（避免與 label feature 混）', () => {
    const f = polygonToFeature({
      id: 'p1', label: 'A', color: '#fff',
      latlngs: [[24, 121], [24, 122], [25, 121.5]],
    });
    expect(f.properties.kind).toBeUndefined();
  });

  test('routeToFeature (本體 LineString) 不帶 kind property', () => {
    const f = routeToFeature({
      id: 'r1', label: 'B', color: '#0f0',
      latlngs: [[24, 121], [25, 122]],
    });
    expect(f.properties.kind).toBeUndefined();
  });
});

describe('copEntityToRoute / copEntityToPolygon adapter（PR-G1a cutover）', () => {
  test('copEntityToRoute 把 cop_entity → routeToFeature 吃的 shape', () => {
    const ent = {
      uid: 'manual:r1',
      callsign: '北側主疏散',
      version_clock: 3,
      attributes: {
        kind: 'route',
        vertices: [[24.8, 121.0], [24.9, 121.1]],
        color: '#56d364',
        route_type: 'primary',
        dash: false,
        label_anchor: [24.85, 121.05],
      },
    };
    const shape = copEntityToRoute(ent);
    expect(shape.id).toBe('manual:r1');
    expect(shape.label).toBe('北側主疏散');
    expect(shape.latlngs).toEqual([[24.8, 121.0], [24.9, 121.1]]);
    expect(shape.route_type).toBe('primary');
    expect(shape.label_anchor).toEqual([24.85, 121.05]);
    // 接 routeToFeature → 合法 LineString
    const f = routeToFeature(shape);
    expect(f.geometry.type).toBe('LineString');
    expect(f.properties.id).toBe('manual:r1');
    expect(f.properties.label).toBe('北側主疏散');
  });

  test('copEntityToPolygon 把 cop_entity → polygonToFeature 吃的 shape', () => {
    const ent = {
      uid: 'manual:p1',
      callsign: '北側管制區',
      attributes: {
        kind: 'polygon',
        vertices: [[24.8, 121.0], [24.8, 121.1], [24.9, 121.05]],
        color: '#e05555',
        poly_type: 'control',
        dash: true,
      },
    };
    const shape = copEntityToPolygon(ent);
    expect(shape.id).toBe('manual:p1');
    expect(shape.poly_type).toBe('control');
    expect(shape.dash).toBe(true);
    expect(shape.label_anchor).toBeUndefined(); // 沒設 label_anchor → undefined
    const f = polygonToFeature(shape);
    expect(f.geometry.type).toBe('Polygon');
    expect(f.properties.color).toBe('#e05555');
  });

  test('vertices 缺 / 非陣列 / null entity → 回 null', () => {
    expect(copEntityToRoute(null)).toBeNull();
    expect(copEntityToPolygon(null)).toBeNull();
    expect(copEntityToRoute({ uid: 'x', attributes: {} })).toBeNull();
    expect(copEntityToPolygon({ uid: 'x', attributes: { vertices: 'nope' } })).toBeNull();
    expect(copEntityToRoute({ uid: 'x' })).toBeNull(); // 無 attributes
  });

  test('callsign 缺 → label 空字串；color 缺 → 預設色', () => {
    const r = copEntityToRoute({ uid: 'r', attributes: { vertices: [[0, 0], [1, 1]] } });
    expect(r.label).toBe('');
    expect(r.color).toBe('#58a6ff');
    const p = copEntityToPolygon({ uid: 'p', attributes: { vertices: [[0, 0], [1, 0], [1, 1]] } });
    expect(p.label).toBe('');
    expect(p.color).toBe('#888888');
  });
});

describe('copEntityToEventZone adapter（PR-G1b cutover）', () => {
  test('event cop_entity → 既有事件 zone shape（解撞名：event_group）', () => {
    const ent = {
      uid: 'manual:e1',
      lat: 24.8,
      lon: 121.0,
      callsign: '疑似爆裂物',
      event_id: 'ev-123',  // P2-33b：頂層（junction 權威），不再讀 attributes.event_id
      attributes: { kind: 'event', event_code: 'EV-0529-001', event_group: 'security' },
    };
    const z = copEntityToEventZone(ent);
    expect(z.id).toBe('manual:e1');       // cop uid → zone.id（刪除/回查用）
    expect(z.lat).toBe(24.8);
    expect(z.lng).toBe(121.0);            // cop lon → zone.lng
    expect(z.label).toBe('疑似爆裂物');
    expect(z.event_group).toBe('security');  // #66 PR-B：事件類別走 event_group，不借 node_type
    expect(z.icon).toBe('event');
    expect(z.event_id).toBe('ev-123');
    expect(z.event_code).toBe('EV-0529-001');
  });

  test('back-compat：舊 entity 用 node_type 存 group → 仍還原成 event_group', () => {
    const z = copEntityToEventZone({
      uid: 'manual:e2', lat: 24.8, lon: 121.0, callsign: '受困救援',
      event_id: 'ev-9',  // P2-33b：頂層
      attributes: { kind: 'event', node_type: 'rescue' },  // 舊欄位
    });
    expect(z.event_group).toBe('rescue');
  });

  test('P2-33b：event_id 只在 attributes（glue）不在頂層 → 回 null（junction 為唯一源）', () => {
    expect(copEntityToEventZone({
      uid: 'manual:e3', lat: 24.8, lon: 121.0, callsign: 'x',
      attributes: { kind: 'event', event_id: 'glue-only' },  // 舊 glue、無頂層 → 不認
    })).toBeNull();
  });

  test('非 event kind / 缺 event_id / null → 回 null', () => {
    expect(copEntityToEventZone(null)).toBeNull();
    expect(copEntityToEventZone({ uid: 'x', lat: 1, lon: 2, attributes: { kind: 'route', vertices: [] } })).toBeNull();
    expect(copEntityToEventZone({ uid: 'x', lat: 1, lon: 2, attributes: { kind: 'event' } })).toBeNull(); // 缺 event_id
    expect(copEntityToEventZone({ uid: 'x', event_id: 'e', attributes: { kind: 'event' } })).toBeNull(); // 缺座標
  });
});

describe('copEntityToZone adapter（P1-16 on-demand 節點）', () => {
  test('zone cop_entity → 既有節點 zone shape', () => {
    const ent = {
      uid: 'manual:z1',
      lat: 24.83,
      lon: 121.01,
      callsign: '指揮部',
      attributes: { kind: 'zone', node_type: 'command' },
    };
    const z = copEntityToZone(ent);
    expect(z.id).toBe('manual:z1');      // cop uid → zone.id（刪除/回查用）
    expect(z.lat).toBe(24.83);
    expect(z.lng).toBe(121.01);          // cop lon → zone.lng
    expect(z.label).toBe('指揮部');
    expect(z.node_type).toBe('command');
    expect(z.icon).toBe('pin');
  });

  test('缺 node_type → 預設 command；缺 callsign → 空字串', () => {
    const z = copEntityToZone({ uid: 'manual:z2', lat: 1, lon: 2, attributes: { kind: 'zone' } });
    expect(z.node_type).toBe('command');
    expect(z.label).toBe('');
  });

  test('非 zone kind / 座標非數 / null → 回 null', () => {
    expect(copEntityToZone(null)).toBeNull();
    expect(copEntityToZone({ uid: 'x', lat: 1, lon: 2, attributes: { kind: 'event', event_id: 'e' } })).toBeNull();
    expect(copEntityToZone({ uid: 'x', lat: 1, lon: 2, attributes: {} })).toBeNull(); // 缺 kind
    expect(copEntityToZone({ uid: 'x', lat: 'nope', lon: 2, attributes: { kind: 'zone' } })).toBeNull();
    expect(copEntityToZone({ uid: 'x', attributes: { kind: 'zone' } })).toBeNull(); // 缺座標
  });
});

describe('copEntityToInfra adapter（P1-16 PR-2 on-demand 設施）', () => {
  test('infra cop_entity → 既有設施 infra shape', () => {
    const ent = {
      uid: 'manual:i1',
      lat: 24.83,
      lon: 121.01,
      callsign: '醫院',
      attributes: { kind: 'infra', infra_type: 'hospital' },
    };
    const i = copEntityToInfra(ent);
    expect(i.id).toBe('manual:i1');      // cop uid → infra.id（刪除/回查用）
    expect(i.lat).toBe(24.83);
    expect(i.lng).toBe(121.01);          // cop lon → infra.lng
    expect(i.label).toBe('醫院');
    expect(i.infra_type).toBe('hospital');
  });

  test('缺 infra_type → 預設 utility；缺 callsign → 空字串', () => {
    const i = copEntityToInfra({ uid: 'manual:i2', lat: 1, lon: 2, attributes: { kind: 'infra' } });
    expect(i.infra_type).toBe('utility');
    expect(i.label).toBe('');
  });

  test('color / abbr 不由 adapter 補（交給 caller 用 INFRA_TYPES 對映）', () => {
    const i = copEntityToInfra({ uid: 'x', lat: 1, lon: 2, attributes: { kind: 'infra', infra_type: 'fire' } });
    expect(i.color).toBeUndefined();
    expect(i.abbr).toBeUndefined();
  });

  test('非 infra kind / 座標非數 / null → 回 null', () => {
    expect(copEntityToInfra(null)).toBeNull();
    expect(copEntityToInfra({ uid: 'x', lat: 1, lon: 2, attributes: { kind: 'zone' } })).toBeNull();
    expect(copEntityToInfra({ uid: 'x', lat: 1, lon: 2, attributes: {} })).toBeNull(); // 缺 kind
    expect(copEntityToInfra({ uid: 'x', lat: 'nope', lon: 2, attributes: { kind: 'infra' } })).toBeNull();
    expect(copEntityToInfra({ uid: 'x', attributes: { kind: 'infra' } })).toBeNull(); // 缺座標
  });
});
