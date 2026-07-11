// SPDX-License-Identifier: LicenseRef-Proprietary
// Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
/**
 * tak_photos.test.js — #503 上行：TAK file store 照片附件 loader（map/tak_photos.js）
 *
 * Vitest default env = node（無 DOM）→ loader 本就注入 doc，餵 minimal fake doc（不引 jsdom）。
 * 驗（fake authFetch + fake doc）：
 *   - 有照片 → 逐張建 <a><img>，img.src / a.href 綁 objectURL、target=_blank
 *   - 空清單 → 「無照片」；422 → 「TAK 未配置」；HTTP 錯 → 「載入失敗」
 *   - 無 hash 的項目跳過；單張下載失敗只標該格（✕），不炸整區
 *   - 檔名走 property（title）非 innerHTML → 不可信檔名不成 XSS
 *   - 開新 modal 前 revoke 舊 blob（不累積洩漏）；uid 空 / grid 不在 → 安全 no-op
 */

import { describe, test, expect, beforeEach, vi } from 'vitest';
import {
  takPhotoSectionHtml,
  createTakPhotoLoader,
  TAK_PHOTO_GRID_ID,
  TAK_PHOTO_UPLOAD_ID,
  TAK_PHOTO_PUSH_ID,
  TAK_PHOTO_PUSH_DEST_ID,
} from '../../static/js/map/tak_photos.js';

// ── Minimal fake DOM（注入 doc，不引 jsdom）──
function makeFakeEl(tag) {
  const el = {
    tagName: (tag || 'div').toUpperCase(),
    children: [],
    style: {},
    _text: '',
    appendChild(c) {
      c._parent = this;
      this.children.push(c);
      return c;
    },
    remove() {
      if (this._parent) {
        const i = this._parent.children.indexOf(this);
        if (i >= 0) this._parent.children.splice(i, 1);
      }
    },
    querySelectorAll(sel) {
      const want = sel.toUpperCase();
      const out = [];
      (function walk(n) {
        for (const c of n.children) {
          if (c.tagName === want) out.push(c);
          walk(c);
        }
      })(el);
      return out;
    },
    querySelector(sel) {
      return this.querySelectorAll(sel)[0] || null;
    },
    _listeners: {},
    addEventListener(type, fn) {
      (this._listeners[type] ??= []).push(fn);
    },
    async _fire(type) {
      for (const fn of this._listeners[type] || []) await fn();
    },
  };
  Object.defineProperty(el, 'textContent', {
    get() {
      return el._text;
    },
    set(v) {
      el._text = v;
      el.children = []; // textContent 設值 → 清空子節點（對齊真 DOM）
    },
  });
  return el;
}

function makeFakeDoc() {
  const byId = {};
  return {
    body: makeFakeEl('body'),
    getElementById: (id) => byId[id] || null,
    createElement: (tag) => makeFakeEl(tag),
    _register: (id, el) => {
      byId[id] = el;
    },
    _drop: (id) => {
      delete byId[id];
    },
  };
}

let doc, grid, created, revoked;
beforeEach(() => {
  created = [];
  revoked = [];
  let n = 0;
  URL.createObjectURL = vi.fn(() => {
    const u = `blob:fake-${n++}`;
    created.push(u);
    return u;
  });
  URL.revokeObjectURL = vi.fn((u) => revoked.push(u));
  doc = makeFakeDoc();
  grid = makeFakeEl('div');
  doc._register(TAK_PHOTO_GRID_ID, grid);
});

// _bindPhoto 是 fire-and-forget（漸進顯示，不擋 load）→ 檢查「下載完成後」狀態前先 flush microtask。
const flush = () => new Promise((r) => setTimeout(r, 0));

function _resp({ ok = true, status = 200, files = null } = {}) {
  return {
    ok,
    status,
    json: async () => ({ files }),
    blob: async () => new Blob([new Uint8Array([1, 2, 3])]),
  };
}

