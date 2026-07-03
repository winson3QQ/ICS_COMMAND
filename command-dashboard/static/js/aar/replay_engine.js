// SPDX-License-Identifier: LicenseRef-Proprietary
// Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
// replay_engine.js — AAR 回放核心（P2-20(B) B1，issue #201）
//
// 純函式、零 DOM：吃 /api/exercises/{id}/timeline 的 items（已按 t 排序的事件流），
// 提供「折疊到時刻 T 的世界狀態」。設計 = 事件流模型（#199 定案）：狀態不預存、
// 由事件折疊重建；Step mode 逐事件跳、Play mode（B2）虛擬時鐘等速掃。
//
// 折疊語意：每個 uid 取 t ≤ T 的**最後一筆** track = 該單位在 T 的已知位置
// （來源回報疏 = 位置停在最後已知點，誠實呈現、不內插——#199 記錄的物理限制）。

import { hasNapsgGlyph } from '../map/napsg_glyphs.js'; // #3：事件型別是否有 NAPSG 象形圖（純資料查詢）
import { cotToSidc } from '../map/mil_symbol.js'; // #3：military regime 事件 → 2525 SIDC（純函式）

/** timeline item 的穩定排序鍵（與後端同序保證：t 字串序；後端已排好，此處僅防禦） */
function _byT(a, b) {
  return a.t < b.t ? -1 : a.t > b.t ? 1 : 0;
}

/**
 * 預處理 timeline items → 回放索引。
 * @param {Array<{type,t,actor,payload}>} items 後端已排序事件流
 * @returns {{steps: Array, trackIdx: Array}} steps=全部事件（側欄列表用）；
 *          trackIdx=僅 track 類（折疊用，仍按 t 序）
 */
/** #3：座標有效性——濾掉 (0,0) null island（ATAK 無 GPS fix / 手點位置前的壞點）+ 非數/超範圍。
 *  否則尾跡會從真實位置拉一條線到 (0,0)，整圖橫線。 */
export function validCoord(lat, lon) {
  return (
    Number.isFinite(lat) && Number.isFinite(lon) &&
    !(lat === 0 && lon === 0) &&
    Math.abs(lat) <= 90 && Math.abs(lon) <= 180
  );
}

export function buildReplayIndex(items) {
  const steps = [...(items || [])].sort(_byT);
  // #3：track 點需有效座標才納入折疊/尾跡（壞點仍留在 steps 側欄列表，方便看到原始回報）。
  const trackIdx = steps.filter(it => it.type === 'track' && validCoord(it.payload?.lat, it.payload?.lon));
  // B2 尾跡：per-uid 點序列（各自天然按 t 序——trackIdx 已排序，依序歸戶即保序）
  const tracksByUid = new Map();
  for (const it of trackIdx) {
    const p = it.payload;
    if (!tracksByUid.has(p.uid)) tracksByUid.set(p.uid, []);
    tracksByUid.get(p.uid).push({ t: it.t, lat: p.lat, lon: p.lon, cot_type: p.cot_type });
  }
  // #338：區域生命週期事件（type='zone'，payload {uid, op, attributes}），折疊用（仍按 t 序）。
  const zoneIdx = steps.filter(it => it.type === 'zone');
  // #339：事件（type='event'，payload 帶 markers），上圖用（occurred_at ≤ T 才畫；仍按 t 序）。
  const eventIdx = steps.filter(it => it.type === 'event');
  return { steps, trackIdx, tracksByUid, zoneIdx, eventIdx };
}

/** #338：折疊區域到時刻 T——每 uid 取 ≤T 最後一筆；op==='deleted' → 移除（T 之後才畫的 / 已刪
 *  的區域不出現）。zoneIdx 已按 t 排序（buildReplayIndex 保證）。回 Map<uid, {uid, attributes, t}>。*/
