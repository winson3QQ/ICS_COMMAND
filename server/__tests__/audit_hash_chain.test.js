// SPDX-License-Identifier: LicenseRef-Proprietary
// Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
'use strict';

/**
 * server/__tests__/audit_hash_chain.test.js — Pi audit_log hash chain
 * (Codeberg Issue #1, GAP-AUDIT-04, NIST AU-9(3))
 *
 * 對應 GK Step A Approval §3 AC + Amendment v1 + CA Stage 6 校準:
 *   AC-5  Pi migration 成功 (兩 DB 含 hash_prev) + idempotent
 *   AC-6  Pi 新 INSERT 自動填入 hash_prev (computeNextHashPrev)
 *   AC-7  verifyAuditChain 偵測竄改 (shelter + medical)
 *   AC-8  既有 NULL hash_prev records 不影響新 INSERT
 *
 * 執行: node --test server/__tests__/audit_hash_chain.test.js
 */

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const Database = require('better-sqlite3');

// config.js 模組載入時檢查 --unit argv；測試前注入避免 process.exit。
if (!process.argv.includes('--unit')) {
  process.argv.push('--unit', 'shelter');
}

const { runMigrations } = require('../migrations');
const { canonical, computeHash, computeNextHashPrev, verifyAuditChain } = require('../audit_chain');

function freshDb() {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'audit-chain-'));
  const dbPath = path.join(tmp, 'test.db');
  const db = new Database(dbPath);
  runMigrations(db);
  return { db, dbPath, tmpDir: tmp };
}

function cleanupDb(ctx) {
  try { ctx.db.close(); } catch (e) { /* ignore */ }
  try { fs.rmSync(ctx.tmpDir, { recursive: true, force: true }); } catch (e) { /* ignore */ }
}

function directInsert(db, fields) {
  // bypass writeAuditLog; for pre-task NULL records or tampering setup
  const id = crypto.randomUUID();
  db.prepare(`INSERT INTO audit_log(id,action,operator_name,device_id,session_id,timestamp,detail,hash_prev)
              VALUES(?,?,?,?,?,?,?,?)`)
    .run(
      id,
      fields.action || '',
      fields.operator_name || '',
      fields.device_id || null,
      fields.session_id || null,
      fields.timestamp || new Date().toISOString(),
      fields.detail || null,
      fields.hash_prev === undefined ? null : fields.hash_prev
    );
  return id;
}

function hookedInsert(db, fields) {
  // simulates production writeAuditLog: computeNextHashPrev → INSERT with hash_prev
  const hashPrev = computeNextHashPrev(db);
  const id = crypto.randomUUID();
  db.prepare(`INSERT INTO audit_log(id,action,operator_name,device_id,session_id,timestamp,detail,hash_prev)
              VALUES(?,?,?,?,?,?,?,?)`)
    .run(
      id,
      fields.action || '',
      fields.operator_name || '',
      fields.device_id || null,
      fields.session_id || null,
      fields.timestamp || new Date().toISOString(),
      fields.detail || null,
      hashPrev
    );
  return id;
}

/* ── AC-5: idempotent migration (run twice, no error, hash_prev exists) ── */
test('pi_audit_migration_idempotent_adds_hash_prev', () => {
  const ctx = freshDb();
  try {
    // run again — should not throw
    runMigrations(ctx.db);
    runMigrations(ctx.db);
    const cols = ctx.db.pragma('table_info(audit_log)').map(r => r.name);
    assert.ok(cols.includes('hash_prev'), `hash_prev should be in audit_log cols, got ${cols.join(',')}`);
  } finally {
    cleanupDb(ctx);
  }
});

/* ── AC-6: 新 INSERT 自動填入 hash_prev (computeNextHashPrev) ─────────── */
test('pi_audit_hash_prev_populated_on_insert', () => {
  const ctx = freshDb();
  try {
    const id1 = hookedInsert(ctx.db, { action: 'login', operator_name: 'alice' });
    const id2 = hookedInsert(ctx.db, { action: 'logout', operator_name: 'alice' });

    const r1 = ctx.db.prepare('SELECT hash_prev FROM audit_log WHERE id=?').get(id1);
    const r2 = ctx.db.prepare('SELECT hash_prev FROM audit_log WHERE id=?').get(id2);

    assert.equal(r1.hash_prev, null, 'first INSERT hash_prev should be NULL (chain start)');
    assert.ok(r2.hash_prev !== null && r2.hash_prev !== undefined, 'second INSERT hash_prev should be non-NULL');
    assert.equal(r2.hash_prev.length, 64, 'hash_prev should be 64-char SHA-256 hex');
    assert.match(r2.hash_prev, /^[0-9a-f]{64}$/, 'hash_prev hex only');
  } finally {
    cleanupDb(ctx);
  }
});

