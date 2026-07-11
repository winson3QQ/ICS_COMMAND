// SPDX-License-Identifier: LicenseRef-Proprietary
// Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
/**
 * tak_photos.js — #503 上行：TAK file store 照片附件（地點型雙向照片的「上行」半）
 *
 * 現場 ATAK/iTAK 把照片存進 TAK Enterprise Sync file store、附在自己的 marker 上；ICS 從
 * marker uid 拉下來，在該 marker 的唯讀詳情面板顯示縮圖，點縮圖 → 新分頁開原圖。
 *
 * 認證 / 顯示（不用 `<img src=API>` 直載）：走 authFetch 取 bytes（兼 dev header-token /
 * prod httpOnly cookie 兩套 auth）→ blob objectURL（CSP `img-src 'self' data: blob:` 允許）
 * → 純 img，`.src`/`.href` 用 property 設（非 innerHTML），無 inline handler → CSP 全安全。
 *
 * 依賴注入（authFetch / doc）：純邏輯、可 vitest 測（map.js 重 import maplibregl，本檔抽出免
 * 連累）。對齊 map/entity_layer.js、map/create_popup.js 的「可測子模組」慣例。
 */

export const TAK_PHOTO_GRID_ID = 'tak-photo-grid';
export const TAK_PHOTO_UPLOAD_ID = 'tak-photo-upload';
export const TAK_PHOTO_PUSH_ID = 'tak-photo-push'; // #509-P3 下行「推到現場」檔案輸入
export const TAK_PHOTO_PUSH_DEST_ID = 'tak-photo-push-dest'; // 收件人多選（不選＝廣播）
const _UPLOAD_STATUS_ID = 'tak-photo-upload-status';
const _PUSH_STATUS_ID = 'tak-photo-push-status';
const _CELL_STYLE =
  'display:flex;align-items:center;justify-content:center;width:72px;height:72px;' +
  'border:1px solid var(--border);border-radius:6px;overflow:hidden;background:var(--surface);text-decoration:none;';
const _IMG_STYLE = 'width:100%;height:100%;object-fit:cover;';
// #518 方向徽記（左上）+ 刪除鈕（右上）：絕對定位疊在縮圖上。CSP 安全（style property，非 inline handler）。
const _BADGE_STYLE =
  'position:absolute;top:1px;left:1px;font-size:11px;line-height:1;padding:1px 2px;border-radius:3px;' +
  'background:rgba(0,0,0,0.55);color:#fff;pointer-events:none;';
const _DEL_BTN_STYLE =
  'position:absolute;top:1px;right:1px;font-size:11px;line-height:1;padding:1px 3px;border:none;border-radius:3px;' +
  'background:rgba(0,0,0,0.55);color:#fff;cursor:pointer;';
const _DIALOG_OVERLAY_STYLE =
  'position:fixed;inset:0;z-index:10000;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,0.4);';
const _DIALOG_BOX_STYLE =
  'max-width:320px;padding:14px 16px;border-radius:8px;background:var(--surface,#fff);color:var(--text,#111);' +
  'border:1px solid var(--border,#ccc);box-shadow:0 4px 16px rgba(0,0,0,0.3);font-size:13px;';
const _DIALOG_BTN_STYLE =
  'padding:4px 10px;font-size:12px;border:1px solid var(--border,#ccc);border-radius:4px;cursor:pointer;background:var(--surface,#fff);color:var(--text,#111);';
// #509-P3 收件人選擇視窗（推照片到現場）：較寬、可捲動。
const _PUSH_BOX_STYLE =
  'width:360px;max-width:92vw;max-height:80vh;overflow:auto;padding:14px 16px;border-radius:8px;' +
  'background:var(--surface,#fff);color:var(--text,#111);border:1px solid var(--border,#ccc);' +
  'box-shadow:0 4px 16px rgba(0,0,0,0.3);font-size:13px;';
const _PUSH_ROW = 'display:flex;align-items:center;gap:8px;padding:6px 2px;cursor:pointer;font-size:13px;';
const _FACTION_LABEL = { blue: '藍軍', red: '紅軍', neutral: '中立' };