export function foldZonesAt(zoneIdx, T) {
  const cur = new Map();
  for (const it of zoneIdx) {
    if (it.t > T) break; // 已排序 → 之後全部 > T
    const p = it.payload;
    if (p.op === 'deleted') cur.delete(p.uid);
    else cur.set(p.uid, { uid: p.uid, attributes: p.attributes || {}, t: it.t });
  }
  return cur;
}

/** #338：折疊區域 Map → GeoJSON。polygon→Polygon（自動閉合環）、route→LineString。
 *  vertices 為 [[lat,lon],...] → GeoJSON [lon,lat]。<2 點不畫（成不了線/面）。 */
export function zonesToGeoJSON(zoneMap) {
  const features = [];
  for (const z of zoneMap.values()) {
    const a = z.attributes || {};
    const verts = a.vertices || [];
    if (verts.length < 2) continue;
    const coords = verts.map(([lat, lon]) => [lon, lat]);
    const props = { uid: z.uid, kind: a.kind, color: a.color || '#888888', dash: !!a.dash };
    let geometry;
    if (a.kind === 'polygon') {
      const ring = coords.slice();
      const [f, l] = [ring[0], ring[ring.length - 1]];
      if (f[0] !== l[0] || f[1] !== l[1]) ring.push(f); // 閉合環
      geometry = { type: 'Polygon', coordinates: [ring] };
    } else {
      geometry = { type: 'LineString', coordinates: coords };
    }
    features.push({ type: 'Feature', geometry, properties: props });
  }
  return { type: 'FeatureCollection', features };
}

/**
 * 折疊到 T：每 uid 取 t ≤ T 的最後一筆 track。
 * O(trackIdx 長度)；B1 每次 Step 全掃（事件量級千~萬筆可承受）。
 * B2 若 Play mode 量測偏慢 → 加增量游標 / 記憶體 keyframe（#199 定案：量測證明慢才加）。
 * @returns {Map<string, {uid,lat,lon,t,actor,heading_deg,speed_mps}>}
 */
export function foldPositionsAt(trackIdx, T) {
  const pos = new Map();
  for (const it of trackIdx) {
    if (it.t > T) break; // 已排序 → 之後全部 > T
    const p = it.payload;
    pos.set(p.uid, {
      uid: p.uid,
      lat: p.lat,
      lon: p.lon,
      t: it.t,
      actor: it.actor,
      heading_deg: p.heading_deg,
      speed_mps: p.speed_mps,
      cot_type: p.cot_type,  // #4：畫 2525 符號用
    });
  }
  return pos;
}

/** 折疊結果 → GeoJSON FeatureCollection。#4 起 aar_map 改用自家 _unitsToGeoJSON（多帶
 *  iconId/affiliation 畫 2525 符號），本函式僅供測試/legacy 純資料驗證，非 live source builder。 */
export function positionsToGeoJSON(posMap) {
  return {
    type: 'FeatureCollection',
    features: [...posMap.values()].map(p => ({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [p.lon, p.lat] },
      properties: { uid: p.uid, callsign: p.actor, t: p.t, cot_type: p.cot_type },
    })),
  };
}

/** #2：標記位置歷史 [[t,lat,lon],...]（audit，按時序）折疊到 T——取 ≤T 最後一筆。無 → null。 */
function _foldMarkerHistory(history, T) {
  let last = null;
  for (const h of history || []) {
    if (h[0] > T) break; // 已排序
    last = h;
  }
  return last; // [t, lat, lon] | null
}

/** 事件 → GeoJSON 點，**重用 live COP 原本的事件符號**（severity ◆ + NAPSG 象形/abbr 前景）。
 *  occurred_at ≤ T 才畫。位置優先序（#2 跟隨移動）：① TAK 標記 → posMap 折疊位置（有 tracks）；
 *  ② 手動標記 → audit 位置歷史折疊 @ T；③ 退回標記錨點 lat/lon。無標記 → 不上圖（側欄仍在）。
 *  props 對齊 live `_renderZones` 推導（map.js:3215-3242）：military regime → milsymbol 2525
 *  iconId（由 taxonomy cot_type 生 SIDC；烤不出退 civil ◆）；civil/alert → ◆/▲ + fg（napsg-glyph
 *  有象形圖、否則 napsg-abbr-<abbr>）。#1：label=description。
 *  @param {object} evTax event_type→{abbr,regime,cot_type}（/api/event_taxonomy，與 live 同 SoT）。 */
