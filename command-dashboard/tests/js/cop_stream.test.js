/**
 * tests/js/cop_stream.test.js — issue #29 PR-E：COP 即時同步 merge + 防護單測
 *
 * 鎖住：
 * - per-uid last-write-wins by version_clock（舊/同版本丟棄）
 * - 防護 1（_isSaving）/ 防護 4（dragging）：本地寫/拖中的 uid 不被遠端覆蓋
 * - delete 套用 + 遲到舊刪除忽略
 * - _onMessage 分派（hello/create/update/delete）
 * - placeAtCenter POST + 本地 upsert；_putEntity 409 採 server_entity
 * - resync：套用 server 全量、移除 server 已無者、但保留 in-flight uid（防護 2）
 */
import { describe, expect, test, vi } from "vitest";

import { createCopStream } from "../../static/js/map/cop_stream.js";

// ── mocks ────────────────────────────────────────────────────────────────

class FakeMarker {
  constructor(opts = {}) {
    this.opts = opts;
    this.lngLat = null;
    this.added = false;
    this.removed = false;
    this.handlers = {};
  }
  setLngLat(ll) {
    this.lngLat = ll;
    return this;
  }
  addTo() {
    this.added = true;
    return this;
  }
  remove() {
    this.removed = true;
    return this;
  }
  on(ev, fn) {
    this.handlers[ev] = fn;
    return this;
  }
  getLngLat() {
    return { lat: this.lngLat[1], lng: this.lngLat[0] };
  }
}

function _resp(status, body) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  });
}

function makeStream(over = {}) {
  const fetchCalls = [];
  const authFetch = vi.fn((url, opts) => {
    fetchCalls.push({ url, opts });
    return over.fetchImpl ? over.fetchImpl(url, opts) : _resp(200, {});
  });
  const stream = createCopStream({
    map: { getCenter: () => ({ lat: 25, lng: 121 }), ...over.map },
    getToken: () => "tok",
    authFetch,
    canWrite: over.canWrite || (() => true),
    MarkerCtor: FakeMarker,
    WebSocketCtor: function () {},
    setTimeoutFn: () => 0,
    clearTimeoutFn: () => {},
  });
  return { stream, authFetch, fetchCalls };
}

const E = (uid, vc, extra = {}) => ({ uid, version_clock: vc, lat: 1, lon: 2, ...extra });

// ── merge / LWW ────────────────────────────────────────────────────────────

describe("merge / last-write-wins", () => {
  test("create adds entity", () => {
    const { stream } = makeStream();
    expect(stream._applyEntity(E("a", 1))).toBe(true);
    expect(stream._byUid.size).toBe(1);
  });

  test("higher version replaces, lower/equal ignored", () => {
    const { stream } = makeStream();
    stream._applyEntity(E("a", 1));
    expect(stream._applyEntity(E("a", 2, { callsign: "X" }))).toBe(true);
    expect(stream._byUid.get("a").entity.callsign).toBe("X");
    expect(stream._applyEntity(E("a", 2))).toBe(false); // 同版本
    expect(stream._applyEntity(E("a", 1))).toBe(false); // 舊版本
    expect(stream._byUid.get("a").entity.callsign).toBe("X");
  });
});

// ── 防護 1 / 4 ──────────────────────────────────────────────────────────────

describe("in-flight & dragging guards", () => {
  test("_isSaving uid 不被遠端覆蓋（防護 1）", () => {
    const { stream } = makeStream();
    stream._applyEntity(E("a", 1));
    stream._isSaving.add("a");
    expect(stream._applyEntity(E("a", 5, { callsign: "REMOTE" }))).toBe(false);
    expect(stream._byUid.get("a").entity.callsign).toBeUndefined();
  });

  test("拖移中的 uid 不被遠端拉走（防護 4）", () => {
    const { stream } = makeStream();
    stream._applyEntity(E("a", 1));
    stream._setDragging("a");
    expect(stream._applyEntity(E("a", 9, { lat: 99 }))).toBe(false);
  });
});

// ── delete ───────────────────────────────────────────────────────────────

describe("delete", () => {
  test("delete 移除，遲到舊刪除忽略", () => {
    const { stream } = makeStream();
    stream._applyEntity(E("a", 3));
    expect(stream._applyDelete("a", 2)).toBe(false); // 舊 version 刪除遲到
    expect(stream._byUid.size).toBe(1);
    expect(stream._applyDelete("a", 4)).toBe(true);
    expect(stream._byUid.size).toBe(0);
  });
});

// ── _onMessage 分派 ─────────────────────────────────────────────────────────

describe("_onMessage dispatch", () => {
  test("hello no-op；create/update/delete 生效", () => {
    const { stream } = makeStream();
    stream._onMessage({ op: "hello", exercise_id: null });
    expect(stream._byUid.size).toBe(0);
    stream._onMessage({ op: "create", uid: "a", entity: E("a", 1) });
    expect(stream._byUid.size).toBe(1);
    stream._onMessage({ op: "update", uid: "a", entity: E("a", 2, { callsign: "Y" }) });
    expect(stream._byUid.get("a").entity.callsign).toBe("Y");
    stream._onMessage({ op: "delete", uid: "a", version_clock: 3 });
    expect(stream._byUid.size).toBe(0);
  });
});

// ── REST 寫入 ───────────────────────────────────────────────────────────────

