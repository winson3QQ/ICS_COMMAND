'use strict';

/**
 * Layer 2 — Integration tests for WS Full Security (C1-G)
 *
 * 對齊 Step A Approval REV2 §8 AC-1~AC-10 (AC-7 REV2 移除, AC-8 由 ws_preauth.test.js regression)
 * 對齊 Task Card REV2 §7 自動化 tests 表
 *
 * 執行: node --test server/__tests__/ws_full_security.test.js
 */

const { test }             = require('node:test');
const assert               = require('node:assert/strict');
const { spawn }            = require('node:child_process');
const WebSocket            = require('ws');
const fs                   = require('node:fs');
const path                 = require('node:path');
const os                   = require('node:os');
const crypto               = require('node:crypto');
const Database             = require('better-sqlite3');

const { signMessage, verifyMessage, isWithinTimeWindow, NONCE_WINDOW_MS,
        _clearNonceCache } = require('../ws_signing');

const REPO_ROOT = path.resolve(__dirname, '../..');
const ADMIN_PASS = 'Str0ng@Pass1!';
const TEST_USER  = 'ws_fsec_user';
const TEST_PIN   = '4719';

// 不同 port 避免與 ws_preauth.test.js 並行衝突
const ADMIN_PORT_AC1   = 19781;  const WS_PORT_AC1   = 19782;
const ADMIN_PORT_AC234 = 19783;  const WS_PORT_AC234 = 19784;
const ADMIN_PORT_AC5   = 19785;  const WS_PORT_AC5   = 19786;
const ADMIN_PORT_AC69  = 19787;  const WS_PORT_AC69  = 19788;

// ── 共用工具 ────────────────────────────────────────────────────────────────

function makeTmpEnv(tag = '') {
  const id = crypto.randomBytes(6).toString('hex') + tag;
  const tmpHome = path.join(os.tmpdir(), `ics_home_${id}`);
  fs.mkdirSync(path.join(tmpHome, '.ics'), { recursive: true });
  const secret = crypto.randomBytes(32).toString('hex');  // 64 hex chars 對齊 TI-01 既有格式
  const keyId  = crypto.randomUUID();
  fs.writeFileSync(path.join(tmpHome, '.ics', 'hmac_secret'), secret);
  fs.writeFileSync(path.join(tmpHome, '.ics', 'hmac_key_id'),  keyId);
  return {
    tmpDb:    path.join(os.tmpdir(), `ics_wsf_${id}.db`),
    tmpToken: path.join(os.tmpdir(), `ics_wsf_tok_${id}`),
    tmpHome, secret, keyId,
  };
}

function spawnSrv(adminPort, wsPort, env) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, ['server/index.js', '--unit', 'shelter'], {
      cwd:   REPO_ROOT,
      env:   {
        ...process.env,
        ADMIN_PORT: String(adminPort),
        WS_PORT:    String(wsPort),
        LOG_LEVEL:  'error',
        ...env,    // HOME / DB_PATH / FIRST_RUN_TOKEN_PATH 由呼叫端覆蓋
      },
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
    setTimeout(() => { try { child.kill(); } catch { /* 已停 */ } }, 3000);
  });
}

function cleanup(...paths) {
  for (const p of paths) {
    for (const ext of ['', '-wal', '-shm']) {
      try { fs.rmSync(p + ext, { force: true, recursive: true }); } catch { /* 略 */ }
    }
  }
}

function wsConnect(port, ms = 6000) {
  return new Promise((resolve, reject) => {
    const t  = setTimeout(() => reject(new Error(`WS connect timeout (${port})`)), ms);
    const ws = new WebSocket(`ws://localhost:${port}?src=fsec_test`);
    ws.once('open',  () => { clearTimeout(t); resolve(ws); });
    ws.once('error', e  => { clearTimeout(t); reject(e); });
  });
}

