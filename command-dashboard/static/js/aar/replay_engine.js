// replay_engine.js — AAR 回放核心（P2-20(B) B1，issue #201）
//
// 純函式、零 DOM：吃 /api/exercises/{id}/timeline 的 items（已按 t 排序的事件流），
// 提供「折疊到時刻 T 的世界狀態」。設計 = 事件流模型（#199 定案）：狀態不預存、
// 由事件折疊重建；Step mode 逐事件跳、Play mode（B2）虛擬時鐘等速掃。
//
// 折疊語意：每個 uid 取 t ≤ T 的**最後一筆** track = 該單位在 T 的已知位置
// （來源回報疏 = 位置停在最後已知點，誠實呈現、不內插——#199 記錄的物理限制）。

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
export function buildReplayIndex(items) {
  const steps = [...(items || [])].sort(_byT);
  const trackIdx = steps.filter(it => it.type === 'track');
  return { steps, trackIdx };
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
    });
  }
  return pos;
}

/** 折疊結果 → GeoJSON FeatureCollection（aar_map 的 source data） */
export function positionsToGeoJSON(posMap) {
  return {
    type: 'FeatureCollection',
    features: [...posMap.values()].map(p => ({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [p.lon, p.lat] },
      properties: { uid: p.uid, callsign: p.actor, t: p.t },
    })),
  };
}

/** type → 側欄顯示中文標籤（與後端 timeline type 對齊） */
export const TYPE_LABELS = {
  track: '軌跡',
  chat: '通聯',
  event: '事件',
  event_status: '事件狀態',
  decision: '決策',
  command: '指令',
};

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
    default:
      return it.actor || '';
  }
}

/** ISO Z → 本地 HH:MM:SS（側欄時間欄）。非法值原樣回。 */
export function fmtClock(isoZ) {
  const d = new Date(isoZ);
  return Number.isNaN(d.getTime()) ? String(isoZ) : d.toLocaleTimeString('zh-TW', { hour12: false });
}
