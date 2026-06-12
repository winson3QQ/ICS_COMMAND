/**
 * coord_tools.js — MGRS / WGS84 / UTM 座標工具 + MapLibre MGRS grid 渲染
 *
 * P1-10b 步驟 10：從 map.js 抽出所有座標相關 helpers + MGRS grid port 到 MapLibre。
 *
 * 對外 API：
 *   - latlngToUtm(lat, lng) → { zoneNum, easting, northing }
 *   - utmToLatLng(zoneNum, E, N) → { lat, lng }
 *   - latlngToMgrs(lat, lng, precision=5) → 'NNL CC EEEEE NNNNN' string
 *   - mgrsToLatLng(mgrsStr) → { lat, lng } | null  （格式錯誤回 null）
 *   - parseWgs84(str) → { lat, lng } | null
 *   - mgrsGridSpacing(zoom, centerLat) → number (公尺 / 1, 10, 100, 1000, 10000, 100000)
 *   - mgrsGridLabel(val, spacing) → 字串（依 spacing 決定位數）
 *   - class MgrsGrid — MapLibre 格線渲染
 *
 * 座標規格參考 DMA TM 8358.2（DoD MGRS 規範）。
 *
 * 為什麼把 grid 渲染與純座標數學放同一檔：
 *   - Grid 邏輯本質是「對畫面範圍取 UTM 整數倍 → 連線」，與 UTM 投影緊耦合
 *   - 拆兩檔反而要重複 import 互依，徒增模組邊界 noise
 */

// ── 純數學常數 ────────────────────────────────────────────────
const A = 6378137.0;                          // WGS84 半長軸 (m)
const F = 1 / 298.257223563;                  // 扁平率
const B = A * (1 - F);
const E2 = 1 - (B * B) / (A * A);             // 第一偏心率平方
const EP2 = E2 / (1 - E2);                    // 第二偏心率平方
const K0 = 0.9996;                            // UTM 比例因子

const LAT_BANDS = 'CDEFGHJKLMNPQRSTUVWX';     // MGRS 緯度帶
const COL_SETS = ['ABCDEFGH', 'JKLMNPQR', 'STUVWXYZ'];   // 100km 方格 column 字母
const ROW_ODD = 'ABCDEFGHJKLMNPQRSTUV';
const ROW_EVEN = 'FGHJKLMNPQRSTUVABCDE';

// ── lat/lng → UTM ────────────────────────────────────────────
export function latlngToUtm(lat, lng) {
  const zoneNum = Math.floor((lng + 180) / 6) + 1;
  const phi = lat * Math.PI / 180;
  const lam = lng * Math.PI / 180;
  const lam0 = ((zoneNum - 1) * 6 - 180 + 3) * Math.PI / 180;
  const sinp = Math.sin(phi), cosp = Math.cos(phi), tanp = Math.tan(phi);
  const N = A / Math.sqrt(1 - E2 * sinp * sinp);
  const T = tanp * tanp, C = EP2 * cosp * cosp, ApA = cosp * (lam - lam0);
  const M = A * (
    (1 - E2 / 4 - 3 * E2 * E2 / 64 - 5 * E2 * E2 * E2 / 256) * phi
    - (3 * E2 / 8 + 3 * E2 * E2 / 32 + 45 * E2 * E2 * E2 / 1024) * Math.sin(2 * phi)
    + (15 * E2 * E2 / 256 + 45 * E2 * E2 * E2 / 1024) * Math.sin(4 * phi)
    - (35 * E2 * E2 * E2 / 3072) * Math.sin(6 * phi));
  let E = K0 * N * (ApA + (1 - T + C) * ApA ** 3 / 6
    + (5 - 18 * T + T * T + 72 * C - 58 * EP2) * ApA ** 5 / 120) + 500000;
  let Nn = K0 * (M + N * tanp * (ApA ** 2 / 2 + (5 - T + 9 * C + 4 * C * C) * ApA ** 4 / 24
    + (61 - 58 * T + T * T + 600 * C - 330 * EP2) * ApA ** 6 / 720));
  if (lat < 0) Nn += 10000000;
  return { zoneNum, easting: E, northing: Nn };
}