function nextMsg(ws, ms = 4000) {
  return new Promise((resolve, reject) => {
    if (ws.readyState === WebSocket.CLOSED) return resolve({ _closed: true, code: 0 });
    const t = setTimeout(() => { cleanup_(); reject(new Error('nextMsg timeout')); }, ms);
    const onMsg   = raw => { cleanup_(); resolve(JSON.parse(raw)); };
    const onClose = (code, reason) => { cleanup_(); resolve({ _closed: true, code, reason: reason?.toString() }); };
    const cleanup_ = () => { clearTimeout(t); ws.off('message', onMsg); ws.off('close', onClose); };
    ws.once('message', onMsg);
    ws.once('close', onClose);
  });
}

function drainUntilClose(ws, ms = 4000) {
  return new Promise((resolve, reject) => {
    if (ws.readyState === WebSocket.CLOSED) return resolve({ messages: [], code: 0, reason: '' });
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
  const token = fs.readFileSync(tokenPath, 'utf8').trim();
  const res = await fetch(`${adminBase}/admin/setup`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ first_run_token: token, admin_password: ADMIN_PASS }),
  });
  assert.equal(res.status, 200, `setup 應為 200`);
}

async function createAccount(adminBase, username, pin, role = '組長') {
  const res = await fetch(`${adminBase}/admin/accounts`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json', 'X-Admin-PIN': ADMIN_PASS },
    body:    JSON.stringify({ username, role, pin }),
  });
  assert.equal(res.status, 200, `createAccount 應為 200, got ${res.status}`);
}

// 直接 INSERT DB, 繞過 admin endpoint 的 cfg.roles 限制 (shelter 限 ['組長','一般'])
// 用於 AC-5 測 'observer' / 'commander' role (P2a 4-role enum) 的 broadcast filter
async function createAccountDirect(dbPath, username, pin, role) {
  const db = new Database(dbPath);
  try {
    const salt = crypto.randomBytes(16).toString('hex');
    const hash = await new Promise((resolve, reject) => {
      crypto.pbkdf2(pin, Buffer.from(salt, 'hex'), 200000, 32, 'sha256', (err, key) => {
        if (err) reject(err); else resolve(key.toString('hex'));
      });
    });
    db.prepare(
      `INSERT INTO accounts(id,username,role,pin_hash,pin_salt,status,created_at,created_by)
       VALUES(?,?,?,?,?,?,?,?)`
    ).run(crypto.randomUUID(), username, role, hash, salt, 'active', new Date().toISOString(), 'test_direct');
  } finally { db.close(); }
}

async function authWs(ws, username, pin, deviceId = 'fsec') {
  ws.send(JSON.stringify({ type: 'auth', username, pin, device_id: deviceId }));
  const res = await nextMsg(ws);
  assert.equal(res.type, 'auth_result', 'auth_result 預期');
  assert.equal(res.ok, true, 'auth ok 預期');
  return res;
}

/**
 * 用 secret 對 msg (不含 hmac) 計算簽章, 加 nonce + timestamp + hmac 後回傳完整 msg
 */
function signedMsg(secret, msgWithoutMeta, opts = {}) {
  const nonce     = opts.nonce     ?? crypto.randomBytes(16).toString('hex');
  const timestamp = opts.timestamp ?? Date.now();
  const withMeta  = { ...msgWithoutMeta, nonce, timestamp };
  const hmac      = signMessage(secret, withMeta);
  return { ...withMeta, hmac };
}

// ─── ws_signing.js 單元測試 (純 helper) ─────────────────────────────────────

test('ws_signing__unit__sign_verify_roundtrip', () => {
  _clearNonceCache();
  const secret = crypto.randomBytes(32).toString('hex');
  const msg = signedMsg(secret, { type: 'delta', table: 'beds', record: { _id: 'x' } });
  const res = verifyMessage(secret, msg);
  assert.equal(res.ok, true, 'sign + verify roundtrip 應成功');
});

test('ws_signing__unit__tampered_payload_rejected', () => {
  _clearNonceCache();
  const secret = crypto.randomBytes(32).toString('hex');
  const msg = signedMsg(secret, { type: 'delta', table: 'beds', record: { _id: 'x' } });
  msg.record._id = 'TAMPERED';
  const res = verifyMessage(secret, msg);
  assert.equal(res.ok, false);
  assert.equal(res.reason, 'signature_invalid', '篡改 payload 應 signature_invalid');
});

