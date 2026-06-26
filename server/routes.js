// SPDX-License-Identifier: LicenseRef-Proprietary
// Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
'use strict';

const path = require('path');
const fs   = require('fs');
const cors = require('cors');
const express = require('express');

const { log }  = require('./logger');
const logger   = require('./logger');   // C1-D 結構化日誌
const { cfg, PROTOCOL, getCommandUrl, setCommandUrl } = require('./config');
const { db, nowISO, newUUID, randomHex, getLastSyncToCommand } = require('./db');
const { hashPin, getAdminPinHash } = require('./auth');
const { writeAuditLog } = require('./audit');
const {
  verifyToken, validateAdminPassword,
  deleteTokenFile, getTokenPath,
} = require('./first_run');
const { adminAuth } = require('./middleware');
const { clients } = require('./ws_handler');
const { startPiPush, getPiApiKey } = require('./sync');

// First-run HTTP gate：setup 完成前阻擋非白名單路徑
const _FIRST_RUN_WHITELIST = new Set([
  'GET /admin/status',
  'GET /cert',
  'GET /cert/install',
  'GET /',
  'POST /admin/setup',
]);

function firstRunGate(req, res, next) {
  if (getAdminPinHash()) return next(); // 已完成首次設定
  const normalizedPath = req.path.replace(/\/+$/, '') || '/'; // 去尾部斜線，防 curl trailing slash
  if (_FIRST_RUN_WHITELIST.has(`${req.method} ${normalizedPath}`)) return next();
  return res.status(423).json({ ok: false, reason: '首次設定未完成', code: 'FIRST_RUN_REQUIRED' });
}