export function eventsToGeoJSON(eventIdx, T, posMap, evTax = {}) {
  const features = [];
  for (const it of eventIdx) {
    if (it.t > T) break; // 已排序 → 之後全部 > T
    const p = it.payload;
    const markers = p.markers || [];
    const m = markers.find(x => x.role === 'primary') || markers[0];
    if (!m) continue; // 無標記 → 不上圖
    const folded = posMap?.get(m.uid);
    const hist = folded ? null : _foldMarkerHistory(m.history, T); // TAK 走 posMap，手動走 audit 歷史
    const lat = folded ? folded.lat : (hist ? hist[1] : m.lat);
    const lon = folded ? folded.lon : (hist ? hist[2] : m.lon);
    if (!validCoord(lat, lon)) continue;
    const et = p.event_type || '';
    const tax = evTax[et] || {};
    const abbr = tax.abbr || '?';
    const fgGlyph = hasNapsgGlyph(et); // 6 種有象形圖；其餘 → 退 abbr（同 live）
    // 視覺規制（同 live）：military → milsymbol 2525 框（drone 紅機/QRF 藍方）；civil/alert → ◆/▲。
    let regime = tax.regime || 'civil';
    let iconId = null;
    if (regime === 'military') {
      const sidc = cotToSidc(tax.cot_type);
      if (sidc) iconId = 'mil-' + sidc;
      else regime = 'civil'; // 烤不出 SIDC → 退 ◆，避免 icon-image 找不到變隱形
    }
    features.push({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [lon, lat] },
      properties: {
        event_id: p.event_id, event_code: p.event_code || '', event_type: et,
        severity: p.severity || 'info', status: p.status || '',
        is_event: true, regime, iconId,
        abbr, fg: fgGlyph ? ('napsg-glyph-' + et) : ('napsg-abbr-' + abbr), fg_glyph: fgGlyph,
        label: p.description || p.event_code || '', // #1：人類描述優先
      },
    });
  }
  return { type: 'FeatureCollection', features };
}

/** type → 側欄顯示中文標籤（與後端 timeline type 對齊） */
export const TYPE_LABELS = {
  track: '軌跡',
  chat: '通聯',
  event: '事件',
  event_status: '事件狀態',
  decision: '決策',
  command: '指令',
  zone: '區域',
  classification: '分類變更', // #475：重分隊（含中途改隊）
};

const _ZONE_OP_LABEL = { created: '建立', updated: '更新', deleted: '刪除' };

/** 側欄一筆的摘要文字（純資料 → 字串；DOM 由 caller 以 textContent 塞，不經 innerHTML） */
export function stepSummary(it) {
  const p = it.payload || {};
  switch (it.type) {
    case 'track':
      return `${it.actor} → (${typeof p.lat === 'number' ? p.lat.toFixed(5) : '—'}, ${typeof p.lon === 'number' ? p.lon.toFixed(5) : '—'})`;
    case 'chat':
      return `${it.actor}${p.group ? `@${p.group}` : ''}：${p.message || ''}`;
    case 'event':
      return `${p.event_code || ''} ${p.event_type || ''}（${p.severity || ''}）${p.description || ''}`;
    case 'event_status':
      return `${it.actor} 更新事件狀態 → ${p.detail?.status || ''}`;
    case 'decision':
      return p.action === 'decision_made'
        ? `${it.actor} 裁示：${p.detail?.action || ''}`
        : `${it.actor} 提案：${p.detail?.title || ''}`;
    case 'command':
      return `${it.actor} 下行：${p.action || ''} ${p.detail?.callsign || p.target || ''}`;
    case 'zone': {
      const a = p.attributes || {};
      return `${_ZONE_OP_LABEL[p.op] || p.op} ${a.kind === 'route' ? '路線' : '區域'}${a.poly_type || a.route_type ? `（${a.poly_type || a.route_type}）` : ''}`;
    }
    default:
      return it.actor || '';
  }
}