// ── UTM → lat/lng ────────────────────────────────────────────
export function utmToLatLng(zoneNum, E, N) {
  const e1 = (1 - Math.sqrt(1 - E2)) / (1 + Math.sqrt(1 - E2));
  const x = E - 500000;
  const M = N / K0;
  const mu = M / (A * (1 - E2 / 4 - 3 * E2 * E2 / 64 - 5 * E2 * E2 * E2 / 256));
  const phi1 = mu
    + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * Math.sin(2 * mu)
    + (21 * e1 * e1 / 16 - 55 * e1 ** 4 / 32) * Math.sin(4 * mu)
    + (151 * e1 ** 3 / 96) * Math.sin(6 * mu)
    + (1097 * e1 ** 4 / 512) * Math.sin(8 * mu);
  const sinp = Math.sin(phi1), cosp = Math.cos(phi1), tanp = Math.tan(phi1);
  const N1 = A / Math.sqrt(1 - E2 * sinp * sinp);
  const T1 = tanp * tanp, C1 = EP2 * cosp * cosp;
  const R1 = A * (1 - E2) / Math.pow(1 - E2 * sinp * sinp, 1.5);
  const D = x / (N1 * K0);
  const latRad = phi1 - (N1 * tanp / R1) * (
    D * D / 2
    - (5 + 3 * T1 + 10 * C1 - 4 * C1 * C1 - 9 * EP2) * D ** 4 / 24
    + (61 + 90 * T1 + 298 * C1 + 45 * T1 * T1 - 252 * EP2 - 3 * C1 * C1) * D ** 6 / 720
  );
  const lngRad = (D
    - (1 + 2 * T1 + C1) * D ** 3 / 6
    + (5 - 2 * C1 + 28 * T1 - 3 * C1 * C1 + 8 * EP2 + 24 * T1 * T1) * D ** 5 / 120
  ) / cosp;
  const lam0 = ((zoneNum - 1) * 6 - 180 + 3) * Math.PI / 180;
  return { lat: latRad * 180 / Math.PI, lng: (lam0 + lngRad) * 180 / Math.PI };
}

// ── lat/lng → MGRS string ────────────────────────────────────
export function latlngToMgrs(lat, lng, precision = 5) {
  const zoneNum = Math.floor((lng + 180) / 6) + 1;
  const latBand = LAT_BANDS[Math.min(Math.floor((lat + 80) / 8), 19)];
  const { easting, northing } = latlngToUtm(lat, lng);
  const colIdx = Math.floor(easting / 100000) - 1;
  const rowIdx = Math.floor(northing / 100000) % 20;
  if (colIdx < 0 || colIdx > 7) return `${zoneNum}${latBand} ??`;
  const colLetter = COL_SETS[(zoneNum - 1) % 3][colIdx];
  const rowLetter = (zoneNum % 2 === 1 ? ROW_ODD : ROW_EVEN)[rowIdx];
  const ep = String(Math.round(easting % 100000)).padStart(5, '0').substring(0, precision);
  const np = String(Math.round(northing % 100000)).padStart(5, '0').substring(0, precision);
  return `${zoneNum}${latBand} ${colLetter}${rowLetter} ${ep} ${np}`;
}

// ── MGRS string → lat/lng ────────────────────────────────────
export function mgrsToLatLng(mgrsStr) {
  if (typeof mgrsStr !== 'string') return null;
  const s = mgrsStr.trim().toUpperCase().replace(/\s+/g, '');
  const m = s.match(/^(\d{1,2})([C-HJ-NP-X])([A-HJ-NP-Z])([A-HJ-NP-V])(\d{2,10})$/);
  if (!m) return null;
  const zoneNum = parseInt(m[1], 10);
  const latBand = m[2];
  const colLtr = m[3];
  const rowLtr = m[4];
  const digits = m[5];
  if (digits.length % 2 !== 0) return null;
  const half = digits.length / 2;
  const scale = Math.pow(10, 5 - half);
  const eOff = parseInt(digits.substring(0, half), 10) * scale;
  const nOff = parseInt(digits.substring(half), 10) * scale;
  const colSet = COL_SETS[(zoneNum - 1) % 3];
  const colIdx = colSet.indexOf(colLtr);
  if (colIdx < 0) return null;
  const utmE = (colIdx + 1) * 100000 + eOff;
  const rowSet = (zoneNum % 2 === 1) ? ROW_ODD : ROW_EVEN;
  const rowIdx = rowSet.indexOf(rowLtr);
  if (rowIdx < 0) return null;
  const bandIdx = LAT_BANDS.indexOf(latBand);
  if (bandIdx < 0) return null;
  // 近似法解 N band 不確定性（同 row letter 對應多個 100km band，靠 latBand 中心估）
  const approxLat = (bandIdx * 8 - 80) + 4;
  const phi = approxLat * Math.PI / 180;
  const Mapprox = A * (
    (1 - E2 / 4 - 3 * E2 * E2 / 64 - 5 * E2 * E2 * E2 / 256) * phi
    - (3 * E2 / 8 + 3 * E2 * E2 / 32 + 45 * E2 * E2 * E2 / 1024) * Math.sin(2 * phi)
    + (15 * E2 * E2 / 256 + 45 * E2 * E2 * E2 / 1024) * Math.sin(4 * phi)
    - (35 * E2 * E2 * E2 / 3072) * Math.sin(6 * phi));
  let approxNn = K0 * Mapprox;
  if (approxLat < 0) approxNn += 10000000;
  let nBand = Math.floor(approxNn / 100000);
  const approxRowIdx = nBand % 20;
  let diff = (rowIdx - approxRowIdx + 20) % 20;
  if (diff > 10) diff -= 20;
  nBand += diff;
  const utmN = nBand * 100000 + nOff;
  return utmToLatLng(zoneNum, utmE, utmN);
}