test('ws_signing__unit__nonce_replay_rejected', () => {
  _clearNonceCache();
  const secret = crypto.randomBytes(32).toString('hex');
  const msg = signedMsg(secret, { type: 'delta', table: 'beds', record: { _id: 'x' } });
  const r1 = verifyMessage(secret, msg);
  assert.equal(r1.ok, true, '第一次 verify 應成功');
  const r2 = verifyMessage(secret, msg);
  assert.equal(r2.ok, false);
  assert.equal(r2.reason, 'nonce_replay', '相同 nonce 第二次應 nonce_replay');
});

test('ws_signing__unit__timestamp_out_of_window', () => {
  _clearNonceCache();
  const secret = crypto.randomBytes(32).toString('hex');
  const oldTs  = Date.now() - NONCE_WINDOW_MS - 5000;
  const msg = signedMsg(secret, { type: 'delta', table: 'beds' }, { timestamp: oldTs });
  const res = verifyMessage(secret, msg);
  assert.equal(res.ok, false);
  assert.equal(res.reason, 'timestamp_out_of_window', '超 30 秒應 timestamp_out_of_window');
});

test('ws_signing__unit__missing_signature_fields', () => {
  const secret = crypto.randomBytes(32).toString('hex');
  const r1 = verifyMessage(secret, { type: 'delta' });  // 完全缺 meta
  assert.equal(r1.ok, false);
  assert.equal(r1.reason, 'missing_signature_fields');

  const r2 = verifyMessage(secret, { type: 'delta', hmac: 'abc', nonce: 'n1' });  // 缺 timestamp
  assert.equal(r2.ok, false);
  assert.equal(r2.reason, 'missing_signature_fields');
});

test('ws_signing__unit__hmac_secret_missing', () => {
  const res = verifyMessage(null, signedMsg('dummy', { type: 'delta' }));
  assert.equal(res.ok, false);
  assert.equal(res.reason, 'hmac_secret_missing');
});

test('ws_signing__unit__isWithinTimeWindow', () => {
  const now = Date.now();
  assert.equal(isWithinTimeWindow(now, now), true);
  assert.equal(isWithinTimeWindow(now - 29_000, now), true);
  assert.equal(isWithinTimeWindow(now + 29_000, now), true);
  assert.equal(isWithinTimeWindow(now - 31_000, now), false);
  assert.equal(isWithinTimeWindow(now + 31_000, now), false);
  assert.equal(isWithinTimeWindow('not-a-number', now), false);
});

// ─── AC-1: valid HMAC accepted ──────────────────────────────────────────────

test('ws_full_security__AC1__valid_hmac_accepted', async () => {
  const env = makeTmpEnv('ac1');
  const adminBase = `http://localhost:${ADMIN_PORT_AC1}`;
  let srv;
  try {
    srv = await spawnSrv(ADMIN_PORT_AC1, WS_PORT_AC1, {
      DB_PATH: env.tmpDb, FIRST_RUN_TOKEN_PATH: env.tmpToken, HOME: env.tmpHome,
    });
    await waitReady(adminBase);
    await completeSetup(adminBase, env.tmpToken);
    await createAccount(adminBase, TEST_USER, TEST_PIN);

    const ws = await wsConnect(WS_PORT_AC1);
    await nextMsg(ws);  // skip welcome
    await authWs(ws, TEST_USER, TEST_PIN);

    // valid signed delta → 不應 close, 連線存活
    const msg = signedMsg(env.secret, {
      type: 'delta', table: 'beds', action: 'upsert',
      record: { _id: 'AC1' }, ts: new Date().toISOString(), src: 'fsec',
    });
    ws.send(JSON.stringify(msg));

    // 連線存活檢查: 送 ping 應收 pong
    ws.send(JSON.stringify({ type: 'ping' }));
    const pong = await nextMsg(ws);
    assert.equal(pong.type, 'pong', 'AC-1: valid HMAC delta 後 ping → pong, 連線存活');

    ws.close();
  } finally {
    if (srv) await stopSrv(srv.child);
    cleanup(env.tmpDb, env.tmpToken, env.tmpHome);
  }
});