describe("REST writes", () => {
  test("placeAtCenter POST + 本地 upsert", async () => {
    const created = E("new-1", 1, { callsign: "C" });
    const { stream, fetchCalls } = makeStream({ fetchImpl: () => _resp(201, created) });
    const ent = await stream.placeAtCenter();
    expect(ent.uid).toBe("new-1");
    expect(stream._byUid.has("new-1")).toBe(true);
    expect(fetchCalls[0].url).toMatch(/\/api\/cop\/entities$/);
    expect(fetchCalls[0].opts.method).toBe("POST");
  });

  test("observer（canWrite=false）placeAtCenter no-op", async () => {
    const { stream, authFetch } = makeStream({ canWrite: () => false });
    const ent = await stream.placeAtCenter();
    expect(ent).toBe(null);
    expect(authFetch).not.toHaveBeenCalled();
  });

  test("dragend → PUT 帶 If-Match；409 採 server_entity", async () => {
    let put = null;
    const { stream } = makeStream({
      fetchImpl: (url, opts) => {
        if (opts && opts.method === "PUT") {
          put = opts;
          return _resp(409, { server_entity: E("a", 7, { lat: 50, lon: 60 }) });
        }
        return _resp(200, {});
      },
    });
    stream._applyEntity(E("a", 3));
    const rec = stream._byUid.get("a");
    rec.marker.lngLat = [62, 51]; // 模擬拖到的位置
    // 觸發 dragstart→dragend
    rec.marker.handlers.dragstart();
    await rec.marker.handlers.dragend();
    expect(put.headers["If-Match"]).toBe("3"); // 帶拖前的 version
    // 409 → 採 server 現值（回到 server 位置）
    expect(stream._byUid.get("a").entity.version_clock).toBe(7);
    expect(stream._byUid.get("a").entity.lat).toBe(50);
    expect(stream._debugState().dragging).toBe(null); // dragend 後解除
  });
});

// ── resync ─────────────────────────────────────────────────────────────────

describe("resync", () => {
  test("套用 server 全量、移除 server 已無者、保留 in-flight uid（防護 2）", async () => {
    const { stream } = makeStream({
      fetchImpl: () => _resp(200, { entities: [E("keep", 5), E("fresh", 1)] }),
    });
    stream._applyEntity(E("keep", 1)); // 會被 server 版本 5 取代
    stream._applyEntity(E("gone", 1)); // server 沒有 → 應移除
    stream._applyEntity(E("mine", 1));
    stream._isSaving.add("mine"); // in-flight → resync 不得動它
    await stream.resync();
    expect(stream._byUid.get("keep").entity.version_clock).toBe(5);
    expect(stream._byUid.has("fresh")).toBe(true);
    expect(stream._byUid.has("gone")).toBe(false);
    expect(stream._byUid.has("mine")).toBe(true); // 保留
  });

  test("resync 飛行中新建的 entity 不被誤刪（BUG #2 fix）", async () => {
    let releaseGet;
    const gate = new Promise((res) => {
      releaseGet = res;
    });
    const { stream } = makeStream({
      fetchImpl: async () => {
        await gate;
        return { ok: true, status: 200, json: async () => ({ entities: [E("old", 2)] }) };
      },
    });
    stream._applyEntity(E("old", 1)); // resync 前已知
    const p = stream.resync(); // 進入 → 快照 known={old} → 卡在 GET
    stream._applyEntity(E("placed", 1)); // resync 飛行中新建（不在 known 快照內）
    releaseGet();
    await p;
    expect(stream._byUid.has("placed")).toBe(true); // 不被 removal loop 誤刪
    expect(stream._byUid.has("old")).toBe(true);
  });
});

// ── 渲染委派 seam（PR-G1：map.js 接管渲染）─────────────────────────────────

describe("render delegation seam", () => {
  test("onChange 註冊後不自建 marker、且每次變更觸發 callback", () => {
    const { stream } = makeStream();
    let fires = 0;
    stream.onChange(() => {
      fires += 1;
    });
    stream._applyEntity(E("a", 1));
    stream._applyEntity(E("a", 2)); // update
    stream._applyDelete("a", 3);
    expect(fires).toBe(3);
    // 委派模式：rec.marker 為 null（map.js 負責畫）
    stream._applyEntity(E("b", 1));
    expect(stream._byUid.get("b").marker).toBe(null);
  });

  test("getEntitiesByKind 依 attributes.kind 過濾", () => {
    const { stream } = makeStream();
    stream.onChange(() => {});
    stream._applyEntity(E("z1", 1, { attributes: { kind: "zone" } }));
    stream._applyEntity(E("r1", 1, { attributes: { kind: "route" } }));
    stream._applyEntity(E("z2", 1, { attributes: { kind: "zone" } }));
    expect(stream.getEntitiesByKind("zone").map((e) => e.uid).sort()).toEqual(["z1", "z2"]);
    expect(stream.getEntitiesByKind("route").map((e) => e.uid)).toEqual(["r1"]);
    expect(stream.getEntitiesByKind("polygon")).toEqual([]);
  });

  test("getEntity 回傳單顆 entity / null", () => {
    const { stream } = makeStream();
    stream._applyEntity(E("a", 7));
    expect(stream.getEntity("a").version_clock).toBe(7);
    expect(stream.getEntity("nope")).toBe(null);
  });
});
