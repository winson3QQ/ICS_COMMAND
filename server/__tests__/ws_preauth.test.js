// SPDX-License-Identifier: LicenseRef-Proprietary
// Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
'use strict';

// Layer 2 — Integration tests for HOTFIX-WS-01 WebSocket Pre-auth Gate
// T-1..T-16：close codes, Layer 0/1 gate, B-FIX-01~04, session_restore Option A, audit events
// 執行：node --test server/__tests__/ws_preauth.test.js

const { test }           = require('node:test');
const assert             = require('node:assert/strict');
const { spawn, spawnSync } = require('node:child_process');
const WebSocket          = require('ws');
const fs                 = require('node:fs');
const path               = require('node:path');
const os                 = require('node:os');
const crypto             = require('node:crypto');
const Database           = require('better-sqlite3');
const { signMessage }    = require('../ws_signing');   // C1-G WS Full Security helper

const REPO_ROOT    = path.resolve(__dirname, '../..');
const ADMIN_PORT_A = 19768;
const WS_PORT_A    = 19767;
const ADMIN_PORT_B = 19770;
const WS_PORT_B    = 19769;
const ADMIN_PASS   = 'Str0ng@Pass1!';
const TEST_USER    = 'ws_test_user';
const TEST_PIN     = '4719';

// ── 共用工具 ────────────────────────────────────────────────────────────────

function makeTmpEnv(tag = '') {
  const id = crypto.randomBytes(6).toString('hex') + tag;
  // C1-G: 加 HMAC_SECRET 環境 (透過 HOME override 注入 ~/.ics/hmac_secret)
  const tmpHome = path.join(os.tmpdir(), `ics_ws_home_${id}`);
  fs.mkdirSync(path.join(tmpHome, '.ics'), { recursive: true });
  const secret = crypto.randomBytes(32).toString('hex');   // 64 hex chars 對齊 TI-01
  const keyId  = crypto.randomUUID();
  fs.writeFileSync(path.join(tmpHome, '.ics', 'hmac_secret'), secret);
  fs.writeFileSync(path.join(tmpHome, '.ics', 'hmac_key_id'),  keyId);
  return {
    tmpDb:    path.join(os.tmpdir(), `ics_ws_${id}.db`),
    tmpToken: path.join(os.tmpdir(), `ics_ws_tok_${id}`),
    tmpHome, secret, keyId,
  };
}

// C1-G: 對 msg (不含 hmac/nonce/timestamp) 加簽章; T-8/T-9b/T-14 用
function signedMsg(secret, msg) {
  const nonce     = crypto.randomBytes(16).toString('hex');
  const timestamp = Date.now();
  const withMeta  = { ...msg, nonce, timestamp };
  return { ...withMeta, hmac: signMessage(secret, withMeta) };
}

function spawnSrv(adminPort, wsPort, extraEnv) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, ['server/index.js', '--unit', 'shelter'], {
      cwd:   REPO_ROOT,
      env:   { ...process.env, ADMIN_PORT: String(adminPort), WS_PORT: String(wsPort), LOG_LEVEL: 'error', ...extraEnv },
      stdio: 'pipe',
    });
    let out = '', err = '';
    child.stdout.on('data', d => { out += d; });
    child.stderr.on('data', d => { err += d; });
    child.on('error', reject);
    resolve({ child, out: () => out, err: () => err });
  });
}

async function waitReady(base, ms = 10_000) {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    try {
      const r = await fetch(`${base}/admin/status`);
      if (r.status === 200 || r.status === 423) return;
    } catch { /* 等待 */ }
    await new Promise(r => setTimeout(r, 150));
  }
  throw new Error(`Server 未就緒 (${base})`);
}

function stopSrv(child) {
  return new Promise(resolve => {
    child.once('close', resolve);
    child.kill('SIGTERM');
    if (process.platform === 'win32') child.kill();
    setTimeout(() => { try { child.kill(); } catch { /* 已停 */ } }, 3000);
  });
}

function cleanup(...files) {
  for (const f of files)
    for (const ext of ['', '-wal', '-shm'])
      try { fs.rmSync(f + ext, { force: true, recursive: true }); } catch { /* 略 */ }
}

function wsConnect(port, ms = 6000) {
  return new Promise((resolve, reject) => {
    const t  = setTimeout(() => reject(new Error(`WS connect timeout (port ${port})`)), ms);
    const ws = new WebSocket(`ws://localhost:${port}?src=test`);
    ws.once('open',  () => { clearTimeout(t); resolve(ws); });
    ws.once('error', e  => { clearTimeout(t); reject(e); });
  });
}

