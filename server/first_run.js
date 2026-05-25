'use strict';

const crypto = require('crypto');
const fs     = require('fs');
const path   = require('path');
const os     = require('os');

function getTokenPath() {
  if (process.env.FIRST_RUN_TOKEN_PATH) return process.env.FIRST_RUN_TOKEN_PATH;
  return path.join(os.homedir(), '.ics', 'first_run_token');
}

function generateToken() {
  return crypto.randomBytes(32).toString('hex'); // 256 bits，64 hex chars
}

function writeTokenFile(token) {
  const tokenPath = getTokenPath();
  fs.mkdirSync(path.dirname(tokenPath), { recursive: true });
  const tmp = tokenPath + '.tmp.' + process.pid;
  try {
    fs.writeFileSync(tmp, token, { encoding: 'utf8', mode: 0o600 });
    fs.chmodSync(tmp, 0o600);
    fs.renameSync(tmp, tokenPath);
    fs.chmodSync(tokenPath, 0o600); // rename 後再確認（umask 影響）
  } catch (err) {
    try { fs.unlinkSync(tmp); } catch { /* 清理 tmp */ }
    throw err;
  }
}

function readTokenFile() {
  try {
    return fs.readFileSync(getTokenPath(), 'utf8').trim();
  } catch {
    return null;
  }
}

function tokenFileExists() {
  return fs.existsSync(getTokenPath());
}

function checkAndFixPermissions() {
  const tokenPath = getTokenPath();
  let stat;
  try { stat = fs.statSync(tokenPath); } catch { return { fixed: false }; }
  const mode = stat.mode & 0o777;
  if (mode !== 0o600) {
    fs.chmodSync(tokenPath, 0o600);
    return { fixed: true, oldMode: mode.toString(8).padStart(3, '0') };
  }
  return { fixed: false };
}

function deleteTokenFile() {
  try { fs.unlinkSync(getTokenPath()); } catch { /* 已不存在 */ }
}

function verifyToken(candidate) {
  if (!candidate) return false;
  const stored = readTokenFile();
  if (!stored) return false;
  const a = Buffer.from(stored,         'utf8');
  const b = Buffer.from(String(candidate), 'utf8');
  if (a.length !== b.length) return false;
  return crypto.timingSafeEqual(a, b);
}

const _SPECIAL = /[!@#$%^&*()\-_=+[\]{};':"\\|,.<>/?`~]/;

function validateAdminPassword(password) {
  if (typeof password !== 'string' || password.length < 8)
    return { ok: false, reason: '密碼長度至少 8 字元' };
  if (!/[A-Z]/.test(password))
    return { ok: false, reason: '密碼須含至少 1 個大寫英文字母' };
  if (!/[a-z]/.test(password))
    return { ok: false, reason: '密碼須含至少 1 個小寫英文字母' };
  if (!/[0-9]/.test(password))
    return { ok: false, reason: '密碼須含至少 1 個數字' };
  if (!_SPECIAL.test(password))
    return { ok: false, reason: '密碼須含至少 1 個特殊符號（如 !@#$%^&*）' };
  return { ok: true };
}

module.exports = {
  getTokenPath,
  generateToken,
  writeTokenFile,
  readTokenFile,
  tokenFileExists,
  checkAndFixPermissions,
  deleteTokenFile,
  verifyToken,
  validateAdminPassword,
};
