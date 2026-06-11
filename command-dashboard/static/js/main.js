/**
 * main.js — 指揮部前端入口點（C1-F CSP 模組化，v3.0.0）
 *
 * 職責：
 *   - 匯入所有模組並執行一次性初始化
 *   - 全局 click 事件委派（取代 153 個 inline onclick=）
 *   - 取得 /api/version 並顯示版號
 *   - 時鐘 + TTX 模式切換
 *   - 認證初始化（_authInit）
 *   - 啟動 poll 迴圈
 *
 * ⚠️  此模組為 <script type="module"> 唯一入口，不掛任何 window.* 函式。
 *     inline handler 已全部移除（Phase 3），所有互動透過 data-action 委派。
 */

import {
  authInit, cmdLogout, PinLock,
  openSettings, closeSettings,
  exportDashboardJSON, showAuditLog,
  openAdminPanel, closeAdminPanel, adminLogin,
  admShowTab, admShowSys, admChangeAdminPin,
  unlockPinLock, setModalHandlers,
  canAccessMapObjects, canCreateEvents, canUseRealModeControls,
  startSessionStatusPolling, continueSessionFromWarning, logoutFromSessionWarning,
  getToken, authFetch, onAuthChange,
} from './auth.js';
import {
  setPollActive, forcePoll,
} from './ws.js';
import {
  initCop, poll, refresh, renderZoneC,
  openModal, closeModal, switchLeftPanel, switchDecTab,
  showIpiBreakdown, renderZoneA, confirmResolve, appConfirm,
  confirmResetDB,
} from './cop.js';
import {
  initChatPanel, switchRightTab, chatFilterRoom,
} from './chat_panel.js';
import {
  getSeries, expandSpark, getExpandedSpark, renderSparklines,
  buildSliceHtml,
} from './charts.js';
import {
  submitDecision, escalateDecision, closeDecision,
} from './decisions.js';
import {
  openEventForm, closeEventForm, submitEvent,
  showEventProcessModal, _toggleEvtFilter, _toggleEvtGroupMode,
  _autoSaveAndAction, _setAssignedUnit,
  _dlSetSign, _dlAdjust, _dlSetMin, _applyDeadline,
  toggleRightExpand, _evtCardDown, _evtCardUp,
  _addEventNote, _updateEvAndRefresh, _updateEvTypeFromCategories, _syncEvSeverity,
  _renderZoneModal, setZoneModalTab,
  loadEventTaxonomy, openTaxonomyEditor, saveTaxonomyFromEditor,
  _resizeEvtList,
} from './events.js';
import {
  initMap, reloadMapConfig, switchMap, cancelPlaceMode, togglePinEditMode,
  toggleCsel, _toggleMgrsGrid, _toggleLayerPanel, setBasemapTheme,
  _startPolyDraw, _cancelPolyDraw, _finishPolyDraw,
  _startRouteDraw, _cancelRouteDraw, _finishRouteDraw,
  _savePolygon, _saveRoute,
  _openPolyForm, _openRouteForm,
  _deletePolygon, _deleteRoute, _deleteInfra, _deleteEventZone,
  _deleteContact, _shareContactTak, _saveContactNote,
  _resetPolyLabelAnchor, _resetRouteLabelAnchor,
  _panToCoordTarget, _mgrsSearch, _toggleCoordMode,
  _populateNapsgCsel,
  onPlaceTypeChange,
  l3SubTab, openL4Detail, backToL3,
  loadL3Records, _loadPwaIncidents,
  saveMapConfig,
  openMapConfigPanel, closeMapConfigPanel, admUploadMapImage,
  admRemoveMapImage, _cancelEventPin,
  _deleteNode,
  applyMapRoleUiGuards,
  _toggleLayer, _closeLayerPanel, toggleTakFilter,
  setCopStream,
  applyEventTaxonomy,
} from './map.js';

const API_BASE = location.origin;
const POLL_INTERVAL = 5000;

// ── COP 即時同步（issue #29 PR-E）──────────────────────────────────────────
// 與既有 map_config 圖層疊加；operator 用 /api/cop/* 建立/拖/刪，WS 廣播即時同步。
let _copStream = null;