/** authFetch stub：/for-entity/ → 清單；否則單張下載（dlOk 決定成敗）。 */
function _fakeAuthFetch({ list, dlOk = true } = {}) {
  return vi.fn(async (url) => {
    if (url.includes('/for-entity/')) return list;
    return _resp({ ok: dlOk, status: dlOk ? 200 : 404 });
  });
}

describe('takPhotoSectionHtml', () => {
  test('含 grid 容器與標題', () => {
    const html = takPhotoSectionHtml();
    expect(html).toContain(`id="${TAK_PHOTO_GRID_ID}"`);
    expect(html).toContain('TAK 照片附件');
  });
});

describe('createTakPhotoLoader', () => {
  test('authFetch 缺 → 建構即拋', () => {
    expect(() => createTakPhotoLoader({})).toThrow(/authFetch/);
  });

  test('有照片 → 逐張建 img，src/href 綁 objectURL、target=_blank', async () => {
    const list = _resp({ files: [{ hash: 'a'.repeat(64), name: 'p1.jpg' }, { hash: 'b'.repeat(64), name: 'p2.png' }] });
    const loader = createTakPhotoLoader({ authFetch: _fakeAuthFetch({ list }), doc });
    await loader.load('U-1');
    await flush();
    expect(grid.querySelectorAll('A').length).toBe(2);
    const imgs = grid.querySelectorAll('IMG');
    expect(imgs.length).toBe(2);
    expect(imgs[0].src).toMatch(/^blob:fake-/);
    const cell = grid.querySelectorAll('A')[0];
    expect(cell.href).toMatch(/^blob:fake-/);
    expect(cell.target).toBe('_blank');
    expect(cell.rel).toBe('noopener');
  });

  test('檔名走 title property（非 innerHTML）→ 不可信檔名不成 XSS', async () => {
    const evil = '<img src=x onerror=alert(1)>';
    const list = _resp({ files: [{ hash: 'c'.repeat(64), name: evil }] });
    const loader = createTakPhotoLoader({ authFetch: _fakeAuthFetch({ list }), doc });
    await loader.load('U-1');
    expect(grid.querySelectorAll('A').length).toBe(1); // 沒因惡意檔名生出額外節點
    expect(grid.querySelector('A').title).toBe(evil); // 原樣存 property、未被當標記解析
  });

  test('空清單 → 無照片', async () => {
    const loader = createTakPhotoLoader({ authFetch: _fakeAuthFetch({ list: _resp({ files: [] }) }), doc });
    await loader.load('U-1');
    expect(grid.textContent).toBe('無照片');
  });

  test('422 → TAK 未配置', async () => {
    const loader = createTakPhotoLoader({ authFetch: _fakeAuthFetch({ list: _resp({ ok: false, status: 422 }) }), doc });
    await loader.load('U-1');
    expect(grid.textContent).toBe('TAK 未配置');
  });

  test('清單 HTTP 錯（503）→ 載入失敗', async () => {
    const loader = createTakPhotoLoader({ authFetch: _fakeAuthFetch({ list: _resp({ ok: false, status: 503 }) }), doc });
    await loader.load('U-1');
    expect(grid.textContent).toBe('載入失敗');
  });

  test('無 hash 的項目跳過', async () => {
    const list = _resp({ files: [{ hash: '', name: 'x' }, { hash: 'd'.repeat(64), name: 'ok' }] });
    const loader = createTakPhotoLoader({ authFetch: _fakeAuthFetch({ list }), doc });
    await loader.load('U-1');
    expect(grid.querySelectorAll('A').length).toBe(1);
  });

  test('單張下載失敗 → 該格標 ✕，不炸整區', async () => {
    const list = _resp({ files: [{ hash: 'e'.repeat(64), name: 'p.jpg' }] });
    const loader = createTakPhotoLoader({ authFetch: _fakeAuthFetch({ list, dlOk: false }), doc });
    await loader.load('U-1');
    await flush();
    const img = grid.querySelector('IMG');
    expect(img.alt).toBe('✕');
    expect(img.src).toBeUndefined(); // 沒綁 objectURL
  });

  test('uid 空 / grid 不在 → 安全 no-op（不拋）', async () => {
    const loader = createTakPhotoLoader({ authFetch: _fakeAuthFetch({ list: _resp({ files: [] }) }), doc });
    await loader.load(''); // 空 uid
    doc._drop(TAK_PHOTO_GRID_ID); // grid 不在
    await loader.load('U-1');
  });

  test('再次 load → revoke 前一批 blob（不累積洩漏）', async () => {
    const list = _resp({ files: [{ hash: 'f'.repeat(64), name: 'p.jpg' }] });
    const loader = createTakPhotoLoader({ authFetch: _fakeAuthFetch({ list }), doc });
    await loader.load('U-1');
    await flush();
    expect(created.length).toBe(1);
    grid = makeFakeEl('div'); // 模擬重開 modal（grid 重置）
    doc._register(TAK_PHOTO_GRID_ID, grid);
    await loader.load('U-2');
    await flush();
    expect(revoked).toContain(created[0]);
  });
});