// 等待下一則訊息，或 close 事件（先到先得）
function nextMsg(ws, ms = 4000) {
  return new Promise((resolve, reject) => {
    if (ws.readyState === WebSocket.CLOSED)
      return resolve({ _closed: true, code: 0 });
    const t       = setTimeout(() => { cleanup_(); reject(new Error('nextMsg timeout')); }, ms);
    const onMsg   = raw => { cleanup_(); resolve(JSON.parse(raw)); };
    const onClose = (code, reason) => { cleanup_(); resolve({ _closed: true, code, reason: reason?.toString() }); };
    const cleanup_ = () => { clearTimeout(t); ws.off('message', onMsg); ws.off('close', onClose); };
    ws.once('message', onMsg);
    ws.once('close', onClose);
  });
}

// 收集所有 message 直到 close 事件
function drainUntilClose(ws, ms = 4000) {
  return new Promise((resolve, reject) => {
    if (ws.readyState === WebSocket.CLOSED)
      return resolve({ messages: [], code: 0, reason: '' });
    const messages = [];
    const t = setTimeout(() => reject(new Error('drainUntilClose timeout')), ms);
    ws.on('message', raw => { try { messages.push(JSON.parse(raw)); } catch { /* 略 */ } });
    ws.once('close', (code, reason) => {
      clearTimeout(t);
      ws.removeAllListeners('message');
      resolve({ messages, code, reason: reason?.toString() || '' });
    });
  });
}

async function completeSetup(adminBase, tokenPath) {
  assert.ok(fs.existsSync(tokenPath), 'token 檔應存在');
  const token = fs.readFileSync(tokenPath, 'utf8').trim();
  const res = await fetch(`${adminBase}/admin/setup`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ first_run_token: token, admin_password: ADMIN_PASS }),
  });
  assert.equal(res.status, 200, `setup 應為 200，got ${res.status}`);
}

async function createAccount(adminBase, username, pin, role = '組長') {
  const res = await fetch(`${adminBase}/admin/accounts`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json', 'X-Admin-PIN': ADMIN_PASS },
    body:    JSON.stringify({ username, role, pin }),
  });
  assert.equal(res.status, 200, `createAccount 應為 200，got ${res.status}`);
}

// ─── T-1 ~ T-10, T-14, T-16：setup 完成的 server ──────────────────────────

