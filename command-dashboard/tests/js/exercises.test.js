/**
 * tests/js/exercises.test.js — P1-14 PR-2：演習管理 + active chip 單測
 *
 * 鎖住：
 * - 純函式：exerciseChipView（active→含演習名 / 無 active→「實戰」；class/title 不含字面 hex）
 * - exerciseStatusLabel / getActiveExercise
 * - renderExerciseChip：active → 文字含演習名；無 active →「實戰」；class/title 不含字面 hex
 * - list / create / activate / archive dispatch → 呼叫正確 endpoint（mock authFetch）
 * - RBAC：非指揮層不渲染建立 / 啟動 / 歸檔鈕
 */
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

// mock ws.js：注入 authFetch 與角色判斷（exercises.js 由 ws.js re-export 取用）
const mockAuthFetch = vi.fn();
let _canManage = true;
let _isSysadmin = true;
vi.mock('../../static/js/ws.js', () => ({
  authFetch: (...a) => mockAuthFetch(...a),
  canUseRealModeControls: () => _canManage,
}));
// exercises.js 由 auth.js 取 hasAnyRole（刪除鈕 sysadmin-only 判斷）
vi.mock('../../static/js/auth.js', () => ({
  hasAnyRole: (...roles) => (roles.includes('sysadmin') ? _isSysadmin : false),
}));

const HEX_RE = /#[0-9a-fA-F]{3,8}\b/;   // 偵測字面 hex 顏色

function resp(status, body) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  });
}

// 最小 DOM 節點（textContent / className / innerHTML / value 可讀寫）
function makeNode(id = '') {
  return { id, textContent: '', className: '', title: '', innerHTML: '', value: '', dataset: {},
    scrollIntoView() {} };
}

let _nodes;
function installDom() {
  _nodes = {};
  const get = id => {
    if (!_nodes[id]) _nodes[id] = makeNode(id);
    return _nodes[id];
  };
  globalThis.document = { getElementById: get };
  return get;
}

beforeEach(() => {
  globalThis.location = { origin: 'http://127.0.0.1:8000' };
  mockAuthFetch.mockReset();
  _canManage = true;
  _isSysadmin = true;
  installDom();
});

afterEach(() => {
  vi.resetModules();
});

const ACTIVE = { id: 1, name: '北區大震演練', type: 'ttx', status: 'active', created_at: '2026-06-01T00:00:00Z' };
const SETUP  = { id: 2, name: '化災桌推', type: 'ttx', status: 'setup', created_at: '2026-06-02T00:00:00Z' };

describe('exercises 純函式', () => {
  test('exerciseChipView：演習(ttx) → 「演習／名稱」、ex-chip--ttx、無字面 hex', async () => {
    const m = await import('../../static/js/exercises.js');
    const v = m.exerciseChipView(ACTIVE);  // type='ttx'
    expect(v.text).toContain('北區大震演練');
    expect(v.text).toContain('演習／');
    expect(v.className).toMatch(/ex-chip--ttx/);
    expect(v.className).not.toMatch(HEX_RE);
    expect(v.title).not.toMatch(HEX_RE);
  });

  test('exerciseChipView：實戰(real) → 「實戰／名稱」、ex-chip--real', async () => {
    const m = await import('../../static/js/exercises.js');
    const v = m.exerciseChipView({ id: 3, name: '颱風應變', type: 'real', status: 'active' });
    expect(v.text).toContain('颱風應變');
    expect(v.text).toContain('實戰／');
    expect(v.className).toMatch(/ex-chip--real/);
    expect(v.className).not.toMatch(HEX_RE);
  });

  test('exerciseChipView：無 active → 「無進行中場次」、idle、無字面 hex', async () => {
    const m = await import('../../static/js/exercises.js');
    const v = m.exerciseChipView(null);
    expect(v.text).toBe('無進行中場次');
    expect(v.className).toMatch(/ex-chip--idle/);
    expect(v.className).not.toMatch(HEX_RE);
  });

  test('getActiveExercise：找 status==="active"', async () => {
    const m = await import('../../static/js/exercises.js');
    expect(m.getActiveExercise([SETUP, ACTIVE])).toBe(ACTIVE);
    expect(m.getActiveExercise([SETUP])).toBeNull();
    expect(m.getActiveExercise(null)).toBeNull();
  });

  test('exerciseStatusLabel：狀態中文化', async () => {
    const m = await import('../../static/js/exercises.js');
    expect(m.exerciseStatusLabel('active')).toBe('進行中');
    expect(m.exerciseStatusLabel('setup')).toBe('準備中');
    expect(m.exerciseStatusLabel('archived')).toBe('已封存');
  });
});

