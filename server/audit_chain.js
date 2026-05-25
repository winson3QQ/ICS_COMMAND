'use strict';

/**
 * server/audit_chain.js — Pi audit_log hash chain (NIST AU-9(3))
 *
 * Codeberg Issue #1 GAP-AUDIT-04 — Pi 端 verify + INSERT hook helper。
 * 兩 DB (shelter_accounts.db / medical_accounts.db) 各自獨立 chain (S-4)。
 *
 * Canonical form (Pi, IA Stage 5b D-3/D-3b verified, 不校準):
 *   [id, action, operator_name, device_id, session_id, timestamp, detail, hash_prev].join("|")
 *   NULL → ""; UTF-8 → SHA-256 hex.
 *
 * Pre-task records (hash_prev=NULL) 視為 chain 起點之前 (Step A Approval A-4)。
 */

const crypto = require('crypto');
const Database = require('better-sqlite3');

// Canonical column 順序 (frozen, 不可改)
const AUDIT_COLS = ['id', 'action', 'operator_name', 'device_id', 'session_id', 'timestamp', 'detail', 'hash_prev'];

const SELECT_SQL =
  'SELECT id, action, operator_name, device_id, session_id, timestamp, detail, hash_prev FROM audit_log ';

function canonical(record) {
  return AUDIT_COLS
    .map(c => (record[c] === null || record[c] === undefined) ? '' : String(record[c]))
    .join('|');
}

function computeHash(record) {
  return crypto.createHash('sha256').update(canonical(record), 'utf8').digest('hex');
}

/**
 * 為下一筆 INSERT 計算 hash_prev = prev record 完整 hash。
 * 若 audit_log 為空 → 回 null (chain 起點)。
 *
 * @param {Database.Database} db better-sqlite3 connection
 * @returns {string | null}
 */
function computeNextHashPrev(db) {
  const row = db.prepare(SELECT_SQL + 'ORDER BY rowid DESC LIMIT 1').get();
  if (!row) return null;
  return computeHash(row);
}

/**
 * Verify hash chain integrity for given DB path。
 * 用於 verify endpoint / admin CLI / test。
 *
 * @param {string} dbPath shelter_accounts.db | medical_accounts.db | other
 * @returns {{ok: boolean, total: number, broken_at: string|null, reason: string|null}}
 */
function verifyAuditChain(dbPath) {
  const db = new Database(dbPath, { readonly: true });
  try {
    const rows = db.prepare(SELECT_SQL + 'ORDER BY rowid ASC').all();

    let prevRecord = null;
    let totalVerified = 0;

    for (const record of rows) {
      if (record.hash_prev === null || record.hash_prev === undefined) {
        prevRecord = record;
        continue;
      }
      if (prevRecord === null) {
        return {
          ok: false,
          total: totalVerified,
          broken_at: record.id,
          reason: `chain record id=${record.id} has no prev record (audit_log 結構異常)`,
        };
      }
      const expected = computeHash(prevRecord);
      if (record.hash_prev !== expected) {
        return {
          ok: false,
          total: totalVerified,
          broken_at: record.id,
          reason: `hash_prev mismatch at id=${record.id}: expected ${expected.slice(0, 16)}..., got ${String(record.hash_prev).slice(0, 16)}...`,
        };
      }
      prevRecord = record;
      totalVerified += 1;
    }

    return { ok: true, total: totalVerified, broken_at: null, reason: null };
  } finally {
    db.close();
  }
}

module.exports = { canonical, computeHash, computeNextHashPrev, verifyAuditChain };
