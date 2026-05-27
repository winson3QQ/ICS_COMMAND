/**
 * event_popup.test.js — EventPopup 單元測試（P1-10b 步驟 9）
 *
 * Vitest default environment 是 node（無 DOM）；本檔需要 DOM 因為 EventPopup 的
 * 主要 surface area 就是建構 DOM tree。沿襲 commander_modules.test.js 的 pattern：
 * 自己 polyfill 一個 minimal DOM shim，不引入 jsdom / happy-dom 增加依賴。
 *
 * Shim 範圍：document.createElement / Event / EventTarget / element.appendChild /
 *   querySelector(All) / addEventListener / dispatchEvent / click / classList。
 *   覆蓋 EventPopup 所需的 API。
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

// ── Minimal DOM shim ─────────────────────────────────────────
class FakeEventTarget {
  constructor() { this._listeners = {}; }
  addEventListener(type, fn) {
    (this._listeners[type] ??= []).push(fn);
  }
  removeEventListener(type, fn) {
    const arr = this._listeners[type];
    if (arr) this._listeners[type] = arr.filter(x => x !== fn);
  }
  dispatchEvent(evt) {
    (this._listeners[evt.type] || []).forEach(fn => fn(evt));
    return true;
  }
}

class FakeElement extends FakeEventTarget {
  constructor(tag) {
    super();
    this.tagName = tag.toUpperCase();
    this._className = '';
    this.textContent = '';
    this.id = '';
    this.children = [];
    this.parentNode = null;
    this.style = {};
    this.value = '';      // for <input>/<select>
    this.type = '';
    this.selected = false;
    this.options = [];    // for <select>
  }
  get className() { return this._className; }
  set className(v) { this._className = v || ''; }
  appendChild(child) {
    if (child.parentNode) {
      const arr = child.parentNode.children;
      const idx = arr.indexOf(child);
      if (idx >= 0) arr.splice(idx, 1);
    }
    child.parentNode = this;
    this.children.push(child);
    if (this.tagName === 'SELECT' && child.tagName === 'OPTION') {
      this.options.push(child);
      if (child.selected) this.value = child.value;
    }
    // 更新 document index
    if (child.id) _IDX.set(child.id, child);
    return child;
  }
  querySelector(sel) {
    return _selectorMatch(this, sel)[0] || null;
  }
  querySelectorAll(sel) {
    return _selectorMatch(this, sel);
  }
  click() {
    this.dispatchEvent({ type: 'click', stopPropagation() {} });
  }
}

function _walk(node, out) {
  out.push(node);
  for (const c of node.children || []) _walk(c, out);
}

function _selectorMatch(root, sel) {
  const all = [];
  for (const c of root.children) _walk(c, all);
  return all.filter(n => _matchOne(n, sel));
}

function _matchOne(n, sel) {
  // 支援多重 class（用空白切，每段 .class 都要 match）
  if (sel.startsWith('#')) return n.id === sel.slice(1);
  if (sel.startsWith('.')) {
    const classes = sel.slice(1).split('.');
    return classes.every(c => n._className.split(/\s+/).includes(c));
  }
  return n.tagName === sel.toUpperCase();
}

// document.getElementById 全域 index
const _IDX = new Map();

const fakeDoc = {
  createElement(tag) { return new FakeElement(tag); },
  getElementById(id) { return _IDX.get(id) || null; },
  body: null,    // 給 test 重建
};

globalThis.document = fakeDoc;
// Event 是 minimal — 只要有 type 與 stopPropagation
globalThis.Event = class { constructor(type) { this.type = type; this.stopPropagation = () => {}; } };

// ── 載入待測模組（必須在 shim 之後） ────────────────────────────
const { EventPopup } = await import('../../static/js/map/event_popup.js');

// ── Mock maplibregl.Popup ────────────────────────────────
function makeMockPopup() {
  return class MockPopup {
    constructor(opts) {
      this.opts = opts;
      this._lnglat = null;
      this._dom = null;
      this._closeListeners = [];
      this._addedTo = null;
      this._removed = false;
    }
    setLngLat(ll) { this._lnglat = ll; return this; }
    setDOMContent(node) { this._dom = node; return this; }
    addTo(map) { this._addedTo = map; return this; }
    on(ev, fn) { if (ev === 'close') this._closeListeners.push(fn); return this; }
    remove() {
      this._removed = true;
      this._closeListeners.forEach(fn => fn());
    }
  };
}

const MOCK_MAP = { _isMockMap: true };

const GROUPS = {
  security: '安全',
  rescue: '救援',
  medical: '醫療',
};

const TYPES = {
  explosive: { label: '疑似爆裂物', group: 'security', severity: 'critical' },
  perimeter: { label: '管制區異常', group: 'security', severity: 'warning' },
  qrf: { label: 'QRF 出動', group: 'rescue', severity: 'warning' },
  mci: { label: '大量傷亡', group: 'medical', severity: 'critical' },
};

function makePopup(overrides = {}) {
  const Popup = makeMockPopup();
  const onSubmit = vi.fn();
  const onReporterChange = vi.fn();
  const ep = new EventPopup(MOCK_MAP, { Popup }, {
    groups: GROUPS,
    types: TYPES,
    reporterOptions: [['command', '指揮部'], ['security', '安全組']],
    getReporter: () => 'command',
    onReporterChange,
    latlngToMgrs: (lat, lng) => `MGRS(${lat.toFixed(2)},${lng.toFixed(2)})`,
    onSubmit,
    ...overrides,
  });
  return { ep, onSubmit, onReporterChange, Popup };
}

describe('EventPopup', () => {
  beforeEach(() => {
    _IDX.clear();
    fakeDoc.body = new FakeElement('body');
  });

  it('constructor 拒絕缺 map / maplibregl.Popup / deps', () => {
    expect(() => new EventPopup(null, { Popup: class {} }, { groups: {}, types: {} })).toThrow(/map required/);
    expect(() => new EventPopup({}, {}, { groups: {}, types: {} })).toThrow(/Popup required/);
    expect(() => new EventPopup({}, { Popup: class {} }, {})).toThrow(/groups.*types/);
  });

  it('open(lat,lng) 建立 popup，setLngLat 用 [lng,lat] 順序', () => {
    const { ep } = makePopup();
    ep.open(24.83, 121.01);
    expect(ep.isOpen()).toBe(true);
    expect(ep._popup._lnglat).toEqual([121.01, 24.83]);
    expect(ep._popup._addedTo).toBe(MOCK_MAP);
    expect(ep.getLatLng()).toEqual({ lat: 24.83, lng: 121.01 });
  });

  it('open() 拒絕非有限 lat/lng', () => {
    const { ep } = makePopup();
    ep.open(NaN, 121);
    expect(ep.isOpen()).toBe(false);
    ep.open(24, undefined);
    expect(ep.isOpen()).toBe(false);
  });

  it('open() 建立 group 按鈕 + reporter select + MGRS header', () => {
    const { ep } = makePopup();
    ep.open(24.83, 121.01);
    const dom = ep._popup._dom;
    expect(dom.querySelector('.ev-popup-header')).toBeTruthy();
    const sel = dom.querySelector('#ev-popup-unit');
    expect(sel).toBeTruthy();
    expect(sel.options.length).toBe(2);
    expect(sel.value).toBe('command');     // 預設 reporter
    const mgrs = dom.querySelector('.ev-popup-mgrs');
    expect(mgrs.textContent).toContain('MGRS(24.83,121.01)');
    const groupBtns = dom.querySelectorAll('.ev-popup-group-btn');
    expect(groupBtns.length).toBe(3);
    expect(groupBtns.map(b => b.textContent)).toEqual(['安全', '救援', '醫療']);
  });

  it('group 按鈕點擊 → 切到 type 列表（過濾該 group 的 types）', () => {
    const { ep } = makePopup();
    ep.open(24.83, 121.01);
    const dom1 = ep._popup._dom;
    const securityBtn = dom1.querySelectorAll('.ev-popup-group-btn').find(b => b.textContent === '安全');
    securityBtn.click();
    const dom2 = ep._popup._dom;
    expect(dom2).not.toBe(dom1);
    const typeBtns = dom2.querySelectorAll('.ev-popup-type-btn');
    expect(typeBtns.length).toBe(2);
    expect(typeBtns.map(b => b.textContent)).toEqual(['疑似爆裂物', '管制區異常']);
    expect(typeBtns[0].className).toContain('sev-critical');
    expect(typeBtns[1].className).toContain('sev-warning');
    expect(dom2.querySelector('.ev-popup-back').textContent).toBe('← 返回');
    expect(dom2.querySelector('.ev-popup-mgrs').textContent).toBe('安全');
  });

  it('「← 返回」回 group 選單', () => {
    const { ep } = makePopup();
    ep.open(24.83, 121.01);
    ep._popup._dom.querySelectorAll('.ev-popup-group-btn').find(b => b.textContent === '醫療').click();
    expect(ep._popup._dom.querySelectorAll('.ev-popup-type-btn').length).toBe(1);
    ep._popup._dom.querySelector('.ev-popup-back').click();
    expect(ep._popup._dom.querySelectorAll('.ev-popup-group-btn').length).toBe(3);
  });

  it('type 按鈕點擊 → 呼叫 onSubmit(typeKey, {lat,lng,reporter}) 並關閉 popup', () => {
    const { ep, onSubmit } = makePopup();
    ep.open(24.83, 121.01);
    ep._popup._dom.querySelectorAll('.ev-popup-group-btn').find(b => b.textContent === '救援').click();
    ep._popup._dom.querySelectorAll('.ev-popup-type-btn').find(b => b.textContent === 'QRF 出動').click();
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit).toHaveBeenCalledWith('qrf', { lat: 24.83, lng: 121.01, reporter: 'command' });
    expect(ep.isOpen()).toBe(false);
  });

  it('reporter select change 觸發 onReporterChange callback', () => {
    const { ep, onReporterChange } = makePopup();
    ep.open(24.83, 121.01);
    const sel = ep._popup._dom.querySelector('#ev-popup-unit');
    sel.value = 'security';
    sel.dispatchEvent({ type: 'change' });
    expect(onReporterChange).toHaveBeenCalledWith('security');
  });

  it('close() 主動關閉並清空 state', () => {
    const { ep } = makePopup();
    ep.open(24.83, 121.01);
    expect(ep.isOpen()).toBe(true);
    ep.close();
    expect(ep.isOpen()).toBe(false);
    expect(ep.getLatLng()).toBe(null);
  });

  it('open() 已有 popup 開啟時自動關掉舊的', () => {
    const { ep } = makePopup();
    ep.open(24.83, 121.01);
    const firstPopup = ep._popup;
    ep.open(25.00, 121.50);
    expect(firstPopup._removed).toBe(true);
    expect(ep._popup).not.toBe(firstPopup);
    expect(ep.getLatLng()).toEqual({ lat: 25.0, lng: 121.5 });
  });

  it('popup close 事件 → state 清空（仿真 user 點 × 或 esc）', () => {
    const { ep } = makePopup();
    ep.open(24.83, 121.01);
    ep._popup.remove();
    expect(ep.isOpen()).toBe(false);
    expect(ep.getLatLng()).toBe(null);
  });
});
