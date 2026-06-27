// SPDX-License-Identifier: LicenseRef-Proprietary
// Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
/**
 * mil_symbol.js — MIL-STD-2525 符號（milsymbol）整合層（P2-05 子塊 c / #110）
 *
 * 職責（純資料轉換，本檔不碰 DOM；bake 渲染在 entity_layer）：
 *   - affiliationFromCot(cotType) → 'friendly'|'hostile'|'neutral'|'unknown'
 *       供既有 entity feature 的 affiliation property + --mil-* 色 token 對映。
 *   - cotToSidc(cotType) → 2525C SIDC 字串，餵 milsymbol `new ms.Symbol(sidc)`。
 *
 * 為何需要本層（reality check 2026-06-05）：
 *   - milsymbol **不直接吃 CoT type**（`a-f-G-U-C` → isValid:false 退預設框），
 *     只吃 2525C/D SIDC（`SFGPU------` valid）→ 必須自建 cot→SIDC。
 *   - 範圍對齊 classification-crosswalk §6 LOCKED：**敵我=2525 框**為主要視覺；
 *     function（框內 entity 符號）細分為長尾，先給 generic 框（affiliation 正確），
 *     精細 function 對映待 cot_type 字典擴充（後續）。
 *
 * CoT type 結構（atom）：`a-{affiliation}-{dimension}[-function...]`，如 a-f-G-U-C。
 * 非 atom（b-*：route/polygon/sensor 等）不走 2525 unit 框（route/polygon = §8 tactical
 * graphics，另案），本層 cotToSidc 對非 atom 回 null（caller fallback 既有渲染）。
 */

// CoT affiliation char（type 第 2 段）→ 內部 4 態（對映 --mil-* 色 token / frame）。
// j(joker)/k(faker) 為演習用敵對擬態 → 色彩歸 hostile；a(assumed friend)→friendly。
// 只列「異於 default」者；u/p/o 等未知系與未列字母一律走 default 'unknown'。
const _COT_AFFILIATION = {
  f: 'friendly', a: 'friendly',
  h: 'hostile', s: 'hostile', j: 'hostile', k: 'hostile',
  n: 'neutral',
};

// 內部 4 態 → 2525C SIDC affiliation code（位 2）。
const _SIDC_AFFILIATION = { friendly: 'F', hostile: 'H', neutral: 'N', unknown: 'U' };

// CoT dimension char（type 第 3 段）→ 2525C battle dimension code（位 3）。
// CoT 與 2525 字母大致一致：P 空間 / A 空中 / G 地面 / S 海面 / U 水下 / F SOF。
const _SIDC_DIMENSION = { P: 'P', A: 'A', G: 'G', S: 'S', U: 'U', F: 'F' };

/** CoT type → 內部 affiliation（4 態）。非 atom 或無法判定 → 'unknown'。 */
export function affiliationFromCot(cotType) {
  if (typeof cotType !== 'string') return 'unknown';
  const parts = cotType.toLowerCase().split('-');
  if (parts[0] !== 'a') return 'unknown'; // 只有 atom 有敵我語意
  return _COT_AFFILIATION[parts[1]] || 'unknown';
}

/**
 * CoT type → 2525C SIDC（供 milsymbol）。
 * 回傳 15 字 SIDC：S{affiliation}{dimension}P + generic function（位 5-10 '------'）+ '-----'。
 * 非 atom（b-*）回 null（不套 2525 unit 框 — route/polygon 走 §8 tactical graphics）。
 */
export function cotToSidc(cotType) {
  if (typeof cotType !== 'string') return null;
  const parts = cotType.split('-');
  if (parts[0] !== 'a') return null;
  const aff = _SIDC_AFFILIATION[affiliationFromCot(cotType)] || 'U';
  const dimChar = (parts[2] || '').toUpperCase();
  const dim = _SIDC_DIMENSION[dimChar] || 'G'; // 預設地面
  // 位：1=S(warfighting) 2=affiliation 3=dimension 4=P(present) 5-10=function 11-15=modifier
  return `S${aff}${dim}P` + '------' + '-----';
}

// P2-30 part 3（#180）：affiliation → generic 地面 CoT type（建手動感知/敵情標記用）。
// affiliationFromCot 的逆——選「敵/不明/中立/友」→ a-{h/u/n/f}-G（generic ground）。
// **實測 iTAK 顯示 a-h-G 正常**（#180，ICS share → 現場端可見）；用 generic 而非 -U-C 因「接觸/
// 感知」未必是 combat unit，generic hostile ground 語意更貼切。未知 → a-u-G（fail-safe 不誤標友軍）。
const _AFFILIATION_COT_TYPE = {
  friendly: 'a-f-G',
  hostile: 'a-h-G',
  neutral: 'a-n-G',
  unknown: 'a-u-G',
};
export function affiliationToCotType(affiliation) {
  return _AFFILIATION_COT_TYPE[affiliation] || _AFFILIATION_COT_TYPE.unknown;
}

// #257 α-3：改既有 marker 的敵我態——只換 CoT type 的 affiliation 字元（位 2），保留維度/功能
// （a-f-G-U-C → a-h-G-U-C）。非 atom 或畸形 type 退回通用 a-{f/h/n/u}-G（fail-safe）。
const _AFFILIATION_CHAR = { friendly: 'f', hostile: 'h', neutral: 'n', unknown: 'u' };
export function swapCotAffiliation(cotType, affiliation) {
  const ch = _AFFILIATION_CHAR[affiliation] || 'u';
  if (typeof cotType !== 'string') return affiliationToCotType(affiliation);
  const parts = cotType.split('-');
  if (parts[0] !== 'a' || parts.length < 2) return affiliationToCotType(affiliation);
  parts[1] = ch;
  return parts.join('-');
}
