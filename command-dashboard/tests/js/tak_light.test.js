/**
 * tests/js/tak_light.test.js — P2-24（#164）前端尾：TAK 連線燈狀態機。
 *
 * 鎖住的核心不變式（消除「沒 task 在跑卻顯示斷線重連中」謊報）：
 * - 未啟用             → 灰(lkp)
 * - 啟用·連線參數未備妥  → 黃(warn)（部署層，非 admin 在 UI 修）
 * - 啟用·已備妥·task 沒起 → 紅(crit)「啟動失敗」（不可顯示「重連中」——根本沒 task 在重連）
 * - 啟用·task 在跑·未連上 → 紅(crit)「斷線（背景重連中）」（這時才是真重連）
 * - 啟用·連上·無串流(>120s) → 黃(warn)
 * - 啟用·連上·近期有 CoT → 綠(ok)
 */
import { describe, expect, test } from 'vitest';

import { takConnState, takLightState } from '../../static/js/tak_light_state.js';

describe('takConnState (header 燈與管理面板共用的單一分類器)', () => {
  test('未啟用 → disabled', () => {
    expect(takConnState({ enabled: false })).toBe('disabled');
  });
  test('啟用·未備妥 → unconfigured', () => {
    expect(takConnState({ enabled: true, configured: false })).toBe('unconfigured');
  });
  test('啟用·已備妥·未跑 → failed', () => {
    expect(takConnState({ enabled: true, configured: true, running: false })).toBe('failed');
  });
  test('啟用·在跑·未連上 → disconnected', () => {
    expect(takConnState({ enabled: true, configured: true, running: true, connected: false })).toBe('disconnected');
  });
  test('啟用·連上·無串流 → stale', () => {
    expect(takConnState({ enabled: true, configured: true, running: true, connected: true, last_cot_age_s: 300 })).toBe('stale');
  });
  test('啟用·連上·近期有 CoT → ok', () => {
    expect(takConnState({ enabled: true, configured: true, running: true, connected: true, last_cot_age_s: 5 })).toBe('ok');
  });
});

describe('takLightState', () => {
  test('未啟用 → 灰', () => {
    expect(takLightState({ enabled: false }).level).toBe('lkp');
  });

  test('啟用但連線參數未備妥 → 黃（不是紅）', () => {
    const r = takLightState({ enabled: true, configured: false });
    expect(r.level).toBe('warn');
    expect(r.title).toContain('未備妥');
  });

  test('啟用·已備妥·task 沒起 → 紅「啟動失敗」，非「重連中」', () => {
    const r = takLightState({ enabled: true, configured: true, running: false, connected: false });
    expect(r.level).toBe('crit');
    expect(r.title).toContain('啟動失敗');
    expect(r.title).not.toContain('重連');   // 關鍵：沒 task 在跑時不得謊報重連
  });

  test('啟用·task 在跑·未連上 → 紅「斷線（背景重連中）」', () => {
    const r = takLightState({ enabled: true, configured: true, running: true, connected: false });
    expect(r.level).toBe('crit');
    expect(r.title).toContain('重連中');
  });

  test('啟用·連上·無串流(>120s) → 黃', () => {
    const r = takLightState({ enabled: true, configured: true, running: true, connected: true, last_cot_age_s: 300 });
    expect(r.level).toBe('warn');
    expect(r.title).toContain('無串流');
  });

  test('啟用·連上·尚未收到 CoT → 黃', () => {
    const r = takLightState({ enabled: true, configured: true, running: true, connected: true, last_cot_age_s: null });
    expect(r.level).toBe('warn');
  });

  test('啟用·連上·近期有 CoT → 綠', () => {
    const r = takLightState({ enabled: true, configured: true, running: true, connected: true, last_cot_age_s: 10 });
    expect(r.level).toBe('ok');
  });

  test('舊欄位缺 configured/running（後端未升級）時不誤判：connected 即綠/黃', () => {
    // 向後相容：舊 status 無 configured/running → 不應落入 warn/crit 兩新分支
    const r = takLightState({ enabled: true, connected: true, last_cot_age_s: 10 });
    expect(r.level).toBe('ok');
  });
});