// ── #506 M3 下行：upload / bindUpload ──
describe('takPhotoSectionHtml upload 控制', () => {
  test('canUpload=true 含上傳控制', () => {
    const h = takPhotoSectionHtml(true);
    expect(h).toContain(`id="${TAK_PHOTO_UPLOAD_ID}"`);
    expect(h).toContain('上傳照片');
  });
  test('canUpload=false（預設）不含上傳控制', () => {
    expect(takPhotoSectionHtml(false)).not.toContain(TAK_PHOTO_UPLOAD_ID);
    expect(takPhotoSectionHtml()).not.toContain(TAK_PHOTO_UPLOAD_ID);
  });
});

describe('upload / bindUpload', () => {
  test('upload POST multipart 到 /api/tak/files/upload', async () => {
    let captured;
    const af = vi.fn(async (url, opts) => {
      captured = { url, opts };
      return { ok: true };
    });
    const loader = createTakPhotoLoader({ authFetch: af, doc });
    const ok = await loader.upload('U-1', new Blob([new Uint8Array([1, 2])], { type: 'image/png' }));
    expect(ok).toBe(true);
    expect(captured.url).toBe('/api/tak/files/upload');
    expect(captured.opts.method).toBe('POST');
    expect(captured.opts.body).toBeInstanceOf(FormData);
    expect(captured.opts.body.get('marker_uid')).toBe('U-1');
  });

  test('bindUpload：選檔 → 上傳成功 → reload', async () => {
    const input = makeFakeEl('input');
    input.files = [new Blob([new Uint8Array([1])], { type: 'image/png' })];
    input.value = 'x';
    doc._register(TAK_PHOTO_UPLOAD_ID, input);
    const calls = [];
    const af = vi.fn(async (url) => {
      calls.push(url);
      if (url.includes('/upload')) return { ok: true };
      return { ok: true, status: 200, json: async () => ({ files: [] }) }; // reload → for-entity
    });
    const loader = createTakPhotoLoader({ authFetch: af, doc });
    loader.bindUpload('U-1');
    await input._fire('change');
    expect(calls.some((u) => u.includes('/api/tak/files/upload'))).toBe(true);
    expect(calls.some((u) => u.includes('/for-entity/U-1'))).toBe(true); // 上傳後 reload
    expect(input.value).toBe(''); // 清空允許再傳
  });

  test('bindUpload：無 input → 安全 no-op', () => {
    const loader = createTakPhotoLoader({ authFetch: vi.fn(), doc });
    loader.bindUpload('U-1'); // 沒註冊 input → 不拋
  });
});

