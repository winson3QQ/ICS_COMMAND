/**
 * draw_tools.js — MapLibre 繪製模式 preview（polygon / route）
 *
 * P1-10b 步驟 8：取代原 map.js 內 _addPolyVertex / _addRouteVertex
 * 走 Leaflet L.circleMarker + L.polygon/polyline 的路徑（MapLibre 不存在）。
 *
 * 設計：
 *   - 共用一份 'draw-vertices' source（Point geometry）— 用 circle layer 渲染頂點
 *   - 共用一份 'draw-shape' source（Polygon 或 LineString）— fill + line layer 渲染預覽形狀
 *   - kind='polygon' 時 ≥3 頂點 render Polygon；kind='route' 時 ≥2 頂點 render LineString
 *   - 顏色依 kind 區分（polygon 藍 / route 綠）— 與 banner 配色一致
 *
 * 不負責（caller 處理）：
 *   - 開始 / 結束 banner UI 切換
 *   - cursor 樣式
 *   - click event 截獲（caller 在 onClick callback 內判 state 後叫 addVertex）
 *   - 完成後 saveMapConfig + render（caller 的 _save* 函式）
 */

const EMPTY_FC = Object.freeze({ type: 'FeatureCollection', features: [] });

const COLORS = {
  polygon: '#58a6ff',
  route: '#56d364',
};

export class DrawPreview {
  /**
   * @param {maplibregl.Map} map
   */
  constructor(map) {
    if (!map) throw new Error('DrawPreview: map required');
    this.map = map;
    this.kind = null;      // 'polygon' | 'route' | null
    this.latlngs = [];     // [[lat, lng], ...]
    this._installed = false;
  }

  _install() {
    if (this._installed) return;
    if (!this.map.getSource('draw-vertices')) {
      this.map.addSource('draw-vertices', { type: 'geojson', data: EMPTY_FC });
    }
    if (!this.map.getSource('draw-shape')) {
      this.map.addSource('draw-shape', { type: 'geojson', data: EMPTY_FC });
    }
    // Preview fill（polygon 才有）
    if (!this.map.getLayer('draw-shape-fill')) {
      this.map.addLayer({
        id: 'draw-shape-fill', source: 'draw-shape', type: 'fill',
        filter: ['==', ['geometry-type'], 'Polygon'],
        paint: { 'fill-color': ['get', 'color'], 'fill-opacity': 0.08 },
      });
    }
    // Preview outline（polygon 邊 + route line 共用 line layer）
    if (!this.map.getLayer('draw-shape-stroke')) {
      this.map.addLayer({
        id: 'draw-shape-stroke', source: 'draw-shape', type: 'line',
        paint: {
          'line-color': ['get', 'color'],
          'line-width': 1.5,
          'line-dasharray': [3, 1.5],
        },
      });
    }
    // Vertex 圓點
    if (!this.map.getLayer('draw-vertices')) {
      this.map.addLayer({
        id: 'draw-vertices', source: 'draw-vertices', type: 'circle',
        paint: {
          'circle-radius': 4,
          'circle-color': ['get', 'color'],
          'circle-stroke-color': '#ffffff',
          'circle-stroke-width': 2,
        },
      });
    }
    this._installed = true;
  }

  /** 進入繪製模式 — kind: 'polygon' | 'route' */
  start(kind) {
    if (kind !== 'polygon' && kind !== 'route') throw new Error(`DrawPreview.start: invalid kind '${kind}'`);
    this._install();
    this.kind = kind;
    this.latlngs = [];
    this._render();
  }

  /** 取消（清掉所有頂點 + preview，但保留 layer/source 等下次 start 重用） */
  cancel() {
    this.kind = null;
    this.latlngs = [];
    this._renderEmpty();
  }

  /** 加新頂點。caller 自己判斷是否在 draw mode 內才呼叫 */
  addVertex(lat, lng) {
    if (!this.kind) return;
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) return;
    this.latlngs.push([lat, lng]);
    this._render();
  }

  /** 拿目前 latlngs（caller finish 時用） */
  getLatlngs() {
    return [...this.latlngs];
  }

  isActive() {
    return this.kind !== null;
  }

  vertexCount() {
    return this.latlngs.length;
  }

  /** 已收集頂點數能否完成（polygon ≥ 3 / route ≥ 2） */
  canFinish() {
    if (this.kind === 'polygon') return this.latlngs.length >= 3;
    if (this.kind === 'route') return this.latlngs.length >= 2;
    return false;
  }

  _renderEmpty() {
    if (!this._installed) return;
    this.map.getSource('draw-vertices').setData(EMPTY_FC);
    this.map.getSource('draw-shape').setData(EMPTY_FC);
  }

  _render() {
    if (!this._installed) return;
    const color = COLORS[this.kind] || '#888888';

    // Vertex points
    const vertexFeats = this.latlngs.map(([lat, lng]) => ({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [lng, lat] },
      properties: { color },
    }));
    this.map.getSource('draw-vertices').setData({
      type: 'FeatureCollection', features: vertexFeats,
    });

    // Shape preview
    let shapeFeat = null;
    if (this.latlngs.length >= 2) {
      const coords = this.latlngs.map(([lat, lng]) => [lng, lat]);
      if (this.kind === 'polygon' && this.latlngs.length >= 3) {
        // 自動閉合
        const closed = [...coords, coords[0]];
        shapeFeat = {
          type: 'Feature',
          geometry: { type: 'Polygon', coordinates: [closed] },
          properties: { color },
        };
      } else {
        // route 或 polygon 還未到 3 點 → 用 LineString 預覽
        shapeFeat = {
          type: 'Feature',
          geometry: { type: 'LineString', coordinates: coords },
          properties: { color },
        };
      }
    }
    this.map.getSource('draw-shape').setData({
      type: 'FeatureCollection', features: shapeFeat ? [shapeFeat] : [],
    });
  }

  /** 拆 layer + source（map switch 或 reset 時用；caller 通常不需要） */
  destroy() {
    if (!this._installed) return;
    ['draw-vertices', 'draw-shape-stroke', 'draw-shape-fill'].forEach((id) => {
      if (this.map.getLayer(id)) this.map.removeLayer(id);
    });
    ['draw-vertices', 'draw-shape'].forEach((id) => {
      if (this.map.getSource(id)) this.map.removeSource(id);
    });
    this._installed = false;
    this.kind = null;
    this.latlngs = [];
  }
}
