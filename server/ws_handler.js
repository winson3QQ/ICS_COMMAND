// SPDX-License-Identifier: LicenseRef-Proprietary
// Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
'use strict';

const crypto    = require('crypto');
const http      = require('http');
const https     = require('https');
const WebSocket = require('ws');

const { log }    = require('./logger');
const logger     = require('./logger');   // C1-D 結構化日誌
const { cfg, tlsOpts, WS_PORT, WS_PROTOCOL, HMAC_SECRET } = require('./config');
const { db, nowISO, appendDelta, getRecentDeltas, getLastSyncToCommand, updateLastSyncToCommand } = require('./db');
const { writeAuditLog } = require('./audit');
const { hashPin, isLoginLocked, recordLoginFailure, getAdminPinHash } = require('./auth');
const { getSiteSalt } = require('./db');
const { piPushOnce, getCommandStatus, setBroadcast } = require('./sync');
const { verifyMessage, startNonceCleanup } = require('./ws_signing');

const wsRawServer = tlsOpts ? https.createServer(tlsOpts) : http.createServer();
const wss         = new WebSocket.Server({ server: wsRawServer, perMessageDeflate: false });
const clients     = new Map();

// ── WS Full Security (C1-G) 常數 ────────────────────────────────────────────
// 對齊 Step A Approval REV2 frozen decisions

// W-1 type whitelist (對齊 F-11/F-13: 列在 ws_handler.js 常數定義, 非外部 config)
//   10 incoming types, 對齊 D-1 grep 結果
const WS_ALLOWED_TYPES = new Set([
  'auth', 'delta', 'debug_ping', 'session_restore', 'catchup_req',
  'sync_push', 'time_sync_req', 'audit_event', 'clear_table', 'ping',
]);

// HMAC 不需簽章的 type (對齊 H-4 / F-5: PRE_AUTH_ALLOWED + heartbeat)
//   session_restore 在 Layer 1 永遠 close, 不到 HMAC verify
//   其他 5 個 (delta / sync_push / audit_event / clear_table / catchup_req) 需 HMAC
const HMAC_NOT_REQUIRED = new Set(['auth', 'ping', 'time_sync_req', 'debug_ping']);

// R-2 PII fields (對齊 F-15: Observer 不收)
const PII_FIELDS = ['symptom', 'allergy', 'medication', 'patient_id'];

// WS close codes (對齊 WS-01 既有 4xxx pattern; 4400-4499 application-defined)
//   既有 (WS-01): 4401 unauthorized / 4423 setup_required
//   新增 (本 task):
const WS_CLOSE_UNKNOWN_TYPE      = 4400;  // 對齊 W-2
const WS_CLOSE_SIGNATURE_INVALID = 4406;  // 對齊 F-10 篡改 payload
const WS_CLOSE_NONCE_REPLAY      = 4408;  // 對齊 F-10 重放 nonce

function _stripPii(record) {
  if (!record || typeof record !== 'object') return record;
  const out = { ...record };
  for (const f of PII_FIELDS) delete out[f];
  return out;
}

function _filterForObserver(msgObj) {
  // 對齊 F-15: 只對含 record 的訊息 (delta) 過濾 PII
  if (!msgObj || typeof msgObj !== 'object' || !msgObj.record) return msgObj;
  return { ...msgObj, record: _stripPii(msgObj.record) };
}

// 對齊 R-3 / F-16: filter 在 broadcast 函式內實作 (per-client role check)
//   originatingWs: 若指定, 此 client 不收 (對齊既有 delta/sync_push relay 「不回送原 sender」邏輯)
//   觀察員 role 判定: 對齊 P2a 4-role enum 'observer' (lowercase, role_enum.py)
//   ⚠️ 註: Pi server 端目前無 'observer' role 連線 (PWA 用中文 role: 組長/一般/檢傷官/etc)
//          本 filter 是「未來護城河」: W-C1-A 改造 PWA role mapping 後啟用 (Human owner 2026-05-04 裁定 b)
function _broadcastWithFilter(msgObj, originatingWs) {
  const fullStr     = JSON.stringify(msgObj);
  const observerStr = JSON.stringify(_filterForObserver(msgObj));
  wss.clients.forEach(client => {
    if (originatingWs && client === originatingWs) return;
    if (client.readyState !== WebSocket.OPEN) return;
    const role = clients.get(client)?.role;
    client.send(role === 'observer' ? observerStr : fullStr);
  });
}

