'use strict';

const crypto  = require('crypto');
const { db, nowISO } = require('./db');
const { log } = require('./logger');
const logger  = require('./logger');   // C1-D 結構化日誌
const { cfg, CA_CERT, PI_PUSH_INTERVAL_MS, MAX_QUEUE_AGE_MS, getCommandUrl, HMAC_SECRET, HMAC_KEY_ID } = require('./config');

// 由 index.js 在兩個模組都載入後注入，避免循環依賴
let _broadcast = () => {};
function setBroadcast(fn) { _broadcast = fn; }

let _lastPushHash  = '';
let _commandStatus = { ok: false, lastOkAt: null, lastError: null };
let _piPushStarted = false;

function getCommandStatus() { return _commandStatus; }

/* ─── HTTP/HTTPS POST helpers ──────────────────────────────── */

function _postJSON(urlStr, body, extraHeaders = {}, timeoutMs = 8000) {
  return new Promise((resolve, reject) => {
    const url     = new URL(urlStr);
    const data    = JSON.stringify(body);
    const isHttps = url.protocol === 'https:';
    const mod     = isHttps ? require('https') : require('http');
    const opts    = {
      hostname: url.hostname,
      port:     url.port || (isHttps ? 443 : 80),
      path:     url.pathname + url.search,
      method:   'POST',
      headers:  {
        'Content-Type':   'application/json',
        'Content-Length': Buffer.byteLength(data),
        ...extraHeaders,
      },
    };
    if (isHttps && CA_CERT) opts.ca = CA_CERT;
    const req = mod.request(opts, (res) => {
      let buf = '';
      res.on('data', c => buf += c);
      res.on('end', () => resolve({ status: res.statusCode, body: buf }));
    });
    req.setTimeout(timeoutMs, () => { req.destroy(); reject(new Error('timeout')); });
    req.on('error', reject);
    req.write(data);
    req.end();
  });
}

function _postWithBearer(urlStr, body, bearerToken, timeoutMs = 8000) {
  return _postJSON(urlStr, body, { Authorization: `Bearer ${bearerToken}` }, timeoutMs);
}

/* ─── TI-01 HMAC-SHA256 helpers ────────────────────────────── */

/**
 * query string → sorted key=value&key=value（Decision-2）
 * @param {string} qs
 * @returns {string}
 */
function _queryCanonical(qs) {
  if (!qs) return '';
  return qs.split('&')
    .map(p => p.split('='))
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([k, v]) => `${k}=${v ?? ''}`)
    .join('&');
}

/**
 * Compute HMAC-SHA256 headers（Decision-2 canonical string）。
 * @param {string} method   HTTP method（"POST"）
 * @param {string} urlStr   Full URL string
 * @param {Buffer|string} bodyBytes  Raw request body
 * @returns {Object}  Four X-ICS-* headers，or {} if credentials missing
 */
function _buildHmacHeaders(method, urlStr, bodyBytes) {
  if (!HMAC_SECRET || !HMAC_KEY_ID) {
    log.warn('[TI-01] HMAC credentials missing — push will be rejected by Command dashboard');
    return {};
  }
  const url         = new URL(urlStr);
  const timestampMs = String(Date.now());
  const nonce       = crypto.randomUUID();  // UUID v4，Node.js 18+
  const bodyBuf     = Buffer.isBuffer(bodyBytes)
                        ? bodyBytes
                        : Buffer.from(bodyBytes, 'utf8');
  const bodyHash    = crypto.createHash('sha256').update(bodyBuf).digest('hex');

  const canonical = [
    method.toUpperCase(),
    url.pathname,
    _queryCanonical(url.search.replace(/^\?/, '')),
    timestampMs,
    nonce,
    bodyHash,
  ].join('\n');

  const signature = crypto
    .createHmac('sha256', HMAC_SECRET)
    .update(canonical)
    .digest('hex');

  return {
    'X-ICS-Key-Id'    : HMAC_KEY_ID,
    'X-ICS-Timestamp' : timestampMs,
    'X-ICS-Nonce'     : nonce,
    'X-ICS-Signature' : signature,
  };
}