// ─── AC-2 + AC-3 + AC-4: tampered / replay / unknown_type 全 close ──────────

test('ws_full_security__AC2_AC3_AC4__tampered_replay_unknown_close', async () => {
  const env = makeTmpEnv('ac234');
  const adminBase = `http://localhost:${ADMIN_PORT_AC234}`;
  let srv;
  try {
    srv = await spawnSrv(ADMIN_PORT_AC234, WS_PORT_AC234, {
      DB_PATH: env.tmpDb, FIRST_RUN_TOKEN_PATH: env.tmpToken, HOME: env.tmpHome,
    });
    await waitReady(adminBase);
    await completeSetup(adminBase, env.tmpToken);
    await createAccount(adminBase, TEST_USER, TEST_PIN);

    // ── AC-2: 篡改 payload → close 4406 ─────────────────────────────────
    {
      const ws = await wsConnect(WS_PORT_AC234);
      await nextMsg(ws);
      await authWs(ws, TEST_USER, TEST_PIN, 'dev-ac2');

      const msg = signedMsg(env.secret, {
        type: 'delta', table: 'beds', action: 'upsert',
        record: { _id: 'AC2' }, ts: new Date().toISOString(), src: 'fsec',
      });
      // 篡改 record._id (HMAC 不再對齊)
      msg.record._id = 'TAMPERED';
      ws.send(JSON.stringify(msg));

      const { code } = await drainUntilClose(ws);
      assert.equal(code, 4406, 'AC-2: 篡改 payload → close 4406 (signature_invalid)');
    }

    // ── AC-3: nonce replay → close 4408 ─────────────────────────────────
    {
      const ws1 = await wsConnect(WS_PORT_AC234);
      await nextMsg(ws1);
      await authWs(ws1, TEST_USER, TEST_PIN, 'dev-ac3a');

      const sharedNonce = crypto.randomBytes(16).toString('hex');
      const sharedTs    = Date.now();
      const msg = signedMsg(env.secret, {
        type: 'delta', table: 'beds', action: 'upsert',
        record: { _id: 'AC3' }, ts: new Date().toISOString(), src: 'fsec',
      }, { nonce: sharedNonce, timestamp: sharedTs });

      ws1.send(JSON.stringify(msg));   // 第一次接受
      // 等一下確保 server 處理完
      await new Promise(r => setTimeout(r, 200));
      ws1.close();

      // 第二個連線送同一 nonce → replay
      const ws2 = await wsConnect(WS_PORT_AC234);
      await nextMsg(ws2);
      await authWs(ws2, TEST_USER, TEST_PIN, 'dev-ac3b');
      ws2.send(JSON.stringify(msg));   // 同 nonce + 同 timestamp + 同 hmac

      const { code } = await drainUntilClose(ws2);
      assert.equal(code, 4408, 'AC-3: nonce replay → close 4408 (nonce_replay)');
    }

    // ── AC-4: unknown type → close 4400 ─────────────────────────────────
    {
      const ws = await wsConnect(WS_PORT_AC234);
      await nextMsg(ws);
      await authWs(ws, TEST_USER, TEST_PIN, 'dev-ac4');

      ws.send(JSON.stringify({ type: 'admin_override', payload: { evil: true } }));
      const { code } = await drainUntilClose(ws);
      assert.equal(code, 4400, 'AC-4: unknown type → close 4400 (unknown_type)');
    }
  } finally {
    if (srv) await stopSrv(srv.child);
    cleanup(env.tmpDb, env.tmpToken, env.tmpHome);
  }
});

// ─── AC-5: Observer role 不收 PII (對齊 R-2 / F-15) ─────────────────────────
//   ⚠️ 註: Pi server 現有 PWA role 為中文 (組長/一般/檢傷官/etc), 無 'observer' role.
//          R-1/R-2 filter 為「未來護城河」(Human owner 2026-05-04 裁定 b).
//          本 test 用 role='observer' 直接建 account 模擬 P2a 4-role enum mapping
//          以 verify filter 邏輯本身正確; 真實 PWA receiver 等 W-C1-A close 後第二輪 drill.

