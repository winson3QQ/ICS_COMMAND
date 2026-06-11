/**
 * create_popup.js — 長按地圖 → 統一「建立」contextual 對話框（P2-34 / #220）
 *
 * 取代原 event_popup.js 的單一用途事件 popup：升成**多類別 launcher**。長按是位置錨定的，
 * 本就是建立動作的正確 altitude——所有建立（事件 / 設施 / 節點 / 感知·敵情標記）收進這裡，
 * filter 面板（☰）回歸純 view（#219）。
 *
 * 三段流程：
 *   stage 0  類別按鈕（caller 已依角色濾，只傳可建立的類別）+ MGRS 座標
 *   stage 1  該類別子型：
 *              - 一般類別（contact/infra/zone）→ 單層子型清單 → onCreate(cat, subValue)
 *              - 事件（twoStage）→ NAPSG group 按鈕（+ 回報單位下拉）
 *   stage 2  （僅事件）group → type 按鈕 → onCreate(eventCat, { typeKey, reporter })
 *   任何 stage 可「← 返回」上一層；× 或重新長按關閉。
 *
 * 設計取捨（reality check #220）：
 *   - **marker-first**：caller 傳入的類別順序即顯示順序，感知/敵情標記排首（doctrine）。
 *   - **深度**：事件仍是 類別→group→type 三層；其餘類別 類別→子型 兩層（標記=2 tap）。
 *   - **角色**：本檔不判權限——caller 依 canCreateEvents / canUseRealModeControls /
 *     canAccessMapObjects 決定 `categories` 內容；空清單則 caller 不開 popup。
 *
 * caller 不負責的事都在 deps callback：onCreate（建立）/ reporter 同步 / latlngToMgrs。
 */

export class CreatePopup {
  /**
   * @param {maplibregl.Map} map
   * @param {object} maplibreglNs - window.maplibregl
   * @param {object} deps
   *   - categories: Array<{
   *       key: string, label: string,
   *       // 一般類別：單層子型
   *       subtypes?: Array<{ value: string, label: string, color?: string }>,
   *       // 事件類別：兩層 + 回報單位
   *       twoStage?: { groups: {[k]:string}, types: {[k]:{label,group,severity,deleted?}} },
   *       reporter?: { options: [[v,t]...], get: ()=>string, onChange: (v)=>void },
   *     }>
   *   - onCreate: (categoryKey, payload) => void
   *       payload：一般類別 = 子型 value 字串；事件 = { typeKey, reporter }
   *   - latlngToMgrs: (lat, lng) => string
   */
  constructor(map, maplibreglNs, deps) {
    if (!map) throw new Error('CreatePopup: map required');
    if (!maplibreglNs?.Popup) throw new Error('CreatePopup: maplibregl.Popup required');
    if (!Array.isArray(deps?.categories) && typeof deps?.getCategories !== 'function') {
      throw new Error('CreatePopup: deps.categories array or deps.getCategories required');
    }
    this.map = map;
    this.maplibregl = maplibreglNs;
    this.deps = deps;
    this._popup = null;
    this._latlng = null;
  }