/* ── AC-7 (shelter): verifyAuditChain pass + tampering detect ─────────── */
test('pi_audit_chain_verify_pass_shelter', () => {
  const ctx = freshDb();
  try {
    for (let i = 0; i < 5; i++) {
      hookedInsert(ctx.db, {
        action: 'event',
        operator_name: `user${i}`,
        device_id: `dev${i}`,
        session_id: `sess${i}`,
        timestamp: `2026-05-07T10:0${i}:00Z`,
        detail: JSON.stringify({ i }),
      });
    }
    const result = verifyAuditChain(ctx.dbPath);
    assert.equal(result.ok, true, `chain should be intact: ${JSON.stringify(result)}`);
    assert.equal(result.total, 4, '4 chain records (skip first NULL)');
    assert.equal(result.broken_at, null);
  } finally {
    cleanupDb(ctx);
  }
});

/* ── AC-7 (medical): same logic, separate DB path 證明 S-4 chain 各自 ── */
test('pi_audit_chain_verify_pass_medical', () => {
  // 用 freshDb 模擬 medical_accounts.db (S-4: medical 跟 shelter 各自 chain, 邏輯一樣)
  const ctx = freshDb();
  try {
    for (let i = 0; i < 4; i++) {
      hookedInsert(ctx.db, {
        action: 'medical_event',
        operator_name: `medic${i}`,
        device_id: `tablet${i}`,
        session_id: `medsess${i}`,
        timestamp: `2026-05-07T11:0${i}:00Z`,
        detail: JSON.stringify({ medical_i: i }),
      });
    }
    const result = verifyAuditChain(ctx.dbPath);
    assert.equal(result.ok, true);
    assert.equal(result.total, 3);
  } finally {
    cleanupDb(ctx);
  }
});

/* ── AC-7: tampering detect (shelter, mirrors pytest test_audit_chain_detect_tampering) ── */
test('pi_audit_chain_detect_tampering', () => {
  const ctx = freshDb();
  try {
    const ids = [];
    for (let i = 0; i < 5; i++) {
      ids.push(hookedInsert(ctx.db, {
        action: 'event',
        operator_name: `user${i}`,
        device_id: `dev${i}`,
        session_id: `sess${i}`,
        timestamp: `2026-05-07T10:0${i}:00Z`,
        detail: JSON.stringify({ i }),
      }));
    }
    // pristine
    assert.equal(verifyAuditChain(ctx.dbPath).ok, true);

    // 竄改 ids[2] 的 detail
    ctx.db.prepare('UPDATE audit_log SET detail=? WHERE id=?')
      .run(JSON.stringify({ tampered: true }), ids[2]);

    const result = verifyAuditChain(ctx.dbPath);
    assert.equal(result.ok, false, 'tampering must be detected');
    assert.equal(result.broken_at, ids[3], `broken_at should be next record (${ids[3]}); got ${result.broken_at}`);
    assert.match(result.reason, /hash_prev mismatch/);
  } finally {
    cleanupDb(ctx);
  }
});

/* ── AC-8: backward compat (pre-task NULL records + new chain records) ── */
test('pi_audit_backward_compat_null_hash_prev', () => {
  const ctx = freshDb();
  try {
    // 3 筆 pre-task (hash_prev=NULL, 模擬 m012 deploy 前的既有 records)
    for (let i = 0; i < 3; i++) {
      directInsert(ctx.db, {
        action: 'legacy_event',
        operator_name: `legacy${i}`,
        device_id: `dev${i}`,
        timestamp: `2026-05-01T10:0${i}:00Z`,
        detail: JSON.stringify({ legacy: i }),
        hash_prev: null,
      });
    }
    // 3 筆新 (經 hook)
    const newIds = [];
    for (let i = 0; i < 3; i++) {
      newIds.push(hookedInsert(ctx.db, {
        action: 'new_event',
        operator_name: `new${i}`,
        device_id: `dev${i}`,
        timestamp: `2026-05-07T10:0${i}:00Z`,
        detail: JSON.stringify({ new: i }),
      }));
    }

    // 第一筆新 record 應該有 hash_prev (對齊最後 pre-task)
    const first = ctx.db.prepare('SELECT hash_prev FROM audit_log WHERE id=?').get(newIds[0]);
    assert.ok(first.hash_prev !== null, 'first new record should have hash_prev');

    const result = verifyAuditChain(ctx.dbPath);
    assert.equal(result.ok, true, `backward compat broken: ${JSON.stringify(result)}`);
    assert.equal(result.total, 3, '3 new chain records');
  } finally {
    cleanupDb(ctx);
  }
});

/* ── canonical / hash determinism (sanity) ─────────────────────────────── */
test('pi_canonical_hash_deterministic', () => {
  const rec = {
    id: 'abc-123', action: 'login', operator_name: 'alice',
    device_id: 'd1', session_id: 's1', timestamp: '2026-05-07T10:00:00Z',
    detail: '{"k":"v"}', hash_prev: null,
  };
  const c1 = canonical(rec);
  const c2 = canonical(rec);
  const h1 = computeHash(rec);
  const h2 = computeHash(rec);
  assert.equal(c1, c2);
  assert.equal(h1, h2);
  assert.equal(h1.length, 64);
  // 改 detail → hash 必變
  const rec2 = { ...rec, detail: '{"k":"changed"}' };
  assert.notEqual(computeHash(rec2), h1);
});
