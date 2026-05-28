/**
 * label_markers.test.js — LabelMarkerManager 單元測試
 *
 * 重點：步驟 10 dogfood 發現的 stuck-dragging regression 修正鎖定。
 * 若 dragstart 後 dragend race / browser interrupt 沒 fire，feature-state.dragging
 * 會永遠卡 true，導致 SDF label 透過 text-opacity case 永遠 invisible。
 * 修法：sync() 期間若 marker 非當前 drag 中且 source 殘留 dragging:true → 主動清。
 *
 * Mock map（含 getFeatureState / setFeatureState）+ minimal DOM shim。
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

// ── Minimal DOM shim（與其他測試共用 pattern） ───────────────
class FakeEventTarget {
  constructor() { this._listeners = {}; }
  addEventListener(type, fn) { (this._listeners[type] ??= []).push(fn); }
  dispatchEvent(evt) { (this._listeners[evt.type] || []).forEach((fn) => fn(evt)); return true; }
}
class FakeElement extends FakeEventTarget {
  constructor(tag) {
    super();
    this.tagName = tag.toUpperCase();
    this._className = '';
    this.textContent = '';
    this.children = [];
    this.style = { cssText: '' };
    this.dataset = {};
  }
  get className() { return this._className; }
  set className(v) { this._className = v || ''; }
  appendChild(c) { this.children.push(c); c.parentNode = this; return c; }
  remove() { /* no-op */ }
  getElement() { return this; }
}
globalThis.document = { createElement: (t) => new FakeElement(t) };

const { LabelMarkerManager } = await import('../../static/js/map/label_markers.js');

// ── Mock maplibregl.Marker ─────────────────────────────
function makeMockMarker() {
  return class MockMarker {
    constructor(opts) {
      this.opts = opts;
      this._lnglat = null;
      this._listeners = {};
    }
    setLngLat(ll) { this._lnglat = ll; return this; }
    addTo(_m) { return this; }
    on(ev, fn) { (this._listeners[ev] ??= []).push(fn); return this; }
    remove() { /* no-op */ }
    getLngLat() { return { lng: this._lnglat[0], lat: this._lnglat[1] }; }
    getElement() { return this.opts.element; }
    _fire(ev) { (this._listeners[ev] || []).forEach((fn) => fn()); }
  };
}

function makeMockMap() {
  const state = new Map();   // key = `${source}|${id}` → state object
  return {
    _state: state,
    setFeatureState({ source, id }, st) {
      const key = `${source}|${id}`;
      const cur = state.get(key) || {};
      state.set(key, { ...cur, ...st });
    },
    getFeatureState({ source, id }) {
      return state.get(`${source}|${id}`) || {};
    },
    removeFeatureState({ source, id }) {
      state.delete(`${source}|${id}`);
    },
  };
}

function makeMgr() {
  const map = makeMockMap();
  const Marker = makeMockMarker();
  return { mgr: new LabelMarkerManager(map, { Marker }, 'polygons'), map };
}

describe('LabelMarkerManager — basic lifecycle', () => {
  it('constructor 拒絕缺 map / Marker / sourceId', () => {
    expect(() => new LabelMarkerManager(null, { Marker: class {} }, 's')).toThrow(/map required/);
    expect(() => new LabelMarkerManager({}, {}, 's')).toThrow(/Marker required/);
    expect(() => new LabelMarkerManager({}, { Marker: class {} }, '')).toThrow(/sourceId required/);
  });

  it('sync 跳過無 label 的 feature', () => {
    const { mgr } = makeMgr();
    mgr.sync(
      [
        { id: 'a', label: '名稱', label_anchor: [24, 121] },
        { id: 'b', label_anchor: [24, 121] },
      ],
      () => {},
    );
    expect(mgr.getMarkerCount()).toBe(1);
  });

  it('再次 sync 同 id 不重建 marker', () => {
    const { mgr } = makeMgr();
    mgr.sync([{ id: 'a', label: '名', label_anchor: [24, 121] }], () => {});
    const first = [...mgr.markers.values()][0].marker;
    mgr.sync([{ id: 'a', label: '名', label_anchor: [25, 122] }], () => {});
    const second = [...mgr.markers.values()][0].marker;
    expect(second).toBe(first);
    expect(second._lnglat).toEqual([122, 25]);
  });

  it('clear() 清空 markers', () => {
    const { mgr } = makeMgr();
    mgr.sync([{ id: 'a', label: '名', label_anchor: [24, 121] }], () => {});
    expect(mgr.getMarkerCount()).toBe(1);
    mgr.clear();
    expect(mgr.getMarkerCount()).toBe(0);
  });
});

