'use strict';

// Layer 2 — Integration test for HOTFIX-PI-01 first-run gate
// 使用 child_process.spawn 啟動真實 server，驗證完整 happy-path
// 執行：node --test server/__tests__/first_run_integration.test.js

const { test } = require('node:test');
const assert   = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs        = require('node:fs');
const path      = require('node:path');
const os        = require('node:os');
const crypto    = require('node:crypto');

const REPO_ROOT       = path.resolve(__dirname, '../..');
const TEST_ADMIN_PORT = 19766;
const TEST_WS_PORT    = 19765;
const READY_TIMEOUT   = 10_000; // ms

function makeTmpEnv() {
  const id      = crypto.randomBytes(8).toString('hex');
  const tmpDb   = path.join(os.tmpdir(), `ics_test_${id}.db`);
  const tmpToken = path.join(os.tmpdir(), `ics_token_${id}`);
  return { tmpDb, tmpToken, id };
}

async function spawnServer(extraEnv) {
  return new Promise((resolve, reject) => {
    const child = spawn(
      process.execPath,
      ['server/index.js', '--unit', 'shelter'],
      {
        cwd: REPO_ROOT,
        env: {
          ...process.env,
          ADMIN_PORT: String(TEST_ADMIN_PORT),
          WS_PORT:    String(TEST_WS_PORT),
          LOG_LEVEL:  'error', // 安靜輸出，減少 test 雜訊
          ...extraEnv,
        },
        stdio: 'pipe',
      }
    );

    let stdout = '';
    let stderr = '';
    child.stdout.on('data', d => { stdout += d.toString(); });
    child.stderr.on('data', d => { stderr += d.toString(); });
    child.on('error', reject);

    resolve({
      child,
      getStdout: () => stdout,
      getStderr: () => stderr,
    });
  });
}