test('ws_full_security__AC5__observer_no_pii', async () => {
  const env = makeTmpEnv('ac5');
  const adminBase = `http://localhost:${ADMIN_PORT_AC5}`;
  let srv;
  try {
    srv = await spawnSrv(ADMIN_PORT_AC5, WS_PORT_AC5, {
      DB_PATH: env.tmpDb, FIRST_RUN_TOKEN_PATH: env.tmpToken, HOME: env.tmpHome,
    });
    await waitReady(adminBase);
    await completeSetup(adminBase, env.tmpToken);
    await createAccount(adminBase, TEST_USER, TEST_PIN, '組長');     // sender (full payload)
    // observer/commander 是 P2a 4-role enum, 不在 shelter cfg.roles 白名單
    // 直接 INSERT DB 繞過 admin endpoint validation (對齊 「未來 W-C1-A 才會有真實 PWA receiver」 紀律)
    await createAccountDirect(env.tmpDb, 'observer_user',  TEST_PIN, 'observer');
    await createAccountDirect(env.tmpDb, 'commander_user', TEST_PIN, 'commander');

    // observer + commander 連線並 auth
    const wsObs = await wsConnect(WS_PORT_AC5);
    await nextMsg(wsObs);
    await authWs(wsObs, 'observer_user', TEST_PIN, 'dev-obs');

    const wsCmd = await wsConnect(WS_PORT_AC5);
    await nextMsg(wsCmd);
    await authWs(wsCmd, 'commander_user', TEST_PIN, 'dev-cmd');

    // sender 連線並送含 PII 的 delta
    const wsSend = await wsConnect(WS_PORT_AC5);
    await nextMsg(wsSend);
    await authWs(wsSend, TEST_USER, TEST_PIN, 'dev-snd');

    const msg = signedMsg(env.secret, {
      type: 'delta', table: 'patients', action: 'upsert',
      record: {
        _id: 'AC5_P1',
        name: '張三',
        symptom:    '咳嗽',           // PII
        allergy:    '盤尼西林',       // PII
        medication: '止咳糖漿',       // PII
        patient_id: 'P1234',          // PII
        bed_id:     'B001',           // 非 PII
      },
      ts: new Date().toISOString(), src: 'fsec',
    });
    wsSend.send(JSON.stringify(msg));

    // observer 收到的 delta record 應無 PII 欄位
    const obsRelay = await nextMsg(wsObs);
    assert.equal(obsRelay.type, 'delta', 'AC-5: observer 收到 delta');
    assert.equal(obsRelay.record._id, 'AC5_P1', 'AC-5: _id 保留');
    assert.equal(obsRelay.record.bed_id, 'B001', 'AC-5: 非 PII 欄位保留');
    assert.equal(obsRelay.record.symptom,    undefined, 'AC-5: symptom 移除');
    assert.equal(obsRelay.record.allergy,    undefined, 'AC-5: allergy 移除');
    assert.equal(obsRelay.record.medication, undefined, 'AC-5: medication 移除');
    assert.equal(obsRelay.record.patient_id, undefined, 'AC-5: patient_id 移除');

    // commander 收到的 delta record 應有完整 payload
    const cmdRelay = await nextMsg(wsCmd);
    assert.equal(cmdRelay.type, 'delta', 'AC-5: commander 收到 delta');
    assert.equal(cmdRelay.record.symptom,    '咳嗽',          'AC-5: commander 收到 symptom');
    assert.equal(cmdRelay.record.allergy,    '盤尼西林',      'AC-5: commander 收到 allergy');
    assert.equal(cmdRelay.record.medication, '止咳糖漿',      'AC-5: commander 收到 medication');
    assert.equal(cmdRelay.record.patient_id, 'P1234',         'AC-5: commander 收到 patient_id');

    wsObs.close(); wsCmd.close(); wsSend.close();
  } finally {
    if (srv) await stopSrv(srv.child);
    cleanup(env.tmpDb, env.tmpToken, env.tmpHome);
  }
});

// ─── AC-6 + AC-9: audit_event HMAC + PRE_AUTH_NO_SIG types ──────────────────

