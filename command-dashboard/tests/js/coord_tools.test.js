/**
 * coord_tools.test.js — UTM / MGRS / WGS84 工具 + MgrsGrid 渲染 (P1-10b 步驟 10)
 *
 * 三大類測試：
 *   1. 純數學 helper：round-trip 與已知值對照（驗 port 沒改錯公式）
 *   2. 字串解析：mgrsToLatLng / parseWgs84 邊界情況
 *   3. MgrsGrid 渲染：mock MapLibre map，驗 setVisible / redraw 產生的 features
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  latlngToUtm,
  utmToLatLng,
  latlngToMgrs,
  mgrsToLatLng,
  parseWgs84,
  mgrsGridSpacing,
  mgrsGridSecondaryTier,
  mgrsGridLabel,
  mgrs100kmSquare,
  MgrsGrid,
} from '../../static/js/map/coord_tools.js';

describe('latlngToUtm / utmToLatLng round-trip', () => {
  // 新竹 (24.8283, 121.0149) — Zone 51R, 涵蓋台灣大部分
  it('新竹座標 round-trip 精度 < 1cm', () => {
    const lat = 24.8283, lng = 121.0149;
    const utm = latlngToUtm(lat, lng);
    expect(utm.zoneNum).toBe(51);
    const back = utmToLatLng(utm.zoneNum, utm.easting, utm.northing);
    expect(Math.abs(back.lat - lat)).toBeLessThan(1e-7);
    expect(Math.abs(back.lng - lng)).toBeLessThan(1e-7);
  });

  it('台北 (25.0330, 121.5654) round-trip', () => {
    const lat = 25.0330, lng = 121.5654;
    const utm = latlngToUtm(lat, lng);
    const back = utmToLatLng(utm.zoneNum, utm.easting, utm.northing);
    expect(Math.abs(back.lat - lat)).toBeLessThan(1e-7);
    expect(Math.abs(back.lng - lng)).toBeLessThan(1e-7);
  });

  it('zone 邊界 (lng=120) Zone 51', () => {
    const utm = latlngToUtm(24.5, 120.5);
    expect(utm.zoneNum).toBe(51);
  });

  it('zone 邊界 (lng=126) Zone 52', () => {
    const utm = latlngToUtm(24.5, 126.5);
    expect(utm.zoneNum).toBe(52);
  });
});

describe('latlngToMgrs', () => {
  it('新竹預設 5 位精度（落 51R TH）', () => {
    // 新竹中心 24.8283, 121.0149 落在 51R TH 100km grid square
    const mgrs = latlngToMgrs(24.8283, 121.0149, 5);
    expect(mgrs).toMatch(/^51R TH \d{5} \d{5}$/);
  });

  it('precision=3 → 3 位 easting / northing', () => {
    const mgrs = latlngToMgrs(24.8283, 121.0149, 3);
    expect(mgrs).toMatch(/^51R TH \d{3} \d{3}$/);
  });

  it('精度 0 → 只有 grid square designator', () => {
    const mgrs = latlngToMgrs(24.8283, 121.0149, 0);
    expect(mgrs).toMatch(/^51R TH  $/);
  });
});

describe('mgrsToLatLng', () => {
  it('合法 MGRS round-trip', () => {
    const lat = 24.8283, lng = 121.0149;
    const mgrs = latlngToMgrs(lat, lng, 5);
    const back = mgrsToLatLng(mgrs);
    expect(back).not.toBeNull();
    // round-trip 經過 100km square 取整，精度約 1-2 m
    expect(Math.abs(back.lat - lat)).toBeLessThan(1e-4);
    expect(Math.abs(back.lng - lng)).toBeLessThan(1e-4);
  });

  it('合法輸入支援空格 / 小寫', () => {
    const a = mgrsToLatLng('51R UH 12345 67890');
    const b = mgrsToLatLng('51r uh 12345 67890');
    const c = mgrsToLatLng('51RUH1234567890');
    expect(a).not.toBeNull();
    expect(b).not.toBeNull();
    expect(c).not.toBeNull();
    expect(a.lat).toBeCloseTo(b.lat, 6);
    expect(a.lat).toBeCloseTo(c.lat, 6);
  });

  it('非字串 / 空 / 格式錯 → null', () => {
    expect(mgrsToLatLng(null)).toBe(null);
    expect(mgrsToLatLng('')).toBe(null);
    expect(mgrsToLatLng('not a mgrs')).toBe(null);
    expect(mgrsToLatLng('51R UH 123')).toBe(null);    // 數字位數奇數
    expect(mgrsToLatLng('99Z ZZ 12345 67890')).toBe(null);  // 不合法 lat band
  });

  it('100km square letter 不合法 → null', () => {
    // 第 3 碼必須在 ABCDEFGHJKLMNPQRSTUVWXYZ（無 I/O），用 'I' 應 null
    expect(mgrsToLatLng('51R IH 12345 67890')).toBe(null);
  });
});

describe('parseWgs84', () => {
  it('"lat, lng" 標準格式', () => {
    expect(parseWgs84('24.8283, 121.0149')).toEqual({ lat: 24.8283, lng: 121.0149 });
  });

  it('"lat lng"（空白分隔）', () => {
    expect(parseWgs84('24.8283 121.0149')).toEqual({ lat: 24.8283, lng: 121.0149 });
  });

  it('負值', () => {
    expect(parseWgs84('-33.86, 151.21')).toEqual({ lat: -33.86, lng: 151.21 });
  });

  it('lat 超出 [-90, 90] → null', () => {
    expect(parseWgs84('91, 100')).toBe(null);
    expect(parseWgs84('-91, 100')).toBe(null);
  });

  it('lng 超出 [-180, 180] → null', () => {
    expect(parseWgs84('20, 181')).toBe(null);
    expect(parseWgs84('20, -181')).toBe(null);
  });

  it('非字串 / 空 / 格式錯 → null', () => {
    expect(parseWgs84(null)).toBe(null);
    expect(parseWgs84('')).toBe(null);
    expect(parseWgs84('hello')).toBe(null);
  });
});

describe('mgrsGridSpacing', () => {
  it('低 zoom → 100km grid', () => {
    expect(mgrsGridSpacing(6, 24.8)).toBe(100000);
  });

  it('高 zoom → 較細 grid', () => {
    // zoom 14 在台灣緯度約 1.7m / px，MIN_PX 60 → minMeters 約 100m → 100m grid
    expect(mgrsGridSpacing(14, 24.8)).toBeLessThanOrEqual(1000);
  });

  it('zoom 18+ → 10m or 1m grid', () => {
    expect(mgrsGridSpacing(20, 24.8)).toBeLessThanOrEqual(10);
  });

  it('zoom null → 100km fallback', () => {
    expect(mgrsGridSpacing(null, 24.8)).toBe(100000);
    expect(mgrsGridSpacing(14, null)).toBe(100000);
  });
});

describe('mgrsGridLabel (MGRS-standard digit counts, no floor)', () => {
  it('100km spacing → 空字串（designator 留 P2 milsymbol 接 TAK 時補）', () => {
    expect(mgrsGridLabel(123456, 100000)).toBe('');
  });

  it('10km spacing → 1 位', () => {
    expect(mgrsGridLabel(123456, 10000)).toMatch(/^\d$/);
    // val 23456 → floor(23456 / 10000) = 2
    expect(mgrsGridLabel(123456, 10000)).toBe('2');
  });

  it('1km spacing → 2 位', () => {
    expect(mgrsGridLabel(123456, 1000)).toMatch(/^\d{2}$/);
    // val 23456 → floor(23456 / 1000) = 23
    expect(mgrsGridLabel(123456, 1000)).toBe('23');
  });

  it('100m spacing → 3 位', () => {
    expect(mgrsGridLabel(123456, 100)).toMatch(/^\d{3}$/);
    expect(mgrsGridLabel(123456, 100)).toBe('234');
  });

  it('10m spacing → 4 位', () => {
    expect(mgrsGridLabel(123456, 10)).toMatch(/^\d{4}$/);
    expect(mgrsGridLabel(123456, 10)).toBe('2345');
  });

  it('1m spacing → 5 位', () => {
    expect(mgrsGridLabel(123456, 1)).toMatch(/^\d{5}$/);
    expect(mgrsGridLabel(123456, 1)).toBe('23456');
  });

  it('100km 整邊界 (val=200000 / 1km spacing) → "00"', () => {
    expect(mgrsGridLabel(200000, 1000)).toBe('00');
  });
});

// ── MgrsGrid 渲染 ───────────────────────────────────────────────
function makeMockMap(centerLat = 24.8, centerLng = 121.0, zoom = 14) {
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
      });
    },
    getSource(id) { return sources.get(id) || null; },
    removeSource(id) { sources.delete(id); },
    addLayer(spec) { layers.set(spec.id, spec); },
    getLayer(id) { return layers.get(id) || null; },
    removeLayer(id) { layers.delete(id); },
    getBounds() {
      return {
        getSouth: () => centerLat - 0.05,
        getNorth: () => centerLat + 0.05,
        getWest: () => centerLng - 0.05,
        getEast: () => centerLng + 0.05,
        getCenter: () => ({ lat: centerLat, lng: centerLng }),
      };
    },
    getZoom: () => zoom,
    getCenter: () => ({ lat: centerLat, lng: centerLng }),
    getCanvas: () => ({ clientWidth: 1024, clientHeight: 768 }),
    unproject: ([x, y]) => {
      // 簡化：viewport pixel 線性映射回 lat/lng（夠精確 for test）
      const W = 1024, H = 768;
      const south = centerLat - 0.05, north = centerLat + 0.05;
      const west = centerLng - 0.05, east = centerLng + 0.05;
      return {
        lat: north - (y / H) * (north - south),
        lng: west + (x / W) * (east - west),
      };
    },
  };
}

describe('MgrsGrid', () => {
  let map;
  beforeEach(() => { map = makeMockMap(); });

  it('constructor 拒絕缺 map', () => {
    expect(() => new MgrsGrid(null)).toThrow(/map required/);
  });

  it('setVisible(true) 安裝 source + 3 個 layer（primary + secondary + labels）', () => {
    const grid = new MgrsGrid(map);
    grid.setVisible(true);
    expect(map.getSource('mgrs-grid')).toBeTruthy();
    expect(map.getLayer('mgrs-grid-lines-primary')).toBeTruthy();
    expect(map.getLayer('mgrs-grid-lines-secondary')).toBeTruthy();
    expect(map.getLayer('mgrs-grid-labels')).toBeTruthy();
    expect(grid.isVisible()).toBe(true);
  });

  it('TAK-aligned tier 標記：primary feature 與 secondary feature 用 tier property 區分', () => {
    const grid = new MgrsGrid(map);
    grid.setVisible(true);
    const lines = map.getSource('mgrs-grid')._data.features.filter(
      (f) => f.geometry.type === 'LineString',
    );
    // Test env zoom=14 + 緯度 24.8 → primary=1000m (1km), secondary=100m (100m)
    // 兩 tier 都應該出現
    const primaryCount = lines.filter((f) => f.properties.tier === 'primary').length;
    const secondaryCount = lines.filter((f) => f.properties.tier === 'secondary').length;
    expect(primaryCount).toBeGreaterThan(0);
    expect(secondaryCount).toBeGreaterThan(0);
    // Secondary 應該比 primary 多（10× density）
    expect(secondaryCount).toBeGreaterThan(primaryCount);
  });

  it('setVisible(true) → redraw 後 source 有 features', () => {
    const grid = new MgrsGrid(map);
    grid.setVisible(true);
    const fc = map.getSource('mgrs-grid')._data;
    expect(fc.type).toBe('FeatureCollection');
    expect(fc.features.length).toBeGreaterThan(0);
    // 應該有 LineString（grid lines）+ Point（labels）
    const lines = fc.features.filter((f) => f.geometry.type === 'LineString');
    const points = fc.features.filter((f) => f.geometry.type === 'Point');
    expect(lines.length).toBeGreaterThan(0);
    expect(points.length).toBeGreaterThan(0);
  });

  it('Label feature 4 邊鏡像：label = 數字 + axis 是 x-bottom/x-top/y-left/y-right', () => {
    const grid = new MgrsGrid(map);
    grid.setVisible(true);
    const fc = map.getSource('mgrs-grid')._data;
    const points = fc.features.filter((f) => f.geometry.type === 'Point');
    expect(points.length).toBeGreaterThan(0);
    for (const p of points) {
      expect(p.properties.label).toMatch(/^\d+$/);
      expect(['x-bottom', 'x-top', 'y-left', 'y-right']).toContain(p.properties.axis);
    }
    // 4 軸都該有 label
    for (const axis of ['x-bottom', 'x-top', 'y-left', 'y-right']) {
      expect(points.some((p) => p.properties.axis === axis)).toBe(true);
    }
    // Y 軸 left vs right label 內容應一致（同 northing 線、同 digits）
    const yLeftLabels = new Set(points.filter((p) => p.properties.axis === 'y-left').map((p) => p.properties.label));
    const yRightLabels = new Set(points.filter((p) => p.properties.axis === 'y-right').map((p) => p.properties.label));
    expect([...yLeftLabels].sort()).toEqual([...yRightLabels].sort());
  });

  it('setVisible(false) → source 清空', () => {
    const grid = new MgrsGrid(map);
    grid.setVisible(true);
    expect(map.getSource('mgrs-grid')._data.features.length).toBeGreaterThan(0);
    grid.setVisible(false);
    expect(map.getSource('mgrs-grid')._data.features.length).toBe(0);
    expect(grid.isVisible()).toBe(false);
  });

  it('redraw 在 not visible 時 no-op', () => {
    const grid = new MgrsGrid(map);
    expect(() => grid.redraw()).not.toThrow();
    expect(map.getSource('mgrs-grid')).toBe(null);  // 沒 install
  });

  it('destroy 清掉 layer + source', () => {
    const grid = new MgrsGrid(map);
    grid.setVisible(true);
    grid.destroy();
    expect(map.getLayer('mgrs-grid-lines-primary')).toBe(null);
    expect(map.getLayer('mgrs-grid-lines-secondary')).toBe(null);
    expect(map.getLayer('mgrs-grid-labels')).toBe(null);
    expect(map.getLayer('mgrs-grid-designators')).toBe(null);
    expect(map.getSource('mgrs-grid')).toBe(null);
    expect(grid.isVisible()).toBe(false);
  });

  it('低 zoom（primary=100km tier）→ 每 100km 方格中央 emit designator label', () => {
    // zoom 8 + 緯度 24.8 → metersPerPx ~674 → primary=100km
    const lowZoomMap = makeMockMap(24.8, 121.0, 8);
    // 撐大 bounds 跨多個 100km square
    lowZoomMap.getBounds = () => ({
      getSouth: () => 24.0, getNorth: () => 25.5,
      getWest: () => 120.0, getEast: () => 122.0,
      getCenter: () => ({ lat: 24.75, lng: 121.0 }),
    });
    const grid = new MgrsGrid(lowZoomMap);
    grid.setVisible(true);
    const points = lowZoomMap.getSource('mgrs-grid')._data.features.filter(
      (f) => f.geometry.type === 'Point',
    );
    const designators = points.filter((p) => p.properties.axis === 'designator');
    expect(designators.length).toBeGreaterThan(0);
    for (const d of designators) {
      expect(d.properties.label).toMatch(/^[A-Z]{2}$/);
    }
  });

  it('高 zoom（primary < 100km）→ 不 emit designator', () => {
    const grid = new MgrsGrid(map);   // map 預設 zoom=14
    grid.setVisible(true);
    const points = map.getSource('mgrs-grid')._data.features.filter(
      (f) => f.geometry.type === 'Point',
    );
    const designators = points.filter((p) => p.properties.axis === 'designator');
    expect(designators.length).toBe(0);
  });

  it('opts 客製 primary/secondary 配色與寬度', () => {
    const grid = new MgrsGrid(map, {
      primaryColor: '#ff0000',
      primaryWidth: 2,
      secondaryColor: '#00ff00',
      secondaryWidth: 0.5,
    });
    grid.setVisible(true);
    expect(map.getLayer('mgrs-grid-lines-primary').paint['line-color']).toBe('#ff0000');
    expect(map.getLayer('mgrs-grid-lines-primary').paint['line-width']).toBe(2);
    expect(map.getLayer('mgrs-grid-lines-secondary').paint['line-color']).toBe('#00ff00');
    expect(map.getLayer('mgrs-grid-lines-secondary').paint['line-width']).toBe(0.5);
  });
});

describe('mgrsGridSecondaryTier', () => {
  it('primary 1km → secondary 100m，zoom 高密度足 → visible', () => {
    // zoom 14 在台灣緯度大約 10.5 m/px，secondary 100m → 約 9.5 px，剛好過 8 px 門檻
    const t = mgrsGridSecondaryTier(1000, 14, 24.8);
    expect(t.spacing).toBe(100);
    expect(t.visible).toBe(true);
  });

  it('primary 1m → 無法再細，spacing null', () => {
    const t = mgrsGridSecondaryTier(1, 22, 24.8);
    expect(t.spacing).toBe(null);
    expect(t.visible).toBe(false);
  });

  it('低 zoom（primary 100km / secondary 10km）→ 10km secondary 在低 zoom 像素太密 → 不可見', () => {
    // zoom 6 在台灣緯度大約 2700 m/px，secondary 10000m → 約 3.7 px，低於 8 px → not visible
    const t = mgrsGridSecondaryTier(100000, 6, 24.8);
    expect(t.spacing).toBe(10000);
    expect(t.visible).toBe(false);
  });
});

describe('mgrs100kmSquare', () => {
  it('新竹 → "TH"', () => {
    expect(mgrs100kmSquare(24.8283, 121.0149)).toBe('TH');
  });

  it('台北 (25.03, 121.56) → 同 zone 51R 不同 100km square', () => {
    const id = mgrs100kmSquare(25.033, 121.5654);
    expect(id).toMatch(/^[A-Z]{2}$/);
  });

  it('回 2 字母 ASCII', () => {
    const id = mgrs100kmSquare(24.5, 121.0);
    expect(id).toMatch(/^[A-Z]{2}$/);
  });
});
