// SPDX-License-Identifier: LicenseRef-Proprietary
// Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
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
  exportDashboardJSON, showAuditLog, downloadSBOM,
  openAdminPanel, closeAdminPanel,
  admShowTab,
  admBackupNow, admRefreshBackups, admToggleDetail, admToggleBackupList, admRestore, admDownloadBackup, admRestoreFromList,
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
  initChatPanel, switchRightTab, chatFilterRoom, chatClearSender, chatRowDown, chatRowUp,
  refreshChatNow, restoreRightTab, chatSend,
} from './chat_panel.js';
import { initRoster, stopRoster } from './roster_panel.js';
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
  _deleteContact, _shareContactTak, _saveContactNote, _changeMarkerAffiliation,
  _resetPolyLabelAnchor, _resetRouteLabelAnchor,
  _startVertexEdit, _finishVertexEdit, _cancelVertexEdit, _saveWaypointName,
  _panToCoordTarget, _mgrsSearch, _toggleCoordMode,
  _populateNapsgCsel,
  onPlaceTypeChange,
  l3SubTab, openL4Detail, backToL3,
  loadL3Records, _loadPwaIncidents,
  saveMapConfig,
  openMapConfigPanel, closeMapConfigPanel, admUploadMapImage,
  admRemoveMapImage, _cancelEventPin,
  _deleteNode, _shareNodeTak,
  applyMapRoleUiGuards,
  _toggleLayer, _closeLayerPanel, toggleTakFilter,
  setCopStream,
  applyEventTaxonomy,
  setExerciseMode,
} from './map.js';

const API_BASE = location.origin;
const POLL_INTERVAL = 5000;

// ── COP 即時同步（issue #29 PR-E）──────────────────────────────────────────
// 與既有 map_config 圖層疊加；operator 用 /api/cop/* 建立/拖/刪，WS 廣播即時同步。
let _copStream = null;
// #267：目前 active 演習型別（'ttx'|'real'|null）。供 roster 判「有無 active 演習」以標常駐候選
// （演習中 NULL 單位＝常駐；無 active 演習則所有單位都常駐、不標）。隨 setExerciseMode 同步更新。
let _activeExType = null;
let _activeExId = null;  // #267：active 演習 id（roster 納編/退編 endpoint 用）

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
    // #267 常駐層疊看：指揮層（sysadmin/commander）的連線帶 standing → 演習中也收 NULL 常駐 entity
    // （後端再 gate 一次）。可見性由地圖圖層 toggle 控（預設關），訂閱恆開、不重連。
    includeStanding: canUseRealModeControls(),
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
    stopRoster();  // #269：清掉 roster 週期重繪 timer（防 logout 後殘留）
  } else if (type === 'login' || type === 'unlock') {
    _initCopStream();
  }
});
// 分頁關閉 / reload 前 cleanup
window.addEventListener('beforeunload', () => {
  if (_copStream) _copStream.stop();
});

// P1-14：切換 active 演習後，前端要依新 scope 重抓資料（否則 map 圖釘 / 面板停在舊場，
// 要 hard reload 才更新）。poll() 重抓 dashboard。
// #265：原本走 stop()+connect()，但 stop() 同步清空整個 entity 快取 + 關 socket，connect()
// 立刻開新 socket → (a) 清快取到 resync 回來的空窗地圖空白；(b) 舊 socket onclose 遲到時
// _stopped 已 false → 雙 socket race、誤排重連 → 新場 entity 可能永不 render。
// 改為「就地對帳」：server 端已在切換時把本連線就地 rescope 到新 active（cop_hub.rescope_active），
// 故這裡只需 (1) resync() 換掉舊場 entity、補新場（不清空、不關 socket）；(2) connect() 兜底——
// socket 已連線則 idempotent early-return，僅在 socket 已死時才重連。
function _refreshAfterExerciseSwitch() {
  try { poll(); } catch (e) { /* poll 失敗不阻斷 */ }
  if (_copStream) { _copStream.resync(); _copStream.connect(); }
}