// ── 通用 WGS84 字串解析（"lat, lng" / "lat lng"） ───────────
export function parseWgs84(str) {
  if (typeof str !== 'string') return null;
  const m = str.match(/(-?\d+\.?\d*)[,\s]+(-?\d+\.?\d*)/);
  if (!m) return null;
  const lat = parseFloat(m[1]), lng = parseFloat(m[2]);
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
  if (lat < -90 || lat > 90 || lng < -180 || lng > 180) return null;
  return { lat, lng };
}

// ── MGRS grid helpers ────────────────────────────────────────

/**
 * 對應 lat/lng 所在 100km square 的 2 字母 designator（如 'UH' / 'TH'）。
 *
 * 用於 TAK / ATAK 慣例：當 grid 顯示 100km tier 時，於每個 100km 方格中央
 * 標示 designator，而非 easting/northing 數字（數字此時無意義）。
 *
 * @returns {string | null}  2 字母 designator，或越界時 null
 */
export function mgrs100kmSquare(lat, lng) {
  const utm = latlngToUtm(lat, lng);
  const colIdx = Math.floor(utm.easting / 100000) - 1;
  const rowIdx = Math.floor(utm.northing / 100000) % 20;
  if (colIdx < 0 || colIdx > 7) return null;
  const colSet = COL_SETS[(utm.zoneNum - 1) % 3];
  const colLetter = colSet[colIdx];
  const rowSet = (utm.zoneNum % 2 === 1) ? ROW_ODD : ROW_EVEN;
  const rowLetter = rowSet[rowIdx];
  return colLetter + rowLetter;
}


/**
 * 依當前 zoom 與緯度，挑出畫面上看得清楚的 primary 格線間距。
 *
 * MIN_PX = 40 對齊 ATAK / Mapbox 社群 mgrs-grid plugin 慣例（原本 60 太鬆，
 * zoom 10→11 會從 100km 直接跳到 10km，視覺上有不連續感）。
 *
 * @param {number} zoom MapLibre zoom level
 * @param {number} centerLat 畫面中心緯度（用於計算實際公尺對 pixel 比例）
 * @returns {number} 公尺（GRID_LEVELS 之一）
 */
// 1-2-5 半階階梯（非純十進制）：讓中間 zoom 落更細格距 → 標籤多一位精度、少內插（#56 follow-up）。
// secondary tier 取能整除 primary 的相鄰細階（primary/2 或 /5）以保持 nesting（格網對齊）。
export const GRID_LEVELS = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000, 100000];

export function mgrsGridSpacing(zoom, centerLat) {
  const MIN_PX = 40;
  if (zoom == null || centerLat == null) return 100000;
  const metersPerPx = metersPerPixel(zoom, centerLat);
  const minMeters = metersPerPx * MIN_PX;
  return GRID_LEVELS.find((s) => s >= minMeters) || 100000;
}

/** 工具：MapLibre Web Mercator 在指定 zoom + 緯度的公尺 / 像素比 */
export function metersPerPixel(zoom, centerLat) {
  return (40075016.686 / (256 * Math.pow(2, zoom)))
    / Math.cos(centerLat * Math.PI / 180);
}

/**
 * 計算 secondary tier 間距（primary / 10）+ 是否值得畫。
 *
 * TAK / ATAK 慣例：總是兩層 — major（粗）+ minor（細）。但 minor 在像素密度
 * 過高時（< 8px / line）會收掉，避免畫面糊掉。
 *
 * @returns {{ spacing: number | null, visible: boolean }}
 *   - spacing: null 表示已在最細層（1m）沒有再細的可畫
 *   - visible: spacing 存在且像素間距 ≥ 8px
 */
export function mgrsGridSecondaryTier(primary, zoom, centerLat) {
  if (primary <= 1) return { spacing: null, visible: false };
  // 取能整除 primary 的相鄰細階（/2 優先，否則 /5），確保 secondary 線在 primary 線上 nesting。
  const half = primary / 2;
  const spacing = GRID_LEVELS.includes(half) ? half : primary / 5;
  const pxSpacing = spacing / metersPerPixel(zoom, centerLat);
  return { spacing, visible: pxSpacing >= 8 };
}

/**
 * 把 UTM easting / northing 整數值對應的 MGRS 內 5 位數字段，依 spacing 取相應位數。
 *
 * **嚴格對齊 MGRS 標準位數** — log10(100000 / spacing) 決定 digits，沒有 floor：
 *   spacing=100km → 0 位（只該標 designator 如 'UH'，P2 milsymbol 接 TAK 時補；
 *                          step 10 暫回空字串，symbol layer 自然不渲染）
 *   spacing=10km  → 1 位（'0' ~ '9'）
 *   spacing=1km   → 2 位（'00' ~ '99'）
 *   spacing=100m  → 3 位
 *   spacing=10m   → 4 位
 *   spacing=1m    → 5 位
 *
 * 對齊 ATAK / iTAK / WinTAK 慣例。原本 Math.max(3, …) 把最少位數釘 3 位，
 * 在 10km/1km grid 時顯示 '980', '981' 看起來像 100m grid，誤導判讀。
 */
