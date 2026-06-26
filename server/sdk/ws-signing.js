// SPDX-License-Identifier: LicenseRef-Proprietary
// Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
/**
 * W-C1-A — Browser-side WS HMAC signing SDK
 *
 * Pi server static-served at /lib/ws-signing.js (Option δ, Step A Amendment v2)
 * Loaded by shelter-pwa + medical-pwa via <script src="/lib/ws-signing.js">
 *
 * 對齊 Step A Approval frozen decisions:
 *   F-1 (H-1)  HMAC-SHA256
 *   F-2 (H-2)  簽章方向: WS client (PWA) → Pi server (incoming verify)
 *   F-4        簽章覆蓋: payload + nonce + timestamp (不簽 type / hmac envelope)
 *   F-15 (N-1) Nonce 16-byte random (Web Crypto API: crypto.getRandomValues)
 *   F-16 (N-2) timestamp Unix ms (Date.now())
 *   F-21       PWA 從 auth_result.hmac_secret 取得 master secret (Sync v5)
 *
 * Canonical 對齊 server/ws_signing.js (Node.js):
 *   JSON.stringify(msg with sorted keys, removing {hmac, type})
 *
 * 對齊紅線 D-1 / D-2: 不引入新 npm package, 用 browser 內建 Web Crypto API + TextEncoder
 *
 * 使用方式:
 *   const nonce = window.WsSigning.generateNonce()
 *   const timestamp = window.WsSigning.generateTimestamp()
 *   const payload = { type: 'delta', table: 'beds', record: {...}, ... }
 *   const hmac = await window.WsSigning.sign(payload, nonce, timestamp, hmac_secret)
 *   ws.send(JSON.stringify({ ...payload, nonce, timestamp, hmac }))
 */

(function (global) {
  'use strict';

  // ── Canonical (對齊 server/ws_signing.js _canonicalize) ─────────────────
  function _canonicalize(msg) {
    if (!msg || typeof msg !== 'object') return '';
    const keys = Object.keys(msg).filter(function (k) {
      return k !== 'hmac' && k !== 'type';
    }).sort();
    const sorted = {};
    for (let i = 0; i < keys.length; i++) sorted[keys[i]] = msg[keys[i]];
    return JSON.stringify(sorted);
  }

  // ── Hex helpers ─────────────────────────────────────────────────────────
  function _bytesToHex(bytes) {
    const arr = new Array(bytes.length);
    for (let i = 0; i < bytes.length; i++) {
      arr[i] = bytes[i].toString(16).padStart(2, '0');
    }
    return arr.join('');
  }

  // ── 計算 HMAC-SHA256 (對齊 F-1) ────────────────────────────────────────
  async function _computeHmac(canonical, hmac_secret) {
    const enc = new TextEncoder();
    const key = await crypto.subtle.importKey(
      'raw',
      enc.encode(hmac_secret),
      { name: 'HMAC', hash: 'SHA-256' },
      false,
      ['sign']
    );
    const sigBuf = await crypto.subtle.sign('HMAC', key, enc.encode(canonical));
    return _bytesToHex(new Uint8Array(sigBuf));
  }

  // ── Public API ─────────────────────────────────────────────────────────

  /**
   * 對 payload + nonce + timestamp 算 HMAC, 回傳 hex hmac
   *
   * @param {Object} payload  含 type 與其他訊息 fields
   * @param {string} nonce    hex string (generateNonce 產生)
   * @param {number} timestamp Unix ms
   * @param {string} hmac_secret  hex 64 chars (從 auth_result.hmac_secret 取得)
   * @returns {Promise<string>} hex hmac
   */
  async function sign(payload, nonce, timestamp, hmac_secret) {
    if (!hmac_secret) throw new Error('hmac_secret_missing');
    const combined = Object.assign({}, payload, { nonce: nonce, timestamp: timestamp });
    const canonical = _canonicalize(combined);
    return await _computeHmac(canonical, hmac_secret);
  }

  /**
   * Verify message HMAC (provided for symmetry, server is canonical verifier)
   *
   * @param {Object} message 含 hmac / nonce / timestamp / type / ...
   * @param {string} hmac_secret
   * @returns {Promise<boolean>} true 若 HMAC 對齊
   */
  async function verify(message, hmac_secret) {
    if (!hmac_secret) return false;
    if (!message || typeof message !== 'object') return false;
    const hmac = message.hmac;
    if (typeof hmac !== 'string') return false;
    const canonical = _canonicalize(message);
    const expected = await _computeHmac(canonical, hmac_secret);
    // Constant-time compare (Web Crypto 無 timingSafeEqual; bitwise XOR loop 等效)
    if (hmac.length !== expected.length) return false;
    let diff = 0;
    for (let i = 0; i < hmac.length; i++) {
      diff |= hmac.charCodeAt(i) ^ expected.charCodeAt(i);
    }
    return diff === 0;
  }

  /**
   * 產生 16-byte random nonce, 對齊 F-7 / N-1
   * @returns {string} hex 32 chars
   */
  function generateNonce() {
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    return _bytesToHex(bytes);
  }

  /**
   * 產生 Unix ms timestamp, 對齊 F-7 / N-2
   * @returns {number}
   */
  function generateTimestamp() {
    return Date.now();
  }

  // ── Attach to global namespace (window in browser, globalThis in tests) ──
  global.WsSigning = {
    sign: sign,
    verify: verify,
    generateNonce: generateNonce,
    generateTimestamp: generateTimestamp,
    // for tests/debug — private API prefixed _
    _canonicalize: _canonicalize,
  };
})(typeof window !== 'undefined' ? window : globalThis);
