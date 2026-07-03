/**
 * tests/js/cop_stream.test.js — issue #29 PR-E：COP 即時同步 merge + 防護單測
 *
 * 鎖住：
 * - per-uid last-write-wins by version_clock（舊/同版本丟棄）
 * - 防護 1（_isSaving）/ 防護 4（dragging）：本地寫/拖中的 uid 不被遠端覆蓋
 * - delete 套用 + 遲到舊刪除忽略
 * - _onMessage 分派（hello/create/update/delete）
 * - createEntity POST + 本地 upsert；updateEntity/deleteEntity 回傳成功/失敗（409 採 server_entity）
 * - resync：套用 server 全量、移除 server 已無者、但保留 in-flight uid（防護 2）
 */
import { describe, expect, test, vi } from "vitest";

import { createCopStream } from "../../static/js/map/cop_stream.js";

// ── mocks ────────────────────────────────────────────────────────────────
// PR-H：cop_stream 退役自建 marker → 純資料層，測試不再需要 FakeMarker。

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
    getToken: () => "tok",
    authFetch,
    canWrite: over.canWrite || (() => true),
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

  test("resync op → 全量對帳（admin reset 後清掉 server 已無者）", async () => {
    // server reset 後 GET 回空 → 本地既有 entity 全移除
    const { stream } = makeStream({ fetchImpl: () => _resp(200, { entities: [] }) });
    stream._applyEntity(E("a", 1, { attributes: { kind: "event", event_id: "x" } }));
    stream._applyEntity(E("b", 1, { attributes: { kind: "route" } }));
    expect(stream._byUid.size).toBe(2);
    stream._onMessage({ op: "resync" });
    await Promise.resolve(); // 等 resync 內的 await fetch microtask
    await Promise.resolve();
    expect(stream._byUid.size).toBe(0);
  });

  test("exercise_switched op → dispatch document 'exercise:switched'（多 session 即時切場）", () => {
    // 此 env 無 jsdom，臨時 stub document/CustomEvent 驗 dispatch（用後還原）。
    const events = [];
    const prevDoc = globalThis.document;
    const prevCE = globalThis.CustomEvent;
    globalThis.CustomEvent = class { constructor(type) { this.type = type; } };
    globalThis.document = { dispatchEvent: (e) => { events.push(e.type); return true; } };
    try {
      const { stream } = makeStream();
      stream._onMessage({ op: "exercise_switched" });
      expect(events).toContain("exercise:switched");
    } finally {
      globalThis.document = prevDoc;
      globalThis.CustomEvent = prevCE;
    }
  });
});

// ── REST 寫入 ───────────────────────────────────────────────────────────────

