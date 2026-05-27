/**
 * draw_tools.test.js — P1-10b 步驟 8 DrawPreview unit test
 *
 * Mock MapLibre map（避免 jsdom + WebGL），驗證：
 *   - start / cancel / addVertex / canFinish lifecycle 正確
 *   - polygon vs route 渲染（fill+line vs line only）
 *   - render 邏輯 emit 正確 GeoJSON（Point vertices + Polygon/LineString shape）
 *   - destroy 正確拆 layer + source
 */

import { describe, test, expect, beforeEach } from 'vitest';
import { DrawPreview } from '../../static/js/map/draw_tools.js';

/** 與 entity_layer.test.js 共用 mock map pattern */
function makeMockMap() {
  const sources = new Map();
  const layers = new Map();
  return {
    sources,
    layers,
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
  };
}

describe('DrawPreview', () => {
  let map;
  beforeEach(() => { map = makeMockMap(); });

  test('constructor 拒絕缺 map', () => {
    expect(() => new DrawPreview(null)).toThrow(/map required/);
  });

  test('初始狀態 inactive', () => {
    const dp = new DrawPreview(map);
    expect(dp.isActive()).toBe(false);
    expect(dp.vertexCount()).toBe(0);
    expect(dp.canFinish()).toBe(false);
    expect(dp.getLatlngs()).toEqual([]);
  });

  test('start(polygon) lazy install source + layer', () => {
    const dp = new DrawPreview(map);
    dp.start('polygon');
    expect(dp.isActive()).toBe(true);
    expect(map.getSource('draw-vertices')).toBeTruthy();
    expect(map.getSource('draw-shape')).toBeTruthy();
    expect(map.getLayer('draw-vertices')).toBeTruthy();
    expect(map.getLayer('draw-shape-fill')).toBeTruthy();
    expect(map.getLayer('draw-shape-stroke')).toBeTruthy();
  });

  test('start 拒非法 kind', () => {
    const dp = new DrawPreview(map);
    expect(() => dp.start('triangle')).toThrow(/invalid kind/);
    expect(() => dp.start(null)).toThrow(/invalid kind/);
  });

  test('polygon: addVertex 累積 + canFinish 在 3 點 true', () => {
    const dp = new DrawPreview(map);
    dp.start('polygon');
    dp.addVertex(24.82, 121.01);
    expect(dp.canFinish()).toBe(false);  // 1 點
    dp.addVertex(24.83, 121.02);
    expect(dp.canFinish()).toBe(false);  // 2 點
    dp.addVertex(24.84, 121.03);
    expect(dp.canFinish()).toBe(true);   // 3 點 OK
    expect(dp.vertexCount()).toBe(3);
    expect(dp.getLatlngs()).toEqual([[24.82, 121.01], [24.83, 121.02], [24.84, 121.03]]);
  });

  test('route: addVertex canFinish 在 2 點 true', () => {
    const dp = new DrawPreview(map);
    dp.start('route');
    dp.addVertex(24.82, 121.01);
    expect(dp.canFinish()).toBe(false);  // 1 點
    dp.addVertex(24.83, 121.02);
    expect(dp.canFinish()).toBe(true);   // 2 點 OK
  });

  test('polygon ≥ 3 點 render Polygon geometry（自動閉合）', () => {
    const dp = new DrawPreview(map);
    dp.start('polygon');
    dp.addVertex(24.82, 121.01);
    dp.addVertex(24.83, 121.02);
    dp.addVertex(24.84, 121.03);
    const shape = map.getSource('draw-shape').serialize().data.features[0];
    expect(shape.geometry.type).toBe('Polygon');
    expect(shape.geometry.coordinates[0]).toHaveLength(4);  // 3 點 + 閉合
    // 座標 [lng, lat]
    expect(shape.geometry.coordinates[0][0]).toEqual([121.01, 24.82]);
    expect(shape.geometry.coordinates[0][3]).toEqual([121.01, 24.82]);  // 閉合
    expect(shape.properties.color).toBe('#58a6ff');
  });

  test('polygon < 3 點時用 LineString 預覽（過渡狀態）', () => {
    const dp = new DrawPreview(map);
    dp.start('polygon');
    dp.addVertex(24.82, 121.01);
    dp.addVertex(24.83, 121.02);
    const shape = map.getSource('draw-shape').serialize().data.features[0];
    expect(shape.geometry.type).toBe('LineString');  // < 3 點時還沒形成 polygon
  });

  test('route render LineString geometry', () => {
    const dp = new DrawPreview(map);
    dp.start('route');
    dp.addVertex(24.82, 121.01);
    dp.addVertex(24.83, 121.02);
    const shape = map.getSource('draw-shape').serialize().data.features[0];
    expect(shape.geometry.type).toBe('LineString');
    expect(shape.geometry.coordinates).toEqual([[121.01, 24.82], [121.02, 24.83]]);
    expect(shape.properties.color).toBe('#56d364');
  });

  test('vertex source 每個頂點一個 Point feature', () => {
    const dp = new DrawPreview(map);
    dp.start('polygon');
    dp.addVertex(24.82, 121.01);
    dp.addVertex(24.83, 121.02);
    const verts = map.getSource('draw-vertices').serialize().data.features;
    expect(verts).toHaveLength(2);
    expect(verts[0].geometry.coordinates).toEqual([121.01, 24.82]);
    expect(verts[0].properties.color).toBe('#58a6ff');
  });

  test('cancel 清掉狀態 + render empty', () => {
    const dp = new DrawPreview(map);
    dp.start('polygon');
    dp.addVertex(24.82, 121.01);
    dp.addVertex(24.83, 121.02);
    dp.addVertex(24.84, 121.03);
    dp.cancel();
    expect(dp.isActive()).toBe(false);
    expect(dp.vertexCount()).toBe(0);
    expect(map.getSource('draw-vertices').serialize().data.features).toEqual([]);
    expect(map.getSource('draw-shape').serialize().data.features).toEqual([]);
  });

  test('inactive 時 addVertex no-op', () => {
    const dp = new DrawPreview(map);
    dp.addVertex(24.82, 121.01);
    expect(dp.vertexCount()).toBe(0);
  });

  test('addVertex 拒非 finite 座標', () => {
    const dp = new DrawPreview(map);
    dp.start('polygon');
    dp.addVertex(NaN, 121);
    dp.addVertex(24, undefined);
    expect(dp.vertexCount()).toBe(0);
  });

  test('start 兩次先 reset 狀態（不重複 install）', () => {
    const dp = new DrawPreview(map);
    dp.start('polygon');
    dp.addVertex(0, 0);
    dp.start('route');
    expect(dp.kind).toBe('route');
    expect(dp.vertexCount()).toBe(0);
    expect(map.sources.size).toBe(2);  // 不重複建 source
  });

  test('destroy 拆 layer + source，可重複呼叫', () => {
    const dp = new DrawPreview(map);
    dp.start('polygon');
    dp.destroy();
    expect(map.getLayer('draw-vertices')).toBeNull();
    expect(map.getLayer('draw-shape-fill')).toBeNull();
    expect(map.getLayer('draw-shape-stroke')).toBeNull();
    expect(map.getSource('draw-vertices')).toBeNull();
    expect(map.getSource('draw-shape')).toBeNull();
    expect(() => dp.destroy()).not.toThrow();
  });
});