test('ws_preauth__T01_to_T10_T14_T16__setup_complete', async () => {
  const { tmpDb, tmpToken, tmpHome, secret } = makeTmpEnv('A');
  const adminBase = `http://localhost:${ADMIN_PORT_A}`;
  let srv;
  try {
    srv = await spawnSrv(ADMIN_PORT_A, WS_PORT_A, { DB_PATH: tmpDb, FIRST_RUN_TOKEN_PATH: tmpToken, HOME: tmpHome });
    await waitReady(adminBase);
    await completeSetup(adminBase, tmpToken);
    await createAccount(adminBase, TEST_USER, TEST_PIN);

    // ── T-1：未認證 → delta → close 4401 ─────────────────────────────────
    {
      const ws = await wsConnect(WS_PORT_A);
      await nextMsg(ws); // skip welcome
      ws.send(JSON.stringify({ type: 'delta', table: 'beds', action: 'upsert',
        record: { _id: 'T1' }, ts: new Date().toISOString(), src: 'test' }));
      const { code, messages } = await drainUntilClose(ws);
      assert.equal(code, 4401, 'T-1: 未認證 delta → close 4401');
      assert.ok(messages.some(m => m.type === 'error'), 'T-1: 應先送 error 訊息');
    }

    // ── T-2：未認證 → sync_push → close 4401 ─────────────────────────────
    {
      const ws = await wsConnect(WS_PORT_A);
      await nextMsg(ws);
      ws.send(JSON.stringify({ type: 'sync_push', tables: {}, device_id: 'test' }));
      const { code } = await drainUntilClose(ws);
      assert.equal(code, 4401, 'T-2: 未認證 sync_push → close 4401');
    }

    // ── T-3（B-FIX-03）：未認證 → catchup_req → close 4401 ───────────────
    {
      const ws = await wsConnect(WS_PORT_A);
      await nextMsg(ws);
      ws.send(JSON.stringify({ type: 'catchup_req', since: '1970-01-01T00:00:00.000Z' }));
      const { code } = await drainUntilClose(ws);
      assert.equal(code, 4401, 'T-3(B-FIX-03): 未認證 catchup_req → close 4401');
    }

    // ── T-4（B-FIX-02）：未認證 → audit_event → close 4401 ───────────────
    {
      const ws = await wsConnect(WS_PORT_A);
      await nextMsg(ws);
      ws.send(JSON.stringify({ type: 'audit_event', action: 'fake', operator_name: 'attacker', device_id: 'evil' }));
      const { code } = await drainUntilClose(ws);
      assert.equal(code, 4401, 'T-4(B-FIX-02): 未認證 audit_event → close 4401（注入防護）');
    }

    // ── T-5：未認證 → auth → 通過，回 auth_result ─────────────────────────
    {
      const ws = await wsConnect(WS_PORT_A);
      await nextMsg(ws);
      ws.send(JSON.stringify({ type: 'auth', username: TEST_USER, pin: TEST_PIN, device_id: 'dev-t5' }));
      const msg = await nextMsg(ws);
      assert.equal(msg.type, 'auth_result', 'T-5: auth 不被 Layer 1 阻擋');
      assert.equal(msg.ok, true, 'T-5: auth 應成功');
      ws.close();
    }

    // ── T-6：未認證 → ping → pong（非 state-changing，應通過）─────────────
    {
      const ws = await wsConnect(WS_PORT_A);
      await nextMsg(ws);
      ws.send(JSON.stringify({ type: 'ping' }));
      const msg = await nextMsg(ws);
      assert.equal(msg.type, 'pong', 'T-6: ping 無需認證即可通過');
      ws.close();
    }

    // ── T-7：未認證 → time_sync_req → time_sync_resp ──────────────────────
    {
      const ws = await wsConnect(WS_PORT_A);
      await nextMsg(ws);
      ws.send(JSON.stringify({ type: 'time_sync_req', device_id: 'dev-t7' }));
      const msg = await nextMsg(ws);
      assert.equal(msg.type, 'time_sync_resp', 'T-7: time_sync_req 無需認證即可通過');
      ws.close();
    }

    // ── T-8（AC-8）：auth 成功 → clients Map 設值驗證（行為證明 + account.role 確認）─
    // ws.isAuthed 已由 clients.has(ws) 取代；clients Map 設值正確的行為證明：
    // auth 後 valid HMAC delta 不觸發 close (C1-G WS Full Security 加入後校準)
    {
      const ws = await wsConnect(WS_PORT_A);
      await nextMsg(ws);
      ws.send(JSON.stringify({ type: 'auth', username: TEST_USER, pin: TEST_PIN, device_id: 'dev-t8' }));
      const authRes = await nextMsg(ws);
      assert.equal(authRes.ok, true, 'T-8: auth 應成功');
      assert.equal(authRes.role, '組長', 'T-8(AC-8): auth_result.role 應為 DB account.role，非 client-claimed 值');
      assert.equal(authRes.username, TEST_USER, 'T-8(AC-8): auth_result.username 應與帳號一致');
      // C1-G: state-changing 需 HMAC; 不簽章會 close 4406 (signature_invalid)
      ws.send(JSON.stringify(signedMsg(secret, { type: 'delta', table: 'beds', action: 'upsert',
        record: { _id: 'T8' }, ts: new Date().toISOString(), src: 'test' })));
      ws.send(JSON.stringify({ type: 'ping' }));
      const pong = await nextMsg(ws);
      assert.equal(pong.type, 'pong', 'T-8(AC-8): 認證後 valid HMAC delta 不觸發 close（clients.has(ws)=true 行為證明），連線存活');
      ws.close();
    }

    // ── T-9（AC-9 negative）：auth 訊息含 client-claimed role → server 用 DB role ─
    // TEST_USER 在 DB 的 role = '組長'，auth 訊息自報 role = '指揮官'
    {
      const ws = await wsConnect(WS_PORT_A);
      await nextMsg(ws);
      ws.send(JSON.stringify({ type: 'auth', username: TEST_USER, pin: TEST_PIN,
        role: '指揮官', device_id: 'dev-t9' }));
      const msg = await nextMsg(ws);
      assert.equal(msg.type, 'auth_result', 'T-9: auth 應回 auth_result');
      assert.equal(msg.ok, true, 'T-9: auth 應成功');
      assert.equal(msg.role, '組長', 'T-9(AC-9): server 應用 DB role "組長"，非 client-claimed "指揮官"');
      assert.notEqual(msg.role, '指揮官', 'T-9: client-claimed role 不得被採用');
      ws.close();
    }

    // ── T-9b：認證後 catchup_req 功能驗證（B-FIX-03 正向）────────────────
    {
      const ws = await wsConnect(WS_PORT_A);
      await nextMsg(ws);
      ws.send(JSON.stringify({ type: 'auth', username: TEST_USER, pin: TEST_PIN, device_id: 'dev-t9b' }));
      const authRes = await nextMsg(ws);
      assert.equal(authRes.ok, true, 'T-9b 前置: auth 應成功');
      // C1-G: catchup_req 屬 state-changing, 需 HMAC
      ws.send(JSON.stringify(signedMsg(secret, { type: 'catchup_req', since: '1970-01-01T00:00:00.000Z' })));
      const resp = await nextMsg(ws);
      assert.equal(resp.type, 'catchup_resp', 'T-9b: 認證後 valid HMAC catchup_req → catchup_resp');
      ws.close();
    }

    // ── T-10：session_restore → close 4401, reason = session_expired ──────
    {
      const ws = await wsConnect(WS_PORT_A);
      await nextMsg(ws);
      ws.send(JSON.stringify({ type: 'session_restore', username: TEST_USER, role: '組長', device_id: 'dev-t10' }));
      const { code, reason, messages } = await drainUntilClose(ws);
      assert.equal(code, 4401, 'T-10: session_restore → close 4401');
      assert.ok(reason.includes('session_expired'), `T-10: reason 應含 session_expired，got: ${reason}`);
      assert.ok(messages.some(m => m.type === 'error' && m.reason === 'session_expired'),
        'T-10: error 訊息 reason = session_expired');
    }

    // ── T-14（B-FIX-01）：認證後 → clear_table → clear_table_ack ──────────
    {
      const ws = await wsConnect(WS_PORT_A);
      await nextMsg(ws);
      ws.send(JSON.stringify({ type: 'auth', username: TEST_USER, pin: TEST_PIN, device_id: 'dev-t14' }));
      const authRes = await nextMsg(ws);
      assert.equal(authRes.ok, true, 'T-14 前置: auth 應成功');
      // C1-G: clear_table 屬 state-changing, 需 HMAC
      ws.send(JSON.stringify(signedMsg(secret, { type: 'clear_table', table: 'beds' })));
      const ack = await nextMsg(ws);
      assert.equal(ack.type, 'clear_table_ack', 'T-14(B-FIX-01): 認證後 valid HMAC clear_table → clear_table_ack');
      assert.equal(ack.ok, true, 'T-14: ack.ok 應為 true');
      ws.close();
    }

    // ── T-14b（AC-14）：未認證 → clear_table → close 4401（Layer 1）────────
    {
      const ws = await wsConnect(WS_PORT_A);
      await nextMsg(ws);
      ws.send(JSON.stringify({ type: 'clear_table', table: 'beds' }));
      const { code } = await drainUntilClose(ws);
      assert.equal(code, 4401, 'T-14b(AC-14): 未認證 clear_table → close 4401（Layer 1 擋截）');
    }

    // ── T-16：稽核日誌欄位驗證（server-side fields）──────────────────────
    // T-1~T-4 已觸發 ws_unauthorized_message_blocked
    // T-10 已觸發 ws_session_restore_rejected（Layer 1）
    // T-5, T-8, T-9, T-14 已觸發 ws_auth_success
    {
      await new Promise(r => setTimeout(r, 200)); // 等 DB 寫入完成
      const db = new Database(tmpDb, { readonly: true });
      try {
        const blocked = db.prepare(
          `SELECT detail FROM audit_log WHERE action='ws_unauthorized_message_blocked' LIMIT 1`
        ).get();
        assert.ok(blocked, 'T-16: ws_unauthorized_message_blocked 應存在');
        const bd = JSON.parse(blocked.detail);
        assert.ok(bd.ws_id,         'T-16 blocked: ws_id 應在 detail');
        assert.ok(bd.source_ip,     'T-16 blocked: source_ip 應在 detail');
        assert.ok(bd.message_type,  'T-16 blocked: message_type 應在 detail');
        assert.equal(bd.close_code, 4401, 'T-16 blocked: close_code 應為 4401');

        const restored = db.prepare(
          `SELECT detail FROM audit_log WHERE action='ws_session_restore_rejected' LIMIT 1`
        ).get();
        assert.ok(restored, 'T-16: ws_session_restore_rejected 應存在');
        const rd = JSON.parse(restored.detail);
        assert.ok(rd.ws_id,      'T-16 restore: ws_id 應在 detail');
        assert.ok(rd.source_ip,  'T-16 restore: source_ip 應在 detail');
        assert.equal(rd.reason, 'no_token', 'T-16 restore: reason 應為 no_token');

        const authSucc = db.prepare(
          `SELECT detail FROM audit_log WHERE action='ws_auth_success' LIMIT 1`
        ).get();
        assert.ok(authSucc, 'T-16: ws_auth_success 應存在');
        const ad = JSON.parse(authSucc.detail);
        assert.ok(ad.ws_id,      'T-16 auth: ws_id 應在 detail');
        assert.ok(ad.username,   'T-16 auth: username 應在 detail');
        assert.ok(ad.role,       'T-16 auth: role 應在 detail');
        assert.ok(ad.session_id, 'T-16 auth: session_id 應在 detail');
        assert.ok(ad.source_ip,  'T-16 auth: source_ip 應在 detail');

        // AC-4 injection 防護：T-4 被 Layer 1 攔截的 audit_event 不應寫入 DB
        const fakeRows = db.prepare(
          `SELECT COUNT(*) AS cnt FROM audit_log WHERE action='fake'`
        ).get();
        assert.equal(fakeRows.cnt, 0, 'T-16(AC-4): 被 Layer 1 攔截的 audit_event 不應插入 DB（injection blocked）');
      } finally {
        db.close();
      }
    }

  } finally {
    if (srv) await stopSrv(srv.child);
    cleanup(tmpDb, tmpToken, tmpHome);
  }
});