function registerRoutes(app) {
  app.use(cors({ origin: '*' }));
  app.use(express.json());

  const PUBLIC_DIR = process.env.PUBLIC_DIR || path.resolve(cfg.publicDir);
  if (fs.existsSync(PUBLIC_DIR)) {
    app.use(express.static(PUBLIC_DIR));
    log.info(`[Static] 提供 PWA 靜態檔案：${PUBLIC_DIR}`);
  }

  // ⭐ W-C1-A (Option δ, Step A Amendment v2): /lib static route
  //   PWA 端 <script src="/lib/ws-signing.js"></script> 載入 SDK
  //   server/sdk/ 為 source-of-truth, Pi server static-served at /lib/
  //   對齊紅線 D-1/D-2: 不引入新 npm package
  const SDK_DIR = path.resolve(__dirname, 'sdk');
  if (fs.existsSync(SDK_DIR)) {
    app.use('/lib', express.static(SDK_DIR));
    log.info(`[Static] 提供 PWA SDK：${SDK_DIR} → /lib/`);
  }

  app.use(firstRunGate); // 靜態檔案之後、所有 route 之前

  app.get('/', (req, res) => {
    const pwaPath   = path.join(PUBLIC_DIR, cfg.pwaHtml);
    const adminPath = path.resolve(__dirname, '..', 'admin_v2.0.html');
    if (fs.existsSync(pwaPath))   return res.sendFile(pwaPath);
    if (fs.existsSync(adminPath)) return res.sendFile(adminPath);
    res.send('<h1>admin_v2.0.html 未找到</h1>');
  });

  /* ── 管理員首次設定（需 first-run token + admin password）── */
  app.post('/admin/setup', async (req, res) => {
    if (getAdminPinHash()) return res.status(400).json({ ok: false, reason: '管理員 PIN 已設定' });

    const { first_run_token, admin_password } = req.body || {};

    // 明確區分「欄位不存在」(401) 與「欄位存在但錯誤」(403)
    if (first_run_token === undefined || first_run_token === null || first_run_token === '')
      return res.status(401).json({ ok: false, reason: '缺少 first_run_token' });
    if (!verifyToken(first_run_token))
      return res.status(403).json({ ok: false, reason: 'token 無效或已過期' });

    const validation = validateAdminPassword(admin_password);
    if (!validation.ok)
      return res.status(400).json({ ok: false, reason: validation.reason });

    const salt = randomHex(16);
    const hash = await hashPin(admin_password, salt);
    db.prepare("INSERT OR REPLACE INTO config(key,value) VALUES('admin_pin_hash',?)").run(hash);
    db.prepare("INSERT OR REPLACE INTO config(key,value) VALUES('admin_pin_salt',?)").run(salt);

    deleteTokenFile();
    writeAuditLog('first_run_token_consumed', 'system', '', null,
      { token_path: getTokenPath() });
    writeAuditLog('admin_pin_initialized', 'system', '', null, {});

    res.json({ ok: true, message: '管理員設定成功' });
  });

  /* ── 帳號管理 ── */
  app.get('/admin/accounts', adminAuth, (req, res) => {
    const accounts = db.prepare(
      'SELECT id,username,role,status,created_at,created_by,last_login,device_id FROM accounts ORDER BY created_at'
    ).all();
    res.json({ ok: true, accounts });
  });

  app.post('/admin/accounts', adminAuth, async (req, res) => {
    const { username, role, pin, created_by } = req.body;
    if (!username || !role || !pin) return res.status(400).json({ ok: false, reason: '缺少必填欄位' });
    if (!cfg.roles.includes(role)) return res.status(400).json({ ok: false, reason: '角色不在允許清單' });
    if (!/^\d{4,6}$/.test(pin))   return res.status(400).json({ ok: false, reason: 'PIN 格式不符' });
    if (db.prepare('SELECT id FROM accounts WHERE username=?').get(username))
      return res.status(400).json({ ok: false, reason: '帳號名稱已存在' });
    const salt = randomHex(16);
    const hash = await hashPin(pin, salt);
    const id   = newUUID();
    db.prepare(`INSERT INTO accounts(id,username,role,pin_hash,pin_salt,status,created_at,created_by) VALUES(?,?,?,?,?,?,?,?)`)
      .run(id, username, role, hash, salt, 'active', nowISO(), created_by || 'admin');
    writeAuditLog('account_created', created_by || 'admin', '', null, { target: username, role });
    res.json({ ok: true, id, username, role });
  });

  app.put('/admin/accounts/:username/status', adminAuth, (req, res) => {
    const { username } = req.params;
    const { status, updated_by } = req.body;
    if (!['active', 'suspended'].includes(status))
      return res.status(400).json({ ok: false, reason: 'status 格式不符' });
    if (!db.prepare('SELECT id FROM accounts WHERE username=?').get(username))
      return res.status(404).json({ ok: false, reason: '帳號不存在' });
    db.prepare('UPDATE accounts SET status=? WHERE username=?').run(status, username);
    writeAuditLog('account_status_changed', updated_by || 'admin', '', null, { target: username, new_status: status });
    res.json({ ok: true, username, status });
  });

  app.put('/admin/accounts/:username/pin', adminAuth, async (req, res) => {
    const { username } = req.params;
    const { new_pin, updated_by } = req.body;
    if (!new_pin || !/^\d{4,6}$/.test(new_pin))
      return res.status(400).json({ ok: false, reason: 'PIN 格式不符' });
    if (!db.prepare('SELECT id FROM accounts WHERE username=?').get(username))
      return res.status(404).json({ ok: false, reason: '帳號不存在' });
    const salt = randomHex(16);
    const hash = await hashPin(new_pin, salt);
    db.prepare('UPDATE accounts SET pin_hash=?, pin_salt=? WHERE username=?').run(hash, salt, username);
    writeAuditLog('pin_reset', updated_by || 'admin', '', null, { target: username });
    res.json({ ok: true, username, message: 'PIN 已重設' });
  });

  app.delete('/admin/accounts/:username', adminAuth, (req, res) => {
    const { username } = req.params;
    const { deleted_by } = req.body || {};
    if (!db.prepare('SELECT id FROM accounts WHERE username=?').get(username))
      return res.status(404).json({ ok: false, reason: '帳號不存在' });
    db.prepare('DELETE FROM accounts WHERE username=?').run(username);
    writeAuditLog('account_deleted', deleted_by || 'admin', '', null, { target: username });
    res.json({ ok: true, username });
  });

  app.put('/admin/accounts/:username/role', adminAuth, (req, res) => {
    const { username } = req.params;
    const { role, updated_by } = req.body || {};
    if (!cfg.roles.includes(role)) return res.status(400).json({ ok: false, reason: '角色不在允許清單' });
    if (!db.prepare('SELECT id FROM accounts WHERE username=?').get(username))
      return res.status(404).json({ ok: false, reason: '帳號不存在' });
    db.prepare('UPDATE accounts SET role=? WHERE username=?').run(role, username);
    writeAuditLog('account_role_changed', updated_by || 'admin', '', null, { target: username, new_role: role });
    res.json({ ok: true, username, role });
  });

  app.post('/admin/accounts/suspend-all', adminAuth, (req, res) => {
    const { suspended_by } = req.body;
    const result = db.prepare("UPDATE accounts SET status='suspended'").run();
    writeAuditLog('all_accounts_suspended', suspended_by || 'admin', '', null, { count: result.changes });
    res.json({ ok: true, suspended_count: result.changes });
  });

  /* ── 稽核日誌查詢 ── */
  app.get('/admin/audit-log', adminAuth, (req, res) => {
    const limit  = parseInt(req.query.limit  || '100');
    const offset = parseInt(req.query.offset || '0');
    const rows   = db.prepare('SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ? OFFSET ?').all(limit, offset);
    const total  = db.prepare('SELECT COUNT(*) as cnt FROM audit_log').get().cnt;
    res.json({ ok: true, logs: rows, total, limit, offset });
  });

  /* ── 狀態查詢 ── */
  app.get('/admin/sync-status', adminAuth, (req, res) => {
    res.json({
      ok: true,
      last_sync_to_command: getLastSyncToCommand(),
      pi_time:              nowISO(),
      connected_clients:    clients.size,
    });
  });

  app.get('/admin/status', (req, res) => {
    const accountCount = db.prepare("SELECT COUNT(*) as cnt FROM accounts WHERE status='active'").get().cnt;
    const deltaCount   = db.prepare("SELECT COUNT(*) as cnt FROM delta_log").get().cnt;
    res.json({
      ok: true,
      server_version:       '2.1',
      pi_time:              nowISO(),
      active_accounts:      accountCount,
      delta_log_count:      deltaCount,
      connected_clients:    clients.size,
      admin_pin_setup:      !!getAdminPinHash(),
      last_sync_to_command: getLastSyncToCommand(),
      command_url:          getCommandUrl() || null,
    });
  });

  /* ── CA 憑證下載 ── */
  app.get('/cert', (req, res) => {
    const caPath = path.resolve(__dirname, '..', 'certs', 'rootCA.pem');
    if (!fs.existsSync(caPath))
      return res.status(404).send('rootCA.pem 未找到。請先將憑證部署到 certs/ 目錄。');
    res.download(caPath, 'rootCA.pem');
  });

  app.get('/cert/install', (req, res) => {
    res.send(`<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>安裝 CA 憑證</title>
<style>
  body{font-family:-apple-system,sans-serif;max-width:600px;margin:40px auto;padding:0 20px;line-height:1.6}
  h1{font-size:1.4em}
  .btn{display:inline-block;padding:14px 28px;background:#c0392b;color:#fff;text-decoration:none;border-radius:8px;font-size:1.1em;margin:20px 0}
  .step{background:#f5f5f5;padding:12px 16px;margin:8px 0;border-radius:6px}
  .num{font-weight:bold;color:#c0392b}
</style></head><body>
<h1>ICS_DMAS CA 憑證安裝</h1>
<p>安裝此憑證後，裝置才能信任本系統的 HTTPS/WSS 連線。</p>
<a class="btn" href="/cert">下載 CA 憑證</a>
<h2>iOS 安裝步驟</h2>
<div class="step"><span class="num">1.</span> 點擊上方按鈕下載</div>
<div class="step"><span class="num">2.</span> 設定 → 已下載描述檔 → 安裝</div>
<div class="step"><span class="num">3.</span> 設定 → 一般 → 關於本機 → 憑證信任設定 → 開啟信任</div>
<h2>Android 安裝步驟</h2>
<div class="step"><span class="num">1.</span> 點擊上方按鈕下載</div>
<div class="step"><span class="num">2.</span> 設定 → 安全性 → 加密與憑證 → 安裝憑證 → CA 憑證</div>
<div class="step"><span class="num">3.</span> 選擇下載的 rootCA.pem</div>
</body></html>`);
  });

  /* ── 指揮部 URL 設定 ── */
  app.get('/admin/command-url', adminAuth, (req, res) => {
    res.json({ ok: true, command_url: getCommandUrl() || null });
  });

  app.post('/admin/command-url', adminAuth, async (req, res) => {
    const { url } = req.body || {};
    if (!url || !url.startsWith('https://'))
      return res.status(400).json({ ok: false, error: '格式錯誤，範例：https://192.168.1.100:8000（必須使用 HTTPS）' });
    const cleaned = url.replace(/\/$/, '');
    setCommandUrl(cleaned);
    db.prepare("INSERT OR REPLACE INTO config(key,value) VALUES('command_url',?)").run(cleaned);
    log.debug(`[Config] command_url set to: ${cleaned}`);
    startPiPush();
    try {
      const r    = await fetch(`${cleaned}/api/health`, { signal: AbortSignal.timeout(5000) });
      const body = await r.json();
      res.json({ ok: true, command_url: cleaned, health: body });
    } catch (err) {
      logger.error('pi_sync_failed', { msg: err.message,
        detail: { phase: 'command_url_health_check' } });
      res.json({ ok: true, command_url: cleaned, health_error: err.message });
    }
  });

  /* ── Pi API Key 管理 ── */
  app.get('/admin/pi-api-key', adminAuth, (req, res) => {
    const key = getPiApiKey();
    res.json({ ok: true, has_key: !!key, key_suffix: key ? key.slice(-8) : null });
  });

  app.post('/admin/pi-api-key', adminAuth, (req, res) => {
    const { api_key } = req.body || {};
    if (!api_key || api_key.length < 16)
      return res.status(400).json({ ok: false, error: 'api_key 至少 16 字元' });
    db.prepare("INSERT OR REPLACE INTO config(key,value) VALUES('pi_api_key',?)").run(api_key);
    log.info('[Config] pi_api_key 已設定');
    startPiPush();
    res.json({ ok: true });
  });
}

module.exports = { registerRoutes };
