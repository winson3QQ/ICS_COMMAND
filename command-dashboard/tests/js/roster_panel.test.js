/**
 * roster_panel.test.js — 隊伍名冊純資料模型（#269 / #267；#358-2 改 faction 主軸）
 *
 * 鎖住 rosterModel（無 DOM 純邏輯）：
 *   - #358-2：**按 faction 分組**（藍 blue / 紅 red / 中立 neutral / 未分類 _none，未分類排末）
 *   - 成員資格：有 faction（admin 分類，即使自報 a-h 敵對）入編成；純敵情接觸（a-h 且無 faction）排除；
 *     友軍（a-f）無 faction → 列「未分類」
 *   - online/offline 以 stale > now 判定（與地圖 _isAging 同套；離線單位由後端即時移出 list）
 *   - _esc 完整跳脫含引號（屬性脈絡 XSS 防護）
 */
import { afterEach, describe, expect, test, vi } from 'vitest';

import { _esc, initRoster, renderRoster, rosterModel, scopeStatus, stopRoster } from '../../static/js/roster_panel.js';

const NOW = '2026-06-15T10:00:00Z';
// 預設友軍（a-f）；faction = admin 分類（blue/red/neutral/null）；online → stale 未過。
const U = (uid, faction, online, extra = {}) => ({
  uid,
  faction,
  type: 'a-f-G-U-C',
  stale: online ? '2026-06-15T10:05:00Z' : '2026-06-15T09:59:00Z',
  ...extra,
});

