// aar_replay_engine.test.js — P2-20(B) B1（#201）回放核心純函式
import { describe, expect, test } from 'vitest';

import {
  buildReplayIndex, foldPositionsAt, positionsToGeoJSON, stepSummary, fmtClock, validCoord,
  foldZonesAt, zonesToGeoJSON, eventsToGeoJSON,
} from '../../static/js/aar/replay_engine.js';

// #338：區域生命週期事件 helper（type='zone'）
const zn = (uid, t, op, attributes) => ({ type: 'zone', t, actor: 'cmd', payload: { uid, op, attributes } });
const _POLY = { kind: 'polygon', vertices: [[24.70, 121.00], [24.72, 121.03], [24.69, 121.05]], color: '#ff0000' };

const tk = (uid, t, lat, lon, actor = uid) => ({
  type: 'track', t, actor, payload: { uid, lat, lon, heading_deg: null, speed_mps: null },
});

const ITEMS = [
  { type: 'chat', t: '2026-01-01T01:00:00Z', actor: 'ALPHA', payload: { group: 'ops', message: '到位' } },
  tk('u1', '2026-01-01T02:00:00Z', 24.0, 120.0, 'ALPHA'),
  tk('u1', '2026-01-01T03:00:00Z', 24.1, 120.1, 'ALPHA'),
  tk('u2', '2026-01-01T03:30:00Z', 25.0, 121.0, 'BRAVO'),
  { type: 'event', t: '2026-01-01T04:00:00Z', actor: 'shelter',
    payload: { event_code: 'EV-0101-001', event_type: 'fire', severity: 'critical', description: 'x' } },
];

describe('buildReplayIndex', () => {
  test('steps 全收且按 t 排序；trackIdx 只含 track', () => {
    const shuffled = [ITEMS[4], ITEMS[1], ITEMS[0], ITEMS[3], ITEMS[2]];
    const { steps, trackIdx } = buildReplayIndex(shuffled);
    expect(steps.map(s => s.t)).toEqual(ITEMS.map(s => s.t)); // 防禦排序
    expect(trackIdx).toHaveLength(3);
    expect(trackIdx.every(it => it.type === 'track')).toBe(true);
  });

  test('空 / undefined 容忍', () => {
    expect(buildReplayIndex([]).steps).toEqual([]);
    expect(buildReplayIndex(undefined).steps).toEqual([]);
  });

  test('#3：(0,0) null island 壞點不進 trackIdx/tracksByUid（仍留 steps 側欄）', () => {
    const items = [
      tk('u1', '2026-01-01T02:00:00Z', 0, 0, 'ALPHA'),       // GPS no-fix 壞點
      tk('u1', '2026-01-01T02:05:00Z', 24.0, 120.0, 'ALPHA'), // 正常
    ];
    const { steps, trackIdx, tracksByUid } = buildReplayIndex(items);
    expect(steps).toHaveLength(2);                 // 側欄仍見原始回報
    expect(trackIdx).toHaveLength(1);              // 折疊/尾跡只收有效點
    expect(tracksByUid.get('u1')).toHaveLength(1); // 尾跡不會拉線到 (0,0)
  });
});

describe('validCoord (#3)', () => {
  test('擋 (0,0) / 非數 / 超範圍，放行正常', () => {
    expect(validCoord(0, 0)).toBe(false);
    expect(validCoord(NaN, 120)).toBe(false);
    expect(validCoord(24.0, 200)).toBe(false);
    expect(validCoord(91, 120)).toBe(false);
    expect(validCoord(24.77, 121.0)).toBe(true);
  });
});

describe('foldPositionsAt', () => {
  const { trackIdx } = buildReplayIndex(ITEMS);

  test('每 uid 取 ≤T 最後一筆（u1 在 T=02:30 停在 02:00 的點）', () => {
    const pos = foldPositionsAt(trackIdx, '2026-01-01T02:30:00Z');
    expect(pos.size).toBe(1);
    expect(pos.get('u1').lat).toBe(24.0);
  });

  test('T 推進 → u1 更新到 03:00、u2 於 03:30 出現', () => {
    const pos = foldPositionsAt(trackIdx, '2026-01-01T03:30:00Z');
    expect(pos.get('u1').lat).toBe(24.1);
    expect(pos.get('u2').actor).toBe('BRAVO');
  });

  test('T 早於全部 → 空（單位尚未回報，不畫）', () => {
    expect(foldPositionsAt(trackIdx, '2026-01-01T00:00:00Z').size).toBe(0);
  });
});