export function mgrsGridLabel(val, spacing) {
  // ceil（非 round）：十進制階梯下與 round 等價（既有行為不變），半階格距（500/200/50…）
  // 則多取一位，使相鄰格線可區分（如 500m → '455' 而非 '45'）。#56 follow-up。
  const digits = Math.ceil(Math.log10(100000 / spacing));
  if (digits <= 0) return '';
  const divisor = Math.pow(10, 5 - digits);
  const v = Math.round(val % 100000);
  return String(Math.floor(v / divisor)).padStart(digits, '0');
}

// #56：低於此 zoom 改畫 GZD 帶（100km 細格在世界尺度過密、且跨多 UTM zone 投影失真）。
export const GZD_ZOOM = 6;
// GZD 區間密度補強：視野跨度小於此才疊加 100km 多 zone 細格（避免世界尺度橫線爆量）。
export const GZD_FINE_MAX_LON_SPAN = 40;
export const GZD_FINE_MAX_LAT_SPAN = 30;

// 經度 → UTM zone（1..60，wrap-safe）。
export function utmZoneFromLng(lng) {
  return ((Math.floor((lng + 180) / 6) % 60) + 60) % 60 + 1;
}

// 緯度 → MGRS 緯度帶字母（C..X，每 8°，X 帶 72..84 為 12°）。範圍外回 ''。
export function latBandFromLat(lat) {
  if (lat < -80 || lat > 84) return '';
  let i = Math.floor((lat + 80) / 8);
  if (i > 19) i = 19;            // 80..84 → X
  return LAT_BANDS[i] || '';
}

// #56：GZD 帶 features（純函式，可測）。6° 經度 zone 界 + 8° 緯度帶界 + designator（如 '51R'）。
// 線走 tier='primary'（沿用既有 primary line layer）、designator 走 axis='designator'。
export function computeGzdFeatures(west, east, south, north) {
  const feats = [];
  const s = Math.max(south, -80);
  const n = Math.min(north, 84);
  if (n <= s) return feats;
  const line = (coords) => feats.push({ type: 'Feature', geometry: { type: 'LineString', coordinates: coords }, properties: { tier: 'primary' } });
  // 6° 經度 zone 界線（垂直）
  const k0 = Math.floor((west + 180) / 6);
  const k1 = Math.ceil((east + 180) / 6);
  for (let k = k0; k <= k1; k++) {
    const lng = k * 6 - 180;
    line([[lng, s], [lng, n]]);
  }
  // 8° 緯度帶界線（水平）：-80..72 每 8° + 頂界 84
  const latLines = [];
  for (let lat = -80; lat <= 72; lat += 8) latLines.push(lat);
  latLines.push(84);
  for (const lat of latLines) {
    if (lat < s - 1e-6 || lat > n + 1e-6) continue;
    line([[west, lat], [east, lat]]);
  }
  // designator '51R' 等：每個可見 zone×band 中央
  for (let k = k0; k < k1; k++) {
    const lngC = k * 6 - 180 + 3;
    if (lngC < west || lngC > east) continue;
    const zoneNum = ((k % 60) + 60) % 60 + 1;
    for (let lat = -80; lat < 84; lat += 8) {
      const top = lat === 72 ? 84 : lat + 8;
      const latC = (lat + top) / 2;
      if (latC < s || latC > n) continue;
      const band = latBandFromLat(latC);
      if (!band) continue;
      feats.push({ type: 'Feature', geometry: { type: 'Point', coordinates: [lngC, latC] }, properties: { label: `${zoneNum}${band}`, axis: 'designator' } });
    }
  }
  return feats;
}

/** #213 b3-2：取定位座標 —— entity 現位（cop_entity）優先，否則 fallback（chat 自帶 point）；
 *  0,0（無 point 哨兵）/ 非有限 → 視為無座標。回 `[lng, lat]` 或 null。 */
export function pickLocateCoords(entity, fallback) {
  for (const c of [entity, fallback]) {
    if (!c) continue;
    const lat = Number(c.lat);
    const lon = Number(c.lon);
    if (Number.isFinite(lat) && Number.isFinite(lon) && !(lat === 0 && lon === 0)) return [lon, lat];
  }
  return null;
}

// ── MgrsGrid 渲染 class ──────────────────────────────────────

const EMPTY_FC = Object.freeze({ type: 'FeatureCollection', features: [] });