  /** 開 popup 於長按座標，初始顯示類別按鈕。已開則先關。空類別清單則不開。
   *  類別於 open() 時解析（deps.getCategories 優先，否則 deps.categories）——
   *  讓角色 / 事件 taxonomy 變動即時反映，不必重建 popup。 */
  open(lat, lng) {
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) return;
    this._categories = this.deps.getCategories ? this.deps.getCategories() : this.deps.categories;
    if (!this._categories?.length) return;
    this.close();
    this._latlng = { lat, lng };
    this._popup = new this.maplibregl.Popup({
      className: 'ev-popup',          // 沿用既有 .ev-popup CSS
      closeButton: true,
      closeOnClick: false,
      maxWidth: '280px',
      offset: [0, -6],
    })
      .setLngLat([lng, lat])
      .setDOMContent(this._buildCategories())
      .addTo(this.map);
    this._popup.on('close', () => {
      this._popup = null;
      this._latlng = null;
    });
  }

  close() {
    if (this._popup) {
      this._popup.remove();
      this._popup = null;
    }
    this._latlng = null;
  }

  isOpen() {
    return this._popup !== null;
  }

  getLatLng() {
    return this._latlng ? { ...this._latlng } : null;
  }

  // ── stage 0：類別 ────────────────────────────────────────
  _buildCategories() {
    const wrap = _el('div', 'ev-popup-inner');
    const header = _el('div', 'ev-popup-header', wrap);
    const mgrsSpan = _el('span', 'ev-popup-mgrs', header);
    const { lat, lng } = this._latlng;
    mgrsSpan.textContent = `📍 ${this.deps.latlngToMgrs ? this.deps.latlngToMgrs(lat, lng) : ''}`;

    const grid = _el('div', 'ev-popup-groups', wrap);
    this._categories.forEach((cat) => {
      const btn = _el('button', 'ev-popup-group-btn', grid);
      btn.type = 'button';
      btn.textContent = cat.label;
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        this._showCategory(cat);
      });
    });
    return wrap;
  }

  // ── stage 1：類別子型（事件走 group，其餘走子型清單）────────
  _showCategory(cat) {
    if (!this._popup || !this._latlng) return;
    this._popup.setDOMContent(cat.twoStage ? this._buildEventGroups(cat) : this._buildSubtypes(cat));
  }

  _buildSubtypes(cat) {
    const wrap = _el('div', 'ev-popup-inner');
    const header = _el('div', 'ev-popup-header', wrap);
    _backLink(header, () => this._popup.setDOMContent(this._buildCategories()));
    _el('span', 'ev-popup-mgrs', header).textContent = cat.label;

    const list = _el('div', 'ev-popup-types', wrap);
    (cat.subtypes || []).forEach((s) => {
      const btn = _el('button', 'ev-popup-type-btn', list);
      btn.type = 'button';
      btn.textContent = s.label;
      if (s.color) btn.style.borderLeft = `3px solid ${s.color}`;
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        this._submit(cat.key, s.value);
      });
    });
    return wrap;
  }

  // ── 事件 stage 1：group（+ 回報單位）────────────────────
  _buildEventGroups(cat) {
    const { groups, types } = cat.twoStage;
    const wrap = _el('div', 'ev-popup-inner');
    const header = _el('div', 'ev-popup-header', wrap);
    _backLink(header, () => this._popup.setDOMContent(this._buildCategories()));

    if (cat.reporter) {
      const repWrap = _el('div', 'ev-popup-reporter-wrap', header);
      _el('label', '', repWrap).textContent = '回報：';
      const sel = _el('select', '', repWrap);
      sel.id = 'ev-popup-unit';
      const current = cat.reporter.get?.() || '';
      (cat.reporter.options || []).forEach(([v, t]) => {
        const opt = document.createElement('option');
        opt.value = v;
        opt.textContent = t;
        if (v === current) opt.selected = true;
        sel.appendChild(opt);
      });
      sel.addEventListener('change', () => cat.reporter.onChange?.(sel.value));
    }

    const grid = _el('div', 'ev-popup-groups', wrap);
    const hasLiveType = (gk) => Object.values(types).some((v) => v.group === gk && !v.deleted);
    Object.entries(groups).filter(([k]) => hasLiveType(k)).forEach(([k, label]) => {
      const btn = _el('button', 'ev-popup-group-btn', grid);
      btn.type = 'button';
      btn.textContent = label;
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        this._popup.setDOMContent(this._buildEventTypes(cat, k));
      });
    });
    return wrap;
  }

  // ── 事件 stage 2：type ──────────────────────────────────
  _buildEventTypes(cat, groupKey) {
    const { groups, types } = cat.twoStage;
    const wrap = _el('div', 'ev-popup-inner');
    const header = _el('div', 'ev-popup-header', wrap);
    _backLink(header, () => this._popup.setDOMContent(this._buildEventGroups(cat)));
    _el('span', 'ev-popup-mgrs', header).textContent = groups[groupKey] || groupKey;

    const list = _el('div', 'ev-popup-types', wrap);
    Object.entries(types)
      .filter(([, v]) => v.group === groupKey && !v.deleted)
      .forEach(([k, v]) => {
        const btn = _el('button', `ev-popup-type-btn sev-${v.severity || 'info'}`, list);
        btn.type = 'button';
        btn.textContent = v.label;
        btn.addEventListener('click', (e) => {
          e.stopPropagation();
          const reporter = document.getElementById('ev-popup-unit')?.value || cat.reporter?.get?.() || '';
          this._submit(cat.key, { typeKey: k, reporter });
        });
      });
    return wrap;
  }

  _submit(categoryKey, payload) {
    if (!this._latlng) return;
    const latlng = { ...this._latlng };
    this.close();
    this.deps.onCreate?.(categoryKey, payload, latlng);
  }
}

function _backLink(header, onBack) {
  const back = _el('span', 'ev-popup-back', header);
  back.style.marginBottom = '0';
  back.textContent = '← 返回';
  back.addEventListener('click', (e) => {
    e.stopPropagation();
    onBack();
  });
  return back;
}

function _el(tag, cls, parent) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (parent) parent.appendChild(el);
  return el;
}