describe('positionsToGeoJSON', () => {
  test('Map → FeatureCollection（lon,lat 順序、callsign 屬性）', () => {
    const pos = foldPositionsAt(buildReplayIndex(ITEMS).trackIdx, '2026-01-01T09:00:00Z');
    const gj = positionsToGeoJSON(pos);
    expect(gj.type).toBe('FeatureCollection');
    expect(gj.features).toHaveLength(2);
    const u1 = gj.features.find(f => f.properties.uid === 'u1');
    expect(u1.geometry.coordinates).toEqual([120.1, 24.1]); // [lon, lat]
    expect(u1.properties.callsign).toBe('ALPHA');
  });
});

describe('stepSummary / fmtClock', () => {
  test('各 type 摘要不炸、含關鍵欄位', () => {
    expect(stepSummary(ITEMS[0])).toContain('到位');
    expect(stepSummary(ITEMS[4])).toContain('EV-0101-001');
    expect(stepSummary({ type: 'decision', actor: 'cmd', payload: { action: 'decision_made', detail: { action: 'approved' } } }))
      .toContain('approved');
    expect(stepSummary({ type: 'unknown', actor: 'x', payload: {} })).toBe('x');
  });

  test('fmtClock 非法值原樣回、合法值出 HH:MM:SS', () => {
    expect(fmtClock('not-a-date')).toBe('not-a-date');
    expect(fmtClock('2026-01-01T03:00:00Z')).toMatch(/^\d{2}:\d{2}:\d{2}$/);
  });
});

describe('stepIndexAtOrBefore (P2-21 bookmark 跳轉)', () => {
  test('取 t ≤ refT 最後一筆；早於全部 → 0；晚於全部 → 最後', async () => {
    const { stepIndexAtOrBefore } = await import('../../static/js/aar/replay_engine.js');
    const steps = ITEMS; // 已按 t 排序
    expect(stepIndexAtOrBefore(steps, '2026-01-01T02:30:00Z')).toBe(1); // 02:00 track
    expect(stepIndexAtOrBefore(steps, '2026-01-01T00:30:00Z')).toBe(0);
    expect(stepIndexAtOrBefore(steps, '2099-01-01T00:00:00Z')).toBe(steps.length - 1);
    expect(stepIndexAtOrBefore(steps, '2026-01-01T03:30:00Z')).toBe(3); // 恰等於 → 含
  });
});

describe('B2 Play mode 純函式 (#201)', () => {
  test('tToMs/msToT 互逆且秒精度 Z 格式', async () => {
    const { tToMs, msToT } = await import('../../static/js/aar/replay_engine.js');
    expect(msToT(tToMs('2026-01-01T03:00:00Z'))).toBe('2026-01-01T03:00:00Z');
  });

  test('advanceClock：wall-delta×speed、到 end 夾住回 ended', async () => {
    const { advanceClock } = await import('../../static/js/aar/replay_engine.js');
    const r1 = advanceClock(1000, 500, 2, 10000); // +500ms wall × 2x = +1000
    expect(r1).toEqual({ tMs: 2000, ended: false });
    const r2 = advanceClock(9500, 1000, 4, 10000); // 越過 end → 夾住
    expect(r2).toEqual({ tMs: 10000, ended: true });
  });

  test('增量折疊游標：前進 O(Δ) 結果 = 全折疊；倒退由 caller reset', async () => {
    const { buildReplayIndex, makeFoldCursor, advanceFold, foldPositionsAt } =
      await import('../../static/js/aar/replay_engine.js');
    const { trackIdx } = buildReplayIndex(ITEMS);
    const cur = makeFoldCursor();
    advanceFold(trackIdx, cur, '2026-01-01T02:30:00Z');
    expect(cur.pos.get('u1').lat).toBe(24.0);
    const pos2 = advanceFold(trackIdx, cur, '2026-01-01T03:30:00Z'); // 增量前進
    const full = foldPositionsAt(trackIdx, '2026-01-01T03:30:00Z');
    expect([...pos2.keys()].sort()).toEqual([...full.keys()].sort());
    expect(pos2.get('u1').lat).toBe(full.get('u1').lat);
  });

  test('trailGeoJSON：窗口切片、<2 點不畫、[lon,lat] 序', async () => {
    const { buildReplayIndex, trailGeoJSON } = await import('../../static/js/aar/replay_engine.js');
    const { tracksByUid } = buildReplayIndex(ITEMS);
    // T=03:30，窗口 120 分 → u1 兩點成線；u2 只 1 點不畫
    const gj = trailGeoJSON(tracksByUid, '2026-01-01T03:30:00Z', 120);
    expect(gj.features).toHaveLength(1);
    expect(gj.features[0].properties.uid).toBe('u1');
    expect(gj.features[0].geometry.coordinates).toEqual([[120.0, 24.0], [120.1, 24.1]]);
    // 窗口縮到 10 分 → u1 在窗內只剩 1 點 → 零線
    expect(trailGeoJSON(tracksByUid, '2026-01-01T03:05:00Z', 10).features).toHaveLength(0);
  });
});

