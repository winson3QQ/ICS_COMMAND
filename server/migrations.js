// SPDX-License-Identifier: LicenseRef-Proprietary
// Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
'use strict';

const { cfg } = require('./config');

function runMigrations(db) {
  db.exec(`
    PRAGMA journal_mode=WAL;

    CREATE TABLE IF NOT EXISTS config (
      key   TEXT PRIMARY KEY,
      value TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS accounts (
      id          TEXT PRIMARY KEY,
      username    TEXT UNIQUE NOT NULL,
      role        TEXT NOT NULL,
      pin_hash    TEXT NOT NULL,
      pin_salt    TEXT NOT NULL,
      status      TEXT NOT NULL DEFAULT 'active',
      created_at  TEXT NOT NULL,
      created_by  TEXT NOT NULL DEFAULT 'system',
      last_login  TEXT,
      device_id   TEXT
    );

    CREATE TABLE IF NOT EXISTS audit_log (
      id            TEXT PRIMARY KEY,
      action        TEXT NOT NULL,
      operator_name TEXT NOT NULL,
      device_id     TEXT,
      session_id    TEXT,
      timestamp     TEXT NOT NULL,
      detail        TEXT
    );

    CREATE TABLE IF NOT EXISTS delta_log (
      id          INTEGER PRIMARY KEY AUTOINCREMENT,
      src         TEXT,
      table_name  TEXT,
      record_id   TEXT,
      record_json TEXT,
      ts          TEXT,
      recv_at     TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS login_failures (
      username   TEXT NOT NULL,
      failed_at  TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_login_failures_username ON login_failures(username);

    CREATE TABLE IF NOT EXISTS snapshots (
      snapshot_uuid  TEXT PRIMARY KEY,
      unit_id        TEXT NOT NULL DEFAULT '${cfg.unitId}',
      source         TEXT NOT NULL DEFAULT 'pi_push',
      payload_json   TEXT NOT NULL,
      recv_at        TEXT NOT NULL,
      merged         INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS current_state (
      table_name  TEXT NOT NULL,
      record_id   TEXT NOT NULL,
      record_json TEXT NOT NULL,
      updated_at  TEXT NOT NULL,
      PRIMARY KEY (table_name, record_id)
    );

    CREATE TABLE IF NOT EXISTS push_queue (
      id           INTEGER PRIMARY KEY AUTOINCREMENT,
      records_json TEXT NOT NULL,
      pushed_at    TEXT NOT NULL,
      sent         INTEGER NOT NULL DEFAULT 0,
      sent_at      TEXT
    );
  `);

  if (cfg.roleMigration) db.exec(cfg.roleMigration);

  // ── Codeberg Issue #1 GAP-AUDIT-04: audit_log hash chain (NIST AU-9(3)) ──
  // Pi 端無 schema_migrations 版本追蹤（KL-5），用 idempotent ALTER TABLE。
  // 既有 records hash_prev=NULL（chain 起點之前），新 INSERT 從本 deploy 起 fill。
  const auditCols = db.pragma('table_info(audit_log)').map(r => r.name);
  if (!auditCols.includes('hash_prev')) {
    db.exec('ALTER TABLE audit_log ADD COLUMN hash_prev TEXT');
  }
}

module.exports = { runMigrations };