// #509-P3 下行：把照片推到現場 client 的地圖（打包 mission-package → b-f-t-r）。收件人多選：
// 不選＝廣播全體；選一個以上＝點對點。與「上傳照片」（僅 ICS 側顯示）不同——本控制會送達現場。
function _pushControlHtml() {
  // 選檔後彈出收件人選擇視窗（廣播/群組/指定裝置）——收件方式移到視窗、不再擠在窄面板裡。
  return (
    '<div style="margin-top:8px;font-size:11px;">' +
    `<label style="cursor:pointer;color:var(--accent,#2b6cd9);">📡 推照片到現場<input type="file" id="${TAK_PHOTO_PUSH_ID}" accept="image/*" style="display:none"></label>` +
    `<span id="${_PUSH_STATUS_ID}" style="margin-left:8px;color:var(--text3);"></span></div>`
  );
}

/** 詳情面板照片區塊的靜態 HTML（無使用者資料）。openModal 後由 loader 非同步填 grid。
 *  canUpload=true（指揮層）→ 附「上傳照片」+「推照片到現場」控制。
 *  opts.pushOnly=true（#509-P3 乙）→ 只出「推照片到現場」（ICS 自建 marker 無上行照片可看，
 *  故省 grid/上傳；仍可把 ICS 的觀察〔如無線電情報〕配圖推到現場）。 */
export function takPhotoSectionHtml(canUpload = false, opts = {}) {
  const push = canUpload ? _pushControlHtml() : '';
  if (opts.pushOnly) {
    // 乙：只推送。無 canUpload（指揮層）→ 不出任何控制（回空字串，呼叫端不加區塊）。
    return push
      ? '<div style="margin-top:12px;border-top:1px solid var(--border);padding-top:8px;">' +
          '<div style="font-size:11px;color:var(--text3);margin-bottom:2px;">📷 現場照片</div>' +
          push +
          '</div>'
      : '';
  }
  const upload = canUpload
    ? '<div style="margin-top:8px;font-size:11px;">' +
      `<label style="cursor:pointer;color:var(--accent,#2b6cd9);">📤 上傳照片<input type="file" id="${TAK_PHOTO_UPLOAD_ID}" accept="image/*" style="display:none"></label>` +
      `<span id="${_UPLOAD_STATUS_ID}" style="margin-left:8px;color:var(--text3);"></span></div>`
    : '';
  return (
    '<div style="margin-top:12px;border-top:1px solid var(--border);padding-top:8px;">' +
    '<div style="font-size:11px;color:var(--text3);margin-bottom:6px;">📷 TAK 照片附件</div>' +
    `<div id="${TAK_PHOTO_GRID_ID}" style="display:flex;flex-wrap:wrap;gap:6px;font-size:11px;color:var(--text3);">載入中…</div>` +
    upload +
    push +
    '</div>'
  );
}

/**
 * 建照片 loader。authFetch / doc 注入（測試可餵 fake）。
 * @param {object} deps
 *   - authFetch: (url) => Promise<Response>（必填）
 *   - doc: Document（預設 window.document）
 */
