/**
 * roster_panel.test.js — 隊伍名冊純資料模型（#269 / #267 切片1）
 *
 * 鎖住 rosterModel（無 DOM 純邏輯）：
 *   - 只列友軍（a-f-*）；敵性/中立/不明接觸不入名冊
 *   - 按 team_color 分組；未分組（無 <__group>）排最後
 *   - online/offline 以 stale > now 判定（與地圖 _isAging 同套；離線單位由後端即時移出 list）
 *   - 隊名排序穩定（除未分組外字典序）
 */
import { describe, expect, test } from 'vitest';

import { rosterModel } from '../../static/js/roster_panel.js';

const NOW = '2026-06-15T10:00:00Z';
const U = (uid, team, online, extra = {}) => ({
  uid,
  team_color: team,
  type: 'a-f-G-U-C', // 友軍（預設）；敵情測試另傳 type
  // online → stale 在未來；offline → stale 在過去
  stale: online ? '2026-06-15T10:05:00Z' : '2026-06-15T09:59:00Z',
  ...extra,
});

describe('rosterModel', () => {
  test('按 team_color 分組，未分組排最後', () => {
    const units = [
      U('a', 'Orange', true),
      U('b', 'Cyan', true),
      U('c', '', false), // 無 team → 未分組
      U('d', 'Orange', false),
    ];
    const { groups } = rosterModel(units, NOW);
    const names = groups.map((g) => g[0]);
    expect(names).toEqual(['Cyan', 'Orange', '未分組']); // 字典序 + 未分組墊底
    expect(groups.find((g) => g[0] === 'Orange')[1].map((e) => e.uid)).toEqual(['a', 'd']);
  });

  test('online/offline 以 stale > now 判定', () => {
    const units = [U('a', 'Orange', true), U('b', 'Orange', false), U('c', 'Cyan', true)];
    const { online, offline } = rosterModel(units, NOW);
    expect(online).toBe(2);
    expect(offline).toBe(1);
  });

  test('空清單 → 空 groups、0/0', () => {
    expect(rosterModel([], NOW)).toEqual({ groups: [], online: 0, offline: 0 });
    expect(rosterModel(undefined, NOW)).toEqual({ groups: [], online: 0, offline: 0 });
  });

  test('team_color 前後空白正規化（不另立一組）', () => {
    const { groups } = rosterModel([U('a', 'Orange', true), U('b', '  Orange ', true)], NOW);
    expect(groups).toHaveLength(1);
    expect(groups[0][1]).toHaveLength(2);
  });

  test('友軍無 stale 視為離線', () => {
    const { online, offline } = rosterModel([{ uid: 'x', team_color: 'Cyan', type: 'a-f-G' }], NOW);
    expect(online).toBe(0);
    expect(offline).toBe(1);
  });

  test('常駐(NULL exercise)友軍單位仍列入名冊（#267 enroll 候選）', () => {
    const units = [
      { uid: 'in', team_color: 'Orange', type: 'a-f-G', exercise_id: 5, stale: '2026-06-15T10:05:00Z' },
      { uid: 'stand', team_color: 'Orange', type: 'a-f-G', exercise_id: null, stale: '2026-06-15T10:05:00Z' },
    ];
    const uids = rosterModel(units, NOW).groups.flatMap(([, l]) => l.map((e) => e.uid));
    expect(uids).toContain('in');
    expect(uids).toContain('stand'); // 常駐也列入（候選）
  });

  test('只列友軍：敵性/中立/不明接觸不入名冊', () => {
    const units = [
      U('me', 'Orange', true), // a-f 友軍
      { uid: 'enemy', team_color: 'Orange', type: 'a-h-G', stale: '2026-06-15T10:05:00Z' }, // 敵性接觸
      { uid: 'neutral', type: 'a-n-G', stale: '2026-06-15T10:05:00Z' }, // 中立
      { uid: 'unk', type: 'a-u-G', stale: '2026-06-15T10:05:00Z' }, // 不明
    ];
    const { groups, online, offline } = rosterModel(units, NOW);
    // 只剩友軍 me（Orange）
    expect(groups).toHaveLength(1);
    expect(groups[0][0]).toBe('Orange');
    expect(groups[0][1].map((e) => e.uid)).toEqual(['me']);
    expect(online + offline).toBe(1); // 敵情不計入名冊在線/離線
  });
});