describe('rosterModel（#358-2 faction 主軸）', () => {
  test('按 faction 分組：藍/紅/中立/未分類，未分類排末', () => {
    const units = [
      U('a', 'blue', true),
      U('b', 'red', true),
      U('c', 'neutral', true),
      U('d', null, true), // 友軍無 faction → 未分類
      U('e', 'blue', false),
    ];
    const { groups } = rosterModel(units, NOW);
    expect(groups.map((g) => g[0])).toEqual(['blue', 'red', 'neutral', '_none']); // 固定序、未分類墊底
    expect(groups.find((g) => g[0] === 'blue')[1].map((e) => e.uid)).toEqual(['a', 'e']);
  });

  test('成員資格：有 team_color 的裝置（即使自報 a-h）入編成；marker（無 team_color，即使帶 faction）排除', () => {
    const units = [
      U('blue-f', 'blue', true), // 藍 + 自報友軍（a-f）
      // 藍方裝置但自報 a-h（敵對）：有 team_color = self-SA 端點 → 仍入藍軍（修 Q2）
      { uid: 'blue-h', faction: 'blue', type: 'a-h-G', team_color: 'Red', stale: '2026-06-15T10:05:00Z' },
      // marker：被分類連帶帶 faction=blue，但無 team_color（非 self-SA）→ 排除（修「隊伍多出 marker」）
      { uid: 'mk-blue', faction: 'blue', type: 'a-n-G', stale: '2026-06-15T10:05:00Z' },
      { uid: 'enemy', type: 'a-h-G', stale: '2026-06-15T10:05:00Z' }, // 純敵情接觸、無 team_color/faction → 排除
      { uid: 'fr-unc', type: 'a-f-G', stale: '2026-06-15T10:05:00Z' }, // 友軍無 faction → 未分類
    ];
    const { groups } = rosterModel(units, NOW);
    expect(groups.find((g) => g[0] === 'blue')[1].map((e) => e.uid).sort()).toEqual(['blue-f', 'blue-h']);
    expect(groups.find((g) => g[0] === '_none')[1].map((e) => e.uid)).toEqual(['fr-unc']);
    const all = groups.flatMap(([, l]) => l.map((e) => e.uid));
    expect(all).not.toContain('mk-blue'); // marker 不入編成（即使帶 faction）
    expect(all).not.toContain('enemy'); // 敵情接觸不入編成
  });

  test('online/offline 以 stale > now 判定', () => {
    const units = [U('a', 'blue', true), U('b', 'blue', false), U('c', 'red', true)];
    const { online, offline } = rosterModel(units, NOW);
    expect(online).toBe(2);
    expect(offline).toBe(1);
  });

  test('空清單 → 空 groups、0/0', () => {
    expect(rosterModel([], NOW)).toEqual({ groups: [], online: 0, offline: 0 });
    expect(rosterModel(undefined, NOW)).toEqual({ groups: [], online: 0, offline: 0 });
  });

  test('faction 前後空白正規化（不另立一組）', () => {
    const units = [
      U('a', 'blue', true),
      { uid: 'b', faction: '  blue ', type: 'a-f-G', stale: '2026-06-15T10:05:00Z' },
    ];
    const { groups } = rosterModel(units, NOW);
    expect(groups).toHaveLength(1);
    expect(groups[0][0]).toBe('blue');
    expect(groups[0][1]).toHaveLength(2);
  });

  test('有 faction 但無 stale 視為離線', () => {
    const { online, offline } = rosterModel([{ uid: 'x', faction: 'blue', type: 'a-f-G' }], NOW);
    expect(online).toBe(0);
    expect(offline).toBe(1);
  });

  test('常駐(NULL exercise)單位仍列入名冊（#267 enroll 候選）', () => {
    const units = [
      { uid: 'in', faction: 'blue', type: 'a-f-G', exercise_id: 5, stale: '2026-06-15T10:05:00Z' },
      { uid: 'stand', faction: 'blue', type: 'a-f-G', exercise_id: null, stale: '2026-06-15T10:05:00Z' },
    ];
    const uids = rosterModel(units, NOW).groups.flatMap(([, l]) => l.map((e) => e.uid));
    expect(uids).toContain('in');
    expect(uids).toContain('stand'); // 常駐也列入（候選）
  });

  // #267 純乙可用性：scopeStatus（連線/在場計數 + 空場警示），只算「在線編成單位」。
  test('scopeStatus：空場（有人連線、無人在場）→ warn + gap=connected', () => {
    const units = [
      { uid: 'a', faction: 'blue', type: 'a-f-G', exercise_id: null, stale: '2026-06-15T10:05:00Z' },
      { uid: 'b', faction: 'blue', type: 'a-f-G', exercise_id: null, stale: '2026-06-15T10:05:00Z' },
    ];
    expect(scopeStatus(units, NOW)).toEqual({ connected: 2, scoped: 0, warn: true, gap: 2 });
  });

  test('scopeStatus：部分在場 → 不 warn、gap=未納編數', () => {
    const units = [
      { uid: 'a', faction: 'blue', type: 'a-f-G', exercise_id: 7, stale: '2026-06-15T10:05:00Z' },
      { uid: 'b', faction: 'blue', type: 'a-f-G', exercise_id: null, stale: '2026-06-15T10:05:00Z' },
    ];
    expect(scopeStatus(units, NOW)).toEqual({ connected: 2, scoped: 1, warn: false, gap: 1 });
  });

  test('scopeStatus：離線單位不計、marker 不計', () => {
    const units = [
      { uid: 'a', faction: 'blue', type: 'a-f-G', exercise_id: 7, stale: '2026-06-15T10:05:00Z' }, // 在場在線
      { uid: 'b', faction: 'blue', type: 'a-f-G', exercise_id: 7, stale: '2026-06-15T09:00:00Z' }, // 離線 → 不計
      { uid: 'mk', faction: 'blue', type: 'a-n-G', exercise_id: 7, stale: '2026-06-15T10:05:00Z' }, // marker → 不計
    ];
    expect(scopeStatus(units, NOW)).toEqual({ connected: 1, scoped: 1, warn: false, gap: 0 });
  });

  test('scopeStatus：無連線 → warn 但 gap=0（指引連入而非全加）', () => {
    expect(scopeStatus([], NOW)).toEqual({ connected: 0, scoped: 0, warn: true, gap: 0 });
  });

  test('#292 _esc 完整跳脫含引號（屬性脈絡 XSS 防護）', () => {
    // data-uid="${_esc(uid)}" 之屬性脈絡：引號必須跳脫，否則 CoT 偽造 uid 可突破屬性
    expect(_esc('a"b')).toBe('a&quot;b');
    expect(_esc("a'b")).toBe('a&#39;b');
    expect(_esc('<script>')).toBe('&lt;script&gt;');
    expect(_esc('a&b')).toBe('a&amp;b');
    // 攻擊 payload：屬性突破 + 事件處理器
    expect(_esc('x" onmouseover="alert(1)')).toBe('x&quot; onmouseover=&quot;alert(1)');
    expect(_esc(null)).toBe('');
    expect(_esc(undefined)).toBe('');
  });
});