/**
 * MapLibre MGRS 格線渲染器（兩層 tier，對齊 ATAK / TAK 慣例）。
 *
 * 設計：
 *   - 單一 GeoJSON source 'mgrs-grid'，feature 用 properties.tier 區分：
 *       tier='primary'   — 主格線（自動選間距，較粗較顯眼）
 *       tier='secondary' — 次格線（primary / 10，較細較淡，密度不足時自動隱藏）
 *       tier='label'     — Point 標籤（只給 primary 配 label，避免擁擠）
 *   - 三個 layer：lines-secondary（先畫低層）→ lines-primary → labels
 *   - redraw() 在 moveend 觸發；compute 後 setData，**不**重建 layer/source
 *
 * 為什麼 TAK-aligned 兩層：
 *   - ATAK / iTAK / WinTAK / Mapbox 社群 mgrs-grid plugin 都是兩層 + 像素密度
 *     門檻（secondary px ≥ 8）
 *   - 視覺上 zoom 縮放時兩層各自 fade in/out，沒有單層方案的「突然跳級」感
 *   - P2 整合 TAK 時 grid 渲染風格不用再大改
 *
 * 視覺：
 *   - Primary: rgba(150, 200, 255, 0.45)、line-width 1.5
 *   - Secondary: rgba(150, 200, 255, 0.18)、line-width 1
 *   - Label: Noto Sans Regular SDF（P1-10a vendored fontstack），白字 + 深色 halo
 *   - Label 位置：viewport 左 24px / 底 60px（避開 MGRS 搜尋欄），每次 moveend 重算
 *
 * 不負責（留 P2 milsymbol / TAK 整合處理）：
 *   - 100km 方格 designator（如 "UH"）label
 *   - GZD 6° 經度帶 / 8° 緯度帶 boundary 渲染（zoom < 6 才有意義）
 */
export class MgrsGrid {
  /**
   * @param {maplibregl.Map} map
   * @param {object} [opts]
   *   - primaryColor: default 'rgba(150, 200, 255, 0.45)'
   *   - secondaryColor: default 'rgba(150, 200, 255, 0.18)'
   *   - primaryWidth: default 1.5
   *   - secondaryWidth: default 1
   *   - labelLeftPx / labelRightPx: default 24 — Y 軸 label 距左 / 右邊像素
   *   - labelTopPx: default 60 — X 軸 label 距上邊像素（給時鐘 / coord 顯示讓位）
   *   - labelBottomPx: default 60 — X 軸 label 距底邊像素（避開搜尋欄）
   *
   * 為什麼 4 邊鏡像：TAK / ATAK 慣例，broad-view 下單邊 label 太稀，operator 從畫面
   * 中心 marker 抓座標要拉到對角線邊緣讀，效率差。4 邊鏡像 → 不管 marker 在哪個
   * 象限，最近的兩個邊都有 label，幾秒內定位。
   */
  constructor(map, opts = {}) {
    if (!map) throw new Error('MgrsGrid: map required');
    this.map = map;
    // 顏色為 dark 底圖預設；muted-day（淺底）對比不足，由 applyTheme() 換成深色系。
    this.primaryColor = opts.primaryColor ?? 'rgba(150, 200, 255, 0.45)';
    this.secondaryColor = opts.secondaryColor ?? 'rgba(150, 200, 255, 0.18)';
    this.labelColor = opts.labelColor ?? '#e6edf3';
    this.haloColor = opts.haloColor ?? '#0d1117';
    this.designatorColor = opts.designatorColor ?? 'rgba(180, 215, 255, 0.55)';
    this.primaryWidth = opts.primaryWidth ?? 1.5;
    this.secondaryWidth = opts.secondaryWidth ?? 1;
    this.labelLeftPx = opts.labelLeftPx ?? 24;
    this.labelRightPx = opts.labelRightPx ?? 24;
    this.labelTopPx = opts.labelTopPx ?? 60;        // 避開頂部時鐘 / MGRS 顯示欄
    this.labelBottomPx = opts.labelBottomPx ?? 60;   // 避開底部搜尋欄
    this._visible = false;
    this._installed = false;
  }