// ── #509-P3 下行：push / bindPush / buildPushDialog（推照片到現場，彈窗選收件人）──
describe('takPhotoSectionHtml push 控制', () => {
  test('canUpload=true 含推送控制（收件人選擇移到彈窗，面板不再有 inline select）', () => {
    const h = takPhotoSectionHtml(true);
    expect(h).toContain(`id="${TAK_PHOTO_PUSH_ID}"`);
    expect(h).toContain('推照片到現場');
    expect(h).not.toContain(TAK_PHOTO_PUSH_DEST_ID); // 舊 inline 多選框已移除
  });
  test('canUpload=false 不含推送控制', () => {
    expect(takPhotoSectionHtml(false)).not.toContain(TAK_PHOTO_PUSH_ID);
  });
  test('#509-P3 乙 pushOnly：只出推送控制，無上行 grid / 上傳', () => {
    const h = takPhotoSectionHtml(true, { pushOnly: true });
    expect(h).toContain(`id="${TAK_PHOTO_PUSH_ID}"`);
    expect(h).not.toContain(TAK_PHOTO_GRID_ID); // 無上行 grid
    expect(h).not.toContain(TAK_PHOTO_UPLOAD_ID); // 無上傳
  });
  test('pushOnly 但非指揮層 → 空字串（無任何控制）', () => {
    expect(takPhotoSectionHtml(false, { pushOnly: true })).toBe('');
  });
});

describe('pushToField', () => {
  test('廣播：POST /downlink/photo，不帶 dest', async () => {
    let captured;
    const af = vi.fn(async (url, opts) => ((captured = { url, opts }), { ok: true }));
    const loader = createTakPhotoLoader({ authFetch: af, doc });
    const ok = await loader.pushToField('U-1', new Blob([new Uint8Array([1])], { type: 'image/jpeg' }), '');
    expect(ok).toBe(true);
    expect(captured.url).toBe('/api/tak/downlink/photo');
    expect(captured.opts.body.get('marker_uid')).toBe('U-1');
    expect(captured.opts.body.get('dest')).toBeNull(); // 廣播不帶 dest
  });
  test('點對點：帶 dest（逗號分隔）', async () => {
    let captured;
    const af = vi.fn(async (url, opts) => ((captured = { url, opts }), { ok: true }));
    const loader = createTakPhotoLoader({ authFetch: af, doc });
    await loader.pushToField('U-1', new Blob([new Uint8Array([1])], { type: 'image/jpeg' }), '3QQ-iTAK,3QQ-atak');
    expect(captured.opts.body.get('dest')).toBe('3QQ-iTAK,3QQ-atak');
  });
  test('fetchClients 失敗 → 回空（仍可廣播）', async () => {
    const loader = createTakPhotoLoader({ authFetch: vi.fn(async () => ({ ok: false })), doc });
    expect(await loader.fetchClients()).toEqual([]);
  });
});

