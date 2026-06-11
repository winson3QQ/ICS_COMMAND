/**
 * create_popup.test.js — CreatePopup 單元測試（P2-34 / #220）
 *
 * 取代 event_popup.test.js：長按建立對話框升成多類別 launcher。
 * Vitest default env = node（無 DOM）→ 沿用 minimal DOM shim（不引入 jsdom）。
 *
 * 鎖住：
 *   - constructor 驗證（需 categories 或 getCategories）
 *   - 類別於 open() 解析（getCategories 即時反映角色/taxonomy）；空清單不開
 *   - stage 0 類別按鈕（順序 = marker-first）+ MGRS
 *   - 一般類別（subtypes）：類別 → 子型 → onCreate(catKey, value, latlng)
 *   - 事件類別（twoStage）：類別 → group(+reporter) → type → onCreate('event',{typeKey,reporter},latlng)
 *   - 返回導航、事件 soft-delete 過濾
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

// ── Minimal DOM shim（同 commander_modules / 原 event_popup pattern）──
class FakeEventTarget {
  constructor() { this._listeners = {}; }
  addEventListener(type, fn) { (this._listeners[type] ??= []).push(fn); }
  removeEventListener(type, fn) {
    const arr = this._listeners[type];
    if (arr) this._listeners[type] = arr.filter(x => x !== fn);
  }
  dispatchEvent(evt) { (this._listeners[evt.type] || []).forEach(fn => fn(evt)); return true; }
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
    this.value = '';
    this.type = '';
    this.selected = false;
    this.options = [];
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
    if (child.id) _IDX.set(child.id, child);
    return child;
  }
  querySelector(sel) { return _selectorMatch(this, sel)[0] || null; }
  querySelectorAll(sel) { return _selectorMatch(this, sel); }
  click() { this.dispatchEvent({ type: 'click', stopPropagation() {} }); }
}

function _walk(node, out) { out.push(node); for (const c of node.children || []) _walk(c, out); }
function _selectorMatch(root, sel) {
  const all = [];
  for (const c of root.children) _walk(c, all);
  return all.filter(n => _matchOne(n, sel));
}
function _matchOne(n, sel) {
  if (sel.startsWith('#')) return n.id === sel.slice(1);
  if (sel.startsWith('.')) {
    const classes = sel.slice(1).split('.');
    return classes.every(c => n._className.split(/\s+/).includes(c));
  }
  return n.tagName === sel.toUpperCase();
}

const _IDX = new Map();
const fakeDoc = {
  createElement(tag) { return new FakeElement(tag); },
  getElementById(id) { return _IDX.get(id) || null; },
  body: null,
};
globalThis.document = fakeDoc;
globalThis.Event = class { constructor(type) { this.type = type; this.stopPropagation = () => {}; } };

const { CreatePopup } = await import('../../static/js/map/create_popup.js');

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
    remove() { this._removed = true; this._closeListeners.forEach(fn => fn()); }
  };
}

const MOCK_MAP = { _isMockMap: true };

function eventCategory(reporterOnChange = vi.fn()) {
  return {
    key: 'event', label: '▲ 事件回報',
    twoStage: {
      groups: { security: '安全', rescue: '救援' },
      types: {
        explosive: { label: '疑似爆裂物', group: 'security', severity: 'critical' },
        perimeter: { label: '管制區異常', group: 'security', severity: 'warning' },
        qrf: { label: 'QRF 出動', group: 'rescue', severity: 'warning' },
      },
    },
    reporter: { options: [['command', '指揮部'], ['security', '安全組']], get: () => 'command', onChange: reporterOnChange },
  };
}

function defaultCategories(reporterOnChange) {
  return [
    {
      key: 'contact', label: '📍 感知 / 敵情標記',
      subtypes: [
        { value: 'friendly', label: '友軍', color: '#3da9fc' },
        { value: 'hostile', label: '敵情', color: '#f85149' },
      ],
    },
    eventCategory(reporterOnChange),
    { key: 'infra', label: '＋ 設施', subtypes: [{ value: 'hospital', label: '醫院' }] },
  ];
}

function makePopup(deps = {}) {
  const Popup = makeMockPopup();
  const onCreate = vi.fn();
  const reporterOnChange = vi.fn();
  const cp = new CreatePopup(MOCK_MAP, { Popup }, {
    categories: defaultCategories(reporterOnChange),
    onCreate,
    latlngToMgrs: (lat, lng) => `MGRS(${lat.toFixed(2)},${lng.toFixed(2)})`,
    ...deps,
  });
  return { cp, onCreate, reporterOnChange };
}

describe('CreatePopup', () => {
  beforeEach(() => { _IDX.clear(); fakeDoc.body = new FakeElement('body'); });

  it('constructor 拒絕缺 map / Popup / categories+getCategories', () => {
    expect(() => new CreatePopup(null, { Popup: class {} }, { categories: [] })).toThrow(/map required/);
    expect(() => new CreatePopup({}, {}, { categories: [] })).toThrow(/Popup required/);
    expect(() => new CreatePopup({}, { Popup: class {} }, {})).toThrow(/categories.*getCategories/);
  });

  it('open() 建立 popup，setLngLat 用 [lng,lat]，顯示類別（marker-first 順序）+ MGRS', () => {
    const { cp } = makePopup();
    cp.open(24.83, 121.01);
    expect(cp.isOpen()).toBe(true);
    expect(cp._popup._lnglat).toEqual([121.01, 24.83]);
    const dom = cp._popup._dom;
    expect(dom.querySelector('.ev-popup-mgrs').textContent).toContain('MGRS(24.83,121.01)');
    const btns = dom.querySelectorAll('.ev-popup-group-btn');
    expect(btns.map(b => b.textContent)).toEqual(['📍 感知 / 敵情標記', '▲ 事件回報', '＋ 設施']);
  });

  it('open() 拒絕非有限座標 / 空類別清單不開', () => {
    const { cp } = makePopup();
    cp.open(NaN, 121);
    expect(cp.isOpen()).toBe(false);
    const empty = makePopup({ categories: undefined, getCategories: () => [] });
    empty.cp.open(24, 121);
    expect(empty.cp.isOpen()).toBe(false);
  });

  it('getCategories 於 open() 時解析（角色/taxonomy 即時反映）', () => {
    const seq = [[{ key: 'infra', label: '＋ 設施', subtypes: [] }], defaultCategories()];
    let i = 0;
    const cp = new CreatePopup(MOCK_MAP, { Popup: makeMockPopup() }, {
      getCategories: () => seq[i++],
      onCreate: vi.fn(),
      latlngToMgrs: () => 'X',
    });
    cp.open(24, 121);
    expect(cp._popup._dom.querySelectorAll('.ev-popup-group-btn').map(b => b.textContent)).toEqual(['＋ 設施']);
    cp.open(24, 121);
    expect(cp._popup._dom.querySelectorAll('.ev-popup-group-btn').length).toBe(3);
  });

  it('一般類別（contact）：類別 → 子型 → onCreate(catKey, value, latlng) 並關閉', () => {
    const { cp, onCreate } = makePopup();
    cp.open(24.83, 121.01);
    cp._popup._dom.querySelectorAll('.ev-popup-group-btn').find(b => b.textContent.includes('感知')).click();
    const subs = cp._popup._dom.querySelectorAll('.ev-popup-type-btn');
    expect(subs.map(b => b.textContent)).toEqual(['友軍', '敵情']);
    subs.find(b => b.textContent === '敵情').click();
    expect(onCreate).toHaveBeenCalledTimes(1);
    expect(onCreate).toHaveBeenCalledWith('contact', 'hostile', { lat: 24.83, lng: 121.01 });
    expect(cp.isOpen()).toBe(false);
  });

  it('事件類別（twoStage）：類別 → group(+reporter) → type → onCreate(event,{typeKey,reporter},latlng)', () => {
    const { cp, onCreate } = makePopup();
    cp.open(24.83, 121.01);
    cp._popup._dom.querySelectorAll('.ev-popup-group-btn').find(b => b.textContent.includes('事件')).click();
    // reporter select 出現 + group 按鈕（安全/救援）
    expect(cp._popup._dom.querySelector('#ev-popup-unit')).toBeTruthy();
    const groups = cp._popup._dom.querySelectorAll('.ev-popup-group-btn');
    expect(groups.map(b => b.textContent)).toEqual(['安全', '救援']);
    groups.find(b => b.textContent === '安全').click();
    const types = cp._popup._dom.querySelectorAll('.ev-popup-type-btn');
    expect(types.map(b => b.textContent)).toEqual(['疑似爆裂物', '管制區異常']);
    types.find(b => b.textContent === '疑似爆裂物').click();
    expect(onCreate).toHaveBeenCalledWith('event', { typeKey: 'explosive', reporter: 'command' }, { lat: 24.83, lng: 121.01 });
    expect(cp.isOpen()).toBe(false);
  });

  it('reporter select change → onChange callback', () => {
    const onChange = vi.fn();
    const { cp } = makePopup({ categories: [eventCategory(onChange)] });
    cp.open(24.83, 121.01);
    cp._popup._dom.querySelectorAll('.ev-popup-group-btn').find(b => b.textContent.includes('事件')).click();
    const sel = cp._popup._dom.querySelector('#ev-popup-unit');
    sel.value = 'security';
    sel.dispatchEvent({ type: 'change' });
    expect(onChange).toHaveBeenCalledWith('security');
  });

  it('返回：子型 → 類別；事件 type → group → 類別', () => {
    const { cp } = makePopup();
    cp.open(24.83, 121.01);
    cp._popup._dom.querySelectorAll('.ev-popup-group-btn').find(b => b.textContent.includes('設施')).click();
    cp._popup._dom.querySelector('.ev-popup-back').click();
    expect(cp._popup._dom.querySelectorAll('.ev-popup-group-btn').length).toBe(3);  // 回類別
  });

  it('事件 soft-delete 過濾：deleted type 不出現、該 group 全刪則 group 不顯示', () => {
    const cat = eventCategory();
    cat.twoStage.types.perimeter.deleted = true;     // security 留 explosive
    cat.twoStage.types.qrf.deleted = true;            // rescue 全刪
    const { cp } = makePopup({ categories: [cat] });
    cp.open(24.83, 121.01);
    cp._popup._dom.querySelectorAll('.ev-popup-group-btn').find(b => b.textContent.includes('事件')).click();
    const groups = cp._popup._dom.querySelectorAll('.ev-popup-group-btn');
    expect(groups.map(b => b.textContent)).toEqual(['安全']);   // 救援 全刪不顯示
    groups[0].click();
    expect(cp._popup._dom.querySelectorAll('.ev-popup-type-btn').map(b => b.textContent)).toEqual(['疑似爆裂物']);
  });

  it('close() 清空 state；open() 已開時關掉舊的', () => {
    const { cp } = makePopup();
    cp.open(24.83, 121.01);
    const first = cp._popup;
    cp.open(25.0, 121.5);
    expect(first._removed).toBe(true);
    expect(cp.getLatLng()).toEqual({ lat: 25.0, lng: 121.5 });
    cp.close();
    expect(cp.isOpen()).toBe(false);
    expect(cp.getLatLng()).toBe(null);
  });
});
