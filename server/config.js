// SPDX-License-Identifier: LicenseRef-Proprietary
// Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
'use strict';

const path = require('path');
const fs   = require('fs');
const os   = require('os');
const { log } = require('./logger');

const SERVER_VERSION = 'v1.5.0';

const UNIT_CONFIGS = {
  shelter: {
    wsPort: 8765, adminPort: 8766,
    dbPath: './shelter-pwa/shelter_accounts.db',
    publicDir: './shelter-pwa/public',
    unitId: 'shelter', deviceId: 'shelter_pi',
    logPrefix: 'Shelter',
    pwaHtml: 'shelter_pwa.html',
    roles: ['組長', '一般'],
    defaultRole: '一般',
    roleMigration: "UPDATE accounts SET role='一般' WHERE role IN ('人管','物管','環管')",
    syncTables: ['persons', 'beds', 'resources', 'incidents', 'shifts'],
  },
  medical: {
    wsPort: 8775, adminPort: 8776,
    dbPath: './medical-pwa/medical_accounts.db',
    publicDir: './medical-pwa/public',
    unitId: 'medical', deviceId: 'medical_pi',
    logPrefix: 'Medical',
    pwaHtml: 'medical_pwa.html',
    roles: ['組長', '檢傷官', '治療官', '後送官', '後勤官'],
    defaultRole: '檢傷官',
    roleMigration: null,
    syncTables: ['patients', 'triages', 'incidents', 'shifts'],
  },
};

const unitArg = process.argv.find((a, i) => i > 0 && process.argv[i - 1] === '--unit');
if (!unitArg || !UNIT_CONFIGS[unitArg]) {
  console.error(`用法: node server/index.js --unit <${Object.keys(UNIT_CONFIGS).join('|')}>`);
  process.exit(1);
}
const cfg = UNIT_CONFIGS[unitArg];

const WS_PORT    = parseInt(process.env.WS_PORT)    || cfg.wsPort;
const ADMIN_PORT = parseInt(process.env.ADMIN_PORT) || cfg.adminPort;
const DB_PATH    = process.env.DB_PATH || path.resolve(cfg.dbPath);

const DELTA_LOG_MAX       = 1000;
const PI_PUSH_INTERVAL_MS = parseInt(process.env.PI_PUSH_INTERVAL_MS || '') || 5_000;
const MAX_QUEUE_AGE_MS    = 24 * 60 * 60 * 1000;

const CERT_PATH   = process.env.CERT_PATH || '';
const KEY_PATH    = process.env.KEY_PATH  || '';
// STRICT_TLS=true：沒憑證直接 fail-fast；生產環境（systemd unit）必須設 true
const STRICT_TLS  = (process.env.STRICT_TLS || '').toLowerCase() === 'true';
const _caCertPath = process.env.CA_CERT_PATH ||
  (CERT_PATH ? path.join(path.dirname(CERT_PATH), 'rootCA.pem') : '');
const CA_CERT = (_caCertPath && fs.existsSync(_caCertPath))
  ? (() => { try { return fs.readFileSync(_caCertPath); } catch { return null; } })()
  : null;
if (CA_CERT) log.info(`[TLS] 指揮部推送 CA=${_caCertPath}`);

function loadTlsOptions() {
  if (!CERT_PATH || !KEY_PATH) {
    if (STRICT_TLS) {
      log.error(`[TLS] STRICT_TLS=true 但未提供 CERT_PATH/KEY_PATH，拒絕啟動`);
      process.exit(1);
    }
    return null;
  }
  try {
    return { cert: fs.readFileSync(CERT_PATH), key: fs.readFileSync(KEY_PATH) };
  } catch (e) {
    if (STRICT_TLS) {
      log.error(`[TLS] STRICT_TLS=true 但憑證載入失敗：${e.message}，拒絕啟動`);
      process.exit(1);
    }
    log.warn(`[TLS] 憑證載入失敗：${e.message}，退回 HTTP`);
    return null;
  }
}
const tlsOpts    = loadTlsOptions();
const PROTOCOL    = tlsOpts ? 'https' : 'http';
const WS_PROTOCOL = tlsOpts ? 'wss'   : 'ws';
if (STRICT_TLS && tlsOpts) log.info(`[TLS] STRICT_TLS=true，已載入憑證`);

// command_url 可在執行期被 admin API 更新，用 getter/setter 封裝可變狀態
let _commandUrl = process.env.COMMAND_URL || '';
const getCommandUrl = ()    => _commandUrl;
const setCommandUrl = (url) => { _commandUrl = url; };

// ── TI-01 HMAC 憑證（Pi 端）──────────────────────────────────────────────
// 路徑: ~/.ics/hmac_secret (hex string, 64 chars) chmod 0600
//       ~/.ics/hmac_key_id (UUID v4 plaintext)    chmod 0600
// 產生時機: first-run（需 IT 手動將 key_id + secret 輸入 Command 端 trusted_keys）
// 若檔案不存在，HMAC 功能停用並在每次 push 時 console.warn

function loadHmacCredentials() {
  const secretPath = path.join(os.homedir(), '.ics', 'hmac_secret');
  const keyIdPath  = path.join(os.homedir(), '.ics', 'hmac_key_id');
  try {
    const secret = fs.readFileSync(secretPath, 'utf8').trim();
    const keyId  = fs.readFileSync(keyIdPath,  'utf8').trim();
    if (!secret || !keyId) throw new Error('empty credentials');
    return { secret, keyId };
  } catch (e) {
    log.warn(`[TI-01] HMAC credentials not found: ${e.message} — ingest push will be rejected by Command dashboard`);
    return { secret: null, keyId: null };
  }
}

const HMAC_CREDENTIALS = loadHmacCredentials();

log.info(`${cfg.logPrefix} WS Server ${SERVER_VERSION} | unit=${unitArg} | Log level: ${process.env.LOG_LEVEL || 'debug'}`);

module.exports = {
  SERVER_VERSION, cfg, unitArg,
  WS_PORT, ADMIN_PORT, DB_PATH,
  DELTA_LOG_MAX, PI_PUSH_INTERVAL_MS, MAX_QUEUE_AGE_MS,
  CERT_PATH, KEY_PATH, CA_CERT, tlsOpts, PROTOCOL, WS_PROTOCOL,
  getCommandUrl, setCommandUrl,
  HMAC_SECRET: HMAC_CREDENTIALS.secret,
  HMAC_KEY_ID: HMAC_CREDENTIALS.keyId,
};
