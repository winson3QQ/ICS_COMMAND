'use strict';

const http    = require('http');
const https   = require('https');
const express = require('express');

const { log }                    = require('./logger');
const { ADMIN_PORT, PROTOCOL, tlsOpts } = require('./config');
const { db }                     = require('./db');
const { wss, startWsServer }     = require('./ws_handler');
const { registerRoutes }         = require('./routes');
const { startPiPush }            = require('./sync');
const { getAdminPinHash }        = require('./auth');
const { writeAuditLog }          = require('./audit');
const {
  getTokenPath, tokenFileExists,
  generateToken, writeTokenFile,
  checkAndFixPermissions,
} = require('./first_run');

function initFirstRun() {
  if (getAdminPinHash()) return; // 已完成首次設定，gate 解除

  const tokenPath = getTokenPath();
  if (!tokenFileExists()) {
    const token = generateToken();
    writeTokenFile(token);
    writeAuditLog('first_run_token_issued', 'system', '', null, { token_path: tokenPath });
    log.info(`[FirstRun] first-run token 已寫入 ${tokenPath}（chmod 600）`);
    log.info(`[FirstRun] 部署 IT 請執行：sudo cat ${tokenPath}`);
  } else {
    const { fixed, oldMode } = checkAndFixPermissions();
    if (fixed) {
      writeAuditLog('first_run_token_permissions_fixed', 'system', '', null,
        { token_path: tokenPath, old_mode: oldMode });
      log.warn(`[FirstRun] token 檔權限已從 ${oldMode} 修正為 600`);
    }
    // 沿用現有 token（3a），不重印、不重生
  }
}

const app         = express();
registerRoutes(app);

const adminServer = tlsOpts
  ? https.createServer(tlsOpts, app)
  : http.createServer(app);

adminServer.listen(ADMIN_PORT, () => {
  log.info(`[Admin] v2.1 ${PROTOCOL.toUpperCase()} Listening on port ${ADMIN_PORT}`);
  initFirstRun();
  startPiPush();
});

startWsServer();

process.on('SIGTERM', () => { wss.close(); db.close(); process.exit(0); });
process.on('SIGINT',  () => { wss.close(); db.close(); process.exit(0); });