describe('activeExerciseType（#258 β-1：編輯閘 mode-aware 讀模組快取）', () => {
  test('active ttx → "ttx"（TTX 才放行編輯外部來源）', async () => {
    const m = await import('../../static/js/exercises.js');
    m.renderExerciseChip(ACTIVE);  // type='ttx'
    expect(m.activeExerciseType()).toBe('ttx');
  });
  test('active real → "real"（實戰 → 外部來源唯讀）', async () => {
    const m = await import('../../static/js/exercises.js');
    m.renderExerciseChip({ id: 3, name: '颱風應變', type: 'real', status: 'active' });
    expect(m.activeExerciseType()).toBe('real');
  });
  test('無 active / 未設快取 → null（→ 唯讀）', async () => {
    const m = await import('../../static/js/exercises.js');
    expect(m.activeExerciseType()).toBeNull();   // 預設快取空
    m.renderExerciseChip(null);
    expect(m.activeExerciseType()).toBeNull();
  });
});

describe('renderExerciseChip', () => {
  test('active → 文字含演習名，class/title 不含字面 hex', async () => {
    const get = installDom();
    const m = await import('../../static/js/exercises.js');
    m.renderExerciseChip(ACTIVE);
    const chip = get('exercise-chip');
    expect(chip.textContent).toContain('北區大震演練');
    expect(chip.className).not.toMatch(HEX_RE);
    expect(chip.title).not.toMatch(HEX_RE);
    expect(chip.className).toMatch(/ex-chip--ttx/);
  });

  test('無 active → 「無進行中場次」', async () => {
    const get = installDom();
    const m = await import('../../static/js/exercises.js');
    m.renderExerciseChip(null);
    expect(get('exercise-chip').textContent).toBe('無進行中場次');
    expect(get('exercise-chip').className).toMatch(/ex-chip--idle/);
  });
});

describe('API dispatch → 正確 endpoint', () => {
  test('loadExercises → GET /api/exercises', async () => {
    mockAuthFetch.mockReturnValueOnce(resp(200, [ACTIVE, SETUP]));
    const m = await import('../../static/js/exercises.js');
    const list = await m.loadExercises();
    expect(mockAuthFetch).toHaveBeenCalledWith('http://127.0.0.1:8000/api/exercises');
    expect(list).toEqual([ACTIVE, SETUP]);
  });

  test('createExercise → POST /api/exercises 帶 name + type', async () => {
    mockAuthFetch.mockReturnValueOnce(resp(200, SETUP));
    const m = await import('../../static/js/exercises.js');
    await m.createExercise('化災桌推', 'real');
    const [url, opts] = mockAuthFetch.mock.calls[0];
    expect(url).toBe('http://127.0.0.1:8000/api/exercises');
    expect(opts.method).toBe('POST');
    expect(JSON.parse(opts.body)).toEqual({ name: '化災桌推', type: 'real' });
  });

  test('activateExercise → POST /{id}/activate', async () => {
    mockAuthFetch.mockReturnValueOnce(resp(200, {}));
    const m = await import('../../static/js/exercises.js');
    await m.activateExercise(7);
    expect(mockAuthFetch).toHaveBeenCalledWith(
      'http://127.0.0.1:8000/api/exercises/7/activate', { method: 'POST' });
  });

  test('archiveExercise → POST /{id}/archive', async () => {
    mockAuthFetch.mockReturnValueOnce(resp(200, {}));
    const m = await import('../../static/js/exercises.js');
    await m.archiveExercise(9);
    expect(mockAuthFetch).toHaveBeenCalledWith(
      'http://127.0.0.1:8000/api/exercises/9/archive', { method: 'POST' });
  });

  test('deleteExercise → DELETE /{id}', async () => {
    mockAuthFetch.mockReturnValueOnce(resp(200, { ok: true }));
    const m = await import('../../static/js/exercises.js');
    await m.deleteExercise(5);
    expect(mockAuthFetch).toHaveBeenCalledWith(
      'http://127.0.0.1:8000/api/exercises/5', { method: 'DELETE' });
  });
});