  _install() {
    if (this._installed) return;
    if (!this.map.getSource('mgrs-grid')) {
      this.map.addSource('mgrs-grid', { type: 'geojson', data: EMPTY_FC });
    }
    // Secondary lines 先畫（z-order 較低）— primary 蓋在上面更突出
    // ⚠️ Filter 一律走 properties (tier / axis) 不靠 ['geometry-type'] — 對齊
    // entity_layer.js polygon-label / route-label 修正的 rationale：MapLibre 4.7.1
    // render pipeline 對 ['geometry-type'] expression 在 symbol layer 內 cull 掉
    // features（step 10 dogfood 撞到的 bug，code-review #1 finding）。
    // 邊際效應：line layer 對 Point feature 本來就免疫（type:line 只 render LineString），
    // 拿掉 geometry-type 也安全；symbol layer 由 axis property 唯一判別。
    if (!this.map.getLayer('mgrs-grid-lines-secondary')) {
      this.map.addLayer({
        id: 'mgrs-grid-lines-secondary',
        source: 'mgrs-grid',
        type: 'line',
        filter: ['==', ['get', 'tier'], 'secondary'],
        paint: {
          'line-color': this.secondaryColor,
          'line-width': this.secondaryWidth,
        },
      });
    }
    if (!this.map.getLayer('mgrs-grid-lines-primary')) {
      this.map.addLayer({
        id: 'mgrs-grid-lines-primary',
        source: 'mgrs-grid',
        type: 'line',
        filter: ['==', ['get', 'tier'], 'primary'],
        paint: {
          'line-color': this.primaryColor,
          'line-width': this.primaryWidth,
        },
      });
    }
    if (!this.map.getLayer('mgrs-grid-labels')) {
      this.map.addLayer({
        id: 'mgrs-grid-labels',
        source: 'mgrs-grid',
        type: 'symbol',
        // 只渲染 edge labels（4 軸鏡像），designator 給 mgrs-grid-designators 處理
        filter: ['in', ['get', 'axis'],
          ['literal', ['x-bottom', 'x-top', 'y-left', 'y-right']],
        ],
        layout: {
          'text-field': ['get', 'label'],
          'text-font': ['Noto Sans Regular'],
          'text-size': 10,
          'text-allow-overlap': true,
          'text-ignore-placement': true,
          // TAK / ATAK 慣例：label 鏡像 4 邊。
          //   axis='x-bottom' / 'x-top' → easting digits 在底 / 上邊，anchor=top / bottom
          //   axis='y-left'   / 'y-right' → northing digits 在左 / 右邊，anchor=left / right
          // 每個 anchor 向地圖內側 offset 0.4em，避免壓在 grid line 上 / 搜尋欄。
          'text-anchor': [
            'case',
            ['==', ['get', 'axis'], 'x-bottom'], 'top',
            ['==', ['get', 'axis'], 'x-top'], 'bottom',
            ['==', ['get', 'axis'], 'y-left'], 'left',
            ['==', ['get', 'axis'], 'y-right'], 'right',
            'center',
          ],
          'text-offset': [
            'case',
            ['==', ['get', 'axis'], 'x-bottom'], ['literal', [0, 0.4]],
            ['==', ['get', 'axis'], 'x-top'], ['literal', [0, -0.4]],
            ['==', ['get', 'axis'], 'y-left'], ['literal', [0.4, 0]],
            ['==', ['get', 'axis'], 'y-right'], ['literal', [-0.4, 0]],
            ['literal', [0, 0]],
          ],
        },
        paint: {
          'text-color': this.labelColor,
          'text-halo-color': this.haloColor,
          'text-halo-width': 1.5,
          'text-opacity': 0.85,
        },
      });
    }
    // 100km square designator（如 'UH' / 'TH'）— TAK 慣例放在每個 100km 方格
    // 中央，較大字、淡白。redraw 只在 primary >= 100km 時 emit；其他 tier 不 emit。
    if (!this.map.getLayer('mgrs-grid-designators')) {
      this.map.addLayer({
        id: 'mgrs-grid-designators',
        source: 'mgrs-grid',
        type: 'symbol',
        filter: ['==', ['get', 'axis'], 'designator'],
        layout: {
          'text-field': ['get', 'label'],
          'text-font': ['Noto Sans Regular'],
          'text-size': 22,
          'text-allow-overlap': true,
          'text-ignore-placement': true,
          'text-anchor': 'center',
          'text-letter-spacing': 0.1,
        },
        paint: {
          'text-color': this.designatorColor,  // 與 grid line 同調但更亮（dark）/ 更深（day）
          'text-halo-color': this.haloColor,
          'text-halo-width': 2,
        },
      });
    }
    this._installed = true;
  }

  /**
   * 依底圖主題換 grid 配色（P1-10c 步驟 4 dogfood）。
   * dark 底圖用淺藍 + 淺字深 halo；muted-day 淺底用深藍 + 深字淺 halo，確保對比。
   * grid 非 affiliation/severity 色，doctrine 允許單一 muted 藍作 grid 識別色。
   * 在 layer 安裝前呼叫只更新 instance 欄位（供 _install 取用）；安裝後呼叫同步 live paint。
   * @param {string} theme 'dark' | 'muted-day'
   */
  applyTheme(theme) {
    const day = theme === 'muted-day';
    this.primaryColor    = day ? 'rgba(30, 64, 120, 0.62)' : 'rgba(150, 200, 255, 0.45)';
    this.secondaryColor  = day ? 'rgba(30, 64, 120, 0.30)' : 'rgba(150, 200, 255, 0.18)';
    this.labelColor      = day ? '#14213d' : '#e6edf3';
    this.haloColor       = day ? 'rgba(255, 255, 255, 0.9)' : '#0d1117';
    this.designatorColor = day ? 'rgba(30, 64, 120, 0.7)' : 'rgba(180, 215, 255, 0.55)';
    const m = this.map;
    const set = (id, prop, val) => { if (m.getLayer(id)) m.setPaintProperty(id, prop, val); };
    set('mgrs-grid-lines-primary', 'line-color', this.primaryColor);
    set('mgrs-grid-lines-secondary', 'line-color', this.secondaryColor);
    set('mgrs-grid-labels', 'text-color', this.labelColor);
    set('mgrs-grid-labels', 'text-halo-color', this.haloColor);
    set('mgrs-grid-designators', 'text-color', this.designatorColor);
    set('mgrs-grid-designators', 'text-halo-color', this.haloColor);
  }

  /** 顯示 / 隱藏 grid。隱藏時 source 清空（節省 GPU） */
  setVisible(visible) {
    this._visible = !!visible;
    if (this._visible) {
      this._install();
      this.redraw();
    } else if (this._installed) {
      this.map.getSource('mgrs-grid')?.setData(EMPTY_FC);
    }
  }