async function waitReady(baseUrl, maxMs = READY_TIMEOUT) {
  const deadline = Date.now() + maxMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${baseUrl}/admin/status`);
      if (res.status === 200 || res.status === 423) return; // 200 = open, 423 = gate active（都代表 server ready）
    } catch { /* 尚未就緒 */ }
    await new Promise(r => setTimeout(r, 150));
  }
  throw new Error(`Server 未在 ${maxMs}ms 內就緒`);
}

async function stopServer(child) {
  return new Promise(resolve => {
    child.once('close', resolve);
    child.kill('SIGTERM');
    if (process.platform === 'win32') child.kill(); // Windows fallback
    setTimeout(() => { try { child.kill(); } catch { /* 已停止 */ } }, 3000);
  });
}

function cleanupFiles(...files) {
  for (const f of files) {
    try { fs.rmSync(f, { force: true }); } catch { /* 略過 */ }
    try { fs.rmSync(f + '-wal', { force: true }); } catch { /* 略過 */ }
    try { fs.rmSync(f + '-shm', { force: true }); } catch { /* 略過 */ }
  }
}

// ─────────────────────────────────────────────────────────────────────────────

test('first_run_integration__full_happy_path', async () => {
  const { tmpDb, tmpToken } = makeTmpEnv();
  const base = `http://localhost:${TEST_ADMIN_PORT}`;

  const env = {
    DB_PATH:               tmpDb,
    FIRST_RUN_TOKEN_PATH:  tmpToken,
  };

  let srv;
  try {
    // ── 第一次啟動（fresh DB）────────────────────────────────────────────────
    srv = await spawnServer(env);
    await waitReady(base);

    // 1. GET /admin/status → 200 (whitelist，first-run 中仍回 200)
    {
      const res  = await fetch(`${base}/admin/status`);
      const body = await res.json();
      assert.equal(res.status, 200, 'GET /admin/status 應為 200');
      assert.equal(body.admin_pin_setup, false, 'admin_pin_setup 應為 false');
    }

    // 2. POST /admin/accounts → 423 (HTTP gate)
    {
      const res  = await fetch(`${base}/admin/accounts`, { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: 'test', role: '組長', pin: '1234' }),
      });
      const body = await res.json();
      assert.equal(res.status, 423, 'POST /admin/accounts 應為 423');
      assert.equal(body.code, 'FIRST_RUN_REQUIRED');
    }

    // 3. GET /admin/accounts → 423
    {
      const res = await fetch(`${base}/admin/accounts`);
      assert.equal(res.status, 423, 'GET /admin/accounts 應為 423');
    }

    // 4. POST /admin/setup 無 token → 401
    {
      const res = await fetch(`${base}/admin/setup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ admin_password: 'Str0ng@Pass1!' }),
      });
      assert.equal(res.status, 401, '缺 token 應為 401');
    }

    // 5. POST /admin/setup 錯 token → 403
    {
      const res = await fetch(`${base}/admin/setup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ first_run_token: 'wrong_token', admin_password: 'Str0ng@Pass1!' }),
      });
      assert.equal(res.status, 403, '錯 token 應為 403');
    }

    // 讀取真實 token
    assert.ok(fs.existsSync(tmpToken), 'token 檔應已建立');
    const token = fs.readFileSync(tmpToken, 'utf8').trim();
    assert.ok(token.length >= 64, 'token 應 ≥ 64 chars');

    // B4a. POST /admin/setup/ trailing slash 無 token → 401（非 423，gate 應正常放行）
    {
      const res = await fetch(`${base}/admin/setup/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ admin_password: 'Str0ng@Pass1!' }),
      });
      assert.equal(res.status, 401, 'trailing slash 無 token 應為 401，非 423');
    }

    // B4b. POST /admin/setup 缺 Content-Type → 401（非 423，req.body guard 應生效）
    {
      const res = await fetch(`${base}/admin/setup`, {
        method: 'POST',
        body: 'not-json',
      });
      assert.equal(res.status, 401, '缺 Content-Type 無 token 應為 401，非 423');
    }

    // 6. POST /admin/setup 弱密碼（長度不足）→ 400
    {
      const res  = await fetch(`${base}/admin/setup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ first_run_token: token, admin_password: 'short' }),
      });
      assert.equal(res.status, 400, '弱密碼應為 400');
    }

    // 7. POST /admin/setup 弱密碼（無特殊符號）→ 400
    {
      const res = await fetch(`${base}/admin/setup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ first_run_token: token, admin_password: 'Str0ngPass1' }),
      });
      assert.equal(res.status, 400, '無特殊符號密碼應為 400');
    }

    // 8. POST /admin/setup 正確 token + 合格密碼 → 200
    const adminPass = 'Str0ng@Pass1!';
    {
      const res  = await fetch(`${base}/admin/setup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ first_run_token: token, admin_password: adminPass }),
      });
      const body = await res.json();
      assert.equal(res.status, 200, `setup 應為 200，body: ${JSON.stringify(body)}`);
      assert.equal(body.ok, true);
    }

    // 9. token 檔應被刪除
    assert.equal(fs.existsSync(tmpToken), false, 'setup 後 token 檔應被刪除');

    // 10. token 不應出現在 stdout
    const captured = srv.getStdout() + srv.getStderr();
    assert.ok(!captured.includes(token), 'token 不應出現在任何 log 輸出');

    // 11. setup 後 GET /admin/status → admin_pin_setup: true
    {
      const res  = await fetch(`${base}/admin/status`);
      const body = await res.json();
      assert.equal(body.admin_pin_setup, true, 'setup 後 admin_pin_setup 應為 true');
    }

    // 12. setup 後 POST /admin/accounts → 200（gate 解除，用 X-Admin-PIN 帶密碼）
    {
      const res = await fetch(`${base}/admin/accounts`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Admin-PIN': adminPass },
        body: JSON.stringify({ username: 'alice', role: '組長', pin: '4719' }),
      });
      assert.equal(res.status, 200, 'gate 解除後 POST /admin/accounts 應為 200');
    }

    await stopServer(srv.child);
    srv = null;

    // ── 第二次啟動（same DB，setup 已完成）──────────────────────────────────
    srv = await spawnServer(env);
    await waitReady(base);

    // 13. gate 應解除：GET /admin/accounts → 200（need admin auth）
    {
      const res = await fetch(`${base}/admin/accounts`, {
        headers: { 'X-Admin-PIN': adminPass },
      });
      assert.equal(res.status, 200, 'second boot 後 gate 應解除');
    }

    // 14. POST /admin/setup → 400（PIN 已設定）
    {
      const res  = await fetch(`${base}/admin/setup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ first_run_token: 'anything', admin_password: adminPass }),
      });
      const body = await res.json();
      assert.equal(res.status, 400, 'second boot setup 應為 400');
      assert.match(body.reason, /已設定/);
    }

    // 15. second boot stdout 不應再印 token 路徑提示（因為 admin_pin_hash 已存在）
    assert.ok(!srv.getStdout().includes('[FirstRun]'), 'second boot 不應再印 [FirstRun] 訊息');

  } finally {
    if (srv) await stopServer(srv.child);
    cleanupFiles(tmpDb, tmpToken);
  }
});
