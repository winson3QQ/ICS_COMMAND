// SPDX-License-Identifier: LicenseRef-Proprietary
// Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
/**
 * label_markers.js — MapLibre 透明 HTML drag handle，搭配 SDF symbol layer label
 *
 * P1-10b 步驟 8（hybrid B 設計）：
 *   - 視覺 label：MapLibre symbol layer 的 text-field 渲染（保留 SDF halo 質感）
 *   - 拖曳能力：每個 polygon/route 上方蓋一個透明 maplibregl.Marker (HTML element)
 *               + draggable: true，當作 drag handle
 *   - Drag 流程：
 *     1. dragstart → setFeatureState({dragging: true}) 讓 symbol layer text-opacity → 0
 *                 → handle 內顯示 ghost label（CSS 文字）作為拖曳視覺 feedback
 *     2. drag move → marker 跟著滑鼠走
 *     3. dragend → setFeatureState({dragging: false}) → symbol layer 回顯
 *                → callback 給 caller 新 lat/lng → caller 寫 label_anchor + saveMapConfig
 *                → re-render：symbol layer + handle 都跟到新 anchor 位置
 *
 * Caller 配套：
 *   - symbol layer 加 paint:
 *     `'text-opacity': ['case', ['boolean', ['feature-state', 'dragging'], false], 0, 1]`
 *   - source 需 `promoteId: 'id'`（EntityLayer 已預設）
 *   - polygonCentroid / 自算 route 中點 helper 由 caller 提供（label_anchor 缺時的 default）
 */

const HANDLE_BASE_STYLE = `
  background: transparent;
  cursor: grab;
  user-select: none;
`;

// Hover 時顯示淡淡虛線框，讓 user 知道這裡可拖
const HANDLE_HOVER_STYLE = `
  background: rgba(255, 255, 255, 0.06);
  border-radius: 3px;
  outline: 1px dashed rgba(255, 255, 255, 0.2);
`;

const GHOST_LABEL_STYLE = `
  font-size: 11px;
  font-weight: 700;
  font-family: 'Noto Sans TC', 'PingFang TC', 'Microsoft JhengHei', sans-serif;
  white-space: nowrap;
  pointer-events: none;
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  text-shadow:
    -1px -1px 0 rgba(14, 22, 29, 0.95),
     1px -1px 0 rgba(14, 22, 29, 0.95),
    -1px  1px 0 rgba(14, 22, 29, 0.95),
     1px  1px 0 rgba(14, 22, 29, 0.95);
`;

/** 簡單估 label 寬度（中文 ~11px / char + padding） */
function _estimateLabelWidth(label) {
  return Math.max(36, (label?.length || 1) * 12 + 8);
}

export class LabelMarkerManager {
  /**
   * @param {maplibregl.Map} map
   * @param {object} maplibreglNs - window.maplibregl global
   * @param {string} sourceId - 對應的 EntityLayer source id（'polygons' or 'routes'），
   *                            用於 setFeatureState 拖曳期間隱藏 symbol layer text
   */
  constructor(map, maplibreglNs, sourceId) {
    if (!map) throw new Error('LabelMarkerManager: map required');
    if (!maplibreglNs?.Marker) throw new Error('LabelMarkerManager: maplibregl.Marker required');
    if (!sourceId) throw new Error('LabelMarkerManager: sourceId required');
    this.map = map;
    this.maplibregl = maplibreglNs;
    this.sourceId = sourceId;
    // Map<featureId, { marker, lastLabel, lastColor }>
    this.markers = new Map();
  }

  /**
   * 同步 features → drag handle markers。
   *
   * @param {Array<{id, label, label_anchor?, color, ...}>} features
   * @param {(featureId: string, latlng: {lat, lng}) => void} onDragEnd
   * @param {(feature) => [lng, lat] | null} centroidFn - 算 default 位置（無 label_anchor 時用）
   */
  sync(features, onDragEnd, centroidFn) {
    const seenIds = new Set();
    for (const feat of features) {
      if (!feat?.id || !feat?.label) continue;
      seenIds.add(feat.id);
      const lnglat = this._computeLngLat(feat, centroidFn);
      if (!lnglat) continue;
      this._upsert(feat, lnglat, onDragEnd);
    }
    for (const [id, entry] of this.markers) {
      if (!seenIds.has(id)) {
        entry.marker.remove();
        this.markers.delete(id);
      }
    }
  }

  _computeLngLat(feat, centroidFn) {
    if (Array.isArray(feat.label_anchor) && feat.label_anchor.length === 2) {
      const lat = Number(feat.label_anchor[0]);
      const lng = Number(feat.label_anchor[1]);
      if (Number.isFinite(lat) && Number.isFinite(lng)) return [lng, lat];
    }
    if (typeof centroidFn === 'function') {
      const c = centroidFn(feat);
      if (Array.isArray(c) && c.length === 2 && Number.isFinite(c[0]) && Number.isFinite(c[1])) {
        return c;
      }
    }
    return null;
  }