  isVisible() { return this._visible; }

  /** 重畫格線（在 moveend / setVisible(true) 後呼叫） */
  redraw() {
    if (!this._visible || !this._installed) return;
    const map = this.map;
    const bounds = map.getBounds();
    const zoom = map.getZoom();
    const center = map.getCenter();
    const west = bounds.getWest();
    const east = bounds.getEast();
    const south = bounds.getSouth();
    const north = bounds.getNorth();

    const features = [];

    // #56：低 zoom（世界/區域尺度）→ GZD 帶（6° 經 zone 界 + 8° 緯帶界 + designator）。
    // 跨多 UTM zone 用單一中心 zone 投影會失真到非有限（原 bug）；改逐 zone 投影。
    if (zoom < GZD_ZOOM) {
      for (const f of computeGzdFeatures(west, east, south, north)) features.push(f);
      // 密度補強：純 6°/8° GZD 在中間 zoom 太稀疏（看起來像斷線）→ 視野不過寬時
      // 疊加多 zone 100km 細格（secondary tier），與 zoom≥GZD_ZOOM 的細格平滑銜接。
      // 真世界尺度（跨度過大）才只留 GZD 帶，避免上萬條橫線爆掉。
      if (east - west < GZD_FINE_MAX_LON_SPAN && north - south < GZD_FINE_MAX_LAT_SPAN) {
        const zW = utmZoneFromLng(west);
        const zE = utmZoneFromLng(east);
        for (let z = zW; z <= zE; z++) {
          const lonW = Math.max(west, (z - 1) * 6 - 180);
          const lonE = Math.min(east, z * 6 - 180);
          if (lonE - lonW < 1e-6) continue;
          this._emitZoneLines(features, z, lonW, lonE, south, north, 100000, 'secondary');
        }
      }
      this.map.getSource('mgrs-grid')?.setData({ type: 'FeatureCollection', features });
      return;
    }

    const primary = mgrsGridSpacing(zoom, center.lat);
    const sec = mgrsGridSecondaryTier(primary, zoom, center.lat);
    const sw = latlngToUtm(south, west);
    const ne = latlngToUtm(north, east);
    const ctr = latlngToUtm(center.lat, center.lng);
    const zn = ctr.zoneNum;

    // 4 邊鏡像 label：算出每個邊的 anchor UTM 座標。label 沿著該邊出現，
    // operator 可從畫面任一象限找到最近邊的座標讀值。
    const canvas = map.getCanvas();
    const mapW = canvas.clientWidth;
    const mapH = canvas.clientHeight;
    const llLeft   = map.unproject([this.labelLeftPx, mapH / 2]);
    const llRight  = map.unproject([mapW - this.labelRightPx, mapH / 2]);
    const llTop    = map.unproject([mapW / 2, this.labelTopPx]);
    const llBottom = map.unproject([mapW / 2, mapH - this.labelBottomPx]);
    const labelE_left  = latlngToUtm(llLeft.lat, llLeft.lng).easting;
    const labelE_right = latlngToUtm(llRight.lat, llRight.lng).easting;
    const labelN_top    = latlngToUtm(llTop.lat, llTop.lng).northing;
    const labelN_bottom = latlngToUtm(llBottom.lat, llBottom.lng).northing;

    // #56：格線逐 UTM zone 繪製並 clip 到各自 6° 經度帶。原本整畫面用中心 zone（zn）投影，
    // 跨 zone 時離中央經線越遠越失真 → 點變非有限被丟（格線消失）。窗只跨單 zone 時退化為原行為。
    const zMin = utmZoneFromLng(west);
    const zMax = utmZoneFromLng(east);
    for (let z = zMin; z <= zMax; z++) {
      const lonW = Math.max(west, (z - 1) * 6 - 180);
      const lonE = Math.min(east, z * 6 - 180);
      if (lonE - lonW < 1e-6) continue;
      if (sec.visible) this._emitZoneLines(features, z, lonW, lonE, south, north, sec.spacing, 'secondary');
      this._emitZoneLines(features, z, lonW, lonE, south, north, primary, 'primary');
    }

    // 3. 100km square designator labels（如 'UH' / 'TH'）— 對齊 TAK 慣例
    //    primary 已是 100km 時 edge labels 是空（沒位數可標），改在每個 100km 方格
    //    中央標 designator。primary 較細時不 emit（避免和 edge labels 混淆）。
    if (primary >= 100000) {
      const e0 = Math.floor(sw.easting / 100000) * 100000;
      const e1 = Math.ceil(ne.easting / 100000) * 100000;
      const n0 = Math.floor(sw.northing / 100000) * 100000;
      const n1 = Math.ceil(ne.northing / 100000) * 100000;
      for (let e = e0; e < e1; e += 100000) {
        for (let n = n0; n < n1; n += 100000) {
          const ll = utmToLatLng(zn, e + 50000, n + 50000);
          if (!Number.isFinite(ll.lat)) continue;
          const designator = mgrs100kmSquare(ll.lat, ll.lng);
          if (!designator) continue;
          features.push({
            type: 'Feature',
            geometry: { type: 'Point', coordinates: [ll.lng, ll.lat] },
            properties: { label: designator, axis: 'designator' },
          });
        }
      }
    }

    // Primary tier 的 label — 4 邊鏡像（只放 primary，避免擁擠）。
    // 100km tier mgrsGridLabel 回空字串（designator 留 designator layer）→ 不 emit。
    // axis = 'y-left' / 'y-right' / 'x-bottom' / 'x-top' 給 symbol layer 算對齊。
    const emitLabel = (lng, lat, label, axis) => {
      if (!Number.isFinite(lat) || !Number.isFinite(lng)) return;
      features.push({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [lng, lat] },
        properties: { label, axis },
      });
    };

