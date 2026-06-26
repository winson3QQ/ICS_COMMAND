// SPDX-License-Identifier: LicenseRef-Proprietary
// Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
'use strict';

/**
 * WS Full Security (C1-G) — HMAC sign/verify + nonce cache helpers
 *
 * 對齊 Step A Approval REV2 §1.1 / §1.2 frozen decisions:
 *   F-1   HMAC algorithm: HMAC-SHA256
 *   F-2   簽章方向: WS client → Pi server (incoming verification)
 *   F-3   HMAC key 來源: 既有 TI-01 HMAC_SECRET (config.js export)
 *   F-4   簽章覆蓋範圍: payload + nonce + timestamp (不簽 type / hmac envelope)
 *   F-6   Replay 窗口: 30 秒
 *   F-7   Nonce + timestamp 雙因子
 *   F-8   獨立 nonce cache in ws_handler.js (對齊 N-2: 不共用 TI-01)
 *   F-9   Nonce cache cleanup: 每 5 秒清過期 entries
 *
 * 不依賴新 npm package, 用 Node.js 內建 crypto (對齊 D-1)
 */

const crypto = require('crypto');

const NONCE_WINDOW_MS           = 30_000;  // 對齊 F-6 / N-1: 30 秒
const NONCE_CLEANUP_INTERVAL_MS = 5_000;   // 對齊 F-9: 每 5 秒

// ── 獨立 nonce cache (對齊 N-2: 不共用 TI-01 nonce_cache) ────────────────
//   Map<nonce_string, expiry_ms>
const _nonceCache = new Map();
let   _cleanupTimer = null;

function _cleanupExpiredNonces(nowMs = Date.now()) {
  for (const [nonce, expiry] of _nonceCache) {
    if (expiry <= nowMs) _nonceCache.delete(nonce);
  }
}

function startNonceCleanup() {
  if (_cleanupTimer) return;
  _cleanupTimer = setInterval(_cleanupExpiredNonces, NONCE_CLEANUP_INTERVAL_MS);
  // unref 不擋 process exit (test friendly)
  if (typeof _cleanupTimer.unref === 'function') _cleanupTimer.unref();
}

function stopNonceCleanup() {
  if (_cleanupTimer) {
    clearInterval(_cleanupTimer);
    _cleanupTimer = null;
  }
}

/**
 * Canonical string for HMAC (對齊 F-4)
 *
 * 範圍: msg 中 {hmac, type} 以外的所有欄位, 按 key 排序後 JSON.stringify
 *   - 不簽 type   (W-1 type whitelist 是另一層防護, 不依賴 HMAC, 避免 false positive)
 *   - 不簽 hmac   (self-referential)
 *   - 簽 nonce + timestamp + 其他 payload 欄位
 */
function _canonicalize(msg) {
  if (!msg || typeof msg !== 'object') return '';
  const keys = Object.keys(msg).filter(k => k !== 'hmac' && k !== 'type').sort();
  const sorted = {};
  for (const k of keys) sorted[k] = msg[k];
  return JSON.stringify(sorted);
}

/**
 * 計算 HMAC-SHA256 (對齊 F-1)
 *
 * 提供給 test client 與 W-C1-A reference 使用; server 端用 verifyMessage 驗證
 */
function signMessage(secret, msg) {
  if (!secret) throw new Error('hmac_secret_missing');
  const canonical = _canonicalize(msg);
  return crypto.createHmac('sha256', secret).update(canonical).digest('hex');
}

/**
 * 驗證 timestamp 在 ±NONCE_WINDOW_MS 窗口內 (對齊 F-6)
 */
function isWithinTimeWindow(timestampMs, nowMs = Date.now()) {
  const ts = Number(timestampMs);
  if (!Number.isFinite(ts)) return false;
  return Math.abs(nowMs - ts) <= NONCE_WINDOW_MS;
}

/**
 * Check & store nonce (對齊 F-7 + N-3 雙因子)
 *
 * @returns {boolean} true 若 nonce 為新 (可接受); false 若已用過 (replay)
 */
function checkAndStoreNonce(nonce, nowMs = Date.now()) {
  if (typeof nonce !== 'string' || nonce.length === 0) return false;
  if (_nonceCache.has(nonce)) return false;
  _nonceCache.set(nonce, nowMs + NONCE_WINDOW_MS);
  return true;
}

/**
 * 完整 verify: HMAC + timestamp + nonce
 *
 * @returns {{ok: true} | {ok: false, reason: string}}
 *   reason ∈ {
 *     hmac_secret_missing,         // server 端未設定 HMAC_SECRET (~/.ics/hmac_secret)
 *     missing_signature_fields,    // msg 缺 hmac / nonce / timestamp 任一
 *     timestamp_out_of_window,     // 對齊 F-6: 超 ±30 秒
 *     invalid_hmac_format,         // hmac hex 解析失敗
 *     signature_invalid,           // HMAC 不符 (對齊 F-10 篡改 payload)
 *     nonce_replay,                // 對齊 F-10 重放 nonce
 *   }
 */
function verifyMessage(secret, msg, nowMs = Date.now()) {
  if (!secret) return { ok: false, reason: 'hmac_secret_missing' };
  if (!msg || typeof msg !== 'object') return { ok: false, reason: 'missing_signature_fields' };

  const { hmac, nonce, timestamp } = msg;
  if (typeof hmac !== 'string' || typeof nonce !== 'string' ||
      (typeof timestamp !== 'number' && typeof timestamp !== 'string')) {
    return { ok: false, reason: 'missing_signature_fields' };
  }

  if (!isWithinTimeWindow(timestamp, nowMs)) {
    return { ok: false, reason: 'timestamp_out_of_window' };
  }

  const expected = signMessage(secret, msg);
  let hmacBuf, expBuf;
  try {
    hmacBuf = Buffer.from(hmac, 'hex');
    expBuf  = Buffer.from(expected, 'hex');
  } catch {
    return { ok: false, reason: 'invalid_hmac_format' };
  }
  // constant-time compare (對齊 timing attack 防護)
  if (hmacBuf.length !== expBuf.length || !crypto.timingSafeEqual(hmacBuf, expBuf)) {
    return { ok: false, reason: 'signature_invalid' };
  }

  // Nonce check (擺在 HMAC 通過後, 避免 invalid msg 寫進 cache 浪費空間)
  if (!checkAndStoreNonce(nonce, nowMs)) {
    return { ok: false, reason: 'nonce_replay' };
  }

  return { ok: true };
}

module.exports = {
  NONCE_WINDOW_MS,
  signMessage,
  verifyMessage,
  isWithinTimeWindow,
  checkAndStoreNonce,
  startNonceCleanup,
  stopNonceCleanup,
  // for tests (private API, prefixed _)
  _nonceCacheSize:  () => _nonceCache.size,
  _clearNonceCache: () => _nonceCache.clear(),
};
