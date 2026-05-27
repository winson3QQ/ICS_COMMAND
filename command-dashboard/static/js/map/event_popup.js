/**
 * event_popup.js — 長按事件回報 popup（NAPSG 兩階段選單）
 *
 * P1-10b 步驟 9：取代原 map.js 內 `L.popup` + `L.DomUtil` + `L.DomEvent` 路徑。
 *
 * 流程（不變，僅換 host）：
 *   1. 長按地圖 → caller 給 (lat, lng) 呼叫 open()
 *   2. Popup 顯示 group 按鈕（_EVENT_GROUPS）+ reporter unit 下拉 + MGRS
 *   3. 點 group → showGroup(key)，popup 內容換成該 group 的 type 按鈕（_EVENT_TYPES）
 *   4. 點 type → onSubmit(typeKey) 回 caller（caller 負責 fetch /api/events + 寫 map_config）
 *   5. 點「← 返回」回 step 2；點 × 或重新長按關閉
 *
 * MapLibre 對照：
 *   - L.popup → maplibregl.Popup
 *   - .setLatLng([lat,lng]) → .setLngLat([lng,lat])  ← 順序倒過來
 *   - .setContent(node) → .setDOMContent(node)
 *   - .openOn(map) → .addTo(map)
 *   - on('remove', fn) → on('close', fn)
 *   - L.DomUtil.create(tag, cls, parent) → 原生 createElement + appendChild
 *   - L.DomEvent.on(el, ev, fn) / stopPropagation(e) → 原生 addEventListener / e.stopPropagation()
 *
 * CSS：原 `.leaflet-popup-*` 規則須在 commander_dashboard.html 改為 `.maplibregl-popup-*`
 *      （與本檔同 PR 一併改；本檔不直接動 CSS）。`ev-popup` className 仍透過
 *      Popup({ className: 'ev-popup' }) 套用為 wrapper class，內部結構 class 對應如下：
 *
 *      MapLibre 結構                Leaflet 對照
 *      .maplibregl-popup-content    .leaflet-popup-content-wrapper + .leaflet-popup-content
 *      .maplibregl-popup-tip        .leaflet-popup-tip
 *      .maplibregl-popup-close-button .leaflet-popup-close-button
 */

/**
 * EventPopup — 單例式 popup 控制器。caller 重複呼叫 open() 會關掉舊的開新的。
 *
 * 不負責（caller 處理）：
 *   - long press 判定（maplibre_core 已處理）
 *   - 權限檢查（canCreateEvents）
 *   - 提交後寫 DB / refresh marker（caller 的 onSubmit callback）
 *   - reporter unit 與外部 #place-report-unit 同步（caller 的 onReporterChange callback）
 */
export class EventPopup {
  /**
   * @param {maplibregl.Map} map
   * @param {object} maplibreglNs - window.maplibregl global
   * @param {object} deps
   *   - groups: { [groupKey]: groupLabel } e.g. {security:'安全',rescue:'救援',...}
   *   - types:  { [typeKey]: { label, group, severity } } e.g. {explosive:{label:'疑似爆裂物',group:'security',severity:'critical'}, ...}
   *   - reporterOptions: [[value, text], ...] e.g. [['command','指揮部'],...]
   *   - getReporter: () => string  current default reporter unit value
   *   - onReporterChange: (newVal) => void  sync to external #place-report-unit
   *   - latlngToMgrs: (lat, lng) => string
   *   - onSubmit: (typeKey, { lat, lng, reporter }) => void  caller 在這裡 fetch /api/events
   */
  constructor(map, maplibreglNs, deps) {
    if (!map) throw new Error('EventPopup: map required');
    if (!maplibreglNs?.Popup) throw new Error('EventPopup: maplibregl.Popup required');
    if (!deps?.groups || !deps?.types) throw new Error('EventPopup: deps.groups and deps.types required');
    this.map = map;
    this.maplibregl = maplibreglNs;
    this.deps = deps;
    this._popup = null;
    this._latlng = null;   // { lat, lng } 當前 popup 座標（給 _back / _submit 用）
  }