test('ws_full_security__AC6_AC9__audit_event_hmac_and_pre_auth_no_sig', async () => {
  const env = makeTmpEnv('ac69');
  const adminBase = `http://localhost:${ADMIN_PORT_AC69}`;
  let srv;
  try {
    srv = await spawnSrv(ADMIN_PORT_AC69, WS_PORT_AC69, {
      DB_PATH: env.tmpDb, FIRST_RUN_TOKEN_PATH: env.tmpToken, HOME: env.tmpHome,
    });
    await waitReady(adminBase);
    await completeSetup(adminBase, env.tmpToken);
    await createAccount(adminBase, TEST_USER, TEST_PIN);

    // ── AC-9: PRE_AUTH_ALLOWED messages 不需簽章 ────────────────────────
    //   auth / ping / time_sync_req / debug_ping 都不需 HMAC
    {
      const ws = await wsConnect(WS_PORT_AC69);
      await nextMsg(ws);  // welcome

      // ping → pong (無 HMAC)
      ws.send(JSON.stringify({ type: 'ping' }));
      const pong = await nextMsg(ws);
      assert.equal(pong.type, 'pong', 'AC-9: ping 無需 HMAC');

      // time_sync_req → time_sync_resp (無 HMAC)
      ws.send(JSON.stringify({ type: 'time_sync_req', device_id: 'dev-ac9' }));
      const tsResp = await nextMsg(ws);
      assert.equal(tsResp.type, 'time_sync_resp', 'AC-9: time_sync_req 無需 HMAC');

      // auth (無 HMAC)
      const authRes = await authWs(ws, TEST_USER, TEST_PIN, 'dev-ac9-auth');
      assert.equal(authRes.ok, true, 'AC-9: auth 無需 HMAC');

      ws.close();
    }

    // ── AC-6 positive: audit_event with valid HMAC → audit_log written ──
    let auditWriteOk = false;
    {
      const ws = await wsConnect(WS_PORT_AC69);
      await nextMsg(ws);
      await authWs(ws, TEST_USER, TEST_PIN, 'dev-ac6p');

      const msg = signedMsg(env.secret, {
        type: 'audit_event',
        action: 'ac6_positive_test',
        operator_name: TEST_USER,
        device_id:   'dev-ac6p',
        session_id:  'sess-ac6p',
        detail:      { source: 'AC-6 positive test' },
      });
      ws.send(JSON.stringify(msg));

      // 等 server 寫 audit_log
      await new Promise(r => setTimeout(r, 300));
      ws.close();

      const db = new Database(env.tmpDb, { readonly: true });
      try {
        const row = db.prepare(`SELECT detail FROM audit_log WHERE action='ac6_positive_test' LIMIT 1`).get();
        auditWriteOk = !!row;
        assert.ok(row, 'AC-6 positive: 有效 HMAC 的 audit_event 應寫 audit_log');
      } finally { db.close(); }
    }

    // ── AC-6 negative: tampered audit_event → close 4406, audit_log not written ──
    {
      const ws = await wsConnect(WS_PORT_AC69);
      await nextMsg(ws);
      await authWs(ws, TEST_USER, TEST_PIN, 'dev-ac6n');

      const msg = signedMsg(env.secret, {
        type: 'audit_event',
        action: 'ac6_negative_test',
        operator_name: TEST_USER,
        device_id:  'dev-ac6n',
        session_id: 'sess-ac6n',
        detail:     { source: 'AC-6 negative test' },
      });
      msg.detail.source = 'TAMPERED';   // HMAC 不再對齊
      ws.send(JSON.stringify(msg));

      const { code } = await drainUntilClose(ws);
      assert.equal(code, 4406, 'AC-6 negative: 篡改 audit_event → close 4406');

      await new Promise(r => setTimeout(r, 200));
      const db = new Database(env.tmpDb, { readonly: true });
      try {
        const row = db.prepare(`SELECT COUNT(*) as cnt FROM audit_log WHERE action='ac6_negative_test'`).get();
        assert.equal(row.cnt, 0, 'AC-6 negative: 篡改 audit_event 不應寫 audit_log (HMAC 在 writeAuditLog 前擋)');
      } finally { db.close(); }
    }

    assert.ok(auditWriteOk, 'AC-6 整體: positive 路徑 audit_log 寫入應成功');
  } finally {
    if (srv) await stopSrv(srv.child);
    cleanup(env.tmpDb, env.tmpToken, env.tmpHome);
  }
});