describe("REST writes", () => {
  test("createEntity POST 帶 attributes/kind + 本地 upsert（map 物件 cutover 用）", async () => {
    const created = E("route:1", 1, { callsign: "北線", attributes: { kind: "route" } });
    const { stream, fetchCalls } = makeStream({ fetchImpl: () => _resp(201, created) });
    const ent = await stream.createEntity({
      type: "b-m-r",
      lat: 24.8,
      lon: 121,
      callsign: "北線",
      attributes: { kind: "route", vertices: [[24.8, 121], [24.9, 121.1]], color: "#56d364" },
    });
    expect(ent.uid).toBe("route:1");
    expect(stream.getEntitiesByKind("route").map((e) => e.uid)).toEqual(["route:1"]);
    const body = JSON.parse(fetchCalls[0].opts.body);
    expect(body.attributes.kind).toBe("route");
    expect(body.attributes.vertices.length).toBe(2);
  });

  test("updateEntity PUT 帶 If-Match（label_anchor 改寫 attributes）", async () => {
    let put = null;
    const { stream } = makeStream({
      fetchImpl: (url, opts) => {
        if (opts && opts.method === "PUT") {
          put = opts;
          return _resp(200, E("p1", 4, { attributes: { kind: "polygon", label_anchor: [1, 2] } }));
        }
        return _resp(200, {});
      },
    });
    stream._applyEntity(E("p1", 3, { attributes: { kind: "polygon" } }));
    const ok = await stream.updateEntity("p1", { attributes: { kind: "polygon", label_anchor: [1, 2] } });
    expect(put.headers["If-Match"]).toBe("3");
    expect(stream.getEntity("p1").version_clock).toBe(4);
    expect(ok).toBe(true); // 成功回 true（編輯器據此決定是否提示失敗）
  });

  test("失敗回傳：createEntity 非 ok → null；update/delete → false（不再 silent）", async () => {
    // POST 403 → createEntity 回 null
    const s1 = makeStream({ fetchImpl: () => _resp(403, {}) });
    expect(await s1.stream.createEntity({ type: "b-m-r", lat: 1, lon: 2 })).toBe(null);
    // PUT 409 → updateEntity 回 false（並採 server_entity）
    const s2 = makeStream({
      fetchImpl: (url, opts) =>
        opts?.method === "PUT" ? _resp(409, { server_entity: E("a", 9) }) : _resp(200, {}),
    });
    s2.stream._applyEntity(E("a", 3));
    expect(await s2.stream.updateEntity("a", { callsign: "x" })).toBe(false);
    // DELETE 500 → deleteEntity 回 false
    const s3 = makeStream({
      fetchImpl: (url, opts) => (opts?.method === "DELETE" ? _resp(500, {}) : _resp(200, {})),
    });
    s3.stream._applyEntity(E("b", 1));
    expect(await s3.stream.deleteEntity("b")).toBe(false);
    // 成功 DELETE → true
    const s4 = makeStream({ fetchImpl: () => _resp(200, { status: "deleted" }) });
    s4.stream._applyEntity(E("c", 1));
    expect(await s4.stream.deleteEntity("c")).toBe(true);
  });

  test("observer（canWrite=false）createEntity no-op", async () => {
    const { stream, authFetch } = makeStream({ canWrite: () => false });
    const ent = await stream.createEntity({ type: "a-u-G", lat: 1, lon: 2 });
    expect(ent).toBe(null);
    expect(authFetch).not.toHaveBeenCalled();
  });

  test("updateEntity 409 → 採 server_entity（對齊 server 現值）", async () => {
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
    const ok = await stream.updateEntity("a", { lat: 1, lon: 2 });
    expect(ok).toBe(false);
    expect(put.headers["If-Match"]).toBe("3"); // 帶改前的 version
    // 409 → 採 server 現值（本地對齊到 v7）
    expect(stream.getEntity("a").version_clock).toBe(7);
    expect(stream.getEntity("a").lat).toBe(50);
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

// ── 週期對帳（#160/#161 軟 stale）──────────────────────────────────────────

describe("週期 resync（軟 stale 移除 + 變灰）", () => {
  test("onopen 啟動週期 tick：fire → 再次 resync 並 emit（修 #161 免手動 refresh）", async () => {
    // 捕捉 WS 實例（驅動 onopen）+ 捕捉 setTimeout 回呼（手動 fire tick）。
    let wsInstance = null;
    const Ctor = function () {
      this.readyState = 1;
      wsInstance = this;
    };
    const timers = [];
    const fetchCalls = [];
    let entities = [E("u1", 1)];
    const authFetch = vi.fn((url) => {
      fetchCalls.push(url);
      return _resp(200, { entities });
    });
    let fires = 0;
    const stream = createCopStream({
      getToken: () => "tok",
      authFetch,
      canWrite: () => true,
      WebSocketCtor: Ctor,
      setTimeoutFn: (cb) => {
        timers.push(cb);
        return timers.length;
      },
      clearTimeoutFn: () => {},
    });
    stream.onChange(() => {
      fires += 1;
    });
    stream.connect();
    wsInstance.onopen(); // 觸發首次 resync + 啟動週期 tick
    await Promise.resolve();
    await Promise.resolve();
    expect(stream._byUid.has("u1")).toBe(true); // 首次 resync 帶入
    const getsAfterOpen = fetchCalls.length;
    expect(timers.length).toBe(1); // 週期 tick 已排程

    // 模擬 iTAK 刪除：server 列表移除 u1 → fire tick → 前端應對帳移除（不需手動 refresh）
    entities = [];
    const firesBefore = fires;
    await timers[timers.length - 1](); // fire 週期 tick（async）
    expect(fetchCalls.length).toBeGreaterThan(getsAfterOpen); // tick 內又 resync 一次
    expect(stream._byUid.has("u1")).toBe(false); // 過窗口移除
    expect(fires).toBeGreaterThan(firesBefore); // emit 觸發重繪（含變灰重算）
    expect(timers.length).toBe(2); // self-reschedule 下一輪
  });

  test("stop() 清掉週期 timer（防 logout 後殘留 tick）", () => {
    let wsInstance = null;
    const Ctor = function () {
      this.readyState = 1;
      this.close = () => {};
      wsInstance = this;
    };
    const cleared = [];
    const stream = createCopStream({
      getToken: () => "tok",
      authFetch: () => _resp(200, { entities: [] }),
      canWrite: () => true,
      WebSocketCtor: Ctor,
      setTimeoutFn: () => 42,
      clearTimeoutFn: (id) => cleared.push(id),
    });
    stream.connect();
    wsInstance.onopen();
    stream.stop();
    expect(cleared).toContain(42); // refresh timer 被清
  });
});

// ── 重連 / onclose race 硬化（#265）────────────────────────────────────────
describe("reconnect / onclose hardening (#265)", () => {
  // 捕捉 WS 實例 + setTimeout(cb, delay)（含 delay 以驗 backoff 階）。
  function makeReconnectStream() {
    const sockets = [];
    const timeouts = []; // {cb, delay}
    const Ctor = function () {
      this.readyState = 1;
      this.close = () => {
        this.readyState = 3;
      };
      sockets.push(this);
    };
    const stream = createCopStream({
      getToken: () => "tok",
      authFetch: () => _resp(200, { entities: [] }),
      canWrite: () => true,
      WebSocketCtor: Ctor,
      setTimeoutFn: (cb, delay) => {
        timeouts.push({ cb, delay });
        return timeouts.length;
      },
      clearTimeoutFn: () => {},
    });
    return { stream, sockets, timeouts };
  }

  test("被取代的舊 socket onclose 不誤排重連（identity guard）", () => {
    const { stream, sockets, timeouts } = makeReconnectStream();
    stream.connect(); // socket0 = _ws
    stream.stop(); // close socket0、_ws=null（模擬舊路徑 stop()+connect()）
    stream.connect(); // socket1 = _ws（_stopped 重設 false）
    expect(sockets.length).toBe(2);

    const before = timeouts.length;
    sockets[0].onclose(); // 舊 socket0 遲到關閉 → guard：_ws 已是 socket1 → 不排重連
    expect(timeouts.length).toBe(before);

    sockets[1].onclose(); // 當前 socket1 關閉 → 正常排重連（反證 guard 不誤殺）
    expect(timeouts.length).toBe(before + 1);
  });

  test("stop() 重置 reconnect backoff 階（#265）", () => {
    const { stream, sockets, timeouts } = makeReconnectStream();
    stream.connect(); // socket0
    sockets[0].onclose(); // attempt 0→1，delay=1000
    expect(timeouts.at(-1).delay).toBe(1000);
    timeouts.at(-1).cb(); // 觸發重連 → socket1
    sockets[1].onclose(); // attempt 1→2，delay=2000
    expect(timeouts.at(-1).delay).toBe(2000);

    stream.stop(); // 重置 _reconnectAttempt=0
    stream.connect(); // socket2
    sockets.at(-1).onclose(); // backoff 自 1000 重起（非 4000）
    expect(timeouts.at(-1).delay).toBe(1000);
  });
});

// ── 常駐層疊看參數（#267）────────────────────────────────────────────────
describe("#472：不再帶 standing 參數（常駐疊看移除、可見性軸改 faction）", () => {
  test("WS 與 resync 皆無 standing 參數", async () => {
    let wsUrl = null;
    const fetchUrls = [];
    const Ctor = function (url) {
      wsUrl = url;
      this.readyState = 1;
    };
    const stream = createCopStream({
      getToken: () => "tok",
      authFetch: (url) => {
        fetchUrls.push(url);
        return _resp(200, { entities: [] });
      },
      canWrite: () => true,
      WebSocketCtor: Ctor,
      wsUrl: "ws://h/api/cop/ws/updates",
      setTimeoutFn: () => 0,
      clearTimeoutFn: () => {},
    });
    stream.connect();
    expect(wsUrl).toBe("ws://h/api/cop/ws/updates");
    await stream.resync();
    expect(fetchUrls.every((u) => !u.includes("standing"))).toBe(true);
  });
});

// ── 渲染委派 seam（PR-G1：map.js 接管渲染）─────────────────────────────────

describe("render delegation seam", () => {
  test("onChange 每次變更觸發 callback", () => {
    const { stream } = makeStream();
    let fires = 0;
    stream.onChange(() => {
      fires += 1;
    });
    stream._applyEntity(E("a", 1));
    stream._applyEntity(E("a", 2)); // update
    stream._applyDelete("a", 3);
    expect(fires).toBe(3);
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

// ── dragLocal（PR-G1b 事件拖曳 per-frame 樂觀位移）──────────────────────────
describe("dragLocal", () => {
  test("設 dragging + 就地更新 lat/lon，且不 emit（map.js 自行重繪）", () => {
    const { stream } = makeStream();
    let fires = 0;
    stream.onChange(() => { fires += 1; });
    stream._applyEntity(E("e1", 1, { attributes: { kind: "event", event_id: "x" } }));
    const baseline = fires; // _applyEntity 自己會 emit 一次
    const ok = stream.dragLocal("e1", 25.5, 121.5);
    expect(ok).toBe(true);
    expect(stream.getEntity("e1").lat).toBe(25.5);
    expect(stream.getEntity("e1").lon).toBe(121.5);
    expect(stream._debugState().dragging).toBe("e1"); // 防遠端覆蓋
    expect(fires).toBe(baseline); // dragLocal 不 emit
  });

  test("dragging 中遠端更新被擋；updateEntity 落地後清 dragging", async () => {
    const { stream } = makeStream({ fetchImpl: () => _resp(200, E("e1", 5, { lat: 9, lon: 9 })) });
    stream._applyEntity(E("e1", 1, { attributes: { kind: "event", event_id: "x" } }));
    stream.dragLocal("e1", 25.5, 121.5);
    // 拖曳中：遠端 update 不得覆蓋（防護 4）
    expect(stream._applyEntity(E("e1", 9, { lat: 0, lon: 0 }))).toBe(false);
    // 落地 → 清 dragging
    await stream.updateEntity("e1", { lat: 25.5, lon: 121.5 });
    expect(stream._debugState().dragging).toBe(null);
  });

  test("uid 不存在 → 回 false", () => {
    const { stream } = makeStream();
    expect(stream.dragLocal("nope", 1, 2)).toBe(false);
  });
});