// P1-14：他人 activate/archive 演習 → server broadcast_all → cop_stream 轉發 'exercise:switched'。
// 本 session 重新依新 scope 對帳（map/面板）+ 更新 header chip（顯示新的當前場 / 無場次）。
document.addEventListener('exercise:switched', () => {
  _refreshAfterExerciseSwitch();
  import('./exercises.js').then(async m => {
    // 演習面板開著（正在看演習清單）→ 重渲染清單（含 chip），讓刪除/狀態變更即時反映；
    // 否則只更新 header chip。#346 後清單搬到 #exercise-overlay（openExercisePanel 會 closeSettings），
    // 故須看 exercise-overlay；保留 settings-overlay 判斷以防其他殘留入口。
    const panelOpen = document.getElementById('exercise-overlay')?.classList.contains('show')
      || document.getElementById('settings-overlay')?.classList.contains('show');
    if (panelOpen) await m.renderExercisePanel(); else await m.initExerciseChip();
    _activeExType = m.activeExerciseType();
    _activeExId = m.activeExerciseId();
    setExerciseMode(_activeExType);  // #258 β-1：橋接新模式 → map.js 編輯閘（TTX 才放行外部來源）
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
    // P3（#348-F5）：密碼欄顯示/隱藏切換。委派涵蓋靜態登入框 + 動態首登 overlay。
    // 共用大螢幕有肩窺風險 → 預設遮蔽、點擊才顯（opt-in）。
    case 'pwToggle': {
      const inp = document.getElementById(id);
      if (inp) {
        const hidden = inp.type === 'password';
        inp.type = hidden ? 'text' : 'password';
        btn.textContent = hidden ? '🙈' : '👁';
      }
      break;
    }
    case 'sessionContinue': continueSessionFromWarning(); break;
    case 'sessionLogout':  logoutFromSessionWarning(); break;
    case 'openSettings':   openSettings(); break;   // #346：演習管理移至演習面板（admExerciseSub('manage') 時 render）
    case 'closeSettings':  closeSettings(); break;
    case 'exportJSON': {
      import('./cop.js').then(m => exportDashboardJSON(m.getData()));
      break;
    }
    case 'showAuditLog':   showAuditLog(); break;
    case 'downloadSBOM':   downloadSBOM(); break;   // #419 SBOM 下載（READ_ROLES）
    // #334：AAR 回放——同分頁導航（token 存 sessionStorage，新分頁拿不到登入態；aar.html 有「← 返回指揮台」）。
    case 'openAar':        window.location.href = '/static/aar.html'; break;
    case 'openAdminPanel': openAdminPanel(); break;
    case 'openExercisePanel':  import('./auth.js').then(m => m.openExercisePanel()); break;   // #346 演習面板
    case 'closeExercisePanel': import('./auth.js').then(m => m.closeExercisePanel()); break;
    case 'admExerciseSub':     import('./auth.js').then(m => m.admExerciseSub(btn.dataset.sub)); break;
    case 'openTaxonomyEditor': openTaxonomyEditor(); break;   // #66 PR-C1
    case 'taxSave':        _handleTaxSave(); break;
    case 'closeAdminPanel': closeAdminPanel(); break;
    case 'admShowTab':     admShowTab(btn.dataset.tab); break;
    case 'admAccountSub':  import('./auth.js').then(m => m.admAccountSub(btn.dataset.sub)); break;  // #346
    // P1-12b（#228）整包備份 / 還原（admin 系統 tab）
    case 'admBackupNow':      admBackupNow(); break;
    case 'admRefreshBackups': admRefreshBackups(); break;
    case 'admToggleDetail':   admToggleDetail(btn.dataset.name, btn.dataset.idx); break;
    case 'admToggleBackupList': admToggleBackupList(); break;
    case 'admDownloadBackup': admDownloadBackup(btn.dataset.name); break;
    case 'admRestoreFromList': admRestoreFromList(btn.dataset.name); break;
    case 'admRestore':        admRestore(); break;
    case 'adm-toggle-tak': import('./auth.js').then(m => m.admToggleTak()); break;
    case 'adm-issue-tak-device': import('./auth.js').then(m => m.admIssueTakDevice()); break;
    case 'adm-revoke-tak-device': import('./auth.js').then(m => m.admRevokeTakDevice(btn.dataset.certId)); break;
    case 'adm-delete-tak-device': import('./auth.js').then(m => m.admDeleteTakDevice(btn.dataset.certId)); break;
    case 'adm-reconcile-tak-refresh': import('./auth.js').then(m => m.admLoadTakDeviceCerts()); break;  // #401：重整 TAK 帳號清單
    case 'adm-backfill-tak-revocations': import('./auth.js').then(m => m.admBackfillTakRevocations()); break;  // #318 Slice 3：撤銷補登 TAK
    case 'adm-revoke-by-fingerprint': import('./auth.js').then(m => m.admRevokeByFingerprint()); break;  // #318 Slice 3 part③：按 fingerprint 撤盤點外證
    case 'adm-issue-vpn': import('./auth.js').then(m => m.admIssueVpn(btn.dataset.username)); break;  // VPN-gate：帳號發 WG VPN 設定（label=username）
    case 'adm-revoke-wg-peer': import('./auth.js').then(m => m.admRevokeWgPeer(btn.dataset.callsign)); break;  // 撤 WG-only peer
    case 'adm-deregister-tak-user': import('./auth.js').then(m => m.admDeregisterTakUser(btn.dataset.callsign)); break;  // #401：移除 TAK 殭屍
    case 'adm-strip-anon-tak-user': import('./auth.js').then(m => m.admStripAnonTakUser(btn.dataset.callsign)); break;  // #404：移出 __ANON__ 隔離破口
    case 'adm-download-rootca': import('./auth.js').then(m => m.admDownloadRootCa()); break;
    case 'adm-faction-classify': import('./auth.js').then(m => m.admClassifyFaction(btn.dataset.clientKey, btn.dataset.faction, btn.dataset.callsign)); break;
    case 'adm-faction-override': import('./auth.js').then(m => m.admOverrideFaction()); break;
    case 'unlockPinLock':  unlockPinLock(); break;
    case 'adm-toggle-edit': import('./auth.js').then(m => m.admToggleEdit(btn.dataset.username)); break;
    case 'adm-save-edit': import('./auth.js').then(m => m.admSaveEdit(btn.dataset.username)); break;
    case 'adm-reset-pin': import('./auth.js').then(m => m.admResetPin(btn.dataset.username)); break;  // #348-F5 P2b
    case 'adm-toggle-status': import('./auth.js').then(m => m.admToggleStatus(btn.dataset.username, btn.dataset.status)); break;
    case 'adm-delete': import('./auth.js').then(m => m.admDelete(btn.dataset.username)); break;
    // #275 wave B：裝置憑證（mTLS 第二因子）綁定/撤銷
    case 'adm-toggle-certs': import('./auth.js').then(m => m.admToggleCerts(btn.dataset.username)); break;
    case 'adm-bind-cert': import('./auth.js').then(m => m.admBindCert(btn.dataset.username)); break;
    case 'adm-issue-cert': import('./auth.js').then(m => m.admIssueCert(btn.dataset.username)); break;
    case 'adm-revoke-cert': import('./auth.js').then(m => m.admRevokeCert(btn.dataset.username, btn.dataset.certId)); break;
    case 'adm-toggle-revoked': import('./auth.js').then(m => m.admToggleRevoked(btn.dataset.username)); break;
    case 'adm-purge-revoked': import('./auth.js').then(m => m.admPurgeRevoked(btn.dataset.username)); break;
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
    case 'changeMarkerAffiliation': {  // #257 α-3：點即換敵我態
      if (!canAccessMapObjects()) break;
      _changeMarkerAffiliation(btn.dataset.id, btn.dataset.aff);
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
    // #257 α-2：逐頂點 reshape。詳情鈕進入（先關 modal 再進編輯模式）；banner ✓/✕ 收尾。
    case 'editShapeVertices': {
      if (!canAccessMapObjects()) break;
      closeModal();
      _startVertexEdit(id);
      break;
    }
    case 'finishVertexEdit': {
      if (!canAccessMapObjects()) break;
      _finishVertexEdit();
      break;
    }
    case 'cancelVertexEdit': _cancelVertexEdit(); break;
    case 'saveWaypointName': {  // #260 D2：命名 modal 儲存（id=頂點 index）
      if (!canAccessMapObjects()) break;
      _saveWaypointName(id);
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
    case 'shareNodeTak': {
      if (!canUseRealModeControls()) break;  // #467：節點廣播=指揮層
      _shareNodeTak(id);
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
    // ── 右欄頂層 tab（事件追蹤 ｜ 通聯 ｜ 隊伍 ｜ 待裁示，#269）+ 通聯 room 過濾（#213 b1）──
    // 切到事件才重算列表高（顯示後才量得到 clientHeight；補隱藏期間 window resize 的殘留）。
    // #269：原 `!== 'chat'` 在四 tab 下會對 roster/decisions 也誤觸 → 收緊成 `=== 'events'`。
    case 'switchRightTab':  switchRightTab(btn.dataset.rtab); if (btn.dataset.rtab === 'events') _resizeEvtList(); break;
    case 'chatFilterRoom':  chatFilterRoom(btn.dataset.room); break;
    case 'chatClearSender': chatClearSender(); break;  // #213 b3-1：清除 by-sender 過濾
    case 'chatSend':        chatSend(); break;  // #216：出向 GeoChat compose 送出

    // ── 演習管理（P1-14 PR-2）。#346：openExercisePanel 改開「演習」面板（見上方 case；header chip
    //    與設定入口共用），不再開 settings 捲到區段。 ──
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

// ── 長按事件卡片 + 通聯訊息（mousedown / touchstart）──
// #213 b3-2：通聯訊息列（.chat-row）長按 = 定位發訊單位，與事件卡長按同一套 pointer 機制。
function _longpressDown(e) {
  const card = e.target.closest('[data-longpress-id]');
  if (card) { _evtCardDown(card.dataset.longpressId); return; }
  const row = e.target.closest('.chat-row');
  if (row?.dataset.senderUid) chatRowDown(row.dataset);
}
function _longpressUp() { _evtCardUp(); chatRowUp(); }
document.addEventListener('mousedown', _longpressDown);
document.addEventListener('mouseup', _longpressUp);
document.addEventListener('mouseleave', _longpressUp);
document.addEventListener('touchstart', _longpressDown, { passive: true });
document.addEventListener('touchend', _longpressUp);

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
    const { cmd_version, build } = await resp.json();
    if (cmd_version) {
      document.body.dataset.cmdVersion = cmd_version;
      document.title = 'ICS 指揮部 ' + cmd_version;
      // build 戳記（git sha + dirty + 時間）一併顯示 → 可辨識實際跑的 build；無則顯示 'dev'
      const verText = 'cmd-' + cmd_version + '  ·  build ' + (build || 'dev');
      document.querySelectorAll('.h-ver').forEach(el => {
        el.textContent = verText;
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
      import('./exercises.js').then(async m => {
        await m.initExerciseChip();
        _activeExType = m.activeExerciseType();
        _activeExId = m.activeExerciseId();
        setExerciseMode(_activeExType);  // #258 β-1：初始模式 → map.js 編輯閘
      });
      startSessionStatusPolling();
      setPollActive(true);
      poll();
      refreshChatNow();  // #250：通聯與事件同步在登入後立即載入（否則通聯要等 30s interval 才填）
      setInterval(() => poll(), POLL_INTERVAL);
      // COP 即時同步：地圖就緒後連 WS（issue #29 PR-E）
      _initCopStream();
      // #269：右欄「隊伍」名冊（讀 cop_stream TAK 單位、按 team_color 分組；純前端 v1）
      initRoster({
        getTakUnits: () => (_copStream ? _copStream.getEntitiesBySource('tak') : []),
        // #267：有 active 演習時，NULL 單位＝常駐候選 → 標記；無 active 演習則不標（皆常駐、無對照）。
        getHasActiveExercise: () => _activeExType != null,
        // #267 納編/退編：把單位移進 active 場 / 退回 NULL。後端雙廣播 → cop_stream 就地過渡、roster 重繪。
        onEnroll: async (uid, action) => {
          if (_activeExId == null) return;
          await authFetch(`${API_BASE}/api/exercises/${_activeExId}/enroll`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ uid, action }),
          });
        },
        // #267 純乙可用性：「加入全部連線」一鍵把在線 client（CN）全納入 active 場 roster（後端重 stamp + resync）。
        onAddConnected: async () => {
          if (_activeExId == null) return;
          await authFetch(`${API_BASE}/api/admin/exercises/${_activeExId}/roster/add-connected`, { method: 'POST' });
        },
      });
      // #269：還原 per-session 記憶的右欄 tab（TAK 狀態套用後；記憶為 TAK 頁但已停用則退回事件）
      restoreRightTab();

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
