'use strict';

/**
 * HOTFIX-TI-01 — Node.js trusted_ingest tests
 *
 * AC-8  : Pi push（pushToCommand）自動附加正確 X-ICS-* headers
 * AC-8b : 收到 401 reason=replay → 自動換新 nonce 重試一次，最終成功
 *
 * 執行：node --test server/__tests__/trusted_ingest.test.js
 *
 * 技術備忘：
 *   config.js 在模組載入時檢查 --unit argv；測試在 require 前注入 '--unit shelter'。
 *   db.js 在載入時開啟 SQLite；測試透過 DB_PATH env var 指向 tmp 檔案。
 *   HMAC 憑證透過暫時覆蓋 HOME env var + tmpDir 中的 .ics/ 檔案提供。
 */

const { test, before, after } = require('node:test');
const assert  = require('node:assert/strict');
const http    = require('node:http');
const fs      = require('node:fs');
const path    = require('node:path');
const os      = require('node:os');
const crypto  = require('node:crypto');

const REPO_ROOT = path.resolve(__dirname, '../..');

// ── 測試用 HMAC 憑證 ──────────────────────────────────────────────────────────

const TEST_SECRET = crypto.randomBytes(32).toString('hex');  // 64 hex chars
const TEST_KEY_ID = crypto.randomUUID();

// ── 環境設置 ─────────────────────────────────────────────────────────────────

let tmpHome;
let tmpDbPath;
let origHome;
let origUserProfile;
let origDbPath;
let syncModule;     // loaded once in before()
let configModule;   // config.js（setCommandUrl 在此）

function clearServerCache() {
  const patterns = [
    'server/config', 'server\\config',
    'server/sync',   'server\\sync',
    'server/db',     'server\\db',
    'server/logger', 'server\\logger',
    'server/migrations', 'server\\migrations',
    'server/first_run',  'server\\first_run',
  ];
  for (const k of Object.keys(require.cache)) {
    if (patterns.some(p => k.includes(p))) {
      delete require.cache[k];
    }
  }
}

before(() => {
  // 1. 建立 tmp HOME 並寫入測試 HMAC 憑證
  tmpHome = fs.mkdtempSync(path.join(os.tmpdir(), 'ics-ti01-'));
  const icsDir = path.join(tmpHome, '.ics');
  fs.mkdirSync(icsDir, { recursive: true });
  fs.writeFileSync(path.join(icsDir, 'hmac_secret'), TEST_SECRET, 'utf8');
  fs.writeFileSync(path.join(icsDir, 'hmac_key_id'), TEST_KEY_ID, 'utf8');

  // 2. 暫存並覆寫 env
  origHome        = process.env.HOME;
  origUserProfile = process.env.USERPROFILE;
  origDbPath      = process.env.DB_PATH;
  process.env.HOME        = tmpHome;
  process.env.USERPROFILE = tmpHome;  // Windows

  // 3. 建立 tmp DB（better-sqlite3 會自動建立空檔案）
  tmpDbPath = path.join(os.tmpdir(), `ics-ti01-${crypto.randomBytes(4).toString('hex')}.db`);
  process.env.DB_PATH = tmpDbPath;

  // 4. 在 require 前注入 --unit shelter（config.js 需要）
  process.argv.push('--unit', 'shelter');

  // 5. 清 cache 並載入 config.js + sync.js（帶 tmpHome + tmpDbPath）
  clearServerCache();
  configModule = require(path.join(REPO_ROOT, 'server', 'config.js'));
  syncModule   = require(path.join(REPO_ROOT, 'server', 'sync.js'));
});

after(() => {
  // 還原 env
  if (origHome      !== undefined) process.env.HOME        = origHome;
  if (origUserProfile !== undefined) process.env.USERPROFILE = origUserProfile;
  if (origDbPath    !== undefined) process.env.DB_PATH     = origDbPath;
  else delete process.env.DB_PATH;

  // 移除 --unit shelter（防止污染後續測試）
  const idx = process.argv.indexOf('--unit');
  if (idx !== -1) process.argv.splice(idx, 2);

  // 清 cache
  clearServerCache();

  // 刪除 tmp 目錄 + DB
  try { fs.rmSync(tmpHome,   { recursive: true, force: true }); } catch { /* 忽略 */ }
  try { fs.unlinkSync(tmpDbPath); } catch { /* 忽略 */ }
});

// ── Mock HTTP server 工具 ──────────────────────────────────────────────────

/**
 * 建立 mock HTTP server。
 * @param {Function} handler (req, bodyStr) => { status, body }
 * @returns {Promise<{port, close}>}
 */
function createMockServer(handler) {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      let raw = '';
      req.on('data', c => { raw += c; });
      req.on('end', () => {
        const result = handler(req, raw);
        const statusCode = result.status || 200;
        const body = typeof result.body === 'string'
          ? result.body
          : JSON.stringify(result.body);
        res.writeHead(statusCode, { 'Content-Type': 'application/json' });
        res.end(body);
      });
    });
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      resolve({
        port,
        close: () => new Promise(r => server.close(r)),
      });
    });
  });
}