/**
 * POST JSON with Bearer + HMAC headers。
 * AC-8b：收到 401 reason="replay" 時自動換新 nonce 重試一次。
 * @param {string} urlStr
 * @param {Object} body
 * @param {string} bearerToken
 * @param {number} [timeoutMs=8000]
 * @returns {Promise<{status:number, body:any}>}
 */
function _postWithHmac(urlStr, body, bearerToken, timeoutMs = 8000) {
  const bodyStr = JSON.stringify(body);

  function attempt() {
    const hmacHeaders = _buildHmacHeaders('POST', urlStr, bodyStr);
    return _postJSON(urlStr, body, {
      Authorization: `Bearer ${bearerToken}`,
      ...hmacHeaders,
    }, timeoutMs);
  }

  return attempt().then(res => {
    // AC-8b：replay 拒絕 → 換新 nonce 重試一次
    if (res.status === 401) {
      let reason = '';
      try {
        const parsed = typeof res.body === 'string' ? JSON.parse(res.body) : res.body;
        reason = (parsed && parsed.detail && parsed.detail.reason) || parsed.reason || '';
      } catch (_) {}
      if (reason === 'replay') {
        log.warn('[TI-01] nonce replay rejected, retrying with new nonce');
        return attempt();  // _buildHmacHeaders 會產生全新 nonce
      }
    }
    return res;
  });
}

/* ─── 舊 snapshot 推送（§10.4，仍保留供外部直接呼叫）─────── */
async function pushToCommand(snapshotPayload) {
  const target = getCommandUrl();
  if (!target) return;
  try {
    const res = await _postWithHmac(
      `${target}/api/snapshots`, snapshotPayload, getPiApiKey() || ''
    );
    if (res.status >= 200 && res.status < 300) {
      log.info(`[Command] Snapshot pushed OK: ${snapshotPayload.snapshot_id}`);
    } else {
      log.warn(`[Command] Push failed ${res.status}: ${res.body}`);
    }
  } catch (err) {
    log.warn(`[Command] Push error: ${err.message}`);   // 保留既有
    logger.error('pi_sync_failed', { msg: err.message,
      detail: { phase: 'snapshot_push', snapshot_id: snapshotPayload.snapshot_id } });
  }
}

/* ─── Pi API key ───────────────────────────────────────────── */
function getPiApiKey() {
  const row = db.prepare("SELECT value FROM config WHERE key='pi_api_key'").get();
  return row ? row.value : null;
}

/* ─── Wave 4：current_state 定時推送 ──────────────────────── */

async function _replayUnsentQueue(target, apiKey) {
  const unsent = db.prepare(
    'SELECT id, records_json, pushed_at FROM push_queue WHERE sent=0 ORDER BY id ASC LIMIT 50'
  ).all();
  if (unsent.length <= 1) return;
  for (const row of unsent.slice(0, -1)) {
    try {
      const records = JSON.parse(row.records_json);
      const res = await _postWithHmac(
        `${target}/api/pi-push/${cfg.unitId}`, { records, pushed_at: row.pushed_at }, apiKey
      );
      if (res.status >= 200 && res.status < 300) {
        db.prepare('UPDATE push_queue SET sent=1, sent_at=? WHERE id=?').run(nowISO(), row.id);
        log.info(`[PiPush] Replayed queue#${row.id} OK`);
      } else {
        log.warn(`[PiPush] Replay queue#${row.id} failed: ${res.status}`);
        break;
      }
    } catch (err) {
      log.warn(`[PiPush] Replay error: ${err.message}`);   // 保留既有
      logger.warn('pi_sync_failed', { msg: err.message,
        detail: { phase: 'replay_queue', queue_id: row.id } });
      break;
    }
  }
}