export function createTakPhotoLoader({ authFetch, doc } = {}) {
  if (typeof authFetch !== 'function') throw new Error('createTakPhotoLoader: authFetch required');
  // 惰性預設（不在參數預設直接引 document，否則 node 測試環境無 document 會 ReferenceError）
  const _doc = doc || (typeof document !== 'undefined' ? document : null);
  let objectUrls = []; // 目前 modal 造出的 blob URL；開新 modal 前一律 revoke（免累積洩漏）

  function _revokeAll() {
    for (const u of objectUrls) {
      try {
        URL.revokeObjectURL(u);
      } catch (_) {
        /* revoke 失敗無害 */
      }
    }
    objectUrls = [];
  }

  // 取單張圖 bytes → blob → 綁到 img（縮圖）+ a（點開原圖）。失敗僅標記單格，不炸整區。
  async function _bindPhoto(img, cell, hash) {
    try {
      const r = await authFetch(`/api/tak/files/${encodeURIComponent(hash)}`);
      if (!r.ok) {
        img.alt = '✕';
        return;
      }
      const url = URL.createObjectURL(await r.blob());
      objectUrls.push(url);
      img.src = url;
      cell.href = url; // 點縮圖 → 新分頁開原圖
    } catch (_) {
      img.alt = '✕';
    }
  }

  /**
   * 拉某 marker 的 file store 附件清單 → 逐張塞縮圖進 #tak-photo-grid。
   * best-effort：清單失敗顯示錯誤字、單張失敗只標該格。回傳 Promise（測試可 await）。
   */
  async function load(uid, opts = {}) {
    const canDelete = !!opts.canDelete; // #518：指揮層才給刪除鈕（後端 COMMAND_ROLES 為權威守門）
    const grid = _doc.getElementById(TAK_PHOTO_GRID_ID);
    if (!grid || !uid) return;
    _revokeAll(); // revoke 前一個 modal 的 blob
    let files;
    try {
      const r = await authFetch(`/api/tak/files/for-entity/${encodeURIComponent(uid)}`);
      if (r.status === 422) {
        grid.textContent = 'TAK 未配置';
        return;
      }
      if (!r.ok) {
        grid.textContent = '載入失敗';
        return;
      }
      files = (await r.json()).files || [];
    } catch (_) {
      grid.textContent = '載入失敗';
      return;
    }
    if (!_doc.getElementById(TAK_PHOTO_GRID_ID)) return; // modal 已被關 → 別動 DOM
    if (!files.length) {
      grid.textContent = '無照片';
      return;
    }
    grid.textContent = '';
    for (const f of files) {
      if (!f.hash) continue;
      const cell = _doc.createElement('a');
      cell.target = '_blank';
      cell.rel = 'noopener';
      cell.title = f.name || 'photo'; // property set（非 innerHTML）→ 不可信檔名安全
      cell.style.cssText = _CELL_STYLE + 'position:relative;';
      const img = _doc.createElement('img');
      img.alt = '⏳';
      img.style.cssText = _IMG_STYLE;
      cell.appendChild(img);
      // #518 方向徽記：⬆ 上行（現場→指揮部）/ ⬇ 下行（指揮部→現場）。徽記無 pointer-events，不擋點圖。
      if (f.direction) {
        const badge = _doc.createElement('span');
        badge.textContent = f.direction === 'downlink' ? '⬇' : '⬆';
        badge.title = f.direction === 'downlink' ? '下行（指揮部→現場）' : '上行（現場→指揮部）';
        badge.style.cssText = _BADGE_STYLE;
        cell.appendChild(badge);
      }
      // #518 刪除鈕：僅本地附件（f.local）+ 指揮層。點鈕不觸發縮圖連結（preventDefault/stopPropagation）。
      if (canDelete && f.local) {
        const del = _doc.createElement('button');
        del.type = 'button';
        del.textContent = '🗑';
        del.title = '刪除此照片';
        del.style.cssText = _DEL_BTN_STYLE;
        del.addEventListener('click', (e) => {
          e.preventDefault();
          e.stopPropagation();
          _openDeleteConfirm(f, uid);
        });
        cell.appendChild(del);
      }
      grid.appendChild(cell);
      _bindPhoto(img, cell, f.hash);
    }
  }

  // #518：刪一張本地附件 → DELETE /api/tak/files/{hash}[?purge_server=true]。同源（過 CSRF 守門）+ auth。
  async function deleteAttachment(hash, purgeServer) {
    const q = purgeServer ? '?purge_server=true' : '';
    const r = await authFetch(`/api/tak/files/${encodeURIComponent(hash)}${q}`, { method: 'DELETE' });
    return r.ok;
  }

  // #518 方向感知刪除確認框（純 DOM builder，可測）。方向決定 L2 checkbox 預設：
  //   下行（ICS 為 owner）→ 預設連 server 清；上行（源頭在現場）→ 預設只清本地。無 pkg_hash → checkbox 停用。
  function buildDeleteDialog(f, { onConfirm, onCancel } = {}) {
    const isDown = f.direction === 'downlink';
    const purgeable = !!f.server_purgeable;
    const overlay = _doc.createElement('div');
    overlay.style.cssText = _DIALOG_OVERLAY_STYLE;
    const box = _doc.createElement('div');
    box.style.cssText = _DIALOG_BOX_STYLE;

    const title = _doc.createElement('div');
    title.textContent = '刪除此照片？';
    title.style.cssText = 'font-weight:600;margin-bottom:6px;';
    box.appendChild(title);

    const dir = _doc.createElement('div');
    dir.textContent = isDown
      ? '下行照片（指揮部→現場，指揮部為擁有者）'
      : f.direction === 'uplink'
        ? '上行照片（現場→指揮部，來源在現場）'
        : '方向未知（舊資料）';
    dir.style.cssText = 'font-size:12px;color:var(--text3,#888);margin-bottom:6px;';
    box.appendChild(dir);

    const label = _doc.createElement('label');
    label.style.cssText = 'display:flex;align-items:center;gap:6px;font-size:12px;margin-bottom:6px;';
    const cb = _doc.createElement('input');
    cb.type = 'checkbox';
    cb.checked = purgeable && isDown; // 方向感知預設
    cb.disabled = !purgeable;
    label.appendChild(cb);
    const cbText = _doc.createElement('span');
    cbText.textContent = purgeable ? '連 TAK server 檔庫一起清（不可逆）' : '（無所屬 server 檔，僅能清本地）';
    label.appendChild(cbText);
    box.appendChild(label);

    const note = _doc.createElement('div');
    note.textContent = '⚠ 現場持有裝置本機仍會保留此照片，指揮部無法遠端刪除。';
    note.style.cssText = 'font-size:11px;color:var(--warning,#c60);margin-bottom:8px;';
    box.appendChild(note);

    const btnRow = _doc.createElement('div');
    btnRow.style.cssText = 'display:flex;gap:8px;justify-content:flex-end;';
    const cancel = _doc.createElement('button');
    cancel.type = 'button';
    cancel.textContent = '取消';
    cancel.style.cssText = _DIALOG_BTN_STYLE;
    cancel.addEventListener('click', () => onCancel && onCancel());
    const confirm = _doc.createElement('button');
    confirm.type = 'button';
    confirm.textContent = '確定刪除';
    confirm.style.cssText = _DIALOG_BTN_STYLE + 'background:var(--danger,#c0392b);color:#fff;border-color:var(--danger,#c0392b);';
    confirm.addEventListener('click', () => onConfirm && onConfirm(cb.checked));
    btnRow.appendChild(cancel);
    btnRow.appendChild(confirm);
    box.appendChild(btnRow);

    overlay.appendChild(box);
    return overlay;
  }

  // 開刪除確認框（append 到 body）→ 確定則刪除；**無論成敗都 reload**（成功→照片消失、失敗→照片仍在＝
  // 誠實反映真實狀態，不留「按了沒反應」的錯覺）；失敗另彈提示。取消則移除。
  function _openDeleteConfirm(f, uid) {
    if (!_doc.body) return;
    const dialog = buildDeleteDialog(f, {
      onConfirm: async (purgeServer) => {
        dialog.remove();
        let ok = false;
        try {
          ok = await deleteAttachment(f.hash, purgeServer);
        } catch (_) {
          ok = false;
        }
        if (!ok && typeof alert === 'function') alert('刪除失敗（權限/連線問題），照片仍在。');
        await load(uid, { canDelete: true }); // 成敗都重載：反映真實狀態
      },
      onCancel: () => dialog.remove(),
    });
    _doc.body.appendChild(dialog);
  }

  // #506 M3 下行：上傳一張照片掛到 marker → POST /api/tak/files/upload（multipart）。
  // 同源 POST（Sec-Fetch-Site same-origin，過 #293 CSRF 守門）+ cookie/token auth（authFetch）。
  async function upload(uid, file) {
    const fd = new FormData();
    fd.append('marker_uid', uid);
    fd.append('file', file);
    const r = await authFetch('/api/tak/files/upload', { method: 'POST', body: fd });
    return r.ok;
  }

  // 綁定上傳控制（openModal 後呼叫）：選檔 → 上傳 → 成功後 reload 顯示。CSP 安全（addEventListener，非 inline）。
  function bindUpload(uid) {
    const input = _doc.getElementById(TAK_PHOTO_UPLOAD_ID);
    if (!input || !uid) return;
    const status = _doc.getElementById(_UPLOAD_STATUS_ID);
    input.addEventListener('change', async () => {
      const file = input.files && input.files[0];
      if (!file) return;
      if (status) status.textContent = '上傳中…';
      let ok = false;
      try {
        ok = await upload(uid, file);
      } catch (_) {
        ok = false;
      }
      input.value = ''; // 清掉，允許再上傳同一檔
      if (status) status.textContent = ok ? '✓ 已上傳' : '✕ 上傳失敗';
      if (ok) await load(uid); // 重載顯示新照片
    });
  }

  // #509-P3 下行「推到現場」：線上 client 名單（供收件人多選）。best-effort：失敗回空（仍可廣播）。
  async function fetchClients() {
    try {
      const r = await authFetch('/api/tak/clients');
      if (!r.ok) return [];
      return (await r.json()).clients || [];
    } catch (_) {
      return [];
    }
  }

  // 推一張照片到現場（打包 zip + b-f-t-r）→ POST /api/tak/downlink/photo。dest 空＝廣播、逗號分隔＝點對點。
  async function pushToField(uid, file, dest) {
    const fd = new FormData();
    fd.append('marker_uid', uid);
    fd.append('file', file);
    if (dest) fd.append('dest', dest);
    const r = await authFetch('/api/tak/downlink/photo', { method: 'POST', body: fd });
    return r.ok;
  }

  // 收件人選擇視窗（純 DOM builder，可測）：廣播 / 群組（隊伍 team_color + 陣營 faction 兩軸）/ 指定裝置。
  // 群組與裝置可混選，取聯集去重；不選任何 = 廣播全體。faction 可見度已由後端守門（非 admin 拿不到紅
  // → 紅群自然不出）。dest 只列真裝置（後端 _is_contact_unit 濾掉標記）。onSend(destArray) 回解析後 callsign。
  function buildPushDialog(clients, { onSend, onCancel } = {}, opts = {}) {
    clients = (clients || []).filter((c) => c && c.callsign);
    const teams = new Map(); // team_color -> [callsign]
    const factions = new Map(); // faction -> [callsign]
    for (const c of clients) {
      const tc = (c.team_color || '').trim();
      if (tc) {
        if (!teams.has(tc)) teams.set(tc, []);
        teams.get(tc).push(c.callsign);
      }
      const fa = (c.faction || '').trim();
      if (fa) {
        if (!factions.has(fa)) factions.set(fa, []);
        factions.get(fa).push(c.callsign);
      }
    }
    const selGroups = new Map(); // key -> [members]
    const selClients = new Set();

    const overlay = _doc.createElement('div');
    overlay.style.cssText = _DIALOG_OVERLAY_STYLE;
    const box = _doc.createElement('div');
    box.style.cssText = _PUSH_BOX_STYLE;

    const title = _doc.createElement('div');
    title.textContent = '📡 推照片到現場';
    title.style.cssText = 'font-weight:600;margin-bottom:2px;';
    box.appendChild(title);
    if (opts.filename) {
      const fn = _doc.createElement('div');
      fn.textContent = '檔案：' + opts.filename;
      fn.style.cssText = 'font-size:11px;color:var(--text3,#888);margin-bottom:6px;';
      box.appendChild(fn);
    }
    const hint = _doc.createElement('div');
    hint.textContent = '不選任何 = 廣播全體';
    hint.style.cssText = 'font-size:11px;color:var(--text3,#888);margin-bottom:4px;';
    box.appendChild(hint);

    const send = _doc.createElement('button');
    function _resolve() {
      const set = new Set();
      for (const members of selGroups.values()) for (const m of members) set.add(m);
      for (const cs of selClients) set.add(cs);
      return Array.from(set).sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()));
    }
    function _refresh() {
      const n = _resolve().length;
      send.textContent = n ? `送出（${n} 個收件人）` : '送出（廣播全體）';
    }
    function _row(label, onToggle, sub) {
      const row = _doc.createElement('label');
      row.style.cssText = _PUSH_ROW;
      const cb = _doc.createElement('input');
      cb.type = 'checkbox';
      cb.addEventListener('change', () => {
        onToggle(cb.checked);
        _refresh();
      });
      row.appendChild(cb);
      const txt = _doc.createElement('span');
      txt.textContent = label;
      txt.style.cssText = 'flex:1;';
      row.appendChild(txt);
      if (sub) {
        const s = _doc.createElement('span');
        s.textContent = sub;
        s.style.cssText = 'font-size:11px;color:var(--text3,#888);';
        row.appendChild(s);
      }
      box.appendChild(row);
    }
    function _seclabel(t) {
      const d = _doc.createElement('div');
      d.textContent = t;
      d.style.cssText = 'font-size:11px;color:var(--text3,#888);margin:10px 0 2px;';
      box.appendChild(d);
    }

    if (teams.size) {
      _seclabel('群組 · 隊伍');
      for (const [tc, members] of teams)
        _row(tc + ' 隊', (on) => (on ? selGroups.set('t:' + tc, members) : selGroups.delete('t:' + tc)), members.length + ' 台');
    }
    if (factions.size) {
      _seclabel('群組 · 陣營');
      for (const [fa, members] of factions)
        _row(_FACTION_LABEL[fa] || fa, (on) => (on ? selGroups.set('f:' + fa, members) : selGroups.delete('f:' + fa)), members.length + ' 台');
    }
    _seclabel('指定裝置');
    if (!clients.length) {
      const none = _doc.createElement('div');
      none.textContent = '目前無在線裝置（可廣播）';
      none.style.cssText = 'font-size:12px;color:var(--text3,#888);padding:4px 2px;';
      box.appendChild(none);
    }
    for (const c of clients) _row(c.callsign, (on) => (on ? selClients.add(c.callsign) : selClients.delete(c.callsign)));

    const note = _doc.createElement('div');
    note.textContent = '⚠ 只送給連線中的裝置；現場裝置本機仍會保留照片。';
    note.style.cssText = 'font-size:11px;color:var(--warning,#c60);margin:10px 0 8px;';
    box.appendChild(note);

    const btnRow = _doc.createElement('div');
    btnRow.style.cssText = 'display:flex;gap:8px;justify-content:flex-end;';
    const cancel = _doc.createElement('button');
    cancel.type = 'button';
    cancel.textContent = '取消';
    cancel.style.cssText = _DIALOG_BTN_STYLE;
    cancel.addEventListener('click', () => onCancel && onCancel());
    send.type = 'button';
    send.style.cssText = _DIALOG_BTN_STYLE + 'background:var(--accent,#2b6cd9);color:#fff;border-color:var(--accent,#2b6cd9);';
    send.addEventListener('click', () => onSend && onSend(_resolve()));
    btnRow.appendChild(cancel);
    btnRow.appendChild(send);
    box.appendChild(btnRow);
    _refresh();

    overlay.appendChild(box);
    return overlay;
  }

  // 開收件人選擇視窗（選檔後）→ 送出解析 dest → pushToField → reload。
  async function _openPushDialog(uid, file) {
    if (!_doc.body) return;
    const status = _doc.getElementById(_PUSH_STATUS_ID);
    let clients = [];
    try {
      clients = await fetchClients();
    } catch (_) {
      clients = [];
    }
    const dialog = buildPushDialog(
      clients,
      {
        onSend: async (dest) => {
          dialog.remove();
          if (status) status.textContent = dest.length ? `推送給 ${dest.length} 個…` : '廣播推送…';
          let ok = false;
          try {
            ok = await pushToField(uid, file, dest.join(','));
          } catch (_) {
            ok = false;
          }
          if (status) status.textContent = ok ? '✓ 已推送現場' : '✕ 推送失敗';
          if (ok) await load(uid, { canDelete: true }); // 本地也掛了 → 重載顯示
        },
        onCancel: () => dialog.remove(),
      },
      { filename: file && file.name },
    );
    _doc.body.appendChild(dialog);
  }

  // 綁定「推照片到現場」控制（openModal 後呼叫）：選檔 → 彈收件人視窗選收件方式 → 推送。
  function bindPush(uid) {
    const input = _doc.getElementById(TAK_PHOTO_PUSH_ID);
    if (!input || !uid) return;
    input.addEventListener('change', async () => {
      const file = input.files && input.files[0];
      if (!file) return;
      input.value = ''; // 清掉，允許再推同一檔
      await _openPushDialog(uid, file);
    });
  }

  return {
    load,
    upload,
    bindUpload,
    fetchClients,
    pushToField,
    bindPush,
    buildPushDialog,
    deleteAttachment,
    buildDeleteDialog,
    _revokeAll,
  };
}
