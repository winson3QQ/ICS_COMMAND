import { describe, expect, test, vi, beforeAll } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

// fileURLToPath 正確處理 Windows 路徑（new URL().pathname 在 Windows 有前導 / 問題）
const root = fileURLToPath(new URL('../..', import.meta.url));
const jsDir = join(root, 'static/js');
const file = path => readFileSync(join(root, path), 'utf8');

function storage() {
  const values = new Map();
  return {
    getItem: key => values.has(key) ? values.get(key) : null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: key => values.delete(key),
    clear: () => values.clear(),
  };
}

function node(id = '') {
  return {
    id,
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    style: { setProperty() {} },
    dataset: {},
    appendChild() {},
    addEventListener() {},
    removeEventListener() {},
    setAttribute() {},
    focus() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
    blur() {},
    click() {},
    getContext() { return null; },
    textContent: '',
    innerHTML: '',
    value: '',
  };
}

function statefulNode(id = '') {
  const n = node(id);
  const classes = new Set();
  const attributes = {};
  n.classList = {
    add: cls => classes.add(cls),
    remove: cls => classes.delete(cls),
    toggle: cls => classes.has(cls) ? classes.delete(cls) : classes.add(cls),
    contains: cls => classes.has(cls),
  };
  n.setAttribute = (key, value) => { attributes[key] = String(value); };
  n.getAttribute = key => attributes[key];
  n.focus = () => { globalThis.document.activeElement = n; };
  n.blur = () => {
    if (globalThis.document.activeElement === n) globalThis.document.activeElement = null;
  };
  n.disabled = false;
  return n;
}

function installSessionWarningDom() {
  const nodes = {};
  const listeners = {};
  const get = id => {
    if (!nodes[id]) nodes[id] = statefulNode(id);
    return nodes[id];
  };
  globalThis.document.getElementById = get;
  globalThis.document.addEventListener = (type, cb) => {
    listeners[type] = listeners[type] || [];
    listeners[type].push(cb);
  };
  globalThis.document.activeElement = null;
  return { nodes, listeners, get };
}

function okJson(body = {}) {
  return { status: 200, ok: true, json: async () => body };
}

function statusJson(status, body = {}) {
  return { status, ok: status >= 200 && status < 300, json: async () => body };
}

