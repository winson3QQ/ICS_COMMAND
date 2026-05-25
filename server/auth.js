'use strict';

const crypto = require('crypto');
const { db }  = require('./db');

const LOGIN_WINDOW_MS = 30 * 60 * 1000;
const LOGIN_MAX_FAIL  = 5;

function hashPin(pin, saltHex) {
  return new Promise((resolve, reject) => {
    crypto.pbkdf2(pin, Buffer.from(saltHex, 'hex'), 200000, 32, 'sha256', (err, key) => {
      if (err) reject(err);
      else resolve(key.toString('hex'));
    });
  });
}

function getAdminPinHash() {
  const row = db.prepare("SELECT value FROM config WHERE key='admin_pin_hash'").get();
  return row ? row.value : null;
}

function getAdminPinSalt() {
  const row = db.prepare("SELECT value FROM config WHERE key='admin_pin_salt'").get();
  return row ? row.value : null;
}

function isLoginLocked(username) {
  const windowISO = new Date(Date.now() - LOGIN_WINDOW_MS).toISOString();
  const { cnt } = db.prepare(
    `SELECT COUNT(*) as cnt FROM login_failures WHERE username=? AND failed_at >= ?`
  ).get(username, windowISO);
  return cnt >= LOGIN_MAX_FAIL;
}

function recordLoginFailure(username) {
  const windowISO = new Date(Date.now() - LOGIN_WINDOW_MS).toISOString();
  db.prepare(`DELETE FROM login_failures WHERE username=? AND failed_at < ?`).run(username, windowISO);
  db.prepare(`INSERT INTO login_failures(username, failed_at) VALUES(?,?)`).run(username, new Date().toISOString());
  const { cnt } = db.prepare(
    `SELECT COUNT(*) as cnt FROM login_failures WHERE username=? AND failed_at >= ?`
  ).get(username, windowISO);
  return cnt >= LOGIN_MAX_FAIL;
}

module.exports = { hashPin, getAdminPinHash, getAdminPinSalt, isLoginLocked, recordLoginFailure };