// ─── Sync v5 (W-C1-A Amendment v3, F-21): auth_result 含 hmac_secret + hmac_key_id ──

const ADMIN_PORT_SYNCV5 = 19789;  const WS_PORT_SYNCV5 = 19790;

test('ws_full_security__sync_v5__auth_result_includes_hmac_fields', async () => {
  const env = makeTmpEnv('syncv5');
  const adminBase = `http://localhost:${ADMIN_PORT_SYNCV5}`;
  let srv;
  try {
    srv = await spawnSrv(ADMIN_PORT_SYNCV5, WS_PORT_SYNCV5, {
      DB_PATH: env.tmpDb, FIRST_RUN_TOKEN_PATH: env.tmpToken, HOME: env.tmpHome,
    });
    await waitReady(adminBase);
    await completeSetup(adminBase, env.tmpToken);
    await createAccount(adminBase, TEST_USER, TEST_PIN);

    const ws = await wsConnect(WS_PORT_SYNCV5);
    await nextMsg(ws);  // skip welcome
    ws.send(JSON.stringify({ type: 'auth', username: TEST_USER, pin: TEST_PIN, device_id: 'syncv5' }));
    const authRes = await nextMsg(ws);

    assert.equal(authRes.type, 'auth_result', 'Sync v5: auth_result 預期');
    assert.equal(authRes.ok, true, 'Sync v5: auth ok 預期');

    // Sync v5 additive fields
    assert.equal(typeof authRes.hmac_secret, 'string', 'Sync v5: auth_result.hmac_secret 為 string');
    assert.equal(authRes.hmac_secret, env.secret, 'Sync v5: auth_result.hmac_secret 對齊 server config (TI-01 既有)');
    assert.equal(authRes.hmac_secret.length, 64, 'Sync v5: hmac_secret hex 64 chars');

    assert.equal(typeof authRes.hmac_key_id, 'string', 'Sync v5: auth_result.hmac_key_id 為 string');
    assert.match(authRes.hmac_key_id, /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i,
      'Sync v5: hmac_key_id 為 UUID v4 format');

    // 既有 fields 仍存在 (additive 守線, 紅線 4-A)
    assert.equal(typeof authRes.username, 'string', 'Sync v5 additive 守線: username 仍存在');
    assert.equal(typeof authRes.role, 'string',     'Sync v5 additive 守線: role 仍存在');
    assert.equal(typeof authRes.pi_time, 'string',  'Sync v5 additive 守線: pi_time 仍存在');
    assert.ok('last_sync_to_command' in authRes,    'Sync v5 additive 守線: last_sync_to_command 仍存在');
    assert.ok('site_salt' in authRes,                'Sync v5 additive 守線: site_salt 仍存在');

    // 新 hmac_key_id per session: 第二次 auth 應拿到不同 hmac_key_id (對齊 K-1 「per session」)
    const ws2 = await wsConnect(WS_PORT_SYNCV5);
    await nextMsg(ws2);
    ws2.send(JSON.stringify({ type: 'auth', username: TEST_USER, pin: TEST_PIN, device_id: 'syncv5b' }));
    const authRes2 = await nextMsg(ws2);
    assert.notEqual(authRes2.hmac_key_id, authRes.hmac_key_id,
      'Sync v5 K-3: 重新 auth 應拿到新 hmac_key_id (per session)');
    // 但 hmac_secret 仍對齊 server config (master secret 不變)
    assert.equal(authRes2.hmac_secret, env.secret,
      'Sync v5: hmac_secret master 不隨 session 變 (對齊 TI-01 既有 design)');

    ws.close(); ws2.close();
  } finally {
    if (srv) await stopSrv(srv.child);
    cleanup(env.tmpDb, env.tmpToken, env.tmpHome);
  }
});
