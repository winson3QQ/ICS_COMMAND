/**
 * event_drag.test.js — EventDragManager 單元測試
 *
 * 沿用 event_popup.test.js / drag_handles.test.js 的 minimal DOM shim pattern
 * （不引入 jsdom 依賴）。Mock maplibregl.Marker 驗證 dragstart/dragend/click 流。
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

// ── Minimal DOM shim ─────────────────────────────────────
class FakeEventTarget {
  constructor() { this._listeners = {}; }
  addEventListener(type, fn) { (this._listeners[type] ??= []).push(fn); }
  dispatchEvent(evt) { (this._listeners[evt.type] || []).forEach(fn => fn(evt)); return true; }
}
class FakeElement extends FakeEventTarget {
  constructor(tag) {
    super();
    this.tagName = tag.toUpperCase();
    this._className = '';
    this.textContent = '';
    this.children = [];
    this.style = {};
    this.dataset = {};
  }
  get className() { return this._className; }
  set className(v) { this._className = v || ''; }
}
globalThis.document = { createElement: (t) => new FakeElement(t) };

const { EventDragManager } = await import('../../static/js/map/event_drag.js');

// ── Mock maplibregl.Marker ───────────────────────────────
function makeMockMarker() {
  return class MockMarker {
    constructor(opts) {
      this.opts = opts;
      this._lnglat = null;
      this._addedTo = null;
      this._removed = false;
      this._listeners = {};
    }
    setLngLat(ll) { this._lnglat = ll; return this; }
    addTo(map) { this._addedTo = map; return this; }
    on(ev, fn) { (this._listeners[ev] ??= []).push(fn); return this; }
    remove() { this._removed = true; }
    getLngLat() { return { lng: this._lnglat[0], lat: this._lnglat[1] }; }
    getElement() { return this.opts.element; }
    _fire(ev) { (this._listeners[ev] || []).forEach(fn => fn()); }
  };
}

const MOCK_MAP = { _isMock: true };

function makeMgr() {
  const Marker = makeMockMarker();
  return { mgr: new EventDragManager(MOCK_MAP, { Marker }), Marker };
}

describe('EventDragManager', () => {
  describe('constructor', () => {
    it('拒絕缺 map / maplibregl.Marker', () => {
      expect(() => new EventDragManager(null, { Marker: class {} })).toThrow(/map required/);
      expect(() => new EventDragManager({}, {})).toThrow(/Marker required/);
    });
  });

  describe('sync()', () => {
    it('正常事件 zones → 建 marker', () => {
      const { mgr } = makeMgr();
      mgr.sync([
        { id: 'evt_1', lat: 24.83, lng: 121.01 },
        { id: 'evt_2', lat: 25.00, lng: 121.50 },
      ], () => {});
      expect(mgr.getMarkerCount()).toBe(2);
    });

    it('lat/lng 缺值的 zone 被略過', () => {
      const { mgr } = makeMgr();
      mgr.sync([
        { id: 'ok', lat: 24, lng: 121 },
        { id: 'bad', lat: 'foo', lng: 'bar' },
        { id: 'missing' },
      ], () => {});
      expect(mgr.getMarkerCount()).toBe(1);
    });

    it('再次 sync 移除消失的 zone handle', () => {
      const { mgr } = makeMgr();
      mgr.sync([{ id: 'a', lat: 24, lng: 121 }, { id: 'b', lat: 25, lng: 122 }], () => {});
      expect(mgr.getMarkerCount()).toBe(2);
      mgr.sync([{ id: 'a', lat: 24, lng: 121 }], () => {});
      expect(mgr.getMarkerCount()).toBe(1);
    });

    it('同 id 重 sync → 更新位置不重建', () => {
      const { mgr } = makeMgr();
      mgr.sync([{ id: 'a', lat: 24, lng: 121 }], () => {});
      const first = [...mgr.markers.values()][0];
      mgr.sync([{ id: 'a', lat: 25, lng: 122 }], () => {});
      const second = [...mgr.markers.values()][0];
      expect(second).toBe(first);
      expect(second._lnglat).toEqual([122, 25]);
    });

    it('setLngLat 用 [lng, lat] 順序（不是 Leaflet 的 [lat, lng]）', () => {
      const { mgr } = makeMgr();
      mgr.sync([{ id: 'a', lat: 24.83, lng: 121.01 }], () => {});
      const m = [...mgr.markers.values()][0];
      expect(m._lnglat).toEqual([121.01, 24.83]);
    });
  });

  describe('dragend → onDragEnd(zoneId, to, from)', () => {
    it('觸發 callback 含 6 位小數 round + 帶 dragstart 時的原座標', () => {
      const { mgr } = makeMgr();
      const onDragEnd = vi.fn();
      mgr.sync([{ id: 'evt_1', lat: 24, lng: 121 }], onDragEnd);
      const m = [...mgr.markers.values()][0];
      // 模擬 dragstart：marker 的 lnglat 還在原位
      m._lnglat = [121.000000, 24.000000];
      m._fire('dragstart');
      // 模擬 drag move：lnglat 跟著 mouse 走
      m._lnglat = [121.123456789, 24.987654321];
      m._fire('dragend');
      expect(onDragEnd).toHaveBeenCalledWith(
        'evt_1',
        { lat: 24.987654, lng: 121.123457 },
        { lat: 24, lng: 121 },
      );
    });

    it('沒 dragstart 直接 dragend → from 為 null（保險路徑）', () => {
      const { mgr } = makeMgr();
      const onDragEnd = vi.fn();
      mgr.sync([{ id: 'evt_1', lat: 24, lng: 121 }], onDragEnd);
      const m = [...mgr.markers.values()][0];
      m._lnglat = [121.5, 24.5];
      m._fire('dragend');
      expect(onDragEnd).toHaveBeenCalledWith('evt_1', { lat: 24.5, lng: 121.5 }, null);
    });
  });

  describe('click forwarding', () => {
    it('純 click（無 drag）→ onClick(zoneId)', () => {
      const { mgr } = makeMgr();
      const onClick = vi.fn();
      mgr.sync([{ id: 'evt_1', lat: 24, lng: 121 }], () => {}, onClick);
      const m = [...mgr.markers.values()][0];
      m.getElement().dispatchEvent({ type: 'click', stopPropagation: () => {} });
      expect(onClick).toHaveBeenCalledWith('evt_1');
    });

    it('drag 完的 click 被 swallow（不觸發 onClick）', () => {
      const { mgr } = makeMgr();
      const onClick = vi.fn();
      mgr.sync([{ id: 'evt_1', lat: 24, lng: 121 }], () => {}, onClick);
      const m = [...mgr.markers.values()][0];
      m._fire('dragstart');
      m._fire('dragend');
      m.getElement().dispatchEvent({ type: 'click', stopPropagation: () => {} });
      expect(onClick).not.toHaveBeenCalled();
      // 下一次純 click 該再觸發
      m.getElement().dispatchEvent({ type: 'click', stopPropagation: () => {} });
      expect(onClick).toHaveBeenCalledTimes(1);
    });

    it('沒給 onClick 也不爆', () => {
      const { mgr } = makeMgr();
      mgr.sync([{ id: 'evt_1', lat: 24, lng: 121 }], () => {});
      const m = [...mgr.markers.values()][0];
      expect(() => {
        m.getElement().dispatchEvent({ type: 'click', stopPropagation: () => {} });
      }).not.toThrow();
    });
  });

  describe('drag → onDrag(zoneId, {lat,lng}) per-frame', () => {
    it('marker drag 事件 fire 時呼叫 onDrag', () => {
      const { mgr } = makeMgr();
      const onDrag = vi.fn();
      mgr.sync([{ id: 'evt_1', lat: 24, lng: 121 }], () => {}, undefined, onDrag);
      const m = [...mgr.markers.values()][0];
      m._lnglat = [121.5, 24.5];
      m._fire('drag');
      expect(onDrag).toHaveBeenCalledWith('evt_1', { lat: 24.5, lng: 121.5 });
    });

    it('沒給 onDrag 不爆', () => {
      const { mgr } = makeMgr();
      mgr.sync([{ id: 'evt_1', lat: 24, lng: 121 }], () => {});
      const m = [...mgr.markers.values()][0];
      expect(() => m._fire('drag')).not.toThrow();
    });
  });

  describe('clear()', () => {
    it('清空所有 marker', () => {
      const { mgr } = makeMgr();
      mgr.sync([{ id: 'a', lat: 24, lng: 121 }, { id: 'b', lat: 25, lng: 122 }], () => {});
      expect(mgr.getMarkerCount()).toBe(2);
      mgr.clear();
      expect(mgr.getMarkerCount()).toBe(0);
    });
  });
});