async function piPushOnce() {
  const target = getCommandUrl();
  const apiKey = getPiApiKey();
  if (!target || !apiKey) return;

  const rows    = db.prepare('SELECT table_name, record_id, record_json, updated_at FROM current_state').all();
  const rawJson = rows.length > 0 ? JSON.stringify(rows) : 'empty';
  const hash    = crypto.createHash('md5').update(rawJson).digest('hex');

  if (hash === _lastPushHash || rows.length === 0) {
    try {
      const hbRes = await _postWithHmac(
        `${target}/api/pi-push/${cfg.unitId}`, { records: [], pushed_at: nowISO(), heartbeat: true }, apiKey
      );
      if (hbRes.status >= 200 && hbRes.status < 300) {
        log.debug('[PiPush] heartbeat OK');
        _commandStatus = { ok: true, lastOkAt: nowISO(), lastError: null };
      } else {
        log.debug(`[PiPush] heartbeat failed: HTTP ${hbRes.status}`);
        _commandStatus = { ok: false, lastOkAt: _commandStatus.lastOkAt, lastError: `HTTP ${hbRes.status}` };
      }
    } catch (e) {
      log.debug('[PiPush] heartbeat failed:', e.message);   // 保留既有
      logger.warn('pi_sync_failed', { msg: e.message, detail: { phase: 'heartbeat' } });
      _commandStatus = { ok: false, lastOkAt: _commandStatus.lastOkAt, lastError: e.message };
    }
    _broadcast({ type: 'command_status', ..._commandStatus });
    return;
  }

  const records = rows.map(r => ({
    table_name: r.table_name,
    record_id:  r.record_id,
    record:     JSON.parse(r.record_json),
    updated_at: r.updated_at,
  }));

  const now  = nowISO();
  const info = db.prepare('INSERT INTO push_queue(records_json, pushed_at) VALUES(?,?)').run(JSON.stringify(records), now);
  const qid  = info.lastInsertRowid;

  try {
    const res = await _postWithHmac(
      `${target}/api/pi-push/${cfg.unitId}`, { records, pushed_at: now }, apiKey
    );
    if (res.status >= 200 && res.status < 300) {
      db.prepare('UPDATE push_queue SET sent=1, sent_at=? WHERE id=?').run(nowISO(), qid);
      _lastPushHash  = hash;
      _commandStatus = { ok: true, lastOkAt: nowISO(), lastError: null };
      _broadcast({ type: 'command_status', ..._commandStatus });
      log.info(`[PiPush] OK: ${records.length} records, queue#${qid}`);
      await _replayUnsentQueue(target, apiKey);
    } else {
      log.warn(`[PiPush] Failed ${res.status}: ${res.body}`);
      _commandStatus = { ok: false, lastOkAt: _commandStatus.lastOkAt, lastError: `HTTP ${res.status}` };
      _broadcast({ type: 'command_status', ..._commandStatus });
    }
  } catch (err) {
    log.warn(`[PiPush] Error: ${err.message} (queued #${qid}, will retry)`);
    _commandStatus = { ok: false, lastOkAt: _commandStatus.lastOkAt, lastError: err.message };
    _broadcast({ type: 'command_status', ..._commandStatus });
  }

  const cutoff = new Date(Date.now() - MAX_QUEUE_AGE_MS).toISOString();
  db.prepare('DELETE FROM push_queue WHERE sent=1 AND pushed_at < ?').run(cutoff);
  db.prepare('DELETE FROM push_queue WHERE pushed_at < ?').run(cutoff);
}

function startPiPush() {
  if (_piPushStarted) return;
  const target = getCommandUrl();
  const apiKey = getPiApiKey();
  if (!target || !apiKey) {
    log.info('[PiPush] 未設定 command_url 或 pi_api_key，跳過定時推送');
    return;
  }
  _piPushStarted = true;
  setTimeout(() => piPushOnce(), 5_000);
  setInterval(() => piPushOnce(), PI_PUSH_INTERVAL_MS);
  log.info(`[PiPush] 定時推送 current_state 至指揮部，間隔 ${PI_PUSH_INTERVAL_MS / 1000}s`);
}

module.exports = { setBroadcast, pushToCommand, getPiApiKey, piPushOnce, startPiPush, getCommandStatus };
