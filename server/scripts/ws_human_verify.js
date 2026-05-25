#!/usr/bin/env node
/**
 * HOTFIX-WS-01 Human Verification Script
 * §8 Pi 500 實機驗證腳本
 *
 * 用法：
 *   node server/scripts/ws_human_verify.js [shelter|medical]
 *
 * 前置條件：
 *   1. Pi server 已啟動（./start_pi.sh 或 node server/index.js）
 *   2. First-run 已完成（有管理員帳號）
 *   3. 設定 VERIFY_USER / VERIFY_PIN 環境變數（見下方）
 *
 * 環境變數：
 *   VERIFY_USER=<帳號>   # 測試用帳號（需在 DB 中存在且 status=active）
 *   VERIFY_PIN=<PIN>     # 對應的 PIN
 *   WS_PORT=8765         # 可選，覆蓋預設 port
 */

'use strict';

const WebSocket = require('ws');
const path      = require('path');
const fs        = require('fs');

// ── 設定 ──────────────────────────────────────────
const unit    = process.argv[2] || 'shelter';
const PORT    = process.env.WS_PORT || (unit === 'medical' ? 8775 : 8765);
const DB_PATH = process.env.DB_PATH ||
  path.resolve(unit === 'medical'
    ? './medical-pwa/medical_accounts.db'
    : './shelter-pwa/shelter_accounts.db');

const VERIFY_USER = process.env.VERIFY_USER || '';
const VERIFY_PIN  = process.env.VERIFY_PIN  || '';
const WS_URL      = `ws://localhost:${PORT}`;

// ── 輸出工具 ──────────────────────────────────────
let passed = 0, failed = 0, skipped = 0;

function ok(step, msg)   { console.log(`  ✅ [PASS] Step ${step}: ${msg}`); passed++;  }
function fail(step, msg) { console.log(`  ❌ [FAIL] Step ${step}: ${msg}`); failed++;  }
function skip(step, msg) { console.log(`  ⚠️  [SKIP] Step ${step}: ${msg}`); skipped++; }
function info(msg)       { console.log(`  ℹ️  ${msg}`); }

// ── WS 工具 ──────────────────────────────────────
function connectWs(timeoutMs = 5000) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(WS_URL);
    const t  = setTimeout(() => { ws.terminate(); reject(new Error('connect timeout')); }, timeoutMs);
    ws.on('open',  () => { clearTimeout(t); resolve(ws); });
    ws.on('error', (e) => { clearTimeout(t); reject(e); });
  });
}

function waitMsg(ws, timeoutMs = 3000) {
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error('msg timeout')), timeoutMs);
    ws.once('message', (raw) => {
      clearTimeout(t);
      try { resolve(JSON.parse(raw)); } catch { resolve(raw.toString()); }
    });
    ws.once('close', (code, reason) => {
      clearTimeout(t);
      reject(Object.assign(new Error('closed'), { code, reason: reason?.toString() }));
    });
  });
}

function waitClose(ws, timeoutMs = 3000) {
  return new Promise((resolve) => {
    const t = setTimeout(() => resolve({ code: -1, reason: 'timeout' }), timeoutMs);
    ws.once('close', (code, reason) => {
      clearTimeout(t);
      resolve({ code, reason: reason?.toString() || '' });
    });
    // 如果已 closed
    if (ws.readyState === WebSocket.CLOSED) resolve({ code: ws._closeCode || -1, reason: '' });
  });
}

async function sendAndExpectClose(ws, msg, expectedCode, expectedReason) {
  ws.send(JSON.stringify(msg));
  // 先嘗試讀取 error 訊息，再等 close
  let errorMsg = null;
  try {
    const m = await waitMsg(ws, 1500);
    errorMsg = m;
  } catch (e) {
    if (e.code) return { ok: e.code === expectedCode, code: e.code, reason: e.reason, errorMsg: null };
  }
  const { code, reason } = await waitClose(ws, 2000);
  return { ok: code === expectedCode && reason.includes(expectedReason), code, reason, errorMsg };
}

// ── DB 工具 ──────────────────────────────────────
function queryDB(sql) {
  try {
    const Database = require('better-sqlite3');
    const db = new Database(DB_PATH, { readonly: true });
    const result = db.prepare(sql).all();
    db.close();
    return result;
  } catch (e) {
    return null;
  }
}

