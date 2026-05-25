'use strict';

const crypto   = require('crypto');
const Database = require('better-sqlite3');
const { log }  = require('./logger');
const logger   = require('./logger');   // C1-D 結構化日誌
const { DB_PATH, DELTA_LOG_MAX, cfg, getCommandUrl, setCommandUrl } = require('./config');
const { runMigrations } = require('./migrations');

const nowISO    = () => new Date().toISOString();
const newUUID   = () => crypto.randomUUID ? crypto.randomUUID() : crypto.randomBytes(16).toString('hex');
const randomHex = (bytes = 16) => crypto.randomBytes(bytes).toString('hex');

const db = new Database(DB_PATH);
runMigrations(db);

(function initConfig() {
  if (!db.prepare("SELECT value FROM config WHERE key='last_sync_to_command'").get()) {
    db.prepare("INSERT INTO config(key,value) VALUES('last_sync_to_command','1970-01-01T00:00:00.000Z')").run();
    log.debug('[Config] Initialized last_sync_to_command = epoch');
  }

  if (!db.prepare("SELECT value FROM config WHERE key='site_salt'").get()) {
    const salt = randomHex(16);
    db.prepare("INSERT INTO config(key,value) VALUES('site_salt',?)").run(salt);
    log.info('[Config] Generated new site_salt (first startup)');
  } else {
    log.debug('[Config] site_salt loaded from DB');
  }

  if (!getCommandUrl()) {
    const row = db.prepare("SELECT value FROM config WHERE key='command_url'").get();
    if (row) {
      setCommandUrl(row.value);
      log.debug(`[Config] Loaded command_url from DB: ${row.value}`);
    }
  }

  // 不再 seed 預設 admin/1234；fresh DB 啟動由 first_run.js 產生一次性 token，
  // 等部署 IT 透過 /admin/setup 完成首次設定。
})();

// Delta log（in-memory buffer + persistent）
const deltaLog = [];

function appendDelta(msg) {
  deltaLog.unshift({ ...msg, recv_at: nowISO() });
  if (deltaLog.length > DELTA_LOG_MAX) deltaLog.length = DELTA_LOG_MAX;

  try {
    db.prepare(`INSERT INTO delta_log(src,table_name,record_id,record_json,ts,recv_at) VALUES(?,?,?,?,?,?)`)
      .run(
        msg.src || '',
        msg.table || '',
        String(msg.record?._id ?? msg.record?.id ?? ''),
        JSON.stringify(msg.record || {}),
        msg.ts || '',
        nowISO()
      );
    db.prepare(`DELETE FROM delta_log WHERE id NOT IN (
      SELECT id FROM delta_log ORDER BY id DESC LIMIT ?
    )`).run(DELTA_LOG_MAX);
  } catch (e) {
    logger.warn('db_write_error', { msg: 'delta_log trim failed (non-critical)', detail: { err: e.message } });
  }

  const _tbl = msg.table || '';
  const _rid = String(msg.record?._id ?? msg.record?.id ?? '');
  if (_tbl && _rid) {
    try {
      db.prepare(`INSERT INTO current_state(table_name, record_id, record_json, updated_at)
                  VALUES(?,?,?,?)
                  ON CONFLICT(table_name, record_id) DO UPDATE SET
                    record_json=excluded.record_json, updated_at=excluded.updated_at`)
        .run(_tbl, _rid, JSON.stringify(msg.record || {}), nowISO());
    } catch (e) { log.warn('[current_state] UPSERT error:', e.message); }
  }
}

function getRecentDeltas(sinceISO) {
  try {
    const rows = db.prepare(
      `SELECT src, table_name as "table", record_json, ts FROM delta_log
       WHERE ts >= ? ORDER BY id ASC LIMIT ?`
    ).all(sinceISO || '1970-01-01T00:00:00.000Z', DELTA_LOG_MAX);
    return rows.map(r => ({
      src: r.src, table: r.table,
      record: JSON.parse(r.record_json || '{}'),
      ts: r.ts, action: 'upsert',
    }));
  } catch (e) {
    logger.error('db_write_error', { msg: 'getRecentDeltas query failed, returning empty array', detail: { err: e.message } });
    return [];
  }
}

function getLastSyncToCommand() {
  const row = db.prepare("SELECT value FROM config WHERE key='last_sync_to_command'").get();
  return row ? row.value : '1970-01-01T00:00:00.000Z';
}

function updateLastSyncToCommand(isoTs) {
  db.prepare("INSERT OR REPLACE INTO config(key,value) VALUES('last_sync_to_command',?)").run(isoTs);
}

function getSiteSalt() {
  const row = db.prepare("SELECT value FROM config WHERE key='site_salt'").get();
  if (row) return row.value;
  log.warn('[Config] site_salt missing from DB, regenerating');
  const salt = randomHex(16);
  db.prepare("INSERT OR REPLACE INTO config(key,value) VALUES('site_salt',?)").run(salt);
  return salt;
}

module.exports = {
  db, nowISO, newUUID, randomHex,
  appendDelta, getRecentDeltas,
  getLastSyncToCommand, updateLastSyncToCommand,
  getSiteSalt,
};