async function flushPromises() {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

beforeAll(() => {
  globalThis.location = { origin: 'http://127.0.0.1:8000' };
  globalThis.sessionStorage = storage();
  globalThis.window = globalThis;
  globalThis.document = {
    body: node('body'),
    head: node('head'),
    activeElement: null,
    getElementById: id => node(id),
    createElement: tag => node(tag),
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener() {},
    dispatchEvent() {},
  };
  globalThis.CustomEvent = class CustomEvent {
    constructor(type, init = {}) {
      this.type = type;
      this.detail = init.detail;
    }
  };
  globalThis.fetch = vi.fn(async () => ({
    ok: true,
    status: 200,
    json: async () => ({ maps: { indoor: { zones: [] }, outdoor: { zones: [] } } }),
  }));
  globalThis.requestAnimationFrame = cb => cb();
  globalThis.alert = vi.fn();
  globalThis.confirm = vi.fn(() => true);
  // navigator 在某些 runtime 是 read-only getter，用 defineProperty 覆寫
  try {
    Object.defineProperty(globalThis, 'navigator', {
      value: { clipboard: { writeText: vi.fn() } },
      writable: true,
      configurable: true,
    });
  } catch (_) {
    // 已可寫的環境直接賦值
    globalThis.navigator = { clipboard: { writeText: vi.fn() } };
  }
});

describe('C1-F commander modules', () => {
  test('ws_connects_and_sends', async () => {
    const ws = await import('../../static/js/ws.js');
    expect(typeof ws.connect).toBe('function');
    expect(ws.send({ type: 'noop' })).toBe(false);
    expect(typeof ws.onMessage).toBe('function');
    expect(typeof ws.canAccessMapObjects).toBe('function');
    expect(typeof ws.canCreateEvents).toBe('function');
    expect(typeof ws.canUseRealModeControls).toBe('function');
  });

  test('map_initialises_without_error', async () => {
    const map = await import('../../static/js/map.js');
    await expect(map.initMap()).resolves.toBeUndefined();
    expect(map.getMapConfig()).toEqual({ maps: { indoor: { zones: [] }, outdoor: { zones: [] } } });
    const source = file('static/js/map.js');
    // P1-10b 步驟 4：map.js 委派 outdoor map 給 maplibre_core.js（取代 Leaflet + protomaps-leaflet）
    expect(source).toMatch(/from '\.\/map\/maplibre_core\.js'/);
    expect(source).toMatch(/_initMaplibre\(\)/);
    expect(source).toMatch(/initMaplibre\('leaflet-map'/);
    // 過渡期：entity rendering 函式仍存在但 stubbed（步驟 5+ port）
    expect(source).toMatch(/_napsgIcon/);
    expect(source).toMatch(/_renderPolygons/);
    expect(source).toMatch(/_renderRoutes/);
    // P1-10c：maplibre_core 接上真實 PMTiles 底圖 —— 註冊 pmtiles:// protocol + 載入 basemap style，
    // pmtiles.js 未載入時 fallback 至 empty dark style（#0d1117 背景）。
    const coreSource = file('static/js/map/maplibre_core.js');
    expect(coreSource).toMatch(/addProtocol\('pmtiles'/);          // 註冊 pmtiles protocol
    expect(coreSource).toMatch(/basemap-dark\.json/);              // 真實 basemap style（dark）
    expect(coreSource).toMatch(/basemap-muted-day\.json/);         // muted-day style
    expect(coreSource).toMatch(/background-color': '#0d1117'/);    // fallback empty dark 仍保留
    expect(coreSource).toMatch(/maplibregl\.Map/);
    // P1-10c 步驟 4：dark↔muted-day 主題切換 —— 走「只抽換底圖層」（removeLayer + addLayer，
    // 不 setStyle，overlay 不動）；map.js 提供 toggleBasemapTheme + 工具列按鈕 + main.js 委派。
    expect(coreSource).toMatch(/export async function setBasemapTheme/);
    expect(coreSource).toMatch(/removeLayer/);                     // 抽換底圖層（非 setStyle）
    expect(source).toMatch(/export function toggleBasemapTheme/);
    expect(source).toMatch(/data-action="toggleBasemapTheme"/);    // 工具列切換鈕
    const mainSrc = file('static/js/main.js');
    expect(mainSrc).toMatch(/case 'toggleBasemapTheme':/);         // 委派接線
  });

  test('reloadMapConfig_refetches_after_login_401', async () => {
    // Bug：boot（登入前）GET /api/map_config 回 401 → _mapConfig=null → 地圖空白，
    // 每次登入要 cmd-shift-R。修法：登入後 onEnterDashboard 呼叫 reloadMapConfig 重抓。
    const map = await import('../../static/js/map.js');
    expect(typeof map.reloadMapConfig).toBe('function');
    // 登入前 401：不得把 {detail:...} 錯誤殼寫進 _mapConfig（沿用 issue#24 的 null guard）
    globalThis.fetch.mockResolvedValueOnce({ ok: false, status: 401, json: async () => ({ detail: 'unauthorized' }) });
    await map.reloadMapConfig();
    expect(map.getMapConfig()).not.toHaveProperty('detail');
    // 登入後 200：reloadMapConfig 重抓成功並更新
    const fresh = { maps: { indoor: { zones: [] }, outdoor: { zones: [{ id: 'znew' }] } } };
    globalThis.fetch.mockResolvedValueOnce({ ok: true, status: 200, json: async () => fresh });
    await map.reloadMapConfig();
    expect(map.getMapConfig()).toEqual(fresh);
    // main.js boot 在 onEnterDashboard 確實呼叫 reloadMapConfig（登入後重抓的 wiring）
    expect(file('static/js/main.js')).toMatch(/reloadMapConfig\(\)/);
  });

  test('events_crud_renders', async () => {
    const events = await import('../../static/js/events.js');
    expect(events._evTypeLabel({ event_type: 'mci' })).toContain('大量傷亡');
    expect(events._parseNotes('[{\"note\":\"ok\"}]')).toEqual([{ note: 'ok' }]);
  });

  test('auth_logout_clears_session', async () => {
    const auth = await import('../../static/js/auth.js');
    sessionStorage.setItem('cmd_session_id', 'token');
    sessionStorage.setItem('cmd_username', 'admin');
    sessionStorage.setItem('cmd_role_detail', 'sysadmin');
    auth.clearSession();
    expect(auth.getToken()).toBeNull();
    expect(sessionStorage.getItem('cmd_username')).toBeNull();
    expect(sessionStorage.getItem('cmd_role_detail')).toBeNull();
  });

  test('session_warning_ux_uses_p2a_status_and_get_heartbeat_contract', () => {
    const authSource = file('static/js/auth.js');
    const mainSource = file('static/js/main.js');
    const html = file('static/commander_dashboard.html');

    expect(authSource).toMatch(/SESSION_STATUS_INTERVAL_MS = 30000/);
    expect(authSource).toMatch(/\/api\/session\/status/);
    expect(authSource).toMatch(/idle_remaining_seconds/);
    expect(authSource).toMatch(/warning_threshold_seconds/);
    expect(authSource).toMatch(/_sessionLastUserActivityAt/);
    expect(authSource).toMatch(/effectiveIdleRemaining/);
    expect(authSource).toMatch(/ICS_SESSION_UX/);
    expect(authSource).toMatch(/session_warning_shown/);
    expect(authSource).toMatch(/session_continue_success/);
    expect(authSource).toMatch(/session_continue_failed/);
    expect(authSource).toMatch(/session_expired_to_login/);
    expect(authSource).toMatch(/session_warning_logout_requested/);
    expect(authSource).toMatch(/_releaseSessionWarningFocus\(overlay\)/);
    expect(authSource).toMatch(/continueSessionFromWarning/);
    expect(authSource).toMatch(/authFetch\(API_BASE \+ '\/api\/auth\/heartbeat'\)/);
    expect(authSource).toMatch(/authFetch\(API_BASE \+ '\/api\/auth\/logout', \{method:'POST'\}\)/);
    expect(authSource).toMatch(/閒置過久, 請重新登入/);
    expect(authSource).toMatch(/Session 已過期, 請重新登入/);
    expect(authSource).toMatch(/暫時無法續期, 請重新登入/);
    expect(authSource).toMatch(/stopSessionStatusPolling\(\)/);

    expect(mainSource).toMatch(/startSessionStatusPolling\(\)/);
    expect(mainSource).toMatch(/case 'sessionContinue': continueSessionFromWarning\(\); break;/);
    expect(mainSource).toMatch(/case 'sessionLogout':\s+logoutFromSessionWarning\(\); break;/);

    expect(html).toMatch(/id="session-warning-overlay"/);
    expect(html).toMatch(/id="session-warning-countdown"/);
    expect(html).toMatch(/id="session-warning-continue" data-action="sessionContinue"/);
    expect(html).toMatch(/id="session-warning-logout" data-action="sessionLogout"/);
    expect(html).toMatch(/Session 即將過期/);
    expect(html).toMatch(/繼續使用/);
    expect(html).toMatch(/立即登出/);

    const combined = `${authSource}\n${mainSource}`;
    expect(combined).not.toMatch(/BroadcastChannel/);
    expect(combined).not.toMatch(/addEventListener\(['"]storage['"]/);
  });

  test('session_warning_behavior_drives_modal_timer_fetch_and_logout_paths', async () => {
    vi.useFakeTimers();
    vi.resetModules();
    sessionStorage.clear();
    const dom = installSessionWarningDom();
    const auth = await import('../../static/js/auth.js');
    sessionStorage.setItem('cmd_session_id', 'token-1');
    sessionStorage.setItem('cmd_username', 'admin');
    sessionStorage.setItem('cmd_role_detail', 'sysadmin');
    fetch.mockReset();

    fetch.mockResolvedValueOnce(okJson({
      valid: true,
      idle_remaining_seconds: 2,
      warning_threshold_seconds: 120,
    }));
    auth.startSessionStatusPolling();
    await flushPromises();

    expect(fetch).toHaveBeenLastCalledWith('http://127.0.0.1:8000/api/session/status', {
      headers: { 'X-Session-Token': 'token-1' },
    });
    expect(dom.get('session-warning-overlay').classList.contains('show')).toBe(true);
    expect(dom.get('session-warning-countdown').textContent).toBe('2');
    await vi.advanceTimersByTimeAsync(16);
    expect(document.activeElement).toBe(dom.get('session-warning-continue'));

    const esc = { key: 'Escape', preventDefault: vi.fn(), stopPropagation: vi.fn() };
    dom.listeners.keydown.forEach(cb => cb(esc));
    expect(esc.preventDefault).toHaveBeenCalled();
    expect(esc.stopPropagation).toHaveBeenCalled();

    fetch
      .mockResolvedValueOnce(okJson({ username: 'admin' }))
      .mockResolvedValueOnce(okJson({
        valid: true,
        idle_remaining_seconds: 900,
        warning_threshold_seconds: 120,
      }));
    await auth.continueSessionFromWarning();
    expect(fetch).toHaveBeenNthCalledWith(2, 'http://127.0.0.1:8000/api/auth/heartbeat', {
      headers: { 'X-Session-Token': 'token-1' },
    });
    expect(dom.get('session-warning-overlay').classList.contains('show')).toBe(false);

    fetch.mockResolvedValueOnce(okJson({
      valid: true,
      idle_remaining_seconds: 3,
      warning_threshold_seconds: 120,
    }));
    auth.startSessionStatusPolling();
    await flushPromises();
    fetch.mockResolvedValueOnce(statusJson(503, {}));
    await auth.continueSessionFromWarning();
    expect(dom.get('session-warning-error').textContent).toBe('暫時無法續期, 請重新登入');
    expect(dom.get('session-warning-overlay').classList.contains('show')).toBe(true);
    await vi.advanceTimersByTimeAsync(1000);
    expect(dom.get('session-warning-countdown').textContent).toBe('2');

    fetch.mockResolvedValueOnce(statusJson(401, {}));
    await auth.continueSessionFromWarning();
    expect(auth.getToken()).toBeNull();
    expect(dom.get('login-screen').style.display).toBe('');
    expect(dom.get('cmd-login-warn').textContent).toBe('閒置過久, 請重新登入');

    sessionStorage.setItem('cmd_session_id', 'token-2');
    fetch.mockResolvedValueOnce(okJson({
      valid: true,
      idle_remaining_seconds: 1,
      warning_threshold_seconds: 120,
    }));
    auth.startSessionStatusPolling();
    await flushPromises();
    await vi.advanceTimersByTimeAsync(1000);
    expect(auth.getToken()).toBeNull();
    expect(dom.get('cmd-login-warn').textContent).toBe('Session 已過期, 請重新登入');

    sessionStorage.setItem('cmd_session_id', 'token-3');
    fetch.mockRejectedValueOnce(new Error('offline'));
    dom.get('session-warning-logout').focus();
    await auth.logoutFromSessionWarning();
    expect(auth.getToken()).toBeNull();
    expect(dom.get('login-screen').style.display).toBe('');
    expect(document.activeElement).toBeNull();

    auth.stopSessionStatusPolling();
    vi.useRealTimers();
  });

  test('session_warning_uses_local_idle_when_background_poll_keeps_backend_fresh', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-05-02T00:00:00Z'));
    vi.resetModules();
    sessionStorage.clear();
    const dom = installSessionWarningDom();
    const auth = await import('../../static/js/auth.js');
    sessionStorage.setItem('cmd_session_id', 'token-bg');
    fetch.mockReset();

    fetch.mockResolvedValueOnce(okJson({
      valid: true,
      idle_remaining_seconds: 90,
      warning_threshold_seconds: 30,
    }));
    auth.startSessionStatusPolling();
    await flushPromises();
    expect(dom.get('session-warning-overlay').classList.contains('show')).toBe(false);

    fetch.mockResolvedValueOnce(okJson({
      valid: true,
      idle_remaining_seconds: 90,
      warning_threshold_seconds: 30,
    }));
    await vi.advanceTimersByTimeAsync(31000);
    await flushPromises();
    expect(dom.get('session-warning-overlay').classList.contains('show')).toBe(false);

    fetch.mockResolvedValueOnce(okJson({
      valid: true,
      idle_remaining_seconds: 90,
      warning_threshold_seconds: 30,
    }));
    await vi.advanceTimersByTimeAsync(30000);
    await flushPromises();
    expect(dom.get('session-warning-overlay').classList.contains('show')).toBe(true);
    expect(Number(dom.get('session-warning-countdown').textContent)).toBeGreaterThanOrEqual(29);

    fetch
      .mockResolvedValueOnce(okJson({ username: 'admin' }))
      .mockResolvedValueOnce(okJson({
        valid: true,
        idle_remaining_seconds: 90,
        warning_threshold_seconds: 30,
      }));
    await auth.continueSessionFromWarning();
    await flushPromises();
    expect(fetch).toHaveBeenNthCalledWith(4, 'http://127.0.0.1:8000/api/auth/heartbeat', {
      headers: { 'X-Session-Token': 'token-bg' },
    });
    expect(dom.get('session-warning-overlay').classList.contains('show')).toBe(false);

    auth.stopSessionStatusPolling();
    vi.useRealTimers();
  });

  test('auth_admin_panel_gate_uses_account_manager_role_detail', () => {
    const authSource = file('static/js/auth.js');
    const html = file('static/commander_dashboard.html');
    expect(authSource).toMatch(/cmd_role_detail/);
    expect(authSource).toMatch(/roleDetail === 'sysadmin'/);
    expect(authSource).toMatch(/roleDetail === 'commander'/);
    expect(authSource).toMatch(/stg-admin-section'\)\.style\.display = _isAccountManagerSession\(\) \? '' : 'none'/);
    expect(authSource).not.toMatch(/stg-admin-section'\)\.style\.display = isCommander/);
    expect(html).toMatch(/<div class="stg-section-title">帳號管理<\/div>/);
    expect(html).not.toMatch(/系統管理員（需 Admin PIN）/);
  });

  test('auth_account_management_does_not_render_delete_action', () => {
    const authSource = file('static/js/auth.js');
    expect(authSource).not.toMatch(/data-action="adm-delete"/);
    expect(authSource).toMatch(/data-action="adm-toggle-status"/);
  });

  test('auth_account_role_dropdowns_include_four_rbac_roles', () => {
    const authSource = file('static/js/auth.js');
    for (const role of ['系統管理員', '指揮官', '操作員', '觀察員']) {
      expect(authSource).toContain(role);
    }
    expect(authSource).toMatch(/_admRoleOptions\(a\.role, _isSysadminSession\(\)\)/);
    expect(authSource).toMatch(/_admRoleOptions\('', _isSysadminSession\(\)\)/);
    expect(authSource).toMatch(/\['操作員', '觀察員'\]/);
    expect(authSource).toMatch(/\/api\/admin\/accounts\/' \+ username \+ '\/role'/);
    expect(authSource).toMatch(/JSON\.stringify\(\{role: newRole\}\)/);
    expect(authSource).toMatch(/目前沒有可管理的下屬帳號/);
    expect(authSource).toMatch(/data-action="admShowTab" data-tab="add"/);
  });

  test('rbac_ui_blocks_real_mode_for_operator_observer_and_event_create_for_observer', () => {
    const authSource = file('static/js/auth.js');
    const mainSource = file('static/js/main.js');
    const eventsSource = file('static/js/events.js');
    const mapSource = file('static/js/map.js');
    const wsSource = file('static/js/ws.js');

    expect(authSource).toMatch(/canUseRealModeControls\(\)/);
    expect(authSource).toMatch(/hasAnyRole\('sysadmin', 'commander'\)/);
    expect(authSource).toMatch(/canCreateEvents\(\)/);
    expect(authSource).toMatch(/hasAnyRole\('sysadmin', 'commander', 'operator'\)/);
    expect(authSource).toMatch(/canAccessMapObjects\(\)/);
    expect(mainSource).toMatch(/if \(!canUseRealModeControls\(\)\) break;/);
    expect(mainSource).toMatch(/ttxToggle\.style\.display = canUseRealModeControls\(\) \? '' : 'none'/);
    expect(mainSource).toMatch(/if \(!canCreateEvents\(\)\) break;/);
    expect(eventsSource).toMatch(/if \(!canCreateEvents\(\)\) return;/);
    // P1-10b 步驟 4：長按 popup 改走 maplibre_core onLongPress callback；
    // canCreateEvents() 守門點在 callback 內（_lpMoved 已封裝進 core，map.js 不再見此變數）
    expect(mapSource).toMatch(/onLongPress: \(\{ lat, lng \}\) => \{\s+if \(canCreateEvents\(\)\) _openEventPopup\(lat, lng\);/);
    expect(mapSource).toMatch(/function _openEventPopup\(lat, lng\) {\s+if \(!canCreateEvents\(\)\) return;/);
    // P1-10b 步驟 9：_evPopupSubmit 簽名變 (typeKey, ctx) — ctx 由 EventPopup 帶來 {lat,lng,reporter}
    expect(mapSource).toMatch(/async function _evPopupSubmit\(typeKey, ctx\) {\s+if \(!canCreateEvents\(\)\) return;/);
    expect(wsSource).toMatch(/canCreateEvents/);
    expect(wsSource).toMatch(/canUseRealModeControls/);
    // P1-10b code-review fix：_renderZones 內事件 zone abbr 對映必須以 zone.node_type
    // 為 key 查 _NAPSG_GROUP_ABBR，不能用 _EVENT_TYPES[zone.event_code]（event_code
    // 是 server-generated 'EV-MMDD-NNN'，不是 _EVENT_TYPES 的 type-slug key）。
    expect(mapSource).toMatch(
      /_NAPSG_GROUP_ABBR\[zone\.node_type\]\s*\|\|\s*_NODE_ABBR\[zone\.node_type\]/,
    );
    expect(mapSource).not.toMatch(/_EVENT_TYPES\[zone\.event_code\]/);
  });

  test('observer_cannot_access_map_objects', () => {
    const authSource = file('static/js/auth.js');
    const mainSource = file('static/js/main.js');
    const mapSource = file('static/js/map.js');
    const wsSource = file('static/js/ws.js');

    expect(authSource).toMatch(/canAccessMapObjects\(\)/);
    expect(authSource).toMatch(/hasAnyRole\('sysadmin', 'commander', 'operator'\)/);
    expect(authSource).toMatch(/ROLE_ALIASES/);
    expect(authSource).toMatch(/sessionStorage\.setItem\('cmd_role_detail', data\.role_detail\)/);
    expect(wsSource).toMatch(/canAccessMapObjects/);
    expect(mainSource).toMatch(/if \(!canAccessMapObjects\(\)\) break;/);
    expect(mainSource).toMatch(/applyMapRoleUiGuards\(\)/);
    expect(mainSource).toMatch(/zone && canAccessMapObjects\(\)/);
    expect(mapSource.match(/if \(!canAccessMapObjects\(\)\) return;/g)?.length).toBeGreaterThanOrEqual(10);
    expect(mapSource).toMatch(/export function applyMapRoleUiGuards\(\)/);
    expect(mapSource).toMatch(/const objectTools = canAccessMapObjects\(\)/);
    expect(mapSource).toMatch(/id="btn-poly-draw"/);
    expect(mapSource).toMatch(/id="btn-route-draw"/);
    // PR-H：btn-flow-add（流向）已退役 → 不再斷言
    expect(mapSource).toMatch(/marker\.addEventListener\('click', \(\) => {\s+if \(!canAccessMapObjects\(\)\) return;/);
    // P1-10b 步驟 11：Leaflet legacy marker click handler (含 L.DomEvent) 已刪；
    // MapLibre zone click 由 entity_layer.js 的 onZoneClick handler 處理（auth guard 已備）
    expect(mapSource).toMatch(/export function showZoneDetail\(zone\) {\s+if \(!canAccessMapObjects\(\)\) return;/);
    expect(mapSource).toMatch(/export function openL4Detail\(unitId, tableName, index\) {\s+if \(!canAccessMapObjects\(\)\) return;/);
    expect(mapSource).toMatch(/export function _startPolyDraw\(\) {\s+if \(!canAccessMapObjects\(\)\) return;/);
    // PR-H：_openFlowForm（流向）已退役 → 改驗仍存在的 _saveRoute guard
    expect(mapSource).toMatch(/export async function _saveRoute\(\) {\s+if \(!canAccessMapObjects\(\)\) return;/);
    expect(mapSource).toMatch(/export function _startRouteDraw\(\) {\s+if \(!canAccessMapObjects\(\)\) return;/);
  });

  test('charts_renders_with_chart_utils_data', async () => {
    const charts = await import('../../static/js/charts.js');
    const series = charts.getApiSeries({
      shelter_history: [{ snapshot_time: '2026-04-28T00:00:00Z', bed_used: 9, bed_total: 10, extra: '{}' }],
      medical_history: [{ snapshot_time: '2026-04-28T00:00:00Z', bed_used: 4, bed_total: 5, extra: '{}' }],
    });
    expect(series.sPct).toEqual([90]);
    expect(series.mPct).toEqual([80]);
    expect(charts.ipiCalc(2, 3)).toBe(9);
  });

  test('ai_stub_exports_frozen_object_with_marker', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const mod = await import('../../static/js/ai.js');
    expect(warn).toHaveBeenCalledWith('[ICS_DMAS] ai.js stub - not implemented');
    expect(mod.default).toEqual({ __stub: true });
    expect(Object.isFrozen(mod.default)).toBe(true);
    warn.mockRestore();
  });

  test('ttx_stub_exports_frozen_object_with_marker', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const mod = await import('../../static/js/ttx.js');
    expect(warn).toHaveBeenCalledWith('[ICS_DMAS] ttx.js stub - not implemented');
    expect(mod.default).toEqual({ __stub: true });
    expect(Object.isFrozen(mod.default)).toBe(true);
    warn.mockRestore();
  });

  test('module_boundaries_enforced', () => {
    const modules = Object.fromEntries(
      readdirSync(jsDir).filter(name => name.endsWith('.js')).map(name => [name, file(`static/js/${name}`)])
    );
    expect(modules['auth.js']).not.toMatch(/^import\s/m);
    expect([...modules['ws.js'].matchAll(/from\s+['"]([^'"]+)['"]/g)].map(m => m[1])).toEqual(['./auth.js']);
    for (const name of ['events.js', 'decisions.js', 'charts.js']) {
      const imports = [...modules[name].matchAll(/from\s+['"]([^'"]+)['"]/g)].map(m => m[1]);
      expect(imports.every(spec => spec === './ws.js')).toBe(true);
    }
    // P1-10b 步驟 4：map.js 額外允許 import './map/*.js'（內部拆檔 maplibre_core / entity_layer 等）
    {
      const imports = [...modules['map.js'].matchAll(/from\s+['"]([^'"]+)['"]/g)].map(m => m[1]);
      expect(imports.every(spec => spec === './ws.js' || spec.startsWith('./map/'))).toBe(true);
    }
    expect(modules['cop.js']).toMatch(/from '\.\/ws\.js'/);
    expect(modules['cop.js']).toMatch(/from '\.\/map\.js'/);
    expect(modules['ai.js']).not.toMatch(/^import\s/m);
    expect(modules['ttx.js']).not.toMatch(/^import\s/m);
  });

  test('cmd_version_from_api_version', () => {
    const html = file('static/commander_dashboard.html');
    const main = file('static/js/main.js');
    expect(html).not.toMatch(/CMD_VERSION/);
    expect(main).toMatch(/\/api\/version/);
    expect(main).toMatch(/cmd_version/);
    // P1-10b 步驟 11：leaflet.min.css 已移除；MapLibre 為唯一 map CSS / JS source。
    expect(html).not.toMatch(/href="\/static\/lib\/leaflet\.min\.css"/);
    expect(html).toMatch(/href="\/static\/lib\/maplibre-gl\.css"/);
    expect(main).toMatch(/\/static\/lib\/maplibre-gl\.js/);
    expect(main).toMatch(/\/static\/lib\/pmtiles\.js/);
    expect(main).toMatch(/_waitForGlobal\('maplibregl'\)/);
    expect(main).not.toMatch(/_configureLeafletAssets\(\)/);
    expect(main).not.toMatch(/\/static\/lib\/leaflet\.min\.js/);
    expect(main).not.toMatch(/\/static\/lib\/protomaps-leaflet\.js/);
    expect(main).not.toMatch(/delete window\.L\.Icon\.Default/);
  });

  test('commander_health_light_uses_api_health_details', () => {
    const cop = file('static/js/cop.js');
    expect(cop).toMatch(/\/api\/health/);
    expect(cop).toMatch(/_refreshCommandHealthLight/);
    expect(cop).toMatch(/cd-server/);
    expect(cop).toMatch(/db_writable/);
    expect(cop).toMatch(/disk_free_mb/);
    expect(cop).toMatch(/disk_free_pct/);
    expect(cop).toMatch(/db_latency_ms/);
    expect(cop).toMatch(/schema_version/);
    expect(cop).toMatch(/Command health/);
  });

  test('health_light_polls_independently_from_dashboard', () => {
    const cop = file('static/js/cop.js');
    // 獨立 health 輪詢 interval 存在於 initCop()，不依附 dashboard 成功路徑
    expect(cop).toMatch(/setInterval\(_refreshCommandHealthLight,\s*5000\)/);
    // dot 更新函式由 _setHealthDot 獨立負責
    expect(cop).toMatch(/_setHealthDot/);
    // 舊的 _setCommandHealthLight（dot + label 混用）已移除
    expect(cop).not.toMatch(/_setCommandHealthLight/);
    // poll() 函式本身不直接寫 conn-dot class（防止干擾 health interval）
    const pollSection = cop.slice(cop.indexOf('export async function poll('));
    const pollBody = pollSection.slice(0, pollSection.indexOf('\nexport function refresh('));
    expect(pollBody).not.toMatch(/conn-dot/);
    // ws.js 不得寫 dot className（只更新 label，dot 由 health interval 統一管理）
    const ws = file('static/js/ws.js');
    expect(ws).not.toMatch(/dot\.className\s*=/);
    expect(ws).not.toMatch(/conn-dot/);
  });
});