// #267 純乙可用性：renderRoster 的 DOM 接線（無 live preview 可驗 → 以最小 document stub 驗渲染結果）。
describe('renderRoster top section（#267 空場警示 + 加入全部連線）', () => {
  // 最小 element stub：捕捉 innerHTML、吞掉 addEventListener / style。
  function makeEl() {
    return { innerHTML: '', textContent: '', style: {}, addEventListener() {} };
  }
  function setupDoc(bodyEl) {
    const els = { 'roster-body': bodyEl, 'roster-tab-badge': makeEl() };
    globalThis.document = { getElementById: (id) => els[id] || null, addEventListener() {} };
  }
  const onUnit = (uid, exId) => ({
    uid, faction: 'blue', type: 'a-f-G-U-C', exercise_id: exId, stale: '2999-01-01T00:00:00Z',
  });

  afterEach(() => {
    stopRoster();
    vi.restoreAllMocks();
  });

  test('演習中 + 有人連線但無人在場 → 顯空場警示 + 「加入全部連線」鈕', () => {
    const body = makeEl();
    setupDoc(body);
    initRoster({
      getTakUnits: () => [onUnit('a', null), onUnit('b', null)],
      getHasActiveExercise: () => true,
      onAddConnected: async () => {},
    });
    renderRoster();
    expect(body.innerHTML).toContain('無單位在場');
    expect(body.innerHTML).toContain('data-roster-addall');
    expect(body.innerHTML).toContain('加入全部連線 (2)'); // 鈕帶在線數
    expect(body.innerHTML).toContain('2 連線');
  });

  test('無 onAddConnected（非指揮層）→ 警示在、但不出全加鈕', () => {
    const body = makeEl();
    setupDoc(body);
    initRoster({ getTakUnits: () => [onUnit('a', null)], getHasActiveExercise: () => true });
    renderRoster();
    expect(body.innerHTML).toContain('無單位在場');
    expect(body.innerHTML).not.toContain('data-roster-addall');
  });

  test('已全員在場 → 無警示、無 gap 提示', () => {
    const body = makeEl();
    setupDoc(body);
    initRoster({
      getTakUnits: () => [onUnit('a', 7), onUnit('b', 7)],
      getHasActiveExercise: () => true,
      onAddConnected: async () => {},
    });
    renderRoster();
    expect(body.innerHTML).not.toContain('無單位在場');
    expect(body.innerHTML).not.toContain('未納編');
    expect(body.innerHTML).toContain('2 在場');
  });

  test('無 active 演習 → 不顯 top section（無在場概念）', () => {
    const body = makeEl();
    setupDoc(body);
    initRoster({
      getTakUnits: () => [onUnit('a', null)],
      getHasActiveExercise: () => false,
      onAddConnected: async () => {},
    });
    renderRoster();
    expect(body.innerHTML).not.toContain('連線');
    expect(body.innerHTML).not.toContain('data-roster-addall');
  });
});
