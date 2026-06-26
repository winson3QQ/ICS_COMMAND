// SPDX-License-Identifier: LicenseRef-Proprietary
// Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
'use strict';

const { db, nowISO, newUUID } = require('./db');
const { computeNextHashPrev } = require('./audit_chain');

function writeAuditLog(action, operator, deviceId, sessionId, detail) {
  // GAP-AUDIT-04: hash_prev 對齊 prev record canonical hash (NIST AU-9(3))
  const hashPrev = computeNextHashPrev(db);
  db.prepare(`INSERT INTO audit_log(id,action,operator_name,device_id,session_id,timestamp,detail,hash_prev)
              VALUES(?,?,?,?,?,?,?,?)`)
    .run(newUUID(), action, operator, deviceId || null, sessionId || null, nowISO(), JSON.stringify(detail || {}), hashPrev);
}

module.exports = { writeAuditLog };
