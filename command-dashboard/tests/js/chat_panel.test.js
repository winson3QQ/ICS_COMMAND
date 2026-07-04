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
  isLoggedIn: () => true,
  hasAnyRole: () => true,
  canSendChat: () => true,
}));

import {
  decodeChatMessage, roomLabel, distinctRooms, filterChatsByRoom, countUnread, maxChatId,
  mergeLiveChat, parseSenderUid, filterChatsBySender, clampWatermark,
  buildChatBody, composeTargetLabel,
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

describe('roomLabel — 空 group → 直接（DM）；廣播別名 → 廣播（#250 locale 收斂）', () => {
  test('null / 空字串 → 直接', () => {
    expect(roomLabel(null)).toBe('直接');
    expect(roomLabel('')).toBe('直接');
  });
  test('廣播房中英 locale 別名 → 廣播（#250）', () => {
    expect(roomLabel('All Chat Rooms')).toBe('廣播');   // iTAK 英文
    expect(roomLabel('所有聊天室')).toBe('廣播');         // ATAK 中文
  });
  test('DM 房間名（收件人 callsign）原樣', () => {
    expect(roomLabel('3QQaTak')).toBe('3QQaTak');
  });
});

describe('distinctRooms — #250：廣播併入「全部」（不另立房 chip）+ locale 收斂', () => {
  test('廣播房（中英別名）不成 chip；DM 房保序去重', () => {
    const chats = [
      { group: 'All Chat Rooms' }, { group: 'Alpha' },
      { group: '所有聊天室' }, { group: 'Alpha' }, { group: null },
    ];
    // All Chat Rooms / 所有聊天室 皆併入「全部」→ 不出現；剩 DM 房 Alpha + 直接
    expect(distinctRooms(chats)).toEqual(['Alpha', '直接']);
  });
  test('只有廣播 → 無房 chip（空陣列，chips bar 隱藏）', () => {
    expect(distinctRooms([{ group: 'All Chat Rooms' }, { group: '所有聊天室' }])).toEqual([]);
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

describe('clampWatermark — #250 fix：stale 高水位夾回（DB rollover）', () => {
  test('水位高於現有 max（跨 DB / reset）→ 夾回 max（可下降）', () => {
    // 舊 DB 水位=100，新 DB 只到 50 → 夾回 50，否則新 chat（id≤50）永不算未讀
    expect(clampWatermark(100, [{ id: 1 }, { id: 50 }])).toBe(50);
  });
  test('水位 ≤ 現有 max（正常單調 DB）→ 不動', () => {
    expect(clampWatermark(50, [{ id: 60 }, { id: 55 }])).toBe(50);
    expect(clampWatermark(60, [{ id: 60 }])).toBe(60);  // 等於也不夾
  });
  test('空 chats → 不夾（無資訊，維持持久水位）', () => {
    expect(clampWatermark(100, [])).toBe(100);
    expect(clampWatermark(100, null)).toBe(100);
  });
  test('夾回後 countUnread 能正常算新 chat', () => {
    // 模擬：夾回 50 後，來一筆 id=51 → 應算 1 筆未讀
    const w = clampWatermark(100, [{ id: 50 }]);
    expect(countUnread([{ id: 50 }, { id: 51 }], w)).toBe(1);
  });
});

describe('buildChatBody — #216 出向 compose 目標路由（脈絡 → POST body）', () => {
  test('無單位無房 → 全體廣播（僅 message）', () => {
    expect(buildChatBody('hi', null, '__all__')).toEqual({ message: 'hi' });
  });
  test('選命名房 → chatroom（非 __all__）', () => {
    expect(buildChatBody('hi', null, 'Blue Team')).toEqual({ message: 'hi', chatroom: 'Blue Team' });
  });
  test('選單位 → DM（recipient_uid + callsign）', () => {
    expect(buildChatBody('hi', { uid: 'ANDROID-9', callsign: 'BRAVO' }, '__all__'))
      .toEqual({ message: 'hi', recipient_uid: 'ANDROID-9', recipient_callsign: 'BRAVO' });
  });
  test('選單位無 callsign → 只帶 recipient_uid', () => {
    expect(buildChatBody('hi', { uid: 'ANDROID-9' }, '__all__'))
      .toEqual({ message: 'hi', recipient_uid: 'ANDROID-9' });
  });
  test('單位優先於房（同時存在時走 DM，不帶 chatroom）', () => {
    const b = buildChatBody('hi', { uid: 'U1' }, 'Blue Team');
    expect(b.recipient_uid).toBe('U1');
    expect(b.chatroom).toBeUndefined();
  });
});

describe('composeTargetLabel — #216 送出目標提示文字', () => {
  test('全體廣播', () => {
    expect(composeTargetLabel(null, '__all__')).toBe('→ 廣播（全體）');
  });
  test('命名房', () => {
    expect(composeTargetLabel(null, 'Blue Team')).toBe('→ Blue Team');
  });
  test('DM 帶 callsign / 退 uid', () => {
    expect(composeTargetLabel({ uid: 'U1', callsign: 'BRAVO' }, '__all__')).toBe('→ 私訊 BRAVO');
    expect(composeTargetLabel({ uid: 'U1' }, '__all__')).toBe('→ 私訊 U1');
  });
});
