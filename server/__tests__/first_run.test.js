'use strict';

// Layer 1 — Unit tests for server/first_run.js
// 純函式測試，不依賴 DB 或網路，每個 test < 50ms
// 執行：node --test server/__tests__/first_run.test.js

const { test, before, after, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const fs     = require('node:fs');
const path   = require('node:path');
const os     = require('node:os');
const crypto = require('node:crypto');

// 建立 tmp 目錄，覆寫 token 路徑（避免污染真實 ~/.ics/）
const tmpDir      = fs.mkdtempSync(path.join(os.tmpdir(), 'ics-unit-test-'));
const tokenPath   = path.join(tmpDir, 'first_run_token');
process.env.FIRST_RUN_TOKEN_PATH = tokenPath;

const {
  generateToken,
  writeTokenFile,
  readTokenFile,
  tokenFileExists,
  checkAndFixPermissions,
  deleteTokenFile,
  verifyToken,
  validateAdminPassword,
  getTokenPath,
} = require('../first_run');

after(() => {
  try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch { /* 清理 */ }
});

beforeEach(() => {
  try { fs.unlinkSync(tokenPath); } catch { /* 不存在則略過 */ }
});

// ── generateToken ────────────────────────────────────────────────────────────

test('generateToken__length_ge_64_hex_chars', () => {
  const token = generateToken();
  assert.equal(typeof token, 'string');
  assert.ok(token.length >= 64, `token 長度 ${token.length} < 64`);
  assert.match(token, /^[0-9a-f]+$/i, 'token 應為 hex 字串');
});

test('generateToken__uniqueness', () => {
  const a = generateToken();
  const b = generateToken();
  assert.notEqual(a, b, '兩次呼叫結果不應相同');
});

// ── writeTokenFile / readTokenFile ───────────────────────────────────────────

test('writeTokenFile__creates_file_with_chmod_600', () => {
  if (process.platform === 'win32') {
    // Windows 不強制 Unix 權限，跳過
    return;
  }
  const token = generateToken();
  writeTokenFile(token);
  assert.ok(fs.existsSync(tokenPath), 'token 檔案應已建立');
  const mode = fs.statSync(tokenPath).mode & 0o777;
  assert.equal(mode, 0o600, `權限應為 0600，實際為 0${mode.toString(8)}`);
});

test('readTokenFile__returns_correct_content', () => {
  const token = generateToken();
  writeTokenFile(token);
  const read = readTokenFile();
  assert.equal(read, token, '讀回的 token 應與寫入相同');
});

test('readTokenFile__returns_null_when_no_file', () => {
  assert.equal(readTokenFile(), null, '不存在時應回傳 null');
});

// ── tokenFileExists ───────────────────────────────────────────────────────────

test('tokenFileExists__false_when_no_file', () => {
  assert.equal(tokenFileExists(), false);
});

test('tokenFileExists__true_after_write', () => {
  writeTokenFile(generateToken());
  assert.equal(tokenFileExists(), true);
});

// ── deleteTokenFile ───────────────────────────────────────────────────────────

test('deleteTokenFile__removes_file', () => {
  writeTokenFile(generateToken());
  assert.ok(tokenFileExists(), 'deleteTokenFile 前應存在');
  deleteTokenFile();
  assert.equal(tokenFileExists(), false, 'deleteTokenFile 後應不存在');
});

test('deleteTokenFile__no_throw_when_missing', () => {
  assert.doesNotThrow(() => deleteTokenFile(), '不存在時不應 throw');
});

// ── verifyToken ───────────────────────────────────────────────────────────────

test('verifyToken__correct_token_returns_true', () => {
  const token = generateToken();
  writeTokenFile(token);
  assert.equal(verifyToken(token), true);
});

test('verifyToken__wrong_token_returns_false', () => {
  writeTokenFile(generateToken());
  assert.equal(verifyToken('wrong_token_value'), false);
});

test('verifyToken__returns_false_when_no_file', () => {
  assert.equal(verifyToken('anything'), false, '無 token 檔時應回 false');
});

// ── checkAndFixPermissions ────────────────────────────────────────────────────

test('permissionCheck__auto_fix_non_600_to_600', () => {
  if (process.platform === 'win32') return; // Windows 無法測試 Unix 權限

  writeTokenFile(generateToken());
  fs.chmodSync(tokenPath, 0o644); // 故意改成非 600
  const before = fs.statSync(tokenPath).mode & 0o777;
  assert.equal(before, 0o644, '前置條件：應為 0644');

  const result = checkAndFixPermissions();
  assert.equal(result.fixed, true, 'fixed 應為 true');
  assert.equal(result.oldMode, '644', 'oldMode 應記錄原始值');

  const after = fs.statSync(tokenPath).mode & 0o777;
  assert.equal(after, 0o600, '修正後應為 0600');
});

// ── validateAdminPassword ─────────────────────────────────────────────────────

test('validateAdminPassword__rejects_length_lt_8', () => {
  const result = validateAdminPassword('Ab1!');
  assert.equal(result.ok, false);
  assert.match(result.reason, /長度/);
});

test('validateAdminPassword__rejects_no_uppercase', () => {
  const result = validateAdminPassword('abcde1!x');
  assert.equal(result.ok, false);
  assert.match(result.reason, /大寫/);
});

test('validateAdminPassword__rejects_no_lowercase', () => {
  const result = validateAdminPassword('ABCDE1!X');
  assert.equal(result.ok, false);
  assert.match(result.reason, /小寫/);
});

test('validateAdminPassword__rejects_no_digit', () => {
  const result = validateAdminPassword('Abcdefg!');
  assert.equal(result.ok, false);
  assert.match(result.reason, /數字/);
});

test('validateAdminPassword__rejects_no_special_char', () => {
  const result = validateAdminPassword('Abcde123');
  assert.equal(result.ok, false);
  assert.match(result.reason, /特殊符號/);
});

test('validateAdminPassword__accepts_valid_password', () => {
  const result = validateAdminPassword('Str0ng@Pass');
  assert.equal(result.ok, true, `有效密碼應通過，reason: ${result.reason}`);
});