  /**
   * 開 popup 於指定座標，初始顯示 group 按鈕。
   * 若已有 popup 開啟，先關再開新的。
   */
  open(lat, lng) {
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) return;
    this.close();
    this._latlng = { lat, lng };
    this._popup = new this.maplibregl.Popup({
      className: 'ev-popup',
      closeButton: true,
      closeOnClick: false,
      maxWidth: '280px',
      offset: [0, -6],
    })
      .setLngLat([lng, lat])    // MapLibre 順序：[lng, lat]
      .setDOMContent(this._buildGroups())
      .addTo(this.map);
    this._popup.on('close', () => {
      this._popup = null;
      this._latlng = null;
    });
  }

  /**
   * 切到指定 group 的 type 按鈕列表（caller 通常不直接叫；group 按鈕 click 內部觸發）。
   */
  showGroup(groupKey) {
    if (!this._popup || !this._latlng) return;
    this._popup.setDOMContent(this._buildTypes(groupKey));
  }

  /** 返回 group 選單（type 按鈕 ← 返回 觸發） */
  back() {
    if (!this._latlng) return;
    this._popup.setDOMContent(this._buildGroups());
  }

  /** 主動關閉（caller 在事件提交成功後通常呼叫） */
  close() {
    if (this._popup) {
      this._popup.remove();
      this._popup = null;
    }
    this._latlng = null;
  }

  /** 給外部測試 / 除錯用 */
  isOpen() {
    return this._popup !== null;
  }

  getLatLng() {
    return this._latlng ? { ...this._latlng } : null;
  }

  // ── 內部 DOM 建構 ────────────────────────────────────

  _buildGroups() {
    const { groups, reporterOptions, getReporter, onReporterChange, latlngToMgrs } = this.deps;

    const wrap = _el('div', 'ev-popup-inner');

    const header = _el('div', 'ev-popup-header', wrap);
    const repWrap = _el('div', 'ev-popup-reporter-wrap', header);
    const lbl = _el('label', '', repWrap);
    lbl.textContent = '回報：';
    const sel = _el('select', '', repWrap);
    sel.id = 'ev-popup-unit';
    const currentReporter = getReporter?.() || '';
    (reporterOptions || []).forEach(([v, t]) => {
      const opt = document.createElement('option');
      opt.value = v;
      opt.textContent = t;
      if (v === currentReporter) opt.selected = true;
      sel.appendChild(opt);
    });
    sel.addEventListener('change', () => {
      onReporterChange?.(sel.value);
    });

    const mgrsSpan = _el('span', 'ev-popup-mgrs', header);
    const { lat, lng } = this._latlng;
    mgrsSpan.textContent = `📍 ${latlngToMgrs ? latlngToMgrs(lat, lng) : ''}`;

    const grid = _el('div', 'ev-popup-groups', wrap);
    Object.entries(groups).forEach(([k, label]) => {
      const btn = _el('button', 'ev-popup-group-btn', grid);
      btn.type = 'button';
      btn.textContent = label;
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        this.showGroup(k);
      });
    });
    return wrap;
  }

  _buildTypes(groupKey) {
    const { groups, types } = this.deps;
    const wrap = _el('div', 'ev-popup-inner');

    const header = _el('div', 'ev-popup-header', wrap);
    const back = _el('span', 'ev-popup-back', header);
    back.style.marginBottom = '0';
    back.textContent = '← 返回';
    back.addEventListener('click', (e) => {
      e.stopPropagation();
      this.back();
    });
    const grpSpan = _el('span', 'ev-popup-mgrs', header);
    grpSpan.textContent = groups[groupKey] || groupKey;

    const list = _el('div', 'ev-popup-types', wrap);
    Object.entries(types)
      .filter(([, v]) => v.group === groupKey)
      .forEach(([k, v]) => {
        const sev = v.severity || 'info';
        const btn = _el('button', `ev-popup-type-btn sev-${sev}`, list);
        btn.type = 'button';
        btn.textContent = v.label;
        btn.addEventListener('click', (e) => {
          e.stopPropagation();
          this._handleSubmit(k);
        });
      });
    return wrap;
  }

  _handleSubmit(typeKey) {
    if (!this._latlng) return;
    const { onSubmit } = this.deps;
    // 從 popup 內 select 讀最新值（user 可能改過）；找不到時 caller 從外部 #place-report-unit 取
    const reporter = document.getElementById('ev-popup-unit')?.value || this.deps.getReporter?.() || '';
    const latlng = { ...this._latlng };
    this.close();
    onSubmit?.(typeKey, { ...latlng, reporter });
  }
}

/** _el(tag, cls?, parent?) — 取代 L.DomUtil.create */
function _el(tag, cls, parent) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (parent) parent.appendChild(el);
  return el;
}
