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
    // segmented 日/夜分段鈕（☀ muted-day / ☾ dark），active 標目前主題（對齊站內/站外）
    expect(source).toMatch(/export function setBasemapTheme/);
    expect(source).toMatch(/data-action="setBasemapTheme"/);
    expect(source).toMatch(/data-theme="dark"/);
    expect(source).toMatch(/data-theme="muted-day"/);
    const mainSrc = file('static/js/main.js');
    expect(mainSrc).toMatch(/case 'setBasemapTheme':/);            // 委派接線
    // MGRS grid 配色隨主題（淺底用深色，避免淺藍糊掉）
    expect(source).toMatch(/applyTheme/);
    expect(file('static/js/map/coord_tools.js')).toMatch(/applyTheme\(theme\)/);
  });

  test('event_taxonomy_is_data_driven_from_api', async () => {
    // P1-10d 地基：事件分類改由 /api/event_taxonomy 載入（runtime SoT），
    // 內建常數降為 fallback。map.js 不 import events.js（boundary），由 main.js 橋接。
    const events = file('static/js/events.js');
    const mapSrc = file('static/js/map.js');
    const mainSrc = file('static/js/main.js');
    expect(events).toMatch(/export async function loadEventTaxonomy/);
    expect(events).toMatch(/export function applyTaxonomy/);
    expect(events).toMatch(/\/api\/event_taxonomy/);
    expect(mapSrc).toMatch(/export function applyEventTaxonomy/);   // map.js 自有套用，不 import events.js
    expect(mainSrc).toMatch(/loadEventTaxonomy\(\)/);               // 登入後載入
    expect(mainSrc).toMatch(/applyEventTaxonomy\(tax\)/);           // 橋接到 map.js
  });

  test('applyTaxonomy_mutates_in_place_and_falls_back', async () => {
    const ev = await import('../../static/js/events.js');
    const snapE = JSON.parse(JSON.stringify(ev.NAPSG_EVENTS));
    const snapG = JSON.parse(JSON.stringify(ev.NAPSG_GROUPS));
    const ref = ev.NAPSG_EVENTS;  // 記 identity
    try {
      const ok = ev.applyTaxonomy({
        groups: [{ key: 'g1', label: 'G1' }],
        events: [{ key: 'x', label: 'X', group: 'g1', severity: 'info' }],
      });
      expect(ok).toBe(true);
      expect(ev.NAPSG_EVENTS).toBe(ref);              // 就地 mutate，identity 不變（保留既有 ref）
      expect(ev.NAPSG_EVENTS.x.label).toBe('X');
      expect(ev.NAPSG_EVENTS.explosive).toBeUndefined();  // 舊 key 清掉
      expect(ev.NAPSG_GROUPS.g1).toBe('G1');
      expect(ev.applyTaxonomy(null)).toBe(false);     // 壞輸入 → false（保留 fallback）
      expect(ev.applyTaxonomy({ events: 'nope' })).toBe(false);
    } finally {
      for (const k of Object.keys(ev.NAPSG_EVENTS)) delete ev.NAPSG_EVENTS[k];
      Object.assign(ev.NAPSG_EVENTS, snapE);
      for (const k of Object.keys(ev.NAPSG_GROUPS)) delete ev.NAPSG_GROUPS[k];
      Object.assign(ev.NAPSG_GROUPS, snapG);
    }
  });

  test('p1_10d_event_visual_diamond_severity_pulse', async () => {
    // P1-10d 視覺：事件 ◆ diamond（NAPSG hazard）+ severity NAPSG 色 token + critical 脈動。
    const mapSrc = file('static/js/map.js');
    expect(file('static/css/ds-tokens.css')).toMatch(/--severity-critical:\s*#FF181E/i);  // NAPSG Red token
    // #110/§8：_SEV_COLORS 由寫死 hex 改讀 ds-tokens（cssVar 橋接，fallback=同值 NAPSG 色）
    expect(mapSrc).toMatch(/critical: cssVar\('--severity-critical', '#FF181E'\)/);
    expect(mapSrc).toMatch(/id: 'zones-event'/);             // 事件 diamond 層
    // 乙-2a（#243）：zones-event icon-image 改 regime-driven（alert→▲、其餘→◆）
    expect(mapSrc).toMatch(/'icon-image': \['match', \['get', 'regime'\], 'alert', 'zone-triangle', 'zone-diamond'\]/);
    expect(mapSrc).toMatch(/id: 'zones-crit-pulse'/);        // critical 脈動層
    expect(mapSrc).toMatch(/bakeDiamondSdf\(map, 'zone-diamond'\)/);
    expect(mapSrc).toMatch(/bakeTriangleSdf\(map, 'zone-triangle'\)/);  // 乙-2a：▲ alert
    expect(file('static/js/map/entity_layer.js')).toMatch(/export function bakeDiamondSdf/);
    expect(file('static/js/map/entity_layer.js')).toMatch(/export function bakeTriangleSdf/);
    // 乙-2b（#243）：military 走**獨立 zones-military-icon 層**（全彩 milsymbol、**不套 icon-color**，
    // 否則被 severity 染色）；zones-event/outline/abbr filter 排除 military；async 烤 SIDC + seq guard。
    expect(mapSrc).toMatch(/id: 'zones-military-icon'/);
    expect(mapSrc).toMatch(/\['==', \['get', 'regime'\], 'military'\]/);   // military 層 filter
    expect(mapSrc).toMatch(/\['!=', \['get', 'regime'\], 'military'\]/);   // event/outline/abbr 排除 military
    expect(mapSrc).toMatch(/map\.on\('click', 'zones-military-icon'/);     // 點軍用框 → 事件 modal
    expect(mapSrc).toMatch(/milSidcs\.add\(sidc\)/);
    expect(mapSrc).toMatch(/bakeMilSymbol\(_bakeMap, s\)/);
    expect(mapSrc).toMatch(/seq === _zoneRenderSeq/);  // async bake seq guard
  });

  test('p2_05b_type_palette_uses_design_tokens', async () => {
    // #110/§8：POLY/ROUTE/INFRA/NODE 色由遊離 hex 收斂到 ds-tokens 調色盤（cssVar 橋接）。
    const mapSrc = file('static/js/map.js');
    // cssVar 橋接存在（getComputedStyle 讀 CSS token，fallback 兜底）
    expect(mapSrc).toMatch(/function cssVar\(name, fallback\)/);
    expect(mapSrc).toMatch(/getComputedStyle/);
    // 遊離 hex 不再出現在 type-palette 定義（已映射到標準 token）
    for (const adhoc of ['#e05555', '#c0392b', '#ff7f50', '#56d364', '#f0883e']) {
      expect(mapSrc).not.toContain(adhoc);
    }
    // 色表改讀 token
    expect(mapSrc).toMatch(/control:\s*\{ label: '管制區', color: cssVar\('--red'/);
    expect(mapSrc).toMatch(/fire:\s*\{ label: '消防站', color: cssVar\('--orange'/);
    expect(mapSrc).toMatch(/primary:\s*\{ label: '主要疏散路線', color: cssVar\('--green'/);
  });

  test('napsg_glyph_foreground_select_and_vendor', async () => {
    // P1-10d 正式 icon：有 vendored NAPSG 象形且已 bake → 用 glyph，否則退 abbr（混合）。
    const el = await import('../../static/js/map/entity_layer.js');
    const glyphs = await import('../../static/js/map/napsg_glyphs.js');
    // pickForeground 純函式
    expect(el.pickForeground({ isEvent: true, evType: 'explosive', abbr: '爆', hasGlyph: true }))
      .toEqual({ fg: 'napsg-glyph-explosive', fg_glyph: true });
    expect(el.pickForeground({ isEvent: true, evType: 'explosive', abbr: '爆', hasGlyph: false }))
      .toEqual({ fg: 'napsg-abbr-爆', fg_glyph: false });   // 未 bake → 退 abbr（避免缺圖空白）
    expect(el.pickForeground({ isEvent: true, evType: 'qrf', abbr: 'QR', hasGlyph: false }))
      .toEqual({ fg: 'napsg-abbr-QR', fg_glyph: false });   // 無 vendored glyph（ICS ops）
    expect(el.pickForeground({ isEvent: false, evType: null, abbr: '收', hasGlyph: false }))
      .toEqual({ fg: 'napsg-abbr-收', fg_glyph: false });   // 節點永遠 abbr
    // zoneToNodeFeature 帶 fg / fg_glyph（預設退 abbr，舊呼叫相容）
    const f = el.zoneToNodeFeature({ lat: 1, lng: 2, event_code: 'E1' }, { abbr: '爆' });
    expect(f.properties.fg).toBe('napsg-abbr-爆');
    expect(f.properties.fg_glyph).toBe(false);
    // vendored 集：6 個強配，皆含白色 SVG；hasNapsgGlyph 正確分辨
    expect(Object.keys(glyphs.NAPSG_GLYPH_SVG).sort())
      .toEqual(['comm_fail', 'evacuation', 'explosive', 'facility', 'hazard', 'rescue']);
    for (const k of Object.keys(glyphs.NAPSG_GLYPH_SVG)) {
      expect(glyphs.hasNapsgGlyph(k)).toBe(true);
      expect(glyphs.NAPSG_GLYPH_SVG[k]).toMatch(/<svg[^>]*fill="#fff"/);
    }
    expect(glyphs.hasNapsgGlyph('qrf')).toBe(false);
    expect(glyphs.hasNapsgGlyph(undefined)).toBe(false);
    expect(typeof el.bakeSvgIcon).toBe('function');   // 非 SDF SVG raster
    // map.js wiring：import vendored、bake、fg 前景層
    const mapSrc = file('static/js/map.js');
    expect(mapSrc).toMatch(/from '\.\/map\/napsg_glyphs\.js'/);
    expect(mapSrc).toMatch(/_bakeGlyphs\(map\)/);
    expect(mapSrc).toMatch(/'icon-image': \['coalesce', \['get', 'fg'\]/);
    expect(mapSrc).toMatch(/fg_glyph/);
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

  test('taxonomy_editor_C1_buildBody_and_wiring', async () => {
    // #66 PR-C1：編輯既有 + soft-delete（不增不刪 key）。_buildTaxonomyBody 為純合併函式。
    const ev = await import('../../static/js/events.js');
    const raw = {
      version: 1,
      groups: [{ key: 'security', label: '安全', order: 1 }, { key: 'ops', label: '行動', order: 2 }],
      events: [
        { key: 'explosive', label: '爆裂', group: 'security', severity: 'critical', cot_type: 'a-h-G', defaultAssigned: 'forward', abbr: '爆' },
        { key: 'other', label: '其他', group: 'ops', severity: 'info', cot_type: 'a-u-G', defaultAssigned: null, abbr: '他' },
      ],
    };
    const body = ev._buildTaxonomyBody(raw, {
      groups: { security: { label: '安全威脅', deleted: false } },
      events: {
        explosive: { label: '疑似爆裂物', severity: 'critical', group: 'security', cot_type: 'a-h-G', defaultAssigned: '', deleted: false },
        other: { label: '其他', severity: 'info', group: 'ops', cot_type: 'a-u-G', defaultAssigned: 'command', deleted: true },
      },
    });
    expect(body.groups.find((g) => g.key === 'security').label).toBe('安全威脅');  // rename
    const exp = body.events.find((e) => e.key === 'explosive');
    expect(exp.label).toBe('疑似爆裂物');
    expect(exp.defaultAssigned).toBeNull();   // 清空 → null
    expect(exp.abbr).toBe('爆');              // 未編欄位保留
    expect('deleted' in exp).toBe(false);
    const oth = body.events.find((e) => e.key === 'other');
    expect(oth.deleted).toBe(true);           // soft-delete
    expect(oth.defaultAssigned).toBe('command');
    expect(body.events.map((e) => e.key).sort()).toEqual(['explosive', 'other']);  // 不增不刪 key
    // 空 label / cot_type 保留舊（required，不可清成空）
    const body2 = ev._buildTaxonomyBody(raw, { events: { explosive: { label: '', severity: 'critical', group: 'security', cot_type: '', defaultAssigned: '', deleted: false } } });
    expect(body2.events.find((e) => e.key === 'explosive').label).toBe('爆裂');
    expect(body2.events.find((e) => e.key === 'explosive').cot_type).toBe('a-h-G');
    // wiring：editor 函式 + main.js 派發 + HTML 入口 + auth.js sysadmin 守門
    expect(typeof ev.openTaxonomyEditor).toBe('function');
    expect(typeof ev.saveTaxonomyFromEditor).toBe('function');
    const mainSrc = file('static/js/main.js');
    expect(mainSrc).toMatch(/case 'openTaxonomyEditor'/);
    expect(mainSrc).toMatch(/case 'taxSave'/);
    expect(mainSrc).toMatch(/_reloadTaxonomyPipeline/);
    const html = file('static/commander_dashboard.html');
    expect(html).toMatch(/id="stg-taxonomy-section"/);
    expect(html).toMatch(/data-action="openTaxonomyEditor"/);
    expect(file('static/js/auth.js')).toMatch(/stg-taxonomy-section[\s\S]{0,80}hasAnyRole\('sysadmin'\)/);
  });

  test('taxonomy_softdelete_hides_and_defaultAssigned_prefilled', async () => {
    // #66：soft-delete 的型別不出現在建立事件下拉（兩個下拉都跳過 deleted）。
    const mapSrc = file('static/js/map.js');
    const evSrc = file('static/js/events.js');
    expect(mapSrc).toMatch(/if \(def\.deleted\) continue;/);     // _populateNapsgCsel
    expect(evSrc).toMatch(/if \(v\.deleted\) return;/);          // _updateEvTypeFromCategories
    // 新事件預填 taxonomy 的 defaultAssigned → assigned_unit（兩個創建路徑）。
    expect(mapSrc).toMatch(/assigned_unit: evDef\.defaultAssigned \|\| null/);          // _evPopupSubmit
    expect(evSrc).toMatch(/assigned_unit:\s*NAPSG_EVENTS\[el\('ev-type'\)\.value\]\?\.defaultAssigned/);  // submitEvent
  });

  test('taxonomy_editor_provenance_ui', async () => {
    // 編輯器標出每欄來源（對外 NAPSG/FEMA/TAK vs ICS/NIMS）+ CoT/COP 說明。
    const evSrc = file('static/js/events.js');
    expect(evSrc).toMatch(/tax-help/);                       // 說明框
    expect(evSrc).toMatch(/Cursor on Target/);               // CoT
    expect(evSrc).toMatch(/Common Operating Picture/);       // COP（載體）
    expect(evSrc).toMatch(/NAPSG/);
    expect(evSrc).toMatch(/NIMS/);
    expect(evSrc).toMatch(/tax-band-ext/);                   // 對外帶
    expect(evSrc).toMatch(/tax-band-ics/);                   // ICS 內部帶
    expect(file('static/commander_dashboard.html')).toMatch(/\.tax-band-ext\{/);  // 帶背景 CSS
    // schema「定義」欄（source）：唯讀 badge（非下拉，事實屬性不可由 user 改）+ buildBody 保留 + seed 已填。
    const ev = await import('../../static/js/events.js');
    expect(evSrc).toMatch(/tax-src-badge/);                  // 唯讀 badge
    expect(evSrc).not.toMatch(/tax-e-src/);                  // 不是可編 select
    const raw = { version: 1, groups: [{ key: 'security', label: '安全' }],
      events: [{ key: 'explosive', label: '爆', group: 'security', severity: 'critical', cot_type: 'a-h-G', source: 'napsg' }] };
    // source 唯讀：即使 edits 帶 source，buildBody 也不改（spread 保留原值）
    expect(ev._buildTaxonomyBody(raw, { events: { explosive: { source: 'ics', label: '改名' } } }).events[0].source).toBe('napsg');
    // seed 22 事件都已填 source（napsg/ics）
    const seed = JSON.parse(file('static/event_taxonomy.seed.json'));
    expect(seed.events.every((e) => e.source === 'napsg' || e.source === 'ics')).toBe(true);
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
    // P1-14 PR-2：ttx-toggle 已退役 → 演習建立 / 啟動 / 歸檔 dispatch 沿用 canUseRealModeControls 守門。
    expect(mainSource).toMatch(/case 'exCreate':[\s\S]{0,80}if \(!canUseRealModeControls\(\)\) break;/);
    expect(mainSource).not.toMatch(/ttxToggle/);
    expect(mainSource).not.toMatch(/toggleTTXMode/);
    expect(mainSource).not.toMatch(/#FF6600/);
    expect(mainSource).toMatch(/if \(!canCreateEvents\(\)\) break;/);
    expect(eventsSource).toMatch(/if \(!canCreateEvents\(\)\) return;/);
    // P2-34（#220）：長按 → 統一建立對話框 _openCreatePopup（取代 _openEventPopup）；
    // gate 放寬到「可建任何物件」聯集（canAccessMapObjects || canCreateEvents），類別依角色濾。
    // #193：長按改帶 point 分流（命中 marker→篩通聯、空地→建立），故 onLongPress 簽名 +point，
    // 建立仍走 _openCreatePopup(lat, lng)（空地分支）。
    expect(mapSource).toMatch(/onLongPress: \(\{ lat, lng, point \}\) => \{/);
    expect(mapSource).toMatch(/_openCreatePopup\(lat, lng\);/);
    expect(mapSource).toMatch(/function _openCreatePopup\(lat, lng\) {\s+if \(!canAccessMapObjects\(\) && !canCreateEvents\(\)\) return;/);
    // P1-10b 步驟 9：_evPopupSubmit 簽名 (typeKey, ctx) — ctx 由 CreatePopup onCreate 帶來 {lat,lng,reporter}
    expect(mapSource).toMatch(/async function _evPopupSubmit\(typeKey, ctx\) {\s+if \(!canCreateEvents\(\)\) return;/);
    expect(wsSource).toMatch(/canCreateEvents/);
    expect(wsSource).toMatch(/canUseRealModeControls/);
    // P1-10d 事件資料模型：事件 marker abbr 用「事件型別自己的 abbr」（_EVENT_TYPES[evType]?.abbr,
    // evType=ev.event_type 的 type-slug），同群組事件才分得出；orphan/查無型別退群組 abbr；
    // 節點用 _NODE_ABBR。仍**不可**用 zone.event_code 當 _EVENT_TYPES key（server-gen 'EV-MMDD-NNN'，非 slug）。
    expect(mapSource).toMatch(/_EVENT_TYPES\[evType\]\?\.abbr/);
    expect(mapSource).toMatch(/_NODE_ABBR\[zone\.node_type\]/);
    expect(mapSource).not.toMatch(/_EVENT_TYPES\[zone\.event_code\]/);
    // #66 PR-B 解撞名：事件類別走 event_group（建立 attributes + abbr fallback），不再借 node_type；
    // group 權威來源 = event_type 經 taxonomy 推得。
    expect(mapSource).toMatch(/event_group: evGroup/);                 // 建事件 attributes
    expect(mapSource).toMatch(/event_type: typeKey/);                  // 甲-1（#240）：marker 自帶觀察型別
    expect(mapSource).toMatch(/evType = zone\.event_type \|\| ev\?\.event_type/);  // render 讀 marker 自身優先
    expect(mapSource).toMatch(/_NAPSG_GROUP_ABBR\[evGroup\]/);         // abbr fallback 用 group 非 node_type
    expect(mapSource).not.toMatch(/_NAPSG_GROUP_ABBR\[zone\.node_type\]/);  // 舊撞名寫法已移除
  });

  test('issue222_tak_marker_category_renamed_and_gated_by_tak_enabled', () => {
    const mapSource = file('static/js/map.js');
    const copSource = file('static/js/cop.js');
    // #222：類別改名「TAK 標記」（舊「感知 / 敵情標記」移除）。
    expect(mapSource).toMatch(/label: '📍 TAK 標記'/);
    expect(mapSource).not.toMatch(/感知 \/ 敵情標記/);
    // gate：角色（operator+）且 TAK 開關啟用——關閉 TAK 時不提供 TAK 標記建立入口。
    expect(mapSource).toMatch(/if \(canAccessMapObjects\(\) && _takEnabled\) \{/);
    // _takEnabled fail-closed 預設 false，由 cop.js 的 tak:status 事件餵。
    expect(mapSource).toMatch(/let _takEnabled = false;/);
    expect(mapSource).toMatch(/addEventListener\('tak:status'/);
    expect(mapSource).toMatch(/_takEnabled = !!e\.detail\?\.enabled/);
    // cop.js 只在查詢成功（status 非 null）時才 dispatch enabled（transient 失敗保留上次值，不閃）。
    expect(copSource).toMatch(/if \(status\) \{\s*\n\s*document\.dispatchEvent\(new CustomEvent\('tak:status'/);
    expect(copSource).toMatch(/enabled: !!status\?\.enabled/);
    // #222 review：既有 contact 的廣播按鈕/右鍵選單也受 _takEnabled gate（與建立入口對稱）+ 409 明確訊息。
    expect(mapSource).toMatch(/!canAccessMapObjects\(\) \|\| !_takEnabled \|\| !_copStream/);
    expect(mapSource).toMatch(/TAK 連線已停用，無法廣播/);
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