    // Northing (Y 軸) labels — 左邊 + 右邊
    const n0 = Math.floor(sw.northing / primary) * primary;
    const n1 = Math.ceil(ne.northing / primary) * primary;
    for (let n = n0; n <= n1; n += primary) {
      const label = mgrsGridLabel(n, primary);
      if (!label) continue;
      const ll_l = utmToLatLng(zn, labelE_left, n);
      emitLabel(ll_l.lng, ll_l.lat, label, 'y-left');
      const ll_r = utmToLatLng(zn, labelE_right, n);
      emitLabel(ll_r.lng, ll_r.lat, label, 'y-right');
    }
    // Easting (X 軸) labels — 底邊 + 上邊
    const e0 = Math.floor(sw.easting / primary) * primary;
    const e1 = Math.ceil(ne.easting / primary) * primary;
    for (let e = e0; e <= e1; e += primary) {
      const label = mgrsGridLabel(e, primary);
      if (!label) continue;
      const ll_b = utmToLatLng(zn, e, labelN_bottom);
      emitLabel(ll_b.lng, ll_b.lat, label, 'x-bottom');
      const ll_t = utmToLatLng(zn, e, labelN_top);
      emitLabel(ll_t.lng, ll_t.lat, label, 'x-top');
    }

    this.map.getSource('mgrs-grid')?.setData({
      type: 'FeatureCollection',
      features,
    });
  }

  // #56：emit 單一 UTM zone z 的格線，clip 到該 zone 可見經度窗 [lonW, lonE]。
  // 每條線端點在 z 帶內投影 → 有限；clip lng 讓相鄰 zone 的線在帶界接合不溢出。
  _emitZoneLines(features, z, lonW, lonE, south, north, spacing, tier) {
    const mid = (south + north) / 2;
    const cs = [
      latlngToUtm(south, lonW), latlngToUtm(south, lonE),
      latlngToUtm(north, lonW), latlngToUtm(north, lonE),
      latlngToUtm(mid, lonW), latlngToUtm(mid, lonE),
    ];
    const es = cs.map((c) => c.easting);
    const ns = cs.map((c) => c.northing);
    const eMin = Math.min(...es), eMax = Math.max(...es);
    const nMin = Math.min(...ns), nMax = Math.max(...ns);
    const clip = (lng) => (lng < lonW ? lonW : lng > lonE ? lonE : lng);
    const push = (p1, p2) => features.push({
      type: 'Feature',
      geometry: { type: 'LineString', coordinates: [[clip(p1.lng), p1.lat], [clip(p2.lng), p2.lat]] },
      properties: { tier },
    });
    // 橫線（northing 固定）
    const n0 = Math.floor(nMin / spacing) * spacing;
    const n1 = Math.ceil(nMax / spacing) * spacing;
    for (let n = n0; n <= n1; n += spacing) {
      const p1 = utmToLatLng(z, eMin - spacing, n);
      const p2 = utmToLatLng(z, eMax + spacing, n);
      if (!Number.isFinite(p1.lat) || !Number.isFinite(p2.lat)) continue;
      push(p1, p2);
    }
    // 直線（easting 固定）
    const e0 = Math.floor(eMin / spacing) * spacing;
    const e1 = Math.ceil(eMax / spacing) * spacing;
    for (let e = e0; e <= e1; e += spacing) {
      const p1 = utmToLatLng(z, e, nMin - spacing);
      const p2 = utmToLatLng(z, e, nMax + spacing);
      if (!Number.isFinite(p1.lat) || !Number.isFinite(p2.lat)) continue;
      if ((p1.lng < lonW && p2.lng < lonW) || (p1.lng > lonE && p2.lng > lonE)) continue;
      push(p1, p2);
    }
  }

  destroy() {
    if (!this._installed) return;
    ['mgrs-grid-lines-primary', 'mgrs-grid-lines-secondary', 'mgrs-grid-labels', 'mgrs-grid-designators'].forEach((id) => {
      if (this.map.getLayer(id)) this.map.removeLayer(id);
    });
    if (this.map.getSource('mgrs-grid')) this.map.removeSource('mgrs-grid');
    this._installed = false;
    this._visible = false;
  }
}
