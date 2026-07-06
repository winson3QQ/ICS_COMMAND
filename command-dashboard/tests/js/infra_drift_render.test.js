// SPDX-License-Identifier: LicenseRef-Proprietary
// Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
/**
 * infra_drift_render.test.js — #507 消費半身：auth.js _renderInfraDrift 面板渲染（純函式）
 *
 * auth.js 頂層有 `location.origin`（node 無）→ 測前 stub 全域 location/document，再動態 import
 * 取 _renderInfraDrift（其餘 addEventListener 皆在函式內、import 時不執行）。驗：
 *   空→空字串；ok 無鈕；missing/unregistered 標記 + 出「一鍵對帳」鈕；admin 空宣告顯 —；
 *   惡意 callsign 經 _escAudit 跳脫（CSP/XSS 安全）。
 */
import { describe, test, expect, beforeAll } from 'vitest';

let _renderInfraDrift;

beforeAll(async () => {
  const noop = () => {};
  globalThis.location = { origin: 'http://test' };
  globalThis.document = {
    getElementById: () => null,
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener: noop,
    createElement: () => ({ style: {}, addEventListener: noop, appendChild: noop, setAttribute: noop }),
    body: { appendChild: noop },
  };
  globalThis.window = { addEventListener: noop, location: globalThis.location };
  globalThis.addEventListener = noop;
  globalThis.localStorage = { getItem: () => null, setItem: noop, removeItem: noop };
  const mod = await import('../../static/js/auth.js');
  _renderInfraDrift = mod._renderInfraDrift;
});

describe('_renderInfraDrift（#507 消費半身）', () => {
  test('空 / null → 空字串', () => {
    expect(_renderInfraDrift([])).toBe('');
    expect(_renderInfraDrift(null)).toBe('');
  });

  test('全 ok → 有列、無「一鍵對帳」鈕', () => {
    const html = _renderInfraDrift([
      { callsign: 'ics-cot', status: 'ok', declared: ['blue', 'neutral', 'red'], actual: ['blue', 'neutral', 'red'] },
    ]);
    expect(html).toContain('ics-cot');
    expect(html).toContain('✓ 符合');
    expect(html).not.toContain('adm-reconcile-infra-groups'); // 無漂移不出鈕
  });

  test('missing → 缺群標記 + 出「一鍵對帳」鈕；actual 空陣列顯（無群）', () => {
    const html = _renderInfraDrift([
      { callsign: 'ics-marti-read', status: 'missing', declared: ['blue', 'neutral', 'red'], actual: [] },
    ]);
    expect(html).toContain('⚠ 缺群');
    expect(html).toContain('（無群）');
    expect(html).toContain('data-action="adm-reconcile-infra-groups"');
  });

  test('unregistered → actual=null 顯「未在名冊」+ 出鈕', () => {
    const html = _renderInfraDrift([
      { callsign: 'ics-marti-write', status: 'unregistered', declared: ['blue', 'red', 'neutral'], actual: null },
    ]);
    expect(html).toContain('⚠ 未註冊');
    expect(html).toContain('（未在名冊）');
    expect(html).toContain('adm-reconcile-infra-groups');
  });

  test('admin ok（宣告空群）→ 宣告顯 —、不出鈕', () => {
    const html = _renderInfraDrift([{ callsign: 'ics-tak-admin', status: 'ok', declared: [], actual: [] }]);
    expect(html).toContain('ics-tak-admin');
    expect(html).toContain('宣告 —');
    expect(html).not.toContain('adm-reconcile-infra-groups');
  });

  test('惡意 callsign 經 _escAudit 跳脫（不成 XSS）', () => {
    const html = _renderInfraDrift([
      { callsign: '<img src=x onerror=alert(1)>', status: 'missing', declared: ['blue'], actual: [] },
    ]);
    expect(html).not.toContain('<img src=x');
    expect(html).toContain('&lt;img src=x'); // 已跳脫
  });
});