describe('buildPushDialog 收件人選擇視窗', () => {
  const CLIENTS = [
    { callsign: 'alpha', uid: 'u1', team_color: 'Cyan', faction: 'blue' },
    { callsign: 'bravo', uid: 'u2', team_color: 'Cyan', faction: 'red' },
    { callsign: 'charlie', uid: 'u3', team_color: 'White', faction: null },
  ];
  // checkbox 順序：隊伍(Cyan,White) → 陣營(blue,red) → 裝置(alpha,bravo,charlie)
  function build(clients, onSend) {
    const loader = createTakPhotoLoader({ authFetch: vi.fn(), doc });
    return loader.buildPushDialog(clients, { onSend });
  }
  async function checkAndSend(dlg, idx) {
    const cbs = dlg.querySelectorAll('INPUT');
    cbs[idx].checked = true;
    await cbs[idx]._fire('change');
    await dlg.querySelectorAll('BUTTON')[1]._fire('click'); // [取消, 送出]
  }

  test('不選任何 → onSend 得空陣列（廣播）', async () => {
    let sent;
    const dlg = build(CLIENTS, (d) => (sent = d));
    await dlg.querySelectorAll('BUTTON')[1]._fire('click');
    expect(sent).toEqual([]);
  });
  test('選隊伍群組 → 展開成該隊在線成員 callsign', async () => {
    let sent;
    const dlg = build(CLIENTS, (d) => (sent = d));
    await checkAndSend(dlg, 0); // Cyan 隊 = alpha + bravo
    expect(sent).toEqual(['alpha', 'bravo']);
  });
  test('選陣營群組 → 展開該陣營成員', async () => {
    let sent;
    const dlg = build(CLIENTS, (d) => (sent = d));
    await checkAndSend(dlg, 2); // 陣營 blue = alpha
    expect(sent).toEqual(['alpha']);
  });
  test('選指定裝置 → 該 callsign', async () => {
    let sent;
    const dlg = build(CLIENTS, (d) => (sent = d));
    await checkAndSend(dlg, 6); // 裝置 charlie
    expect(sent).toEqual(['charlie']);
  });
  test('群組 + 裝置混選 → 聯集去重', async () => {
    let sent;
    const dlg = build(CLIENTS, (d) => (sent = d));
    const cbs = dlg.querySelectorAll('INPUT');
    cbs[0].checked = true; // Cyan = alpha,bravo
    await cbs[0]._fire('change');
    cbs[6].checked = true; // charlie
    await cbs[6]._fire('change');
    await dlg.querySelectorAll('BUTTON')[1]._fire('click');
    expect(sent).toEqual(['alpha', 'bravo', 'charlie']);
  });
  test('faction 全空 → 不出陣營軸（後端已守門，非 admin 拿不到紅）', () => {
    const dlg = build([{ callsign: 'a', uid: 'u', team_color: 'White', faction: null }], () => {});
    // checkbox：White(隊) + a(裝置) = 2，無陣營列
    expect(dlg.querySelectorAll('INPUT').length).toBe(2);
  });
  test('無在線裝置 → 仍可送（廣播）', async () => {
    let sent;
    const dlg = build([], (d) => (sent = d));
    await dlg.querySelectorAll('BUTTON')[1]._fire('click');
    expect(sent).toEqual([]);
  });
});

describe('bindPush 端到端', () => {
  test('選檔 → 彈收件人視窗 → 選裝置送出 → push + reload', async () => {
    const input = makeFakeEl('input');
    input.files = [new Blob([new Uint8Array([1])], { type: 'image/jpeg' })];
    doc._register(TAK_PHOTO_PUSH_ID, input);
    const calls = [];
    const af = vi.fn(async (url) => {
      calls.push(url);
      if (url.includes('/clients')) return { ok: true, json: async () => ({ clients: [{ callsign: '3QQ-iTAK', uid: 'u1' }] }) };
      if (url.includes('/downlink/photo')) return { ok: true };
      return { ok: true, status: 200, json: async () => ({ files: [] }) };
    });
    const loader = createTakPhotoLoader({ authFetch: af, doc });
    loader.bindPush('U-1');
    await input._fire('change');
    await flush();
    expect(doc.body.children.length).toBe(1); // 彈出視窗
    const dlg = doc.body.children[0];
    const cbs = dlg.querySelectorAll('INPUT'); // 單 client 無群組 → [3QQ-iTAK]
    cbs[0].checked = true;
    await cbs[0]._fire('change');
    await dlg.querySelectorAll('BUTTON')[1]._fire('click');
    await flush();
    expect(calls.some((u) => u.includes('/api/tak/downlink/photo'))).toBe(true);
    expect(calls.some((u) => u.includes('/for-entity/U-1'))).toBe(true); // 推後 reload
  });
  test('無 input → 安全 no-op', () => {
    const loader = createTakPhotoLoader({ authFetch: vi.fn(), doc });
    loader.bindPush('U-1'); // 沒註冊 input → 不拋
  });
});