async function _initCopStream() {
  if (_copStream) {
    _copStream.connect();
    return;
  }
  // PR-H：cop_stream 是純資料層，**不依賴地圖**（不再吃 map / MarkerCtor）。因此移除舊的
  // 「地圖就緒才 init」guard —— 否則 _initCopStream 若在地圖 ready 前觸發就 early-return、
  // 又不重試，會導致 _copStream 永遠 null（route/polygon 存不了、事件不即時）。
  // 渲染由 map.js 的 setCopStream→onChange 負責，render 函式自身對圖層未就緒容錯。
  const { createCopStream } = await import('./map/cop_stream.js');
  _copStream = createCopStream({
    getToken,
    authFetch,
    canWrite: () => canAccessMapObjects(),
  });
  setCopStream(_copStream); // 交給 map.js 訂閱 onChange 即時重繪 route/polygon/event
  _copStream.connect();
}

// auth 生命週期：logout / lock 關 WS + 清 marker；login / unlock (重)連（防護 6）
// login/unlock 走 _initCopStream（含「首次 init 曾因地圖未就緒失敗」的重試路徑），
// 不只 connect —— 否則初次 getMap() 為 null 時將永遠連不上。
onAuthChange((type) => {
  if (type === 'logout' || type === 'lock') {
    if (_copStream) _copStream.stop();
  } else if (type === 'login' || type === 'unlock') {
    _initCopStream();
  }
});
// 分頁關閉 / reload 前 cleanup
window.addEventListener('beforeunload', () => {
  if (_copStream) _copStream.stop();
});

// P1-14：切換 active 演習後，前端要依新 scope 重抓資料（否則 map 圖釘 / 面板停在舊場，
// 要 hard reload 才更新）。poll() 重抓 dashboard；cop_stream 重連 → server 依新 active 重 scope
// WS + 上線自動全量 resync（zone/route/event 圖釘）→ map onChange 重繪。
function _refreshAfterExerciseSwitch() {
  try { poll(); } catch (e) { /* poll 失敗不阻斷 */ }
  if (_copStream) { _copStream.stop(); _copStream.connect(); }
}

// P1-14：他人 activate/archive 演習 → server broadcast_all → cop_stream 轉發 'exercise:switched'。
// 本 session 重新依新 scope 對帳（map/面板）+ 更新 header chip（顯示新的當前場 / 無場次）。
document.addEventListener('exercise:switched', () => {
  _refreshAfterExerciseSwitch();
  import('./exercises.js').then(m => {
    // 設定面板開著（正在看演習清單）→ 重渲染清單（含 chip），讓刪除/狀態變更即時反映；
    // 否則只更新 header chip。
    const panelOpen = document.getElementById('settings-overlay')?.classList.contains('show');
    if (panelOpen) m.renderExercisePanel(); else m.initExerciseChip();
  });
});

// ══════════════════════════════════════════════════════════════
// 全局 click 事件委派（取代所有 inline onclick=）
// ══════════════════════════════════════════════════════════════

