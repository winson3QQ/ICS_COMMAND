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

/** 詳情面板照片區塊的靜態 HTML（無使用者資料）。openModal 後由 loader 非同步填 grid。
 *  canUpload=true（指揮層）→ 附「上傳照片」控制（#506 M3 下行：推現場照掛此 marker）。 */
export function takPhotoSectionHtml(canUpload = false) {
  const upload = canUpload
    ? '<div style="margin-top:8px;font-size:11px;">' +
      `<label style="cursor:pointer;color:var(--accent,#2b6cd9);">📤 上傳照片<input type="file" id="${TAK_PHOTO_UPLOAD_ID}" accept="image/*" style="display:none"></label>` +
      `<span id="${_UPLOAD_STATUS_ID}" style="margin-left:8px;color:var(--text3);"></span></div>`
    : '';
  // #509-P3 下行：把照片推到現場 client 的地圖（打包 mission-package → b-f-t-r）。收件人多選：
  // 不選＝廣播全體；選一個以上＝點對點。與「上傳照片」（僅 ICS 側顯示）不同——本控制會送達現場。
  const push = canUpload
    ? '<div style="margin-top:8px;font-size:11px;">' +
      `<label style="cursor:pointer;color:var(--accent,#2b6cd9);">📡 推照片到現場<input type="file" id="${TAK_PHOTO_PUSH_ID}" accept="image/*" style="display:none"></label>` +
      `<span id="${_PUSH_STATUS_ID}" style="margin-left:8px;color:var(--text3);"></span>` +
      '<div style="color:var(--text3);margin-top:4px;">收件人（不選＝廣播全體）：</div>' +
      `<select id="${TAK_PHOTO_PUSH_DEST_ID}" multiple size="3" ` +
      'style="width:100%;font-size:11px;margin-top:2px;background:var(--surface);color:var(--text);border:1px solid var(--border);border-radius:4px;">' +
      '</select></div>'
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
  async function load(uid) {
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
      cell.style.cssText = _CELL_STYLE;
      const img = _doc.createElement('img');
      img.alt = '⏳';
      img.style.cssText = _IMG_STYLE;
      cell.appendChild(img);
      grid.appendChild(cell);
      _bindPhoto(img, cell, f.hash);
    }
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

  // 綁定「推照片到現場」控制（openModal 後呼叫）：填 client 選項 → 選檔 → 依所選收件人推送。
  // CSP 安全（addEventListener + property set，非 inline/innerHTML）。
  async function bindPush(uid) {
    const input = _doc.getElementById(TAK_PHOTO_PUSH_ID);
    if (!input || !uid) return;
    const sel = _doc.getElementById(TAK_PHOTO_PUSH_DEST_ID);
    const status = _doc.getElementById(_PUSH_STATUS_ID);
    if (sel) {
      const clients = await fetchClients();
      if (!_doc.getElementById(TAK_PHOTO_PUSH_DEST_ID)) return; // modal 已關 → 別動 DOM
      for (const c of clients) {
        if (!c || !c.callsign) continue;
        const opt = _doc.createElement('option');
        opt.value = c.callsign; // property set → 不可信 callsign 安全（非 innerHTML）
        opt.textContent = c.callsign;
        sel.appendChild(opt);
      }
    }
    input.addEventListener('change', async () => {
      const file = input.files && input.files[0];
      if (!file) return;
      const dest = sel ? Array.from(sel.selectedOptions).map((o) => o.value).join(',') : '';
      if (status) status.textContent = dest ? `推送給 ${dest}…` : '廣播推送…';
      let ok = false;
      try {
        ok = await pushToField(uid, file, dest);
      } catch (_) {
        ok = false;
      }
      input.value = ''; // 清掉，允許再推同一檔
      if (status) status.textContent = ok ? '✓ 已推送現場' : '✕ 推送失敗';
      if (ok) await load(uid); // 本地也掛了 → 重載顯示
    });
  }

  return { load, upload, bindUpload, fetchClients, pushToField, bindPush, _revokeAll };
}
