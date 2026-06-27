// SPDX-License-Identifier: LicenseRef-Proprietary
// Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
'use strict';
/**
 * ICS_DMAS Pi server 結構化日誌 — C1-D / v1.1
 * NODE_ENV=production → INFO+，mask PII，不輸出 stack 路徑 → /var/log/ics/pi.log
 * NODE_ENV=development（預設）→ DEBUG+ → /var/log/ics/pi.log
 * 寫入失敗 → stderr fallback，每 60s 最多警告一次（Q11）
 *
 * 向後相容：仍匯出 { log } 物件供現有程式碼使用
 * 新 C1-D 程式碼使用 const logger = require('./logger')
 */
const fs   = require('fs');
const path = require('path');

const IS_PROD   = process.env.NODE_ENV === 'production';
const LOG_DIR   = '/var/log/ics';
const LOG_FILE  = path.join(LOG_DIR, 'pi.log');
const COMPONENT = 'pi';
const VERSION   = process.env.APP_VERSION || 'v2.1.0';

// 自建 /var/log/ics/（D-6）
try { fs.mkdirSync(LOG_DIR, { recursive: true }); } catch (_) {}

// File stream + stderr fallback（Q11）
let _stream = null;
let _fallbackWarnedAt = 0;

function _getStream() {
  if (_stream) return _stream;
  try {
    _stream = fs.createWriteStream(LOG_FILE, { flags: 'a', encoding: 'utf8' });
    _stream.on('error', err => { _stream = null; _warnFallback(err.message); });
    return _stream;
  } catch (err) {
    _warnFallback(err.message);
    return process.stderr;
  }
}

function _warnFallback(msg) {
  const now = Date.now();
  if (now - _fallbackWarnedAt >= 60_000) {
    process.stderr.write(
      `[ICS-LOG-FALLBACK] log file unavailable, falling back to stderr: ${msg}\n`
    );
    _fallbackWarnedAt = now;
  }
}

// PROD PII mask（Q9, v1.1 §2.2）
function _maskUser(v) {
  if (!IS_PROD || !v) return v || '';
  const s = String(v);
  return s.length > 2 ? s.slice(0, 2) + '**' : '**';
}
function _maskIp(v) {
  if (!IS_PROD || !v) return v || '';
  const parts = String(v).split('.');
  return parts.length === 4 ? parts.slice(0, 3).join('.') + '.x' : v;
}
function _sanitizeDetail(detail) {
  if (!detail || typeof detail !== 'object') return detail;
  const out = { ...detail };
  if (out.ip) out.ip = _maskIp(out.ip);
  if (IS_PROD && out.stack) delete out.stack;   // PROD 不輸出 stack 路徑
  return out;
}

const LEVEL_NUM = { DEBUG: 10, INFO: 20, WARNING: 30, ERROR: 40, CRITICAL: 50 };
const THRESHOLD = IS_PROD ? LEVEL_NUM.INFO : LEVEL_NUM.DEBUG;

/**
 * 核心 log 函式（v1.1 JSON schema）
 * @param {string} level  DEBUG|INFO|WARNING|ERROR|CRITICAL
 * @param {string} event  snake_case 事件名稱
 * @param {object} extra  { correlation_id, user, session_id, msg, detail }
 */
function _log(level, event, extra = {}) {
  if ((LEVEL_NUM[level] ?? 0) < THRESHOLD) return;

  const entry = {
    ts:             new Date().toISOString(),        // UTC，ms 精度（Q8）
    level,
    component:      COMPONENT,
    version:        VERSION,
    event,
    correlation_id: extra.correlation_id ?? '',      // 完整 UUID v4（Q3/Q10）
    user:           _maskUser(extra.user ?? ''),
    session_id:     extra.session_id ?? '',
    msg:            extra.msg ?? '',
    detail:         _sanitizeDetail(extra.detail ?? {}),
  };

  const line   = JSON.stringify(entry) + '\n';
  const stream = _getStream();
  try { stream.write(line); } catch (_) { process.stderr.write(line); }
}

// ── 新 C1-D 結構化 API（const logger = require('./logger')）──────
const logger = {
  debug:    (event, extra) => _log('DEBUG',    event, extra),
  info:     (event, extra) => _log('INFO',     event, extra),
  warn:     (event, extra) => _log('WARNING',  event, extra),
  warning:  (event, extra) => _log('WARNING',  event, extra),
  error:    (event, extra) => _log('ERROR',    event, extra),
  critical: (event, extra) => _log('CRITICAL', event, extra),
};

// ── 向後相容：舊式 log.warn(...) / log.info(...) API ────────────
// 既有程式碼：const { log } = require('./logger')
// 接受可變參數，將第一個字串視為 msg 輸出
const log = {
  error: (...a) => _log('ERROR',   'legacy_log', { msg: a.join(' ') }),
  warn:  (...a) => _log('WARNING', 'legacy_log', { msg: a.join(' ') }),
  info:  (...a) => _log('INFO',    'legacy_log', { msg: a.join(' ') }),
  debug: (...a) => _log('DEBUG',   'legacy_log', { msg: a.join(' ') }),
};

module.exports = { log, ...logger };