// ── Canonical string 驗算（Decision-2，與 sync.js 一致）──────────────────────

function queryCanonical(qs) {
  if (!qs) return '';
  return qs.split('&')
    .map(p => p.split('='))
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([k, v]) => `${k}=${v ?? ''}`)
    .join('&');
}

function verifyHmacHeaders(headers, bodyStr, method, urlStr) {
  const keyId = headers['x-ics-key-id'];
  const ts    = headers['x-ics-timestamp'];
  const nonce = headers['x-ics-nonce'];
  const sig   = headers['x-ics-signature'];

  if (!keyId || !ts || !nonce || !sig) {
    return { ok: false, reason: `missing headers: key=${keyId} ts=${ts} nonce=${nonce} sig=${sig}` };
  }
  if (keyId !== TEST_KEY_ID) {
    return { ok: false, reason: `key_id mismatch: got=${keyId} expected=${TEST_KEY_ID}` };
  }

  const urlObj   = new URL(urlStr);
  const bodyBuf  = Buffer.from(bodyStr, 'utf8');
  const bodyHash = crypto.createHash('sha256').update(bodyBuf).digest('hex');
  const canonical = [
    method.toUpperCase(),
    urlObj.pathname,
    queryCanonical(urlObj.search.replace(/^\?/, '')),
    ts,
    nonce,
    bodyHash,
  ].join('\n');

  const expected = crypto
    .createHmac('sha256', TEST_SECRET)
    .update(canonical)
    .digest('hex');

  if (expected !== sig) {
    return { ok: false, reason: `signature mismatch\n  canonical=${JSON.stringify(canonical)}\n  expected=${expected}\n  got=${sig}` };
  }
  return { ok: true };
}

// ─────────────────────────────────────────────────────────────────────────────
// AC-8：Pi push 自動附加正確 X-ICS-* headers
// ─────────────────────────────────────────────────────────────────────────────

test('pi_push_attaches_valid_hmac_headers', async (_t) => {
  const { pushToCommand } = syncModule;
  const { setCommandUrl } = configModule;

  let capturedReq  = null;
  let capturedBody = '';

  const mock = await createMockServer((req, body) => {
    capturedReq  = req;
    capturedBody = body;
    return { status: 200, body: { ok: true } };
  });

  setCommandUrl(`http://127.0.0.1:${mock.port}`);

  await pushToCommand({
    snapshot_id: 'ti01-hmac-test-001',
    v: 1, type: 'shelter',
    t: new Date().toISOString(),
    src: 'pi-test',
  });
  await mock.close();
  setCommandUrl('');  // 避免汙染下一個 test

  // mock server 應收到請求
  assert.ok(capturedReq !== null, 'mock server 未收到請求（確認 setCommandUrl 是否生效）');

  // 四個 X-ICS-* headers 均應存在
  const h = capturedReq.headers;
  assert.ok(h['x-ics-key-id'],    'X-ICS-Key-Id 應存在');
  assert.ok(h['x-ics-timestamp'], 'X-ICS-Timestamp 應存在');
  assert.ok(h['x-ics-nonce'],     'X-ICS-Nonce 應存在');
  assert.ok(h['x-ics-signature'], 'X-ICS-Signature 應存在');

  // 驗算簽名
  const result = verifyHmacHeaders(
    h, capturedBody, 'POST',
    `http://127.0.0.1:${mock.port}/api/snapshots`,
  );
  assert.ok(result.ok, `AC-8 HMAC 驗算失敗：${result.reason}`);
});

// ─────────────────────────────────────────────────────────────────────────────
// AC-8b：收到 401 reason=replay → 換新 nonce 重試，最終成功
// ─────────────────────────────────────────────────────────────────────────────

test('pi_push_retry_uses_new_nonce_after_401_replay', async (_t) => {
  const { pushToCommand } = syncModule;
  const { setCommandUrl } = configModule;

  const receivedNonces = [];
  let   callCount = 0;

  const mock = await createMockServer((req, _body) => {
    callCount++;
    const nonce = req.headers['x-ics-nonce'];
    if (nonce) receivedNonces.push(nonce);

    if (callCount === 1) {
      // 第一次：回 401 replay
      return { status: 401, body: { detail: { reason: 'replay' } } };
    }
    // 第二次：成功
    return { status: 200, body: { ok: true } };
  });

  setCommandUrl(`http://127.0.0.1:${mock.port}`);

  await pushToCommand({
    snapshot_id: 'ti01-retry-test-001',
    v: 1, type: 'shelter',
    t: new Date().toISOString(),
    src: 'pi-test',
  });
  await mock.close();
  setCommandUrl('');

  // 共發出兩次請求
  assert.equal(callCount, 2,
    `AC-8b：應重試一次（共 2 次），實際：${callCount}`);

  // 兩次請求的 nonce 不同
  assert.equal(receivedNonces.length, 2,
    `應捕捉到 2 個 nonce，實際：${receivedNonces.length}`);
  assert.notEqual(receivedNonces[0], receivedNonces[1],
    `AC-8b：重試時應產生新 nonce，兩次相同：${receivedNonces[0]}`);
});