// ── #518：方向感知刪除（deleteAttachment / buildDeleteDialog / load 刪除鈕）──
describe('#518 方向感知刪除', () => {
  test('deleteAttachment：purge_server=false → DELETE 無 query', async () => {
    let captured;
    const af = vi.fn(async (url, opts) => {
      captured = { url, opts };
      return { ok: true };
    });
    const loader = createTakPhotoLoader({ authFetch: af, doc });
    const ok = await loader.deleteAttachment('a'.repeat(64), false);
    expect(ok).toBe(true);
    expect(captured.url).toBe(`/api/tak/files/${'a'.repeat(64)}`);
    expect(captured.opts.method).toBe('DELETE');
  });

  test('deleteAttachment：purge_server=true → 帶 ?purge_server=true', async () => {
    let captured;
    const af = vi.fn(async (url, opts) => {
      captured = { url, opts };
      return { ok: true };
    });
    const loader = createTakPhotoLoader({ authFetch: af, doc });
    await loader.deleteAttachment('b'.repeat(64), true);
    expect(captured.url).toContain('?purge_server=true');
  });

  test('buildDeleteDialog：下行 + 可清 → L2 checkbox 預設勾（ICS 為 owner）', () => {
    const loader = createTakPhotoLoader({ authFetch: vi.fn(), doc });
    const dlg = loader.buildDeleteDialog({ hash: 'c'.repeat(64), direction: 'downlink', server_purgeable: true }, {});
    const cb = dlg.querySelector('INPUT');
    expect(cb.checked).toBe(true);
    expect(cb.disabled).toBe(false);
  });

  test('buildDeleteDialog：上行 + 可清 → L2 checkbox 預設不勾（源頭在現場）', () => {
    const loader = createTakPhotoLoader({ authFetch: vi.fn(), doc });
    const dlg = loader.buildDeleteDialog({ hash: 'c'.repeat(64), direction: 'uplink', server_purgeable: true }, {});
    expect(dlg.querySelector('INPUT').checked).toBe(false);
  });

  test('buildDeleteDialog：無 pkg（server_purgeable=false）→ checkbox 停用', () => {
    const loader = createTakPhotoLoader({ authFetch: vi.fn(), doc });
    const dlg = loader.buildDeleteDialog({ hash: 'c'.repeat(64), direction: 'downlink', server_purgeable: false }, {});
    const cb = dlg.querySelector('INPUT');
    expect(cb.checked).toBe(false);
    expect(cb.disabled).toBe(true);
  });

  test('buildDeleteDialog：確定鈕 → onConfirm(checkbox 狀態)；取消 → onCancel', async () => {
    const loader = createTakPhotoLoader({ authFetch: vi.fn(), doc });
    let confirmed = null;
    let cancelled = false;
    const dlg = loader.buildDeleteDialog(
      { hash: 'c'.repeat(64), direction: 'downlink', server_purgeable: true },
      { onConfirm: (p) => (confirmed = p), onCancel: () => (cancelled = true) },
    );
    const btns = dlg.querySelectorAll('BUTTON'); // [取消, 確定刪除]
    await btns[1]._fire('click');
    expect(confirmed).toBe(true); // 下行預設勾 → 傳 true
    await btns[0]._fire('click');
    expect(cancelled).toBe(true);
  });

  test('load：canDelete=true + local 附件 → 每格出刪除鈕 + 方向徽記', async () => {
    const list = _resp({
      files: [{ hash: 'a'.repeat(64), name: 'p.jpg', local: true, direction: 'uplink', server_purgeable: true }],
    });
    const loader = createTakPhotoLoader({ authFetch: _fakeAuthFetch({ list }), doc });
    await loader.load('U-1', { canDelete: true });
    expect(grid.querySelectorAll('BUTTON').length).toBe(1); // 刪除鈕
    expect(grid.querySelectorAll('SPAN').length).toBe(1); // 方向徽記
  });

  test('load：canDelete=false → 無刪除鈕（後端仍為權威守門）', async () => {
    const list = _resp({ files: [{ hash: 'a'.repeat(64), name: 'p.jpg', local: true, direction: 'uplink' }] });
    const loader = createTakPhotoLoader({ authFetch: _fakeAuthFetch({ list }), doc });
    await loader.load('U-1', { canDelete: false });
    expect(grid.querySelectorAll('BUTTON').length).toBe(0);
  });
});