document.addEventListener('click', function (e) {
  // 若長按觸發了 highlight，忽略本次 click
  if (typeof window._evtCardDidHighlight !== 'undefined' && window._evtCardDidHighlight) return;

  const btn = e.target.closest('[data-action]');
  if (!btn) return;
  const action = btn.dataset.action;
  const id = btn.dataset.id;

  switch (action) {
    // ── 認證 ──
    case 'cmdLogin':       import('./auth.js').then(m => m.handleCmdLogin()); break;
    case 'cmdLogout':      cmdLogout(); break;
    case 'sessionContinue': continueSessionFromWarning(); break;
    case 'sessionLogout':  logoutFromSessionWarning(); break;
    case 'openSettings':   openSettings(); import('./exercises.js').then(m => m.renderExercisePanel()); break;
    case 'closeSettings':  closeSettings(); break;
    case 'exportJSON': {
      import('./cop.js').then(m => exportDashboardJSON(m.getData()));
      break;
    }
    case 'showAuditLog':   showAuditLog(); break;
    case 'openAdminPanel': openAdminPanel(); break;
    case 'openTaxonomyEditor': openTaxonomyEditor(); break;   // #66 PR-C1
    case 'taxSave':        _handleTaxSave(); break;
    case 'closeAdminPanel': closeAdminPanel(); break;
    case 'adminLogin':     adminLogin(); break;
    case 'admShowTab':     admShowTab(btn.dataset.tab); break;
    case 'admShowSys':     admShowSys(); break;
    case 'admChangePin':
    case 'adm-change-pin': admChangeAdminPin(); break;
    case 'adm-toggle-tak': import('./auth.js').then(m => m.admToggleTak()); break;
    case 'unlockPinLock':  unlockPinLock(); break;
    case 'adm-toggle-edit': import('./auth.js').then(m => m.admToggleEdit(btn.dataset.username)); break;
    case 'adm-save-edit': import('./auth.js').then(m => m.admSaveEdit(btn.dataset.username)); break;
    case 'adm-toggle-status': import('./auth.js').then(m => m.admToggleStatus(btn.dataset.username, btn.dataset.status)); break;
    case 'adm-delete': import('./auth.js').then(m => m.admDelete(btn.dataset.username)); break;
    case 'adm-add-account': import('./auth.js').then(m => m.admAddAccount()); break;
    case 'adm-create-pi-node': import('./auth.js').then(m => m.admCreatePiNode()); break;
    case 'adm-rekey-pi-node': import('./auth.js').then(m => m.admRekeyPiNode(btn.dataset.unitId)); break;
    case 'adm-delete-pi-node': import('./auth.js').then(m => m.admDeletePiNode(btn.dataset.unitId)); break;
    case 'adm-push-key-to-pi': import('./auth.js').then(m => m.admPushKeyToPi()); break;
    case 'pi-copy-key': navigator.clipboard.writeText(document.getElementById('pi-key-value')?.textContent || ''); break;
    case 'audit-filter': {
      import('./auth.js').then(m => {
        if (window._auditLogsCache) m.showAuditLog(window._auditLogsCache, btn.dataset.filter);
      });
      break;
    }

    // ── 全局 modal ──
    case 'closeModal':
    case 'close-modal':    closeModal(); break;
    case 'confirmOk':      confirmResolve(true); break;
    case 'confirmCancel':  confirmResolve(false); break;

    // ── 裁示 ──
    case 'showDecisionModal': {
      import('./decisions.js').then(m => m.showDecisionModal(id));
      break;
    }
    case 'submitDecision': submitDecision(btn.dataset.status); break;
    case 'escalateDecision': escalateDecision(id); break;
    case 'closeDecision':  closeDecision(id); break;

    // ── 事件 ──
    case 'openEventForm': {
      if (!canCreateEvents()) break;
      const unit = btn.dataset.unit || '';
      const lat = btn.dataset.lat !== '' ? parseFloat(btn.dataset.lat) : null;
      const lng = btn.dataset.lng !== '' ? parseFloat(btn.dataset.lng) : null;
      openEventForm(unit, (lat != null && lng != null) ? { lat, lng } : null);
      break;
    }
    case 'closeEventForm': closeEventForm(); break;
    case 'submitEvent': {
      if (!canCreateEvents()) break;
      submitEvent();
      break;
    }
    case 'updateStatus': {
      import('./events.js').then(m => m.updateEventStatus(id, btn.dataset.status));
      break;
    }
    case 'autoSaveAndAction': _autoSaveAndAction(id, btn.dataset.evAction); break;
    case 'addEventNote': _addEventNote(id); break;
    case 'updateEvAndRefresh': _updateEvAndRefresh(id, btn.dataset.evAction); break;
    case 'setAssignedUnit': _setAssignedUnit(id, btn.dataset.unit); break;
    case 'resetDeadlineMenu': {
      import('./events.js').then(m => m._resetDeadlineMenu(id));
      break;
    }
    case 'dlSetSign':    _dlSetSign(parseInt(btn.dataset.sign, 10)); break;
    case 'dlAdjust':     _dlAdjust(parseInt(btn.dataset.delta, 10)); break;
    case 'dlSetMin':     _dlSetMin(parseInt(btn.dataset.min, 10)); break;
    case 'applyDeadline': _applyDeadline(); break;
    case 'toggleEvtFilter':    _toggleEvtFilter(btn.dataset.filter); break;
    case 'clearEvtFilter': {
      import('./events.js').then(m => { m.clearEvtFilter(); renderZoneC(getSeries()); });
      break;
    }
    case 'toggleEvtGroupMode': _toggleEvtGroupMode(btn.dataset.mode); break;
    case 'toggleRightExpand':  toggleRightExpand(btn.dataset.section); break;
    case 'openEventByCode': {
      import('./events.js').then(m => m.openEventByCode(id));
      break;
    }
    case 'zoneTab': {
      setZoneModalTab(btn.dataset.tab);
      _renderZoneModal();
      break;
    }
    case 'l3SubTab':   l3SubTab(btn.dataset.tabid, btn.dataset.active); break;
    case 'openL4Detail': {
      if (!canAccessMapObjects()) break;
      openL4Detail(btn.dataset.unit, btn.dataset.table, parseInt(btn.dataset.index, 10));
      break;
    }
    case 'backToL3':   backToL3(); break;

    // ── 地圖 ──
    case 'switchMap':      switchMap(btn.dataset.map); break;
    case 'cancelPlaceMode': cancelPlaceMode(); break;
    case 'cancelEventPin': _cancelEventPin(); break;
    case 'togglePinEditMode': togglePinEditMode(); break;
    case 'toggleCsel':     toggleCsel(); break;
    case 'toggleMgrsGrid': _toggleMgrsGrid(); break;
    case 'setBasemapTheme': setBasemapTheme(btn.dataset.theme); break;
    case 'toggleLayerPanel': _toggleLayerPanel(); break;
    case 'toggleLayer': _toggleLayer(btn.dataset.layer); break;
    case 'toggleTakFilter': toggleTakFilter(btn.dataset.takfilter); break;
    case 'closeLayerPanel': _closeLayerPanel(); break;
    case 'openMapConfigPanel': openMapConfigPanel(); break;
    case 'closeMapConfigPanel': closeMapConfigPanel(); break;
    case 'admUploadMapImage': admUploadMapImage(); break;
    case 'admRemoveMapImage': admRemoveMapImage(); break;
    case 'startPolyDraw': {
      if (!canAccessMapObjects()) break;
      _startPolyDraw();
      break;
    }
    case 'cancelPolyDraw': _cancelPolyDraw(); break;
    case 'finishPolyDraw': {
      if (!canAccessMapObjects()) break;
      _finishPolyDraw();
      break;
    }
    case 'savePolygon': {
      if (!canAccessMapObjects()) break;
      _savePolygon();
      break;
    }
    case 'deletePolygon': {
      if (!canAccessMapObjects()) break;
      _deletePolygon(id);
      break;
    }
    case 'deleteEventZone': {
      if (!canAccessMapObjects()) break;
      _deleteEventZone(id);
      break;
    }
    case 'resetPolyLabelAnchor': {
      if (!canAccessMapObjects()) break;
      _resetPolyLabelAnchor(id);
      break;
    }
    case 'deleteInfra': {
      if (!canAccessMapObjects()) break;
      _deleteInfra(id);
      break;
    }
    case 'deleteContact': {
      if (!canAccessMapObjects()) break;
      _deleteContact(btn.dataset.id);
      break;
    }
    case 'saveContactNote': {
      if (!canAccessMapObjects()) break;
      _saveContactNote(btn.dataset.id);
      break;
    }
    case 'shareContactTak': {
      if (!canAccessMapObjects()) break;  // P2-30 part 3：廣播放寬 operator+（後端 WRITE_ROLES）
      _shareContactTak(btn.dataset.id);
      break;
    }
    case 'startRouteDraw': {
      if (!canAccessMapObjects()) break;
      _startRouteDraw();
      break;
    }
    case 'cancelRouteDraw': _cancelRouteDraw(); break;
    case 'finishRouteDraw': {
      if (!canAccessMapObjects()) break;
      _finishRouteDraw();
      break;
    }
    case 'saveRoute': {
      if (!canAccessMapObjects()) break;
      _saveRoute();
      break;
    }
    case 'deleteRoute': {
      if (!canAccessMapObjects()) break;
      _deleteRoute(id);
      break;
    }
    case 'resetRouteLabelAnchor': {
      if (!canAccessMapObjects()) break;
      _resetRouteLabelAnchor(id);
      break;
    }
    // P2-34（#220）：放置節點/設施/敵情標記改走長按建立對話框（CreatePopup）；
    // 舊 arm-then-click dispatch（openNodePlace/startNodePlace/openContactPlace/
    // startContactPlace/openInfraForm/startInfraPlace）已隨面板入口退場。
    case 'deleteNode': {
      if (!canAccessMapObjects()) break;
      _deleteNode(id);
      break;
    }
    case 'mgrsSearch':     _mgrsSearch(); break;
    case 'toggleCoordMode': _toggleCoordMode(); break;
    case 'panToCoordTarget': _panToCoordTarget(e); break;
    case 'saveMapConfig':  saveMapConfig(); break;
    case 'openZone': {
      if (!canAccessMapObjects()) break;
      import('./cop.js').then(m => m.openZoneByType(btn.dataset.type));
      break;
    }
    case 'showIpiBreakdown': showIpiBreakdown(); break;
    case 'confirmResetDB': confirmResetDB(); break;
    case 'showSlice': {
      const idx = parseInt(btn.dataset.idx, 10);
      const focusType = btn.dataset.focus || null;
      const d = getSeries();
      if (d) {
        const { title, body } = buildSliceHtml(idx, d);
        openModal(title, body);
      }
      break;
    }

    // ── Sparkline ──
    case 'expandSpark': expandSpark(id); break;

    // ── COP 面板切換 ──
    case 'switchLeftPanel': switchLeftPanel(btn.dataset.group); break;
    case 'switchDecTab':    switchDecTab(btn.dataset.tab); break;
    // ── 右欄頂層 tab（事件追蹤 ｜ 通聯）+ 通聯 room 過濾（#213 b1）──
    // 切回事件即重算列表高（顯示後才量得到 clientHeight；補隱藏期間 window resize 的殘留）。
    case 'switchRightTab':  switchRightTab(btn.dataset.rtab); if (btn.dataset.rtab !== 'chat') _resizeEvtList(); break;
    case 'chatFilterRoom':  chatFilterRoom(btn.dataset.room); break;

    // ── 演習管理（P1-14 PR-2，取代死掉的實戰/演練切換）──
    case 'openExercisePanel': {
      // 開 settings 並渲染 / 捲到演習區
      openSettings();
      import('./exercises.js').then(m => {
        m.renderExercisePanel();
        document.getElementById('stg-exercise-section')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
      break;
    }
    case 'exCreate': {
      if (!canUseRealModeControls()) break;   // 後端亦強制；UI 提前擋
      import('./exercises.js').then(m => m.handleExCreate());
      break;
    }
    case 'exActivate': {
      if (!canUseRealModeControls()) break;
      import('./exercises.js').then(m => m.handleExActivate(btn.dataset.id).then(_refreshAfterExerciseSwitch));
      break;
    }
    case 'exArchive': {
      if (!canUseRealModeControls()) break;
      import('./exercises.js').then(m => m.handleExArchive(btn.dataset.id).then(_refreshAfterExerciseSwitch));
      break;
    }
    case 'exDelete': {
      if (!canUseRealModeControls()) break;   // 後端 SYSADMIN_ONLY 強制；UI 鈕亦僅 sysadmin 顯示
      import('./exercises.js').then(m => m.handleExDelete(btn.dataset.id, btn.dataset.name));
      break;
    }
    case 'exRefresh': {
      import('./exercises.js').then(m => m.handleExRefresh());
      break;
    }

    // ── 行動裝置：右側 panel 展開/收合（#165）──
    case 'toggleMobilePanel': {
      const panel = document.getElementById('panel-right');
      const toggleBtn = document.getElementById('panel-toggle-btn');
      if (!panel) break;
      const isOpen = panel.classList.contains('mobile-open');
      panel.classList.toggle('mobile-open', !isOpen);
      if (toggleBtn) {
        toggleBtn.textContent = isOpen ? '☰' : '✕';
        toggleBtn.setAttribute('aria-label', isOpen ? '開啟事件追蹤面板' : '關閉事件追蹤面板');
      }
      break;
    }
  }
});

// ── 行動裝置：點 panel 外部關閉 overlay panel（#165）──
// matchMedia 與 CSS 斷點同值；改用 data-action 做 guard 避免未來包容器元素時 closest('#id') 失效
const _mobileBreak = window.matchMedia('(max-width:900px)');
document.addEventListener('click', function (e) {
  const panel = document.getElementById('panel-right');
  if (!panel || !panel.classList.contains('mobile-open')) return;
  if (!_mobileBreak.matches) return;
  if (!panel.contains(e.target) && !e.target.closest('[data-action="toggleMobilePanel"]')) {
    panel.classList.remove('mobile-open');
    const btn = document.getElementById('panel-toggle-btn');
    if (btn) { btn.textContent = '☰'; btn.setAttribute('aria-label', '開啟事件追蹤面板'); }
  }
}, { capture: false });

// ── 長按事件卡片（mousedown / touchstart）──
document.addEventListener('mousedown', function (e) {
  const card = e.target.closest('[data-longpress-id]');
  if (card) _evtCardDown(card.dataset.longpressId);
});
document.addEventListener('mouseup', _evtCardUp);
document.addEventListener('mouseleave', _evtCardUp);
document.addEventListener('touchstart', function (e) {
  const card = e.target.closest('[data-longpress-id]');
  if (card) _evtCardDown(card.dataset.longpressId);
}, { passive: true });
document.addEventListener('touchend', _evtCardUp);

// ── change 事件（select）──
document.addEventListener('change', function (e) {
  const sel = e.target.closest('[data-change-action]');
  if (!sel) return;
  const action = sel.dataset.changeAction;
  switch (action) {
    case 'setAssignedUnit': _setAssignedUnit(sel.dataset.id, sel.value); break;
    case 'onPlaceTypeChange': onPlaceTypeChange(); break;
    case 'syncEvSeverity': _syncEvSeverity(); break;
  }
});

// ── keydown 事件 ──
document.addEventListener('keydown', function (e) {
  const target = e.target.closest('[data-key-action]');
  if (!target || e.key !== 'Enter') return;
  if (target.dataset.keyAction === 'mgrsSearch') _mgrsSearch();
});

// ── input 事件 ──
document.addEventListener('input', function (e) {
  const inp = e.target.closest('[data-input-action]');
  if (!inp) return;
  if (inp.dataset.inputAction === 'syncEvSeverity') _syncEvSeverity();
});

// ── overlay 背景點擊關閉 ──
const overlayEl = document.getElementById('overlay');
if (overlayEl) {
  overlayEl.addEventListener('click', function (e) {
    if (e.target === this) closeModal();
  });
}
const eventOverlayEl = document.getElementById('event-overlay');
if (eventOverlayEl) {
  eventOverlayEl.addEventListener('click', function (e) {
    if (e.target === this) closeEventForm();
  });
}

// ── events:showProcessModal CustomEvent（來自 map.js / cop.js）──
document.addEventListener('events:showProcessModal', function (e) {
  const { zone } = e.detail || {};
  if (zone && canAccessMapObjects()) showEventProcessModal(zone);
});

// ══════════════════════════════════════════════════════════════
// 時鐘
// ══════════════════════════════════════════════════════════════

function _updateClock() {
  const now = new Date();
  const clockEl = document.getElementById('clock');
  const dateEl  = document.getElementById('date-label');
  if (clockEl) clockEl.textContent = now.toTimeString().slice(0, 8);
  if (dateEl) {
    const mm = String(now.getMonth() + 1).padStart(2, '0');
    const dd = String(now.getDate()).padStart(2, '0');
    const dayName = ['日', '一', '二', '三', '四', '五', '六'][now.getDay()];
    dateEl.textContent = `${mm}/${dd} (${dayName})`;
  }
}
setInterval(_updateClock, 1000);
_updateClock();

// ══════════════════════════════════════════════════════════════
// 角色 UI 守門
// ══════════════════════════════════════════════════════════════

function _applyRoleUiGuards() {
  // P1-14 PR-2：演習 chip 對所有角色可見（點擊看 list）；建立 / 啟動 / 歸檔鈕
  // 由 exercises.renderExercisePanel() 依 canUseRealModeControls() 決定是否渲染。
  applyMapRoleUiGuards();
}

// #66：taxonomy 變更後重渲染管線（events 側 apply → map 側 apply+bake → 重建兩邊下拉）。
// 登入後 onEnterDashboard 與編輯器存檔後共用，確保即時生效（不必重登）。
async function _reloadTaxonomyPipeline() {
  const tax = await loadEventTaxonomy();
  if (tax) {
    applyEventTaxonomy(tax);
    _populateNapsgCsel();
    _updateEvTypeFromCategories();
  }
}

// #66 PR-C1：編輯器存檔 → 後端守門通過後跑重渲染管線 + 關 modal（失敗訊息留在 modal 內）。
async function _handleTaxSave() {
  const res = await saveTaxonomyFromEditor();
  if (res && res.ok) {
    await _reloadTaxonomyPipeline();
    closeModal();
  }
}

// ══════════════════════════════════════════════════════════════
// resize
// ══════════════════════════════════════════════════════════════

window.addEventListener('resize', () => {
  const d = getSeries();
  import('./cop.js').then(m => {
    if (d) m.refresh();
  });
  import('./events.js').then(m => m._resizeEvtList());
});

// ══════════════════════════════════════════════════════════════
// /api/version：顯示版號
// ══════════════════════════════════════════════════════════════

async function _loadVersion() {
  try {
    const resp = await fetch(API_BASE + '/api/version');
    if (!resp.ok) return;
    const { cmd_version } = await resp.json();
    if (cmd_version) {
      document.body.dataset.cmdVersion = cmd_version;
      document.title = 'ICS 指揮部 ' + cmd_version;
      document.querySelectorAll('.h-ver').forEach(el => {
        el.textContent = 'cmd-' + cmd_version;
      });
    }
  } catch (e) { /* 非關鍵，失敗不影響功能 */ }
}

function _waitForGlobal(name, timeoutMs = 3000) {
  const started = Date.now();
  return new Promise((resolve, reject) => {
    const tick = () => {
      if (window[name]) {
        resolve(window[name]);
        return;
      }
      if (Date.now() - started > timeoutMs) {
        reject(new Error(`${name} not loaded`));
        return;
      }
      setTimeout(tick, 25);
    };
    tick();
  });
}

function _loadClassicScript(src) {
  return new Promise((resolve, reject) => {
    const existing = document.querySelector(`script[src="${src}"]`);
    if (existing) {
      if (existing.dataset.loaded === 'true') resolve();
      else {
        existing.addEventListener('load', resolve, { once: true });
        existing.addEventListener('error', () => reject(new Error(`${src} failed`)), { once: true });
      }
      return;
    }
    const script = document.createElement('script');
    script.src = src;
    script.async = false;
    script.dataset.loaded = 'false';
    script.addEventListener('load', () => {
      script.dataset.loaded = 'true';
      resolve();
    }, { once: true });
    script.addEventListener('error', () => reject(new Error(`${src} failed`)), { once: true });
    document.head.appendChild(script);
  });
}

// P1-10b 步驟 11：Leaflet 套件 load 完整移除（step 5-10 已把 polygons/infra/routes/
// flows/zones/draw/popup/coord_tools/MGRS grid 全 port MapLibre EntityLayer；map.js
// 內所有 L.* 真實使用已刪）。HTML #leaflet-map id + CSS .leaflet-* 保留為 alias
// （見 ROADMAP P1-10b 設計決策）。靜態檔 leaflet.min.js / leaflet.min.css /
// protomaps-leaflet.js / marker-icon{,-2x}.png / marker-shadow.png 由本 PR 一併移除。

// ══════════════════════════════════════════════════════════════
// 啟動
// ══════════════════════════════════════════════════════════════

(async function _boot() {
  // MapLibre + pmtiles（地圖核心，P1-10b 起）
  await _loadClassicScript('/static/lib/maplibre-gl.js').catch(() => null);
  await _loadClassicScript('/static/lib/pmtiles.js').catch(() => null);
  // milsymbol（P2-05 MIL-STD-2525 符號渲染，UMD window.ms）。失敗不阻擋地圖；
  // TAK 單位 fallback 既有渲染（render path 內 guard window.ms）。
  await _loadClassicScript('/static/lib/milsymbol.js').catch(() => null);
  await _waitForGlobal('maplibregl').catch(() => null);

  // 1. 版號
  await _loadVersion();

  // 2. 初始化所有模組
  setModalHandlers({ openModal, closeModal });
  initCop();
  initChatPanel();  // #213 b1：右欄通聯面板（poll + 監聽 tak:conn-state 控 tab 顯隱）

  // 3. 預填表單下拉
  _populateNapsgCsel();
  _updateEvTypeFromCategories();

  // 4. P1-10a UX hotfix B：focus mode expand 已徹底拿掉（commander_dashboard.html
  //    無 toggleRightExpand data-action）。restore 路徑同步移除；舊 session
  //    殘留 _expandedSection 主動清空，避免用戶 reload 後卡在 expanded 無 UI 解。
  //    events.js 的 _applyRightExpand / toggleRightExpand 函式為 dead code，
  //    未來需要 focus mode 再重新接 UI（見 events.js TODO）。
  sessionStorage.removeItem('_expandedSection');

  const savedLeftGroup = sessionStorage.getItem('_leftPanelGroup');
  if (savedLeftGroup) switchLeftPanel(savedLeftGroup);

  const savedSpark = sessionStorage.getItem('_expandedSpark');
  if (savedSpark) {
    const sc = document.getElementById(savedSpark);
    if (sc) sc.classList.add('expanded');
  }

  // 5. 認證：有 session → heartbeat → 進入 dashboard；否則顯示登入畫面
  await authInit({
    onEnterDashboard: async () => {
      _applyRoleUiGuards();
      // P1-10d 地基（#60/#66）：登入後載入事件 taxonomy（boot 時登入前 GET 會 401）。
      // 套用到 events.js（NAPSG_EVENTS）+ map.js（_EVENT_TYPES）後重建事件下拉，確保用的是
      // runtime SoT（/api/event_taxonomy）；admin 編輯（#66）後重登也即時反映。失敗則沿用內建 fallback。
      await _reloadTaxonomyPipeline();
      // 登入後重抓 map_config：boot 時（登入前）的 GET /api/map_config 會 401 → _mapConfig=null
      // → 地圖空白（節點/網格不出現，要 cmd-shift-R）。登入帶 token 後重抓 → 正常 render。
      reloadMapConfig();
      // P1-14 PR-2：登入後初始化 header 演習 chip（顯示 active 演習或「實戰」）。
      import('./exercises.js').then(m => m.initExerciseChip());
      startSessionStatusPolling();
      setPollActive(true);
      poll();
      setInterval(() => poll(), POLL_INTERVAL);
      // COP 即時同步：地圖就緒後連 WS（issue #29 PR-E）
      _initCopStream();

      // 恢復上次開啟的事件 modal
      setTimeout(() => {
        const savedEvId = sessionStorage.getItem('_openEventId');
        if (savedEvId) {
          import('./map.js').then(m => {
            const zone = m.findZoneByEventId(savedEvId);
            if (zone && canAccessMapObjects()) showEventProcessModal(zone);
            else {
              import('./cop.js').then(cop => {
                const data = cop.getData();
                const ev = (data?.events || []).find(e => e.id === savedEvId);
                if (ev) import('./events.js').then(ev_m => ev_m.showEventDetail(ev));
              });
            }
          });
        }
      }, 500);
    },
  });
})();
