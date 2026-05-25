'use strict';

/**
 * W-C1-A — Tests for browser-side SDK (server/sdk/ws-signing.js)
 *
 * Cross-compat verify: SDK 簽章 (Web Crypto API) 必須與 server 端 verify 相容
 *   (server/ws_signing.js Node.js HMAC-SHA256, #35 closed baseline)
 *
 * 執行: node --test server/__tests__/ws_signing_sdk.test.js
 *
 * 依賴: Node.js 19+ (globalThis.crypto.subtle Web Crypto API 內建)
 */

const { test } = require('node:test');
const assert   = require('node:assert/strict');
const fs       = require('node:fs');
const path     = require('node:path');
const crypto   = require('node:crypto');

// 載入 SDK (browser-side IIFE attaches to globalThis.WsSigning)
const SDK_PATH = path.resolve(__dirname, '..', 'sdk', 'ws-signing.js');
const SDK_CODE = fs.readFileSync(SDK_PATH, 'utf8');
// eslint-disable-next-line no-eval
eval(SDK_CODE);  // attaches WsSigning to globalThis (because no `window` in Node)

const WsSigning = globalThis.WsSigning;
const { signMessage, verifyMessage } = require('../ws_signing');  // server-side (#35 closed)

// ── Unit tests ───────────────────────────────────────────────────────────────

test('sdk__attached_to_global', () => {
  assert.ok(WsSigning, 'WsSigning attached to global');
  assert.equal(typeof WsSigning.sign, 'function');
  assert.equal(typeof WsSigning.verify, 'function');
  assert.equal(typeof WsSigning.generateNonce, 'function');
  assert.equal(typeof WsSigning.generateTimestamp, 'function');
});

test('sdk__generateNonce__hex_32_chars', () => {
  const n = WsSigning.generateNonce();
  assert.equal(typeof n, 'string');
  assert.equal(n.length, 32, '16-byte hex = 32 chars');
  assert.match(n, /^[0-9a-f]{32}$/);
  // Uniqueness
  const m = WsSigning.generateNonce();
  assert.notEqual(n, m, 'two consecutive calls produce different nonces');
});

test('sdk__generateTimestamp__close_to_Date_now', () => {
  const ts = WsSigning.generateTimestamp();
  assert.equal(typeof ts, 'number');
  const diff = Math.abs(ts - Date.now());
  assert.ok(diff < 100, `timestamp diff ${diff}ms < 100ms`);
});

test('sdk__sign__missing_secret_throws', async () => {
  await assert.rejects(
    async () => await WsSigning.sign({ type: 'delta' }, 'nonce', 1, null),
    /hmac_secret_missing/,
  );
});

test('sdk__sign_verify__roundtrip', async () => {
  const secret = crypto.randomBytes(32).toString('hex');
  const payload = { type: 'delta', table: 'beds', record: { _id: 'X1' }, src: 'test' };
  const nonce = WsSigning.generateNonce();
  const timestamp = WsSigning.generateTimestamp();
  const hmac = await WsSigning.sign(payload, nonce, timestamp, secret);

  // SDK self-verify
  const msg = Object.assign({}, payload, { nonce, timestamp, hmac });
  const ok = await WsSigning.verify(msg, secret);
  assert.equal(ok, true, 'SDK sign + SDK verify roundtrip');
});

test('sdk__verify__tampered_payload__false', async () => {
  const secret = crypto.randomBytes(32).toString('hex');
  const payload = { type: 'delta', table: 'beds', record: { _id: 'X1' } };
  const nonce = WsSigning.generateNonce();
  const timestamp = WsSigning.generateTimestamp();
  const hmac = await WsSigning.sign(payload, nonce, timestamp, secret);

  const tampered = Object.assign({}, payload, { nonce, timestamp, hmac });
  tampered.record._id = 'TAMPERED';  // 篡改

  const ok = await WsSigning.verify(tampered, secret);
  assert.equal(ok, false, 'SDK verify rejects tampered payload');
});

// ── ⭐ Cross-compat: SDK sign → server verify (#35 closed baseline) ──────────
//   這是 Stage 8 Gate B drill 的 critical verify: PWA 簽章 → Pi server #35 接收 verify
//   不破 = end-to-end HMAC 對齊 (cross-task tight coupling success)

test('sdk_to_server_xcompat__valid_sign__server_accepts', async () => {
  const secret = crypto.randomBytes(32).toString('hex');
  const payload = { type: 'delta', table: 'beds', record: { _id: 'X1' }, src: 'test', ts: '2026-05-04T00:00:00Z' };
  const nonce = WsSigning.generateNonce();
  const timestamp = WsSigning.generateTimestamp();

  // SDK sign (browser-side, Web Crypto API)
  const hmac = await WsSigning.sign(payload, nonce, timestamp, secret);

  // Server verify (#35 closed, Node.js crypto)
  const msg = Object.assign({}, payload, { nonce, timestamp, hmac });
  const res = verifyMessage(secret, msg);
  assert.equal(res.ok, true, 'cross-compat: SDK sign → server #35 verify ✅');
});

test('sdk_to_server_xcompat__server_canonical_matches_sdk_canonical', () => {
  // 對齊 server _canonicalize: msg minus {hmac, type}, sorted keys
  // SDK _canonicalize 應產生 identical canonical string
  const msg = {
    type: 'delta', table: 'beds', src: 'test',
    record: { _id: 'X', name: 'Y' },
    nonce: 'abc123', timestamp: 1700000000000,
    hmac: 'should-be-stripped',
  };
  const sdkCanonical = WsSigning._canonicalize(msg);
  // Server's canonicalize is private — replicate manually for cross-check
  const keys = Object.keys(msg).filter(k => k !== 'hmac' && k !== 'type').sort();
  const sorted = {};
  for (const k of keys) sorted[k] = msg[k];
  const expected = JSON.stringify(sorted);
  assert.equal(sdkCanonical, expected, 'SDK canonical 對齊 server canonical (sort+strip hmac/type)');
});

test('sdk_to_server_xcompat__tampered_signature__server_rejects', async () => {
  const secret = crypto.randomBytes(32).toString('hex');
  const payload = { type: 'delta', table: 'beds', record: { _id: 'X1' }, src: 'test' };
  const nonce = WsSigning.generateNonce();
  const timestamp = WsSigning.generateTimestamp();
  const hmac = await WsSigning.sign(payload, nonce, timestamp, secret);

  const tampered = Object.assign({}, payload, { nonce, timestamp, hmac });
  tampered.record._id = 'TAMPERED';

  const res = verifyMessage(secret, tampered);
  assert.equal(res.ok, false);
  assert.equal(res.reason, 'signature_invalid', 'cross-compat: SDK tampered → server detects');
});