// ── 主流程 ────────────────────────────────────────
async function main() {
  console.log('\n══════════════════════════════════════════════════════');
  console.log('  HOTFIX-WS-01 §8 Human Verification — Pi 500');
  console.log(`  Unit: ${unit.toUpperCase()}  |  WS: ${WS_URL}  |  DB: ${DB_PATH}`);
  console.log('══════════════════════════════════════════════════════\n');

  // ── 前置確認 ──────────────────────────────────
  console.log('【前置確認】');
  if (!VERIFY_USER || !VERIFY_PIN) {
    console.log('  ⚠️  未設定 VERIFY_USER / VERIFY_PIN，Step 6 將略過。');
    console.log('     設定方式：VERIFY_USER=admin VERIFY_PIN=yourpin node server/scripts/ws_human_verify.js\n');
  }

  // ─────────────────────────────────────────────
  // Step 1：確認 server 可連線
  // ─────────────────────────────────────────────
  console.log('【Step 1】確認 server 可連線');
  let ws;
  try {
    ws = await connectWs(5000);
    const welcome = await waitMsg(ws, 3000);
    if (welcome?.type === 'welcome') {
      ok(1, `server 回應 welcome，server_version=${welcome.server_version}`);
    } else {
      fail(1, `預期 welcome，收到：${JSON.stringify(welcome)}`);
    }
    ws.close();
    await waitClose(ws, 1000);
  } catch (e) {
    fail(1, `無法連線：${e.message}`);
    console.log('\n  ➡️  請確認 Pi server 已啟動再重新執行。');
    process.exit(1);
  }

  // 等 server 穩定
  await new Promise(r => setTimeout(r, 300));

  // ─────────────────────────────────────────────
  // Step 2：未認證送 delta → 預期 close(4401, unauthorized)
  // ─────────────────────────────────────────────
  console.log('\n【Step 2】未認證送 delta');
  try {
    ws = await connectWs();
    await waitMsg(ws, 2000); // 吃掉 welcome
    const result = await sendAndExpectClose(ws,
      { type: 'delta', table: 'beds', record: { _id: 'test-001' } },
      4401, 'unauthorized'
    );
    if (result.ok) {
      ok(2, `close(${result.code}, "${result.reason}") — delta 被攔截`);
    } else {
      fail(2, `預期 close(4401, unauthorized)，收到 close(${result.code}, "${result.reason}")`);
    }
    if (result.errorMsg?.reason) info(`收到 error reason="${result.errorMsg.reason}"`);
  } catch (e) {
    fail(2, e.message);
  }
  await new Promise(r => setTimeout(r, 200));

  // ─────────────────────────────────────────────
  // Step 3：未認證送 sync_push → 預期 close(4401, unauthorized)
  // ─────────────────────────────────────────────
  console.log('\n【Step 3】未認證送 sync_push');
  try {
    ws = await connectWs();
    await waitMsg(ws, 2000);
    const result = await sendAndExpectClose(ws,
      { type: 'sync_push', tables: {}, device_id: 'test' },
      4401, 'unauthorized'
    );
    if (result.ok) {
      ok(3, `close(${result.code}, "${result.reason}") — sync_push 被攔截`);
    } else {
      fail(3, `預期 close(4401, unauthorized)，收到 close(${result.code}, "${result.reason}")`);
    }
  } catch (e) {
    fail(3, e.message);
  }
  await new Promise(r => setTimeout(r, 200));

  // ─────────────────────────────────────────────
  // Step 4：未認證送 ping → 預期正常回 pong，連線不斷
  // ─────────────────────────────────────────────
  console.log('\n【Step 4】未認證送 ping（應正常）');
  try {
    ws = await connectWs();
    await waitMsg(ws, 2000); // welcome
    ws.send(JSON.stringify({ type: 'ping' }));
    const resp = await waitMsg(ws, 3000);
    if (resp?.type === 'pong') {
      ok(4, `收到 pong，連線維持（pre-auth gate 未攔截 ping）`);
    } else {
      fail(4, `預期 pong，收到：${JSON.stringify(resp)}`);
    }
    ws.close();
    await waitClose(ws, 1000);
  } catch (e) {
    fail(4, e.message);
  }
  await new Promise(r => setTimeout(r, 200));

  // ─────────────────────────────────────────────
  // Step 5：session_restore 偽造 role → 預期 close(4401, session_expired)
  // ─────────────────────────────────────────────
  console.log('\n【Step 5】session_restore 偽造 role');
  try {
    ws = await connectWs();
    await waitMsg(ws, 2000);
    const result = await sendAndExpectClose(ws,
      { type: 'session_restore', username: 'admin', role: 'commander', session_token: 'fake-token' },
      4401, 'session_expired'
    );
    if (result.ok) {
      ok(5, `close(${result.code}, "${result.reason}") — session_restore Option A 生效`);
    } else {
      fail(5, `預期 close(4401, session_expired)，收到 close(${result.code}, "${result.reason}")`);
    }
    if (result.errorMsg?.reason) info(`收到 error reason="${result.errorMsg.reason}"`);
  } catch (e) {
    fail(5, e.message);
  }
  await new Promise(r => setTimeout(r, 200));

  // ─────────────────────────────────────────────
  // Step 6：正常 auth 後送 delta（應正常）
  // ─────────────────────────────────────────────
  console.log('\n【Step 6】正常 auth 後送 delta');
  if (!VERIFY_USER || !VERIFY_PIN) {
    skip(6, '未設定 VERIFY_USER / VERIFY_PIN，略過');
  } else {
    try {
      ws = await connectWs();
      await waitMsg(ws, 2000); // welcome

      // 送 auth
      ws.send(JSON.stringify({ type: 'auth', username: VERIFY_USER, pin: VERIFY_PIN, device_id: 'verify-script' }));
      const authResp = await waitMsg(ws, 5000);

      if (!authResp?.ok) {
        fail(6, `auth 失敗：${authResp?.reason || 'unknown'}`);
      } else {
        info(`auth 成功：username=${authResp.username} role=${authResp.role}`);

        // 送 delta（auth 後應可通過）
        ws.send(JSON.stringify({
          type: 'delta', table: 'beds',
          record: { _id: 'verify-delta-001', name: '驗證測試床位' },
          src: 'verify-script', ts: new Date().toISOString(),
        }));

        // 等一下確認沒有 error 回來（delta 不回 ack，只是 broadcast）
        await new Promise(r => setTimeout(r, 800));
        if (ws.readyState === WebSocket.OPEN) {
          ok(6, `auth 成功後 delta 通過（連線未被斷，Layer 1 正確放行）`);
        } else {
          fail(6, 'auth 後送 delta，連線被意外斷開');
        }
        ws.close();
        await waitClose(ws, 1000);
      }
    } catch (e) {
      fail(6, e.message);
    }
  }
  await new Promise(r => setTimeout(r, 200));

  // ─────────────────────────────────────────────
  // Step 7：Layer 0 regression（first-run 未完成狀態）
  // ─────────────────────────────────────────────
  console.log('\n【Step 7】Layer 0 regression（PI-01 gate）');
  console.log('  ⚠️  此 Step 需手動確認（需重置 fresh DB）');
  console.log('  手動步驟：');
  console.log('    a. 停止 Pi server');
  console.log(`    b. mv ${DB_PATH} ${DB_PATH}.bak`);
  console.log('    c. 重啟 Pi server（fresh DB，first-run 未完成）');
  console.log(`    d. 連線送 delta：wscat -c ${WS_URL} -x \'{"type":"delta","table":"beds","record":{"_id":"x1"}}\'`);
  console.log('    e. 預期收到 {"type":"error","reason":"setup_required"}，close(4423)');
  console.log(`    f. 還原：mv ${DB_PATH}.bak ${DB_PATH}，重啟 server`);
  skip(7, '需手動執行（見上方步驟）');

  // ─────────────────────────────────────────────
  // Step 8：DB 確認（Step 2/3 的 delta 未寫入）
  // ─────────────────────────────────────────────
  console.log('\n【Step 8】DB 確認（Step 2/3 delta 未寫入）');
  if (!fs.existsSync(DB_PATH)) {
    skip(8, `DB 不存在：${DB_PATH}`);
  } else {
    const rows = queryDB(
      `SELECT COUNT(*) as cnt FROM delta_log
       WHERE recv_at > datetime('now', '-3 minutes')
       AND src NOT LIKE 'verify%'`
    );
    if (rows === null) {
      fail(8, 'DB 查詢失敗（better-sqlite3 可能未安裝）');
    } else {
      const cnt = rows[0]?.cnt ?? '?';
      if (cnt === 0) {
        ok(8, `delta_log 近 3 分鐘新增 ${cnt} 筆 — 未認證 delta 未寫入`);
      } else {
        // Step 6 的 delta 會寫入，排除它
        info(`delta_log 近 3 分鐘有 ${cnt} 筆（Step 6 auth 後 delta 若有寫入屬正常）`);
        const unauth = queryDB(
          `SELECT COUNT(*) as cnt FROM delta_log
           WHERE recv_at > datetime('now', '-3 minutes')
           AND src LIKE '%test%'`
        );
        const unauthCnt = unauth?.[0]?.cnt ?? 0;
        if (unauthCnt === 0) {
          ok(8, `未認證 delta（src LIKE test%）寫入數 = ${unauthCnt} — 確認未寫入`);
        } else {
          fail(8, `發現 ${unauthCnt} 筆未認證 delta 寫入 DB — pre-auth gate 可能有漏`);
        }
      }
    }
  }

  // ── 最終結果 ──────────────────────────────────
  console.log('\n══════════════════════════════════════════════════════');
  console.log('  驗證結果');
  console.log('══════════════════════════════════════════════════════');
  console.log(`  ✅ PASS  : ${passed}`);
  console.log(`  ❌ FAIL  : ${failed}`);
  console.log(`  ⚠️  SKIP  : ${skipped}`);
  console.log('');

  if (failed === 0) {
    console.log('  🟢 整體結論：PASS（Step 7 需另行手動確認）');
    console.log('     可在 GitHub Issue #8 留言：§8 human verification PASS');
  } else {
    console.log('  🔴 整體結論：FAIL — 請回報失敗的 Step 編號');
  }
  console.log('══════════════════════════════════════════════════════\n');

  process.exit(failed > 0 ? 1 : 0);
}

main().catch(e => {
  console.error('腳本執行錯誤：', e);
  process.exit(1);
});
