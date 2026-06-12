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
  mergeLiveChat, parseSenderUid, filterChatsBySender,
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

describe('mergeLiveChat — b2 即時通聯併入（去重 + 升序附末端）', () => {
  test('新 id → 附在末端（最新在下）', () => {
    const out = mergeLiveChat([{ id: 1 }, { id: 2 }], { id: 3, message: 'x' });
    expect(out.map(c => c.id)).toEqual([1, 2, 3]);
  });
  test('重複 id → 回原陣列參考（不重繪訊號）', () => {
    const cur = [{ id: 1 }, { id: 2 }];
    expect(mergeLiveChat(cur, { id: 2 })).toBe(cur);   // 同參考
  });
  test('null / 無 id → 原陣列不動', () => {
    const cur = [{ id: 1 }];
    expect(mergeLiveChat(cur, null)).toBe(cur);
    expect(mergeLiveChat(cur, { message: 'no id' })).toBe(cur);
  });
  test('空陣列 + 一筆 → 單元素新陣列', () => {
    expect(mergeLiveChat([], { id: 5 }).map(c => c.id)).toEqual([5]);
  });
  test('依 (t,id) 排序：遲到（較舊 t）訊息插到正確位置，不卡末端', () => {
    const cur = [{ id: 1, t: '2026-06-05T04:00:00Z' }, { id: 2, t: '2026-06-05T04:02:00Z' }];
    const out = mergeLiveChat(cur, { id: 3, t: '2026-06-05T04:01:00Z' }); // t 介於 1 與 2
    expect(out.map(c => c.id)).toEqual([1, 3, 2]);
  });
});

describe('parseSenderUid — GeoChat.<uid>.<room>.<id> → marker uid（b3-1）', () => {
  test('GeoChat 格式取中段 senderUid', () => {
    expect(parseSenderUid('GeoChat.AC4B3A0B-E4DE.All Chat Rooms.abc123')).toBe('AC4B3A0B-E4DE');
  });
  test('非 GeoChat 格式原樣回傳', () => {
    expect(parseSenderUid('ANDROID-359975090666199')).toBe('ANDROID-359975090666199');
  });
  test('非字串原樣（防禦）', () => {
    expect(parseSenderUid(null)).toBe(null);
    expect(parseSenderUid(undefined)).toBe(undefined);
  });
});

describe('filterChatsBySender — by-sender 過濾（uid 精準比對，b3-1）', () => {
  const chats = [
    { id: 1, sender_uid: 'GeoChat.UNIT-A.All Chat Rooms.x1', callsign: 'ALPHA' },
    { id: 2, sender_uid: 'GeoChat.UNIT-B.Alpha.x2', callsign: 'BRAVO' },
    { id: 3, sender_uid: 'UNIT-A', callsign: 'ALPHA' },  // 非 GeoChat 格式但同 uid
  ];
  test('依解析 uid 比對（含非 GeoChat 格式的同 uid）', () => {
    expect(filterChatsBySender(chats, { uid: 'UNIT-A' }).map(c => c.id)).toEqual([1, 3]);
  });
  test('callsign 不參與比對（uid 不符即不命中，避免同 callsign 跨單位誤混）', () => {
    // id 1、3 callsign=ALPHA，但 uid 不是 NOPE → 不該命中
    expect(filterChatsBySender(chats, { uid: 'NOPE', callsign: 'ALPHA' })).toHaveLength(0);
  });
  test('無 sender → 不過濾', () => {
    expect(filterChatsBySender(chats, null)).toHaveLength(3);
    expect(filterChatsBySender(chats, {})).toHaveLength(3);
  });
});
