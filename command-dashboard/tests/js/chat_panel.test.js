/**
 * tests/js/chat_panel.test.js — 右欄通聯面板純邏輯（#213 b1）
 *
 * 只測無 DOM 依賴的純函式（decode / room 派生 / 過濾 / 未讀計數）；DOM 渲染與 tab/poll
 * 行為由 preview + human verify 覆蓋。auth.js 以 vi.mock 樁掉（避開 top-level location.origin）。
 */
import { describe, expect, test, vi } from 'vitest';

vi.mock('../../static/js/auth.js', () => ({
  API_BASE: '',
  authFetch: () => Promise.resolve({ ok: true, json: async () => ({ chats: [] }) }),
  getToken: () => 't',
}));

import {
  decodeChatMessage, roomLabel, distinctRooms, filterChatsByRoom, countUnread, maxChatId,
} from '../../static/js/chat_panel.js';

describe('decodeChatMessage — 還原 html.escape 的固定 5 實體（純文字、無 innerHTML）', () => {
  test('script 標籤實體還原為可見文字（不執行）', () => {
    expect(decodeChatMessage('&lt;script&gt;alert(1)&lt;/script&gt;'))
      .toBe('<script>alert(1)</script>');
  });
  test('引號 / 單引號 / & 還原', () => {
    expect(decodeChatMessage('can&#x27;t say &quot;hi&quot; &amp; bye'))
      .toBe('can\'t say "hi" & bye');
  });
  test('&amp; 最後還原，避免二次解碼（&amp;lt; → &lt; 非 <）', () => {
    expect(decodeChatMessage('&amp;lt;')).toBe('&lt;');
  });
  test('null / undefined → 空字串', () => {
    expect(decodeChatMessage(null)).toBe('');
    expect(decodeChatMessage(undefined)).toBe('');
  });
});

describe('roomLabel — 空 group → 直接（DM）', () => {
  test('null / 空字串 → 直接', () => {
    expect(roomLabel(null)).toBe('直接');
    expect(roomLabel('')).toBe('直接');
  });
  test('有房間名原樣', () => {
    expect(roomLabel('All Chat Rooms')).toBe('All Chat Rooms');
  });
});

describe('distinctRooms — 動態房間（依首次出現序、不寫死清單）', () => {
  test('去重保序，空 group 歸「直接」', () => {
    const chats = [
      { group: 'All Chat Rooms' }, { group: 'Alpha' },
      { group: 'All Chat Rooms' }, { group: null },
    ];
    expect(distinctRooms(chats)).toEqual(['All Chat Rooms', 'Alpha', '直接']);
  });
  test('空輸入 → 空陣列', () => {
    expect(distinctRooms([])).toEqual([]);
    expect(distinctRooms(null)).toEqual([]);
  });
});

describe('filterChatsByRoom', () => {
  const chats = [
    { id: 1, group: 'Alpha' }, { id: 2, group: null }, { id: 3, group: 'Alpha' },
  ];
  test('__all__ / null → 不過濾', () => {
    expect(filterChatsByRoom(chats, '__all__')).toHaveLength(3);
    expect(filterChatsByRoom(chats, null)).toHaveLength(3);
  });
  test('指定房間 → 只該房間', () => {
    expect(filterChatsByRoom(chats, 'Alpha').map(c => c.id)).toEqual([1, 3]);
  });
  test('「直接」→ 空 group', () => {
    expect(filterChatsByRoom(chats, '直接').map(c => c.id)).toEqual([2]);
  });
});

describe('countUnread / maxChatId — 未讀水位（id 單調遞增）', () => {
  const chats = [{ id: 3 }, { id: 4 }, { id: 7 }];
  test('countUnread = id > lastSeen 的筆數', () => {
    expect(countUnread(chats, 4)).toBe(1);   // 只有 7
    expect(countUnread(chats, 0)).toBe(3);
    expect(countUnread(chats, 7)).toBe(0);
  });
  test('maxChatId 取最大；空 → fallback', () => {
    expect(maxChatId(chats, 0)).toBe(7);
    expect(maxChatId([], 5)).toBe(5);
  });
});