describe('LabelMarkerManager — dragstart / dragend feature-state', () => {
  it('dragstart 設 feature-state dragging=true', () => {
    const { mgr, map } = makeMgr();
    mgr.sync([{ id: 'p1', label: 'A', label_anchor: [24, 121], color: '#fff' }], () => {});
    const entry = [...mgr.markers.values()][0];
    entry.marker._fire('dragstart');
    expect(map.getFeatureState({ source: 'polygons', id: 'p1' })).toEqual({ dragging: true });
    expect(entry.isDragging).toBe(true);
  });

  it('dragend 設 feature-state dragging=false', () => {
    const { mgr, map } = makeMgr();
    mgr.sync([{ id: 'p1', label: 'A', label_anchor: [24, 121], color: '#fff' }], () => {});
    const entry = [...mgr.markers.values()][0];
    entry.marker._fire('dragstart');
    entry.marker._fire('dragend');
    expect(map.getFeatureState({ source: 'polygons', id: 'p1' })).toEqual({ dragging: false });
    expect(entry.isDragging).toBe(false);
  });

  it('dragend onDragEnd callback 收到 6 位小數 round', () => {
    const { mgr } = makeMgr();
    const onDragEnd = vi.fn();
    mgr.sync([{ id: 'p1', label: 'A', label_anchor: [24, 121], color: '#fff' }], onDragEnd);
    const entry = [...mgr.markers.values()][0];
    entry.marker._lnglat = [121.123456789, 24.987654321];
    entry.marker._fire('dragend');
    expect(onDragEnd).toHaveBeenCalledWith('p1', { lat: 24.987654, lng: 121.123457 });
  });
});

describe('LabelMarkerManager — stuck-dragging defensive clear (step 10 dogfood fix)', () => {
  it('source 有殘留 dragging:true，sync() 進 update 分支時清回 false', () => {
    const { mgr, map } = makeMgr();
    mgr.sync([{ id: 'p1', label: 'A', label_anchor: [24, 121], color: '#fff' }], () => {});
    // 模擬 dragstart 後 dragend race 沒 fire（state 卡住）
    map.setFeatureState({ source: 'polygons', id: 'p1' }, { dragging: true });
    expect(map.getFeatureState({ source: 'polygons', id: 'p1' })).toEqual({ dragging: true });
    // 再次 sync → 應該偵測到 isDragging=false 但 source state=true，主動清
    mgr.sync([{ id: 'p1', label: 'A', label_anchor: [24, 121], color: '#fff' }], () => {});
    expect(map.getFeatureState({ source: 'polygons', id: 'p1' })).toEqual({ dragging: false });
  });

  it('新 marker 創建時若 source 有殘留 dragging:true 也清掉', () => {
    const { mgr, map } = makeMgr();
    // 用前次 marker（同 id）留下的 dragging:true 模擬 polly bouncing 過 refreshLeafletMarkers
    map.setFeatureState({ source: 'polygons', id: 'p2' }, { dragging: true });
    mgr.sync([{ id: 'p2', label: 'B', label_anchor: [25, 122], color: '#fff' }], () => {});
    expect(map.getFeatureState({ source: 'polygons', id: 'p2' })).toEqual({ dragging: false });
  });

  it('當前正在 drag 的 marker，sync() 不會清掉 dragging state（不打斷 user 拖曳）', () => {
    const { mgr, map } = makeMgr();
    mgr.sync([{ id: 'p1', label: 'A', label_anchor: [24, 121], color: '#fff' }], () => {});
    const entry = [...mgr.markers.values()][0];
    entry.marker._fire('dragstart');
    expect(entry.isDragging).toBe(true);
    expect(map.getFeatureState({ source: 'polygons', id: 'p1' })).toEqual({ dragging: true });
    // 仿 refreshLeafletMarkers poll 期間 sync()
    mgr.sync([{ id: 'p1', label: 'A', label_anchor: [24, 121], color: '#fff' }], () => {});
    // user 還在拖，state 不該被清
    expect(map.getFeatureState({ source: 'polygons', id: 'p1' })).toEqual({ dragging: true });
    expect(entry.isDragging).toBe(true);
  });

  it('source state 是空 / 不存在 dragging key → sync 不會多寫 dragging:false', () => {
    const { mgr, map } = makeMgr();
    mgr.sync([{ id: 'p1', label: 'A', label_anchor: [24, 121], color: '#fff' }], () => {});
    // 再 sync 一次（state 空）— 不該多寫
    mgr.sync([{ id: 'p1', label: 'A', label_anchor: [24, 121], color: '#fff' }], () => {});
    // state 仍為空（_clearStuckDragState 看到 cur 沒 dragging key 就跳）
    expect(map.getFeatureState({ source: 'polygons', id: 'p1' })).toEqual({});
  });
});