describe('foldZonesAt / zonesToGeoJSON (#338 區域時間精確重現)', () => {
  const ZONES = [
    zn('z1', '2026-01-01T01:00:00Z', 'created', _POLY),
    zn('z1', '2026-01-01T02:00:00Z', 'updated', { ..._POLY, label_anchor: [24.80, 121.09] }),
    zn('z1', '2026-01-01T03:00:00Z', 'deleted', _POLY),
  ];

  test('畫前不存在、畫後出現、刪後消失（draw/delete @ T）', () => {
    const { zoneIdx } = buildReplayIndex(ZONES);
    expect(foldZonesAt(zoneIdx, '2026-01-01T00:30:00Z').size).toBe(0); // 畫之前
    expect(foldZonesAt(zoneIdx, '2026-01-01T01:30:00Z').size).toBe(1); // 畫之後
    expect(foldZonesAt(zoneIdx, '2026-01-01T03:30:00Z').size).toBe(0); // 刪之後 → 不畫
  });

  test('取 ≤T 最後一筆狀態（編輯後的 label_anchor）', () => {
    const { zoneIdx } = buildReplayIndex(ZONES);
    const z = foldZonesAt(zoneIdx, '2026-01-01T02:30:00Z').get('z1');
    expect(z.attributes.label_anchor).toEqual([24.80, 121.09]); // 折到更新後
  });

  test('zonesToGeoJSON：polygon → 閉合環 Polygon、[lon,lat] 序', () => {
    const map = foldZonesAt(buildReplayIndex(ZONES).zoneIdx, '2026-01-01T01:30:00Z');
    const gj = zonesToGeoJSON(map);
    expect(gj.features).toHaveLength(1);
    const f = gj.features[0];
    expect(f.geometry.type).toBe('Polygon');
    const ring = f.geometry.coordinates[0];
    expect(ring[0]).toEqual([121.00, 24.70]); // [lon,lat]
    expect(ring[ring.length - 1]).toEqual(ring[0]); // 自動閉合
    expect(f.properties.color).toBe('#ff0000');
  });

  test('zonesToGeoJSON：route → LineString（不閉合）', () => {
    const map = new Map([['r1', { uid: 'r1', attributes: { kind: 'route', vertices: [[24.7, 121.0], [24.8, 121.1]] } }]]);
    const gj = zonesToGeoJSON(map);
    expect(gj.features[0].geometry.type).toBe('LineString');
    expect(gj.features[0].geometry.coordinates).toEqual([[121.0, 24.7], [121.1, 24.8]]);
  });
});

