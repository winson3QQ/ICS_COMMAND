// aar_replay_engine.test.js — P2-20(B) B1（#201）回放核心純函式
import { describe, expect, test } from 'vitest';

import {
  buildReplayIndex, foldPositionsAt, positionsToGeoJSON, stepSummary, fmtClock,
} from '../../static/js/aar/replay_engine.js';

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