describe('renderExercisePanel + RBAC', () => {
  test('指揮層：渲染建立表單 + 啟動 / 歸檔鈕，chip 同步 active', async () => {
    _canManage = true;
    const get = installDom();
    mockAuthFetch.mockReturnValueOnce(resp(200, [ACTIVE, SETUP]));
    const m = await import('../../static/js/exercises.js');
    await m.renderExercisePanel();
    const html = get('ex-panel-body').innerHTML;
    expect(html).toMatch(/data-action="exCreate"/);
    expect(html).toMatch(/data-action="exActivate" data-id="2"/);   // setup 可啟動
    expect(html).toMatch(/data-action="exArchive"/);
    expect(html).not.toMatch(/data-action="exActivate" data-id="1"/); // active 不再顯示啟動
    // chip 同步為 active 演習
    expect(get('exercise-chip').textContent).toContain('北區大震演練');
    // 面板輸出不含字面 hex（樣式走 CSS class）
    expect(html).not.toMatch(HEX_RE);
  });

  test('非指揮層：仍可看 list，但不渲染建立 / 啟動 / 歸檔鈕', async () => {
    _canManage = false;
    const get = installDom();
    mockAuthFetch.mockReturnValueOnce(resp(200, [ACTIVE, SETUP]));
    const m = await import('../../static/js/exercises.js');
    await m.renderExercisePanel();
    const html = get('ex-panel-body').innerHTML;
    expect(html).toContain('北區大震演練');   // list 仍可見
    expect(html).not.toMatch(/data-action="exCreate"/);
    expect(html).not.toMatch(/data-action="exActivate"/);
    expect(html).not.toMatch(/data-action="exArchive"/);
  });

  test('刪除鈕：sysadmin 才出現、且僅非 active；commander 不顯示', async () => {
    // sysadmin（_isSysadmin=true）：非 active 的 SETUP(id=2) 有刪除鈕，ACTIVE(id=1) 無
    _canManage = true; _isSysadmin = true;
    let get = installDom();
    mockAuthFetch.mockReturnValueOnce(resp(200, [ACTIVE, SETUP]));
    let m = await import('../../static/js/exercises.js');
    await m.renderExercisePanel();
    let html = get('ex-panel-body').innerHTML;
    expect(html).toMatch(/data-action="exDelete" data-id="2"/);
    expect(html).not.toMatch(/data-action="exDelete" data-id="1"/);  // active 不可刪
    // commander（canManage 但非 sysadmin）：無刪除鈕
    vi.resetModules();
    _canManage = true; _isSysadmin = false;
    get = installDom();
    mockAuthFetch.mockReturnValueOnce(resp(200, [ACTIVE, SETUP]));
    m = await import('../../static/js/exercises.js');
    await m.renderExercisePanel();
    html = get('ex-panel-body').innerHTML;
    expect(html).not.toMatch(/data-action="exDelete"/);
    expect(html).toMatch(/data-action="exArchive"/);   // commander 仍可歸檔
  });
});

describe('handleExCreate', () => {
  test('空名稱 → 顯示警告、不打 API', async () => {
    const get = installDom();
    get('ex-new-name').value = '   ';
    const m = await import('../../static/js/exercises.js');
    await m.handleExCreate();
    expect(get('ex-create-warn').textContent).toBe('請輸入演習名稱');
    expect(mockAuthFetch).not.toHaveBeenCalled();
  });

  test('有效輸入 → POST 後重渲染（再 GET list）', async () => {
    const get = installDom();
    get('ex-new-name').value = '北區大震演練';
    get('ex-new-type').value = 'ttx';
    mockAuthFetch
      .mockReturnValueOnce(resp(200, ACTIVE))           // create
      .mockReturnValueOnce(resp(200, [ACTIVE]));        // renderExercisePanel → loadExercises
    const m = await import('../../static/js/exercises.js');
    await m.handleExCreate();
    expect(mockAuthFetch.mock.calls[0][1].method).toBe('POST');
    expect(mockAuthFetch.mock.calls[1][0]).toBe('http://127.0.0.1:8000/api/exercises');
  });
});