function broadcast(msgObj) {
  // Q10：WS message 加 correlation_id 同層欄位，不修改 type（不違反 WS-01 凍結）
  // R-3 / F-16: filter 在此函式內實作
  const enriched = { ...msgObj, correlation_id: crypto.randomUUID() };
  _broadcastWithFilter(enriched, null);
}
setBroadcast(broadcast);

wsRawServer.on('upgrade', (req, socket) => {
  const urlSrc = new URL(req.url, 'wss://localhost').searchParams.get('src') || '?';
  log.debug(`[WS] HTTP Upgrade from ${socket.remoteAddress} src=${urlSrc}`);
});

wss.on('connection', (ws, req) => {
  const ip     = req.socket.remoteAddress;
  const urlSrc = new URL(req.url, 'wss://localhost').searchParams.get('src') || '?';
  log.info(`[WS] Client connected from ${ip} src=${urlSrc}`);
  // 1. ws_connect（v1.1 §4 優先位置 1）
  logger.info('ws_connect', { msg: 'WebSocket client connected', detail: { ip, src: urlSrc } });

  ws.isAlive = true;
  ws.on('pong', () => { ws.isAlive = true; });
  const wsId = `ws_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;

  let _zombieTimer = setTimeout(() => {
    log.warn(`[WS] No message in 5s from ${ip} src=${urlSrc} → zombie suspected`);
  }, 5000);

  const _STATE_CHANGING = new Set(['delta', 'sync_push', 'session_restore', 'audit_event', 'clear_table', 'catchup_req']);

  ws.on('message', async (raw) => {
    if (_zombieTimer) { clearTimeout(_zombieTimer); _zombieTimer = null; }
    let msg;
    try { msg = JSON.parse(raw); } catch { return; }
    log.debug(`[WS] ← ${msg.type} from ${ip} ${msg.table || ''} ${msg.record?._id ?? msg.record?.id ?? ''}`);

    // Layer 0：首次設定完成前阻擋所有 state-changing 訊息
    if (!getAdminPinHash() && _STATE_CHANGING.has(msg.type)) {
      ws.close(4423, 'setup_required');
      return;
    }

    // Layer 1：未認證客戶端阻擋所有 state-changing 訊息
    if (!clients.has(ws) && _STATE_CHANGING.has(msg.type)) {
      const isRestore  = msg.type === 'session_restore';
      const closeReason = isRestore ? 'session_expired' : 'unauthorized';
      ws.send(JSON.stringify({ type: 'error', reason: closeReason }));
      writeAuditLog(
        isRestore ? 'ws_session_restore_rejected' : 'ws_unauthorized_message_blocked',
        'system', ip, null,
        isRestore
          ? { ws_id: wsId, reason: 'no_token', source_ip: ip }
          : { ws_id: wsId, message_type: msg.type, source_ip: ip, close_code: 4401 }
      );
      // 3. ws_auth_failed（v1.1 §4 優先位置 2）— 不修改上方 auth 邏輯
      logger.error('ws_auth_failed', {
        msg: closeReason,
        detail: { reason: closeReason, message_type: msg.type, ip },
      });
      ws.close(4401, closeReason);
      return;
    }

    // ── W-1: type whitelist enforcement (對齊 F-11/F-12) ─────────────────
    if (!WS_ALLOWED_TYPES.has(msg.type)) {
      logger.warn('ws_unknown_type', {
        msg: 'Unknown WS message type rejected',
        detail: { type: msg.type, source_ip: ip, ws_session_id: wsId },
      });
      ws.send(JSON.stringify({ type: 'error', reason: 'unknown_type' }));
      ws.close(WS_CLOSE_UNKNOWN_TYPE, 'unknown_type');
      return;
    }

    // ── HMAC 簽章驗證 (對齊 F-1~F-10) ──────────────────────────────────
    //   PRE_AUTH_ALLOWED 與 heartbeat (auth/ping/time_sync_req/debug_ping) 不需簽章 (H-4/F-5)
    //   其他 state-changing types 需 HMAC + nonce + timestamp 三因子
    if (!HMAC_NOT_REQUIRED.has(msg.type)) {
      const verifyRes = verifyMessage(HMAC_SECRET, msg);
      if (!verifyRes.ok) {
        if (verifyRes.reason === 'nonce_replay') {
          logger.warn('ws_nonce_replay', {
            msg: 'WS nonce replay detected',
            detail: { type: msg.type, nonce: msg.nonce, source_ip: ip, ws_session_id: wsId },
          });
          ws.send(JSON.stringify({ type: 'error', reason: 'nonce_replay' }));
          ws.close(WS_CLOSE_NONCE_REPLAY, 'nonce_replay');
        } else {
          // signature_invalid / missing_signature_fields / timestamp_out_of_window /
          // invalid_hmac_format / hmac_secret_missing 全歸 ws_signature_invalid
          logger.error('ws_signature_invalid', {
            msg: 'WS signature verification failed',
            detail: { type: msg.type, reason: verifyRes.reason, source_ip: ip, ws_session_id: wsId },
          });
          ws.send(JSON.stringify({ type: 'error', reason: 'signature_invalid' }));
          ws.close(WS_CLOSE_SIGNATURE_INVALID, 'signature_invalid');
        }
        return;
      }
    }

    switch (msg.type) {

      /* ── 驗證登入 ── */
      case 'auth': {
        const { username, pin, device_id } = msg;
        if (!username || !pin) {
          ws.send(JSON.stringify({ type: 'auth_result', ok: false, reason: '帳號或 PIN 為空' }));
          return;
        }
        if (isLoginLocked(username)) {
          ws.send(JSON.stringify({ type: 'auth_result', ok: false, reason: '連續錯誤超過 5 次，鎖定 30 分鐘，請稍後再試' }));
          return;
        }
        const account = db.prepare('SELECT * FROM accounts WHERE username=? AND status=?').get(username, 'active');
        if (!account) {
          writeAuditLog('login_failed', username, device_id || '', null, { reason: '帳號不存在或已停用' });
          ws.send(JSON.stringify({ type: 'auth_result', ok: false, reason: '帳號不存在或已停用' }));
          return;
        }
        const hash = await hashPin(pin, account.pin_salt);
        if (hash !== account.pin_hash) {
          const locked = recordLoginFailure(username);
          writeAuditLog('login_failed', username, device_id || '', null, { reason: 'PIN 錯誤', now_locked: locked });
          ws.send(JSON.stringify({
            type: 'auth_result', ok: false,
            reason: locked ? '連續錯誤超過 5 次，鎖定 30 分鐘，請稍後再試' : 'PIN 錯誤',
          }));
          return;
        }
        db.prepare('DELETE FROM login_failures WHERE username=?').run(username);
        db.prepare('UPDATE accounts SET last_login=?, device_id=? WHERE username=?').run(nowISO(), device_id || null, username);
        clients.set(ws, { deviceId: device_id || ip, username, role: account.role, connectedAt: nowISO() });
        writeAuditLog('login_success', username, device_id || '', null, { role: account.role });
        writeAuditLog('ws_auth_success', username, device_id || '', null,
          { ws_id: wsId, username, role: account.role, session_id: wsId, source_ip: ip });
        // ⭐ Sync v5 (W-C1-A Amendment v3, F-21 落地): 對齊 Step A Approval F-1
        //   PWA 從 WS auth_result payload 取得 HMAC_SECRET + hmac_key_id (K-1 finalize)
        //   additive only (既有 fields 不動, 對齊紅線 4-A explicit override)
        ws.send(JSON.stringify({
          type: 'auth_result', ok: true, username, role: account.role,
          pi_time:              nowISO(),
          last_sync_to_command: getLastSyncToCommand(),
          site_salt:            getSiteSalt(),
          hmac_secret:          HMAC_SECRET,            // Sync v5 (additive)
          hmac_key_id:          crypto.randomUUID(),    // Sync v5 (additive, per session)
        }));
        log.info(`[WS] Auth OK: ${username} (${account.role}) from ${ip}`);
        break;
      }

      /* ── Delta 廣播（L2 同步） ── */
      case 'delta': {
        appendDelta(msg);
        // R-3 / F-16: 透過 _broadcastWithFilter 套用 per-client role filter
        _broadcastWithFilter({
          ...msg, _relayed_by_pi: true,
          correlation_id: msg.correlation_id || crypto.randomUUID(),
        }, ws);
        break;
      }

      /* ── debug_ping ── */
      case 'debug_ping': {
        log.info(`[WS] debug_ping from ${ip} source=${msg.source || '?'} device=${msg.device_id || '?'}`);
        break;
      }

      /* ── session_restore（Option A：一律拒絕，要求重新 auth）── */
      case 'session_restore': {
        writeAuditLog('ws_session_restore_rejected',
          'system', ip, null,
          { ws_id: wsId, reason: 'no_token', source_ip: ip });
        ws.send(JSON.stringify({ type: 'error', reason: 'session_expired' }));
        ws.close(4401, 'session_expired');
        break;
      }

      /* ── Catchup 請求 ── */
      case 'catchup_req': {
        const since  = msg.since || '1970-01-01T00:00:00.000Z';
        const deltas = getRecentDeltas(since);
        ws.send(JSON.stringify({ type: 'catchup_resp', deltas, pi_time: nowISO() }));
        log.info(`[WS] Catchup: sent ${deltas.length} deltas since ${since}`);
        break;
      }

      /* ── sync_push（網路恢復後完整記錄推送） ── */
      case 'sync_push': {
        const { sync_start_ts, tables, snapshots: pushSnapshots, device_id: pushDeviceId, full_sync_tables } = msg;
        let recordsApplied  = 0;
        let snapshotsMerged = 0;

        const SYNC_TABLES = cfg.syncTables;
        if (Array.isArray(full_sync_tables)) {
          for (const tbl of full_sync_tables) {
            if (SYNC_TABLES.includes(tbl)) {
              const result = db.prepare('DELETE FROM current_state WHERE table_name=?').run(tbl);
              log.info(`[sync_push] full_sync clear: ${tbl}, deleted ${result.changes} rows`);
            }
          }
        }

        for (const table of SYNC_TABLES) {
          const records = tables?.[table] || [];
          for (const record of records) {
            if (!record || !record._id) continue;
            const delta = {
              src: pushDeviceId || `${cfg.unitId}_push`,
              table, action: 'upsert', record,
              ts: record.updated_at || record.timestamp || nowISO(),
            };
            appendDelta(delta);
            // R-3 / F-16: sync_push 內部 delta relay 也套用 per-client role filter
            _broadcastWithFilter({ ...delta, _relayed_by_pi: true, type: 'delta' }, ws);
            recordsApplied++;
          }
        }

        const passOneResults = [];
        if (Array.isArray(pushSnapshots)) {
          for (const snap of pushSnapshots) {
            if (!snap.snapshot_uuid) continue;
            const existing = db.prepare('SELECT snapshot_uuid FROM snapshots WHERE snapshot_uuid=?').get(snap.snapshot_uuid);
            if (existing) {
              db.prepare(`UPDATE snapshots SET source='merged_from_qr', payload_json=?, merged=1 WHERE snapshot_uuid=?`)
                .run(JSON.stringify(snap), snap.snapshot_uuid);
              passOneResults.push({ uuid: snap.snapshot_uuid, action: 'merged_over_qr' });
            } else {
              db.prepare(`INSERT OR IGNORE INTO snapshots(snapshot_uuid,unit_id,source,payload_json,recv_at,merged) VALUES(?,?,?,?,?,0)`)
                .run(snap.snapshot_uuid, snap.unit_id || cfg.unitId, 'pi_push', JSON.stringify(snap), nowISO());
              passOneResults.push({ uuid: snap.snapshot_uuid, action: 'inserted' });
            }
            snapshotsMerged++;
          }
        }

        const newSyncTs = nowISO();
        updateLastSyncToCommand(newSyncTs);
        writeAuditLog('network_recovery_push', clients.get(ws)?.username || 'unknown',
          pushDeviceId || '', null,
          { sync_start_ts, records_sent: recordsApplied, snapshots_merged: snapshotsMerged, triggered_by: 'ws_reconnect' }
        );

        ws.send(JSON.stringify({
          type: 'sync_ack', ok: true, pi_time: newSyncTs,
          last_sync_to_command: newSyncTs,
          records_applied:  recordsApplied,
          snapshots_merged: snapshotsMerged,
          pass1_results:    passOneResults,
        }));
        log.info(`[WS] sync_push: applied ${recordsApplied} records, merged ${snapshotsMerged} snapshots`);

        piPushOnce().catch(err => {
          log.warn('[PiPush] sync_push trigger error:', err.message);   // 保留既有
          // 4. pi_sync_failed（v1.1 §4 優先位置 8）
          logger.error('pi_sync_failed', {
            msg: err.message,
            detail: { trigger: 'ws_sync_push' },
          });
        });
        break;
      }

      /* ── 時間同步 ── */
      case 'time_sync_req': {
        ws.send(JSON.stringify({ type: 'time_sync_resp', pi_time: nowISO(), device_id: msg.device_id }));
        break;
      }

      /* ── 稽核事件上傳 ── */
      case 'audit_event': {
        try {
          writeAuditLog(
            msg.action        || 'unknown',
            msg.operator_name || '未知',
            msg.device_id     || '',
            msg.session_id    || null,
            msg.detail        || {}
          );
        } catch { /* non-critical */ }
        break;
      }

      /* ── 清除指定 table（床位重建） ── */
      case 'clear_table': {
        if (!clients.has(ws)) { ws.send(JSON.stringify({ type: 'error', reason: '未認證' })); break; }
        const CLEARABLE = ['beds', 'persons', 'resources', 'incidents', 'shifts'];
        const tbl = msg.table;
        if (!CLEARABLE.includes(tbl)) {
          ws.send(JSON.stringify({ type: 'error', reason: `不允許清除 ${tbl}` }));
          break;
        }
        db.prepare('DELETE FROM current_state WHERE table_name=?').run(tbl);
        log.info(`[clear_table] ${tbl} cleared by ${clients.get(ws)?.username || 'unknown'}`);
        ws.send(JSON.stringify({ type: 'clear_table_ack', table: tbl, ok: true }));
        break;
      }

      /* ── Ping ── */
      case 'ping': {
        ws.send(JSON.stringify({ type: 'pong', pi_time: nowISO() }));
        break;
      }
    }
  });

  ws.on('close', (code, reason) => {
    if (_zombieTimer) { clearTimeout(_zombieTimer); _zombieTimer = null; }
    const info = clients.get(ws);
    log.info(`[WS] Disconnected: ${info ? info.username : '(未驗證)'} code=${code} reason=${reason || ''}`);
    // 2. ws_disconnect（v1.1 §4 優先位置 1）
    logger.info('ws_disconnect', {
      msg: 'WebSocket client disconnected',
      user: info ? info.username : undefined,
      detail: { ip, code },
    });
    clients.delete(ws);
  });

  ws.on('error', err => log.warn('[WS] Error:', err.message));

  ws.send(JSON.stringify({
    type: 'welcome', pi_time: nowISO(), server_version: '2.1',
    last_sync_to_command: getLastSyncToCommand(),
    command_status: getCommandStatus(),
  }));
});

wss.on('error', err => log.error('[WS Server Error]', err));

/* ─── Server-side Ping（維持 iOS Safari 連線）── */
setInterval(() => {
  wss.clients.forEach(ws => {
    if (ws.readyState !== WebSocket.OPEN) return;
    if (ws.isAlive === false) {
      log.warn(`[WS] No pong, terminating ${clients.get(ws)?.username || '(未驗證)'}`);
      ws.terminate();
      return;
    }
    ws.isAlive = false;
    ws.ping();
  });
}, 25_000);

function startWsServer() {
  wsRawServer.listen(WS_PORT, () => {
    log.info(`[WS] ${WS_PROTOCOL.toUpperCase()} Server listening on port ${WS_PORT}`);
  });
  // 啟動 nonce cache 清理定時器 (對齊 F-9: 每 5 秒)
  startNonceCleanup();
}

module.exports = { wss, clients, broadcast, startWsServer };
