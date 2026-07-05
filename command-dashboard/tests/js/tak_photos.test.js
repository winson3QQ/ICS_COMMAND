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
import { takPhotoSectionHtml, createTakPhotoLoader, TAK_PHOTO_GRID_ID } from '../../static/js/map/tak_photos.js';

// ── Minimal fake DOM（注入 doc，不引 jsdom）──
function makeFakeEl(tag) {
  const el = {
    tagName: (tag || 'div').toUpperCase(),
    children: [],
    style: {},
    _text: '',
    appendChild(c) {
      this.children.push(c);
      return c;
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
