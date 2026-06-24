/**
 * dom_xss_escape.test.js — #293 DOM-XSS sink 跳脫守門
 *
 * 背景：2026-06-24 DOM-XSS sink 稽核發現 decisions / events / 帳號管理（auth）多處把
 * 使用者/TAK 撰寫的自由文字（裁示標題、事件描述、處置紀錄、display_name 等）未跳脫直塞
 * innerHTML。本測試沿 #292 roster `_esc` 慣例，鎖三檔的跳脫 helper 行為，並對 decisions.js
 * 純函式 renderDecisionList 做端到端 wiring 驗證（payload 進 → 跳脫後 HTML 出）。
 *
 * map.js `_escapeHtml` 為既有且廣用之 helper（本次僅新增呼叫點），且 map.js 為重量級單體
 * （靜態 import maplibre）不宜在單元測試載入，其 sink 由 diff review 覆蓋，不在此測。
 */
import { describe, expect, test, vi } from 'vitest';

// decisions.js / events.js 只靜態 import ./ws.js → mock 掉即可乾淨載入。
vi.mock('../../static/js/ws.js', () => ({
  authFetch: vi.fn(),
  canCreateEvents: () => true,
  canUseRealModeControls: () => true,
}));

// auth.js 無 top-level import，但 module 頂層會跑 `const el = id => document.getElementById(id)`、
// `const API_BASE = location.origin`、`document.addEventListener('keydown', ...)` → 補最小 globals。
globalThis.location = globalThis.location || { origin: 'http://127.0.0.1:8000' };
globalThis.document = globalThis.document || {
  getElementById: () => ({ style: {}, dataset: {}, innerHTML: '', textContent: '' }),
  addEventListener: () => {},
};
// events.js 頂層 `let _expandedSection = sessionStorage.getItem(...)` → 補最小 sessionStorage。
globalThis.sessionStorage = globalThis.sessionStorage || {
  getItem: () => null,
  setItem: () => {},
  removeItem: () => {},
};

// 共用攻擊 payload：屬性突破 + 事件處理器 + script 標籤。
const ATTACK = 'x"><img src=x onerror=alert(1)>';

// 對單一 escape helper 斷言：危險字元全跳脫、無原始 < > " ' &(未跳脫)、null/undefined → ''。
function assertEscaper(esc) {
  expect(esc('<script>')).toBe('&lt;script&gt;');
  expect(esc('a&b')).toBe('a&amp;b');
  expect(esc('a"b')).toBe('a&quot;b');
  expect(esc("a'b")).toBe('a&#39;b');
  // 屬性脈絡突破 payload：引號與角括號都必須跳脫，攻擊字串不得原樣殘留。
  const out = esc(ATTACK);
  expect(out).not.toContain('"');
  expect(out).not.toContain('<');
  expect(out).not.toContain('>');
  expect(out).toContain('&lt;img');
  expect(esc(null)).toBe('');
  expect(esc(undefined)).toBe('');
}

describe('#293 DOM-XSS escape helpers', () => {
  test('decisions.js _esc 完整跳脫', async () => {
    const m = await import('../../static/js/decisions.js');
    assertEscaper(m._esc);
  });

  test('events.js _esc 完整跳脫', async () => {
    const m = await import('../../static/js/events.js');
    assertEscaper(m._esc);
  });

  test('auth.js _escAudit 完整跳脫', async () => {
    const m = await import('../../static/js/auth.js');
    assertEscaper(m._escAudit);
  });
});

describe('#293 wiring：renderDecisionList 跳脫使用者欄位', () => {
  test('惡意 decision_title / decision_type 不得原樣進 innerHTML 字串', async () => {
    const m = await import('../../static/js/decisions.js');
    const html = m.renderDecisionList([
      { id: 1, severity: 'critical', status: 'pending', created_at: '2026-06-24T00:00:00Z',
        decision_title: ATTACK, decision_type: '<b>x</b>' },
    ]);
    // 原始 payload 不得殘留；跳脫形式須存在。
    expect(html).not.toContain('<img src=x onerror=');
    expect(html).not.toContain('<b>x</b>');
    expect(html).toContain('&lt;img');
    expect(html).toContain('&lt;b&gt;x&lt;/b&gt;');
  });

  test('showDecisionModal 跳脫 modal 內所有使用者欄位（含 severity/decision_type fallback）', async () => {
    const m = await import('../../static/js/decisions.js');
    const bodyNode = { innerHTML: '' };
    const titleNode = { textContent: '' };
    const overlayNode = { className: '' };
    const nodes = { 'modal-body': bodyNode, 'modal-title': titleNode, overlay: overlayNode };
    // showDecisionModal 於 call 時讀 globalThis.document；本測試覆寫成可讀回 innerHTML 的節點。
    globalThis.document = {
      getElementById: (id) => nodes[id] || { innerHTML: '', textContent: '', className: '' },
      addEventListener: () => {},
    };
    m.initDecisions({
      getData: () => ({
        decisions: {
          pending: [{
            id: 1, status: 'pending', created_at: '2026-06-24T00:00:00Z',
            severity: ATTACK, decision_type: ATTACK,  // fallback（非 enum）路徑
            decision_title: ATTACK, impact_description: ATTACK,
            suggested_action_a: ATTACK, suggested_action_b: ATTACK,
          }],
          decided: [],
        },
      }),
      getCurrentOperator: () => '',
      closeModal: () => {},
      doPoll: async () => {},
    });
    m.showDecisionModal(1);
    expect(bodyNode.innerHTML).not.toContain('<img src=x onerror=');
    expect(bodyNode.innerHTML).not.toContain('"><img');
    expect(bodyNode.innerHTML).toContain('&lt;img');
  });
});
