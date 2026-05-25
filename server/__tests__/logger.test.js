'use strict';

/**
 * C1-D — server/logger.js 單元測試
 *
 * AC-3  : logger.info/warn/error 輸出合法 JSON Lines，含必要欄位
 *         （ts, level, component, version, event, msg）
 * AC-4  : NODE_ENV=production → PII 遮罩（user[:2]+"**"，detail.ip → /24）
 * AC-14 : 向後相容 log.warn(...) 輸出 event="legacy_log"
 *
 * 執行：node --test server/__tests__/logger.test.js
 */

const { test, before, after } = require('node:test');
const assert   = require('node:assert/strict');
const fs       = require('node:fs');
const path     = require('node:path');
const { Writable } = require('node:stream');

const LOGGER_PATH = path.resolve(__dirname, '../logger.js');

// ─── 工具函式 ────────────────────────────────────────────────────────────────

/** 可被測試捕捉的 Writable stream */
function createCaptureStream() {
  const chunks = [];
  const stream = new Writable({
    write(chunk, _enc, cb) { chunks.push(chunk.toString()); cb(); },
  });
  /** 將所有 chunk 拆成行並解析成 JSON，過濾失敗行 */
  stream.getJsonLines = () =>
    chunks
      .join('')
      .split('\n')
      .filter(l => l.trim())
      .map(l => { try { return JSON.parse(l); } catch { return null; } })
      .filter(Boolean);
  return stream;
}

/** 清除 logger.js 的 require cache，使下次 require 重新執行模組頂層程式碼 */
function clearLoggerCache() {
  delete require.cache[LOGGER_PATH];
}

/**
 * 設定環境、monkeypatch fs.createWriteStream 指向捕捉 stream，
 * require 一份乾淨的 logger，執行 fn，然後完整還原。
 *
 * 傳入的 fn(logger, cap) 中呼叫的任何 logger method 都會寫入 cap。
 */
function withCapturedLogger(nodeEnv, fn) {
  const cap        = createCaptureStream();
  const origEnv    = process.env.NODE_ENV;
  const origCWS    = fs.createWriteStream;

  process.env.NODE_ENV = nodeEnv;
  // 攔截 createWriteStream：pi.log → 導向 cap；其他路徑保持原始行為
  fs.createWriteStream = (p, opts) => {
    if (String(p).endsWith('pi.log')) return cap;
    return origCWS(p, opts);
  };

  try {
    clearLoggerCache();
    const logger = require(LOGGER_PATH);
    fn(logger, cap);
    return cap;
  } finally {
    fs.createWriteStream = origCWS;
    process.env.NODE_ENV = origEnv;
    clearLoggerCache();  // 確保後續測試拿到乾淨 module
  }
}

// ─── 測試 ────────────────────────────────────────────────────────────────────

test('AC-3: logger.info 輸出合法 JSON Lines，含必要欄位', () => {
  const cap = withCapturedLogger('development', (logger) => {
    logger.info('ac3_test_event', {
      msg:    'AC-3 驗證',
      detail: { key: 'value' },
    });
  });

  const records = cap.getJsonLines();
  assert.ok(records.length >= 1, '應至少輸出一條 JSON 記錄');

  const rec = records.find(r => r.event === 'ac3_test_event');
  assert.ok(rec,            'event=ac3_test_event 的記錄未找到');
  assert.ok(rec.ts,         'ts 欄位缺失');
  assert.ok(rec.level,      'level 欄位缺失');
  assert.ok(rec.component,  'component 欄位缺失');
  assert.ok(rec.version,    'version 欄位缺失');
  assert.strictEqual(rec.event, 'ac3_test_event');
  assert.strictEqual(rec.msg,   'AC-3 驗證');
  assert.deepStrictEqual(rec.detail, { key: 'value' });
});

test('AC-4: NODE_ENV=production → PII 遮罩（user / detail.ip）', () => {
  const cap = withCapturedLogger('production', (logger) => {
    logger.info('ac4_pii_test', {
      user:   'admin_user',
      detail: { ip: '192.168.1.55', unit_id: 'shelter' },
      msg:    'AC-4 PII 遮罩驗證',
    });
    // 短 user（≤ 2 char）→ "**"
    logger.info('ac4_short_user', {
      user: 'ab',
      msg:  'short user',
    });
  });

  const records = cap.getJsonLines();

  // ── 一般 user 遮罩 ──
  const rec = records.find(r => r.event === 'ac4_pii_test');
  assert.ok(rec, 'ac4_pii_test 記錄未找到');
  assert.strictEqual(rec.user,       'ad**',          'user 應遮罩為 ad**');
  assert.strictEqual(rec.detail?.ip, '192.168.1.x',   'detail.ip 應遮罩為 /24');
  // 非 IP 欄位不應遮罩
  assert.strictEqual(rec.detail?.unit_id, 'shelter',  'unit_id 不應被遮罩');

  // ── 短 user 遮罩 ──
  const short = records.find(r => r.event === 'ac4_short_user');
  assert.ok(short, 'ac4_short_user 記錄未找到');
  assert.strictEqual(short.user, '**', '長度 ≤ 2 的 user 應全遮罩為 **');
});

test('AC-14: 向後相容 log.warn() 輸出 event="legacy_log"', () => {
  const cap = withCapturedLogger('development', (logger) => {
    // 舊式呼叫：const { log } = require('./logger')
    const { log } = logger;
    log.warn('[PiPush] 傳統 API 測試', 'extra', 'arg');
    log.error('[DB] 錯誤訊息');
    log.info('[WS] 連線');
  });

  const records = cap.getJsonLines();

  const legacyRecords = records.filter(r => r.event === 'legacy_log');
  assert.ok(legacyRecords.length >= 3,
    `legacy_log 記錄數應 ≥ 3，實際：${legacyRecords.length}`);

  // 第一條應包含所有 join 後的字串
  const warn = legacyRecords.find(r => r.msg.includes('[PiPush]'));
  assert.ok(warn,                             'warn 記錄未找到');
  assert.ok(warn.msg.includes('extra'),       'msg 應包含 extra');
  assert.ok(warn.msg.includes('arg'),         'msg 應包含 arg');
  assert.strictEqual(warn.level, 'WARNING',   'level 應為 WARNING');
});