// ─── T-11 ~ T-13：Layer 0 tests（fresh DB，不做 setup）─────────────────────

test('ws_preauth__T11_to_T13__layer0_fresh_db', async () => {
  const { tmpDb, tmpToken, tmpHome } = makeTmpEnv('B');
  const adminBase = `http://localhost:${ADMIN_PORT_B}`;
  let srv;
  try {
    srv = await spawnSrv(ADMIN_PORT_B, WS_PORT_B, { DB_PATH: tmpDb, FIRST_RUN_TOKEN_PATH: tmpToken, HOME: tmpHome });
    await waitReady(adminBase);
    // 刻意不做 setup → getAdminPinHash() = null

    // ── T-11：fresh DB + 未認證 → delta → close 4423（setup_required）─────
    {
      const ws = await wsConnect(WS_PORT_B);
      await nextMsg(ws); // welcome
      ws.send(JSON.stringify({ type: 'delta', table: 'beds', action: 'upsert',
        record: { _id: 'T11' }, ts: new Date().toISOString(), src: 'test' }));
      const { code } = await drainUntilClose(ws);
      assert.equal(code, 4423, 'T-11: fresh DB + delta → close 4423（Layer 0）');
    }

    // ── T-12（AC-12）：fresh DB + catchup_req → close 4423（B-FIX-03 + Layer 0）─
    {
      const ws = await wsConnect(WS_PORT_B);
      await nextMsg(ws);
      ws.send(JSON.stringify({ type: 'catchup_req', since: '1970-01-01T00:00:00.000Z' }));
      const { code } = await drainUntilClose(ws);
      assert.equal(code, 4423, 'T-12(AC-12): fresh DB + catchup_req → close 4423（B-FIX-03 加入 _STATE_CHANGING + Layer 0）');
    }

    // ── T-13（AC-13）：fresh DB + session_restore → close 4423（Layer 0 wins over Layer 1）
    {
      const ws = await wsConnect(WS_PORT_B);
      await nextMsg(ws);
      ws.send(JSON.stringify({ type: 'session_restore', username: 'bob', role: '組長', device_id: 'dev' }));
      const { code } = await drainUntilClose(ws);
      assert.equal(code, 4423, 'T-13(AC-13): fresh DB + session_restore → close 4423（Layer 0 優先，非 4401）');
      assert.notEqual(code, 4401, 'T-13: 不應為 4401（Layer 1）');
    }

  } finally {
    if (srv) await stopSrv(srv.child);
    cleanup(tmpDb, tmpToken, tmpHome);
  }
});

// ─── T-15：PI-01 17 scenario regression ───────────────────────────────────

test('ws_preauth__T15__pi01_17_scenario_regression', () => {
  const result = spawnSync(
    process.execPath,
    ['--test', 'server/__tests__/first_run_integration.test.js'],
    { cwd: REPO_ROOT, encoding: 'utf8', timeout: 90_000 }
  );
  assert.equal(result.status, 0,
    `T-15: PI-01 回歸測試失敗 (exit ${result.status}):\n${result.stderr || result.stdout}`);
});