describe('eventsToGeoJSON (#339 事件畫在標記折疊位置)', () => {
  const ev = (t, severity, markers) => ({
    type: 'event', t, actor: 'RTF',
    payload: { event_id: 'e1', event_code: 'EV-001', event_type: 'contact', severity, status: 'open', markers },
  });

  test('事件騎在 marker 折疊位置（移動標的 → 用 posMap 的當前點，非錨點）', () => {
    const eventIdx = [ev('2026-01-01T02:00:00Z', 'high', [{ uid: 'm1', lat: 0, lon: 0, role: 'primary' }])];
    const posMap = new Map([['m1', { uid: 'm1', lat: 25.5, lon: 121.5 }]]); // marker 已移動到此
    const gj = eventsToGeoJSON(eventIdx, '2026-01-01T03:00:00Z', posMap);
    expect(gj.features).toHaveLength(1);
    expect(gj.features[0].geometry.coordinates).toEqual([121.5, 25.5]); // 折疊位置（非錨點 0,0）
    expect(gj.features[0].properties.severity).toBe('high');
  });

  test('marker 無折疊位置（靜態手動標記）→ 退回錨點 lat/lon', () => {
    const eventIdx = [ev('2026-01-01T02:00:00Z', 'low', [{ uid: 'm2', lat: 24.3, lon: 120.7 }])];
    const gj = eventsToGeoJSON(eventIdx, '2026-01-01T03:00:00Z', new Map());
    expect(gj.features[0].geometry.coordinates).toEqual([120.7, 24.3]); // 錨點兜底
  });

  test('occurred_at > T 不畫；無 marker 不畫', () => {
    const future = ev('2026-01-01T05:00:00Z', 'high', [{ uid: 'm1', lat: 24, lon: 120 }]);
    const noMarker = ev('2026-01-01T01:00:00Z', 'high', []);
    const gj = eventsToGeoJSON([noMarker, future], '2026-01-01T03:00:00Z', new Map());
    expect(gj.features).toHaveLength(0);
  });

  test('primary marker 優先（多 marker N:1）', () => {
    const eventIdx = [ev('2026-01-01T02:00:00Z', 'high', [
      { uid: 'a', lat: 24.0, lon: 120.0 },
      { uid: 'b', lat: 25.0, lon: 121.0, role: 'primary' },
    ])];
    const gj = eventsToGeoJSON(eventIdx, '2026-01-01T03:00:00Z', new Map());
    expect(gj.features[0].geometry.coordinates).toEqual([121.0, 25.0]); // 取 primary（b）
  });

  test('#2：手動標記用 audit 位置歷史折疊（隨 T 移動，無 posMap）', () => {
    const eventIdx = [ev('2026-01-01T01:00:00Z', 'critical', [{
      uid: 'm', lat: 9, lon: 9,
      history: [['2026-01-01T02:00:00Z', 24.0, 120.0], ['2026-01-01T03:00:00Z', 25.0, 121.0]],
    }])];
    expect(eventsToGeoJSON(eventIdx, '2026-01-01T02:30:00Z', new Map())
      .features[0].geometry.coordinates).toEqual([120.0, 24.0]); // ≤T 取 02:00
    expect(eventsToGeoJSON(eventIdx, '2026-01-01T03:30:00Z', new Map())
      .features[0].geometry.coordinates).toEqual([121.0, 25.0]); // 移到 03:00
  });

  test('#3：military→milsymbol iconId；civil 有象形→glyph（同 live _renderZones）', () => {
    const mk = et => [{
      type: 'event', t: '2026-01-01T01:00:00Z',
      payload: { event_id: 'e', event_type: et, severity: 'info', description: 'd', markers: [{ uid: 'm', lat: 24, lon: 120 }] },
    }];
    const tax = { hazard: { regime: 'civil' }, qrf: { abbr: 'QR', regime: 'military', cot_type: 'a-f-G' } };
    // civil 有象形 → ◆ + glyph 前景
    const g = eventsToGeoJSON(mk('hazard'), '2026-01-01T02:00:00Z', new Map(), tax);
    expect(g.features[0].properties.regime).toBe('civil');
    expect(g.features[0].properties.fg).toBe('napsg-glyph-hazard');
    expect(g.features[0].properties.fg_glyph).toBe(true);
    expect(g.features[0].properties.label).toBe('d'); // #1：人類描述
    // military → milsymbol 2525 iconId（QRF 友軍方塊）
    const q = eventsToGeoJSON(mk('qrf'), '2026-01-01T02:00:00Z', new Map(), tax);
    expect(q.features[0].properties.regime).toBe('military');
    expect(q.features[0].properties.iconId).toMatch(/^mil-/);
    expect(q.features[0].properties.is_event).toBe(true);
  });
});