  _upsert(feat, lnglat, onDragEnd) {
    const existing = this.markers.get(feat.id);
    if (existing) {
      existing.marker.setLngLat(lnglat);
      if (existing.lastLabel !== feat.label) {
        existing.marker.getElement().style.width = `${_estimateLabelWidth(feat.label)}px`;
        existing.lastLabel = feat.label;
      }
      existing.lastColor = feat.color;
      // 防 stuck-dragging：若 dragend race / browser interrupt（例 user drag 到一半
      // 切視窗、過 modal、ESC 中斷）導致 feature-state.dragging 卡 true，text-opacity
      // case → 0，label 從此永遠 invisible。每次 sync 在非當前 drag 中時主動清。
      this._clearStuckDragState(feat.id, existing);
      return;
    }
    // 新建 transparent handle —— entry 先建（先 set 進 map）讓 dragstart/dragend
    // closure 抓到。marker 字段稍後 fill。
    const entry = {
      marker: null,
      lastLabel: feat.label,
      lastColor: feat.color,
      isDragging: false,
    };
    this.markers.set(feat.id, entry);
    // 新 marker 也順手清一次：若上一輪 marker（同 feat.id）dragend race
    //  留下 stuck state，這次建新 marker 時 state 仍在 source 裡（promoteId 持久化）。
    this._clearStuckDragState(feat.id, entry);

    const el = document.createElement('div');
    el.className = 'mlb-label-handle';
    el.dataset.featureId = feat.id;
    // position 不寫死 — 讓 maplibregl-marker class 套 position: absolute（class CSS 已備），
    // 避免 'relative' 把 element 拉進 normal flow 跟其他子元素疊。
    // 但 ghost label 用 position:absolute 仍能以 element 為 positioning context
    // （只要 element 是 positioned，absolute 即可）。
    el.style.cssText = HANDLE_BASE_STYLE
      + `width: ${_estimateLabelWidth(feat.label)}px;`
      + 'height: 18px;';
    // Hover feedback — 浮現淡淡虛線提示可拖
    el.addEventListener('mouseenter', () => {
      el.style.cssText += HANDLE_HOVER_STYLE;
    });
    el.addEventListener('mouseleave', () => {
      // 重置到 base + 寬高（不重設 position — maplibregl-marker class 已給 absolute）
      el.style.cssText = HANDLE_BASE_STYLE
        + `width: ${_estimateLabelWidth(feat.label)}px;`
        + 'height: 18px;';
    });
    // 防止 click 穿透到 map（避免在 draw mode 下被誤判為新 vertex）
    el.addEventListener('click', (e) => e.stopPropagation());

    const marker = new this.maplibregl.Marker({
      element: el,
      draggable: true,
      anchor: 'center',
    })
      .setLngLat(lnglat)
      .addTo(this.map);
    entry.marker = marker;

    // 拖曳期間 ghost label：給 user 看到拖到哪
    let ghostEl = null;

    marker.on('dragstart', () => {
      entry.isDragging = true;
      el.style.cursor = 'grabbing';
      // 隱藏 symbol layer text（避免 ghost + symbol 雙層疊）
      try {
        this.map.setFeatureState({ source: this.sourceId, id: feat.id }, { dragging: true });
      } catch (e) {
        // feature 可能尚未進入 source（race），skip
      }
      // 建 ghost label 顯示
      ghostEl = document.createElement('div');
      ghostEl.textContent = feat.label;
      ghostEl.style.cssText = GHOST_LABEL_STYLE + `color: ${feat.color || '#e6edf3'};`;
      el.appendChild(ghostEl);
    });

    marker.on('dragend', () => {
      el.style.cursor = 'grab';
      if (ghostEl) {
        ghostEl.remove();
        ghostEl = null;
      }
      try {
        this.map.setFeatureState({ source: this.sourceId, id: feat.id }, { dragging: false });
      } catch (e) { /* skip */ }
      entry.isDragging = false;
      const ll = marker.getLngLat();
      const lat = Math.round(ll.lat * 1000000) / 1000000;
      const lng = Math.round(ll.lng * 1000000) / 1000000;
      if (typeof onDragEnd === 'function') {
        onDragEnd(feat.id, { lat, lng });
      }
    });
  }

  /**
   * 在 feature 非當前 drag 中時，若 source 內殘留 dragging:true，主動清回 false。
   * 走 getFeatureState 先確認，避免每次 sync 都重 setData 觸發無謂 repaint。
   */
  _clearStuckDragState(featureId, entry) {
    if (entry?.isDragging) return;
    try {
      const cur = this.map.getFeatureState({ source: this.sourceId, id: featureId });
      if (cur && cur.dragging === true) {
        this.map.setFeatureState({ source: this.sourceId, id: featureId }, { dragging: false });
      }
    } catch (_) { /* source 不存在 / id 未進入 source — skip */ }
  }

  clear() {
    for (const entry of this.markers.values()) {
      entry.marker.remove();
    }
    this.markers.clear();
  }

  getMarkerCount() {
    return this.markers.size;
  }
}