/** ISO Z → 本地 HH:MM:SS（側欄時間欄）。非法值原樣回。 */
export function fmtClock(isoZ) {
  const d = new Date(isoZ);
  return Number.isNaN(d.getTime()) ? String(isoZ) : d.toLocaleTimeString('zh-TW', { hour12: false });
}

/** 書籤跳轉（P2-21 #204）：t ≤ refT 的**最後一筆** step index；全部 > refT → 0（跳開頭）。
 *  steps 已按 t 排序（buildReplayIndex 保證）。 */
export function stepIndexAtOrBefore(steps, refT) {
  let idx = 0;
  for (let i = 0; i < steps.length; i++) {
    if (steps[i].t > refT) break;
    idx = i;
  }
  return idx;
}

// ── B2 Play mode（#201）────────────────────────────────────────────────────

/** ISO Z ↔ epoch ms（slider/虛擬時鐘的數值域） */
export function tToMs(isoZ) {
  return new Date(isoZ).getTime();
}

/** epoch ms → ISO Z（秒精度，與 timeline t 同格式、可直接字串比較） */
export function msToT(ms) {
  return new Date(ms).toISOString().replace(/\.\d{3}Z$/, 'Z');
}

/**
 * 虛擬時鐘推進（純函式；caller 在 rAF 餵 wall-clock delta）。
 * 用 wall-delta × speed 而非固定步長：背景分頁 rAF 被節流 → 回前景自動跳補到正確 T。
 * @returns {{tMs: number, ended: boolean}} 到 endMs 夾住並回 ended=true（caller 停播）。
 */
export function advanceClock(tMs, wallDeltaMs, speed, endMs) {
  const next = tMs + wallDeltaMs * speed;
  if (next >= endMs) return { tMs: endMs, ended: true };
  return { tMs: next, ended: false };
}

/**
 * 增量折疊游標（B2 前進播放 O(Δ新事件)；#199 定案不做 keyframe）。
 * cursor = {idx, pos}（**就地推進**）；T 倒退時 caller 用 resetFoldCursor 重來（全折疊）。
 */
export function makeFoldCursor() {
  return { idx: 0, pos: new Map() };
}

export function advanceFold(trackIdx, cursor, T) {
  while (cursor.idx < trackIdx.length && trackIdx[cursor.idx].t <= T) {
    const it = trackIdx[cursor.idx];
    const p = it.payload;
    cursor.pos.set(p.uid, {
      uid: p.uid, lat: p.lat, lon: p.lon, t: it.t, actor: it.actor,
      heading_deg: p.heading_deg, speed_mps: p.speed_mps, cot_type: p.cot_type,  // #4
    });
    cursor.idx += 1;
  }
  return cursor.pos;
}

/**
 * 尾跡：每 uid 取 [T - windowMin 分, T] 窗口內的點 → LineString FeatureCollection。
 * <2 點的 uid 不畫（畫不成線）。tracksByUid 各陣列已按 t 序（buildReplayIndex 保證）。
 */
export function trailGeoJSON(tracksByUid, T, windowMin = 10) {
  const from = msToT(tToMs(T) - windowMin * 60_000);
  const features = [];
  for (const [uid, pts] of tracksByUid) {
    const seg = pts.filter(p => p.t >= from && p.t <= T);
    if (seg.length < 2) continue;
    features.push({
      type: 'Feature',
      geometry: { type: 'LineString', coordinates: seg.map(p => [p.lon, p.lat]) },
      properties: { uid, cot_type: seg[seg.length - 1].cot_type },  // #4：尾跡依敵我態配色（取最新點型別）
    });
  }
  return { type: 'FeatureCollection', features };
}
