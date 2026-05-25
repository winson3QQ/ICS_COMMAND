/**
 * charts.js — 圖表繪製模組（C1-F CSP 模組化，v3.0.0）
 *
 * 職責：
 *   - 完整包含 chart_utils.js 的所有功能（chart_utils.js 將被廢除）
 *   - 提供 drawSparkline、ipiCalc、_autoGridSteps
 *   - 提供算法工具：slope、rate、minsUntil
 *   - 提供 renderSparklines、setRag、expandSpark、setPill、showSlice
 *
 * 不可 import 任何其他模組（含 auth.js / ws.js）
 * 資料由外部呼叫 setDashData() 注入，解耦資料層與圖表層
 */

// charts.js 不 import 任何其他模組（規格要求）

// ══════════════════════════════════════════════════════════════
// 時間格式化（本地時間，與 auth.js 的 fmtLocalDT 同邏輯，
// 因 charts.js 不可 import 其他模組，故自備）
// ══════════════════════════════════════════════════════════════
function _fmtLocalDT(isoStr) {
  if (!isoStr) return '';
  const d = new Date(isoStr.endsWith('Z') ? isoStr : isoStr + 'Z');
  const p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

// ══════════════════════════════════════════════════════════════
// ── 從 chart_utils.js 完整移植 ────────────────────────────────
// ══════════════════════════════════════════════════════════════

// 全域共用 tooltip div
let _chartTooltip = null;
function _getTooltip() {
  if (!_chartTooltip) {
    _chartTooltip = document.createElement('div');
    _chartTooltip.style.cssText =
      'position:fixed;pointer-events:none;z-index:9999;display:none;' +
      'background:rgba(13,17,23,.96);border:1px solid rgba(48,54,61,.8);border-radius:6px;' +
      'padding:7px 11px;font:11px IBM Plex Mono,monospace;color:#e6edf3;' +
      'box-shadow:0 4px 16px rgba(0,0,0,.6);white-space:nowrap;line-height:1.8;';
    document.body.appendChild(_chartTooltip);
  }
  return _chartTooltip;
}

// 各 canvas 的 hover listener 參照（避免重複掛載）
const _hoverState = {};

/**
 * drawSparkline(canvasId, datasets, opts, N, expanded, times)
 *
 * @param {string}  canvasId   - canvas 元素 ID
 * @param {Array}   datasets   - [{data, color, w, fill, fillColor, dash, unit, label,
 *                                 extraData, extraUnit}]
 * @param {Object}  opts       - {yMin, yMax, yUnit, noEndLabel, hover,
 *                                thresholds, stackedArea, gridSteps, hoverExtra,
 *                                rightAxis:{max, unit, label}}
 * @param {number}  N          - 資料點數
 * @param {boolean} expanded   - 是否展開模式
 * @param {Array}   times      - X 軸時間標籤
 */
export function drawSparkline(canvasId, datasets, opts, N, expanded, times) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const par = canvas.parentElement;
  const W = par.clientWidth, H = par.clientHeight;
  if (!W || !H) return;
  canvas.width = W * devicePixelRatio;
  canvas.height = H * devicePixelRatio;
  const ctx = canvas.getContext('2d');
  ctx.scale(devicePixelRatio, devicePixelRatio);

  const fs = expanded ? {axis:11, threshold:10, value:13, dot:3.5} : {axis:7, threshold:7, value:9, dot:2};
  const hasRightAxis = expanded && opts.rightAxis;
  // hasRightAxis 時右側需容納兩個 zone：末端標籤（~36px）+ 右軸數字（~30px）
  const pad = expanded ? {t:8, r: hasRightAxis ? 72 : 44, b:24, l:40} : {t:2, r:4, b:2, l:4};
  const cW = W-pad.l-pad.r, cH = H-pad.t-pad.b;
  const yMin = opts.yMin||0, yMax = opts.yMax||100, yR = yMax-yMin||1;
  function px(i){ return pad.l + (i/Math.max(N-1,1)) * cW; }
  function py(v){ return pad.t + cH - ((v-yMin)/yR)*cH; }

  // 展開：左側 Y 軸 grid
  if (expanded) {
    const steps = opts.gridSteps || _autoGridSteps(yMin, yMax);
    ctx.strokeStyle='rgba(110,118,129,.2)'; ctx.lineWidth=0.5;
    ctx.fillStyle='#8b949e'; ctx.font=fs.axis+'px IBM Plex Mono,monospace'; ctx.textAlign='right';
    steps.forEach(v => {
      if (v < yMin || v > yMax) return;
      const y = py(v);
      ctx.beginPath(); ctx.moveTo(pad.l,y); ctx.lineTo(pad.l+cW,y); ctx.stroke();
      ctx.fillText(v + (opts.yUnit||''), pad.l-4, y+4);
    });
  }

  // 右側 Y 軸（zone 2：raX = cW+40 起，zone 1：cW+4~36 留給末端標籤）
  if (hasRightAxis) {
    const ra = opts.rightAxis;
    const raMax = ra.max || 100;
    const raSteps = _autoGridSteps(0, raMax);
    const raX = pad.l + cW + 40;
    ctx.fillStyle='#8b949e'; ctx.font=fs.axis+'px IBM Plex Mono,monospace'; ctx.textAlign='left';
    raSteps.forEach(v => {
      const leftV = (v / raMax) * (yMax - yMin) + yMin;
      const y = py(leftV);
      ctx.fillText(v + (ra.unit||''), raX, y + 4);
    });
    if (ra.label) {
      ctx.fillStyle='#6e7681'; ctx.font=(fs.axis-1)+'px IBM Plex Mono,monospace';
      ctx.fillText(ra.label, raX, pad.t - 1);
    }
  }

  // 門檻線
  (opts.thresholds||[]).forEach(th => {
    ctx.strokeStyle=th.color; ctx.lineWidth=expanded?1:0.5; ctx.setLineDash([4,3]);
    const y = py(th.value);
    ctx.beginPath(); ctx.moveTo(pad.l,y); ctx.lineTo(pad.l+cW,y); ctx.stroke();
    ctx.setLineDash([]);
    if (expanded) {
      ctx.fillStyle=th.color; ctx.font=fs.threshold+'px IBM Plex Mono,monospace'; ctx.textAlign='left';
      ctx.fillText(th.label||th.value, pad.l+cW+3, y+4);
    }
  });

  // 堆疊面積
  if (opts.stackedArea) {
    const cumul = Array(N).fill(0);
    opts.stackedArea.forEach(layer => {
      const top = layer.data.map((v,i) => cumul[i] + (v||0));
      ctx.beginPath();
      for (let i=0; i<N; i++) { i===0 ? ctx.moveTo(px(i), py(top[i])) : ctx.lineTo(px(i), py(top[i])); }
      for (let i=N-1; i>=0; i--) { ctx.lineTo(px(i), py(cumul[i])); }
      ctx.closePath();
      ctx.fillStyle = layer.fillColor; ctx.fill();
      top.forEach((v,i) => cumul[i] = v);
    });
  }

  // 線條 + 資料點
  datasets.forEach(ds => {
    if (ds.fill) {
      ctx.beginPath();
      ds.data.forEach((v,i) => { i===0 ? ctx.moveTo(px(i),py(v)) : ctx.lineTo(px(i),py(v)); });
      ctx.lineTo(px(N-1),py(yMin)); ctx.lineTo(px(0),py(yMin)); ctx.closePath();
      ctx.fillStyle = ds.fillColor||'rgba(100,150,255,.1)'; ctx.fill();
    }
    ctx.strokeStyle=ds.color; ctx.lineWidth=expanded?(ds.w||1.5)*1.5:(ds.w||1.5); ctx.lineJoin='round';
    ctx.setLineDash(ds.dash||[]);
    ctx.beginPath();
    ds.data.forEach((v,i) => { i===0 ? ctx.moveTo(px(i),py(v)) : ctx.lineTo(px(i),py(v)); });
    ctx.stroke(); ctx.setLineDash([]);

    if (expanded) {
      // 資料點
      ds.data.forEach((v,i) => {
        ctx.fillStyle=ds.color; ctx.beginPath(); ctx.arc(px(i),py(v),fs.dot,0,Math.PI*2); ctx.fill();
      });
      // 末端數值標籤（opts.noEndLabel 時省略）
      if (!opts.noEndLabel) {
        const lv = ds.data[N-1];
        if (lv != null) {
          ctx.fillStyle=ds.color; ctx.font='bold '+fs.value+'px IBM Plex Mono,monospace'; ctx.textAlign='left';
          ctx.fillText(lv+(ds.unit||''), px(N-1)+6, py(lv)+4);
        }
      }
    }
  });

  // 展開：X 軸時間標籤
  if (expanded && times && times.length >= N) {
    ctx.fillStyle='#8b949e'; ctx.font=fs.axis+'px IBM Plex Mono,monospace'; ctx.textAlign='center';
    const step = N <= 15 ? 1 : N <= 30 ? 2 : 3;
    for (let i=0; i<N; i++) {
      if (i % step === 0 || i === N-1) ctx.fillText(times[i], px(i), H-3);
    }
  }

  // ── Hover 互動 ────────────────────────────────────────────────
  // 清除舊 listener（避免重複掛載）
  if (_hoverState[canvasId]) {
    const {mm, ml} = _hoverState[canvasId];
    canvas.removeEventListener('mousemove', mm);
    canvas.removeEventListener('mouseleave', ml);
    delete _hoverState[canvasId];
  }

  if (expanded && opts.hover) {
    // 靜態快照：hover 時先還原此快照再疊高亮點
    const snapshot = ctx.getImageData(0, 0, canvas.width, canvas.height);

    function restoreSnapshot() {
      ctx.save();
      ctx.setTransform(1,0,0,1,0,0); // putImageData 不受 transform 影響，需先重置
      ctx.putImageData(snapshot, 0, 0);
      ctx.restore(); // 恢復 scale(dpr, dpr)
    }

    const SNAP_R = 22; // 邏輯像素，游標距點在此半徑內才觸發
    const mm = (e) => {
      const rect = canvas.getBoundingClientRect();
      // 換算到 canvas 邏輯座標
      const logicX = (e.clientX - rect.left) * (W / rect.width);
      const logicY = (e.clientY - rect.top)  * (H / rect.height);

      // 找 2D 距離最近的資料點，距離超過 SNAP_R 則不顯示
      let nearest = -1, minD2 = SNAP_R * SNAP_R;
      for (let i = 0; i < N; i++) {
        const dx = px(i) - logicX;
        for (const ds of datasets) {
          const v = ds.data[i];
          if (v == null) continue;
          const dy = py(v) - logicY;
          const d2 = dx*dx + dy*dy;
          if (d2 < minD2) { minD2 = d2; nearest = i; }
        }
      }
      if (nearest === -1) {
        restoreSnapshot();
        _getTooltip().style.display = 'none';
        return;
      }
      // 還原靜態畫面，再疊高亮點
      restoreSnapshot();
      datasets.forEach(ds => {
        const v = ds.data[nearest];
        if (v == null) return;
        ctx.fillStyle = ds.color;
        ctx.strokeStyle = 'rgba(230,236,240,.85)';
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.arc(px(nearest), py(v), fs.dot * 2.2, 0, Math.PI*2);
        ctx.fill();
        ctx.stroke();
      });
      // 建 tooltip
      let html = '';
      if (times && times[nearest]) {
        html += `<div style="color:#8b949e;border-bottom:1px solid rgba(48,54,61,.6);` +
                `margin-bottom:5px;padding-bottom:3px">${times[nearest]}</div>`;
      }
      datasets.forEach(ds => {
        const v = ds.data[nearest];
        if (v == null) return;
        const pct = v + (ds.unit||'');
        const abs = ds.extraData ? ` / ${ds.extraData[nearest]}${ds.extraUnit||''}` : '';
        const lbl = ds.label ? ds.label + '&ensp;' : '';
        html += `<div><span style="color:${ds.color}">●</span>&ensp;${lbl}<b>${pct}${abs}</b></div>`;
      });
      // hoverExtra：只在 tooltip 顯示，不繪製線條（stackedArea 分解、補充數值等）
      (opts.hoverExtra||[]).forEach(he => {
        const v = he.data[nearest];
        if (v == null) return;
        const lbl = he.label ? he.label + '&ensp;' : '';
        html += `<div style="color:${he.color||'#8b949e'}"><span>●</span>&ensp;${lbl}<b>${v}${he.unit||''}</b></div>`;
      });
      const tip = _getTooltip();
      tip.innerHTML = html;
      tip.style.display = 'block';
      // 位置：貼游標右上，防右溢
      const tipW = tip.offsetWidth || 140;
      const tx = Math.min(e.clientX + 14, window.innerWidth - tipW - 8);
      tip.style.left = tx + 'px';
      tip.style.top  = (e.clientY - 10) + 'px';
    };

    const ml = () => {
      restoreSnapshot();
      _getTooltip().style.display = 'none';
    };

    canvas.addEventListener('mousemove', mm);
    canvas.addEventListener('mouseleave', ml);
    _hoverState[canvasId] = {mm, ml};
  }
}

/** IPI 計算（high*3 + medium） */
export function ipiCalc(h, m) { return h * 3 + m; }

export function _autoGridSteps(yMin, yMax) {
  const range = yMax - yMin;
  const step = range <= 10 ? 2 : range <= 50 ? 10 : 25;
  const steps = [];
  for (let v = yMin; v <= yMax; v += step) steps.push(v);
  return steps;
}

// ══════════════════════════════════════════════════════════════
// 算法工具
// ══════════════════════════════════════════════════════════════

export function slope(arr) {
  const n = arr.length;
  if (n < 4) return 0;
  return ((arr[n-1]+arr[n-2]+arr[n-3])/3) - ((arr[0]+arr[1]+arr[2])/3);
}

export function rate(arr) {
  const n = arr.length;
  if (n < 2) return 0;
  return (arr[n-1]-arr[0])/(n-1);
}

export function minsUntil(current, threshold, ratePerSnap, snapMins) {
  if (ratePerSnap <= 0) return null;
  const remaining = threshold - current;
  if (remaining <= 0) return 0;
  return Math.round((remaining / ratePerSnap) * snapMins);
}

// ══════════════════════════════════════════════════════════════
// Sparkline RAG 狀態燈
// ══════════════════════════════════════════════════════════════

export function setRag(cardId, lvl) {
  const card = document.getElementById(cardId);
  if (!card) return;
  card.classList.remove('bg-warn','bg-crit');
  if (lvl === 'crit') card.classList.add('bg-crit');
  else if (lvl === 'warn') card.classList.add('bg-warn');
  const rag = document.getElementById(cardId + '-rag');
  if (rag) { rag.className = 'spark-rag ' + lvl; }
}

// ══════════════════════════════════════════════════════════════
// Sparkline 放大/縮小
// ══════════════════════════════════════════════════════════════
let _expandedSpark = sessionStorage.getItem('_expandedSpark') || null;

export function expandSpark(cardId) {
  const card = document.getElementById(cardId);
  if (!card) return;
  if (_expandedSpark === cardId) {
    card.classList.remove('expanded');
    _expandedSpark = null;
    sessionStorage.removeItem('_expandedSpark');
  } else {
    if (_expandedSpark) {
      const prev = document.getElementById(_expandedSpark);
      if (prev) prev.classList.remove('expanded');
    }
    card.classList.add('expanded');
    _expandedSpark = cardId;
    sessionStorage.setItem('_expandedSpark', cardId);
  }
  // 延遲重繪（需等 cop.js 的 renderSparklines，由外部呼叫）
  // 發出自訂事件供 cop.js 監聽
  document.dispatchEvent(new CustomEvent('charts:expandSparkChanged', { detail: { cardId } }));
}

export function getExpandedSpark() { return _expandedSpark; }

// ══════════════════════════════════════════════════════════════
// setPill（倒數時間膠囊）
// ══════════════════════════════════════════════════════════════

export function setPill(pillId, timeId, mins, warnThresh, okThresh) {
  const pill = document.getElementById(pillId);
  const timeEl = document.getElementById(timeId);
  if (!pill || !timeEl) return;
  if (mins == null) {
    timeEl.textContent = 'OK';
    timeEl.className = 'zc-time ok';
    pill.className = 'zc-pill';
    return;
  }
  const lvl = mins < warnThresh ? 'crit' : mins < okThresh ? 'warn' : 'ok';
  const text = mins < 60 ? mins + 'm' : Math.floor(mins/60) + 'h+';
  timeEl.textContent = text;
  timeEl.className = 'zc-time ' + lvl;
  pill.className = 'zc-pill ' + (lvl === 'ok' ? '' : lvl);
}

// ══════════════════════════════════════════════════════════════
// renderSparklines（從 cop.js 傳入 series 資料）
// ══════════════════════════════════════════════════════════════

/**
 * @param {Object} d  - getSeries() 回傳的 series 物件
 * @param {string} sessionType - 'real' | 'exercise'
 * @param {Function} openZoneByType - cop.js 傳入的鑽探函式
 * @param {Function} showIpiBreakdown - cop.js 傳入的 IPI 分解函式
 */
export function renderSparklines(d, sessionType, openZoneByType, showIpiBreakdown) {
  const N = d.N;
  const isExp = id => _expandedSpark === id;
  const t = d.times; // X 軸時間陣列

  // ① 量能
  const sp = d.sPct[N-1], mp = d.mPct[N-1];
  const capMax = Math.max(sp, mp);
  const capLvl = capMax >= 90 ? 'crit' : capMax >= 70 ? 'warn' : 'ok';
  const capEl = document.getElementById('sp-cap-v');
  if (capEl) {
    capEl.textContent = '收' + sp + '% 醫' + mp + '%';
    if (sessionType === 'real') {
      capEl.style.cursor = 'pointer';
      capEl.dataset.action = 'openZone';
      capEl.dataset.type = 'shelter';
    } else {
      capEl.style.cursor = 'default';
      capEl.removeAttribute('data-action');
    }
  }
  setRag('sp-cap', capLvl);
  const sBt = d.sBt[N-1]||50, mBt = d.mBt[N-1]||30;
  drawSparkline('sc1', [
    {data:d.sPct, color:'#E67E22', w:1.5, fill:true, fillColor:'rgba(230,126,34,.15)', unit:'%', label:'收容', extraData:d.sBu, extraUnit:'人'},
    {data:d.mPct, color:'#C0392B', w:1.5, fill:true, fillColor:'rgba(192,57,43,.12)', unit:'%', label:'醫療', extraData:d.mBu, extraUnit:'人'},
  ], {yMin:0, yMax:100, yUnit:'%', noEndLabel:true, hover:true,
    thresholds:[{value:70,color:'rgba(212,144,0,.4)',label:'70%'},{value:90,color:'rgba(204,42,42,.4)',label:'90%'}],
    rightAxis:{max:Math.max(sBt,mBt), unit:'人', label:'人'}}, N, isExp('sp-cap'), t);

  // ② 流向（傷患來源分布）
  const sa=d.srcA[N-1], sb=d.srcB[N-1], sc=d.srcC[N-1];
  const srcTotal = sa + sb + sc;
  const flowLvl = srcTotal >= 10 ? 'crit' : srcTotal >= 5 ? 'warn' : 'ok';
  const flowText = srcTotal > 0
    ? `在站${srcTotal}人` + (sa?` 前${sa}`:'') + (sb?` 收${sb}`:'') + (sc?` 自${sc}`:'')
    : '無在站傷患';
  const flowEl = document.getElementById('sp-flow-v');
  if (flowEl) {
    flowEl.textContent = flowText;
    if (sessionType === 'real') {
      flowEl.style.cursor = 'pointer';
      flowEl.dataset.action = 'openZone';
      flowEl.dataset.type = 'medical';
    } else {
      flowEl.style.cursor = 'default';
      flowEl.removeAttribute('data-action');
    }
  }
  setRag('sp-flow', flowLvl);
  const srcMax = Math.max(...d.srcA.map((a,i)=>a+(d.srcB[i]||0)+(d.srcC[i]||0)), 5);
  drawSparkline('sc2', [
    {data:d.srcA, color:'#E74C3C', w:1.5, unit:'人', label:'前進'},
    {data:d.srcB, color:'#F0883E', w:1.5, unit:'人', label:'收容'},
    {data:d.srcC, color:'#2E7D32', w:1.5, unit:'人', label:'自行'},
  ], {yMin:0, yMax:srcMax+2, yUnit:'人', noEndLabel:true, hover:true}, N, isExp('sp-flow'), t);

  // ③ 事件
  const totalIPI = ipiCalc(d.incHigh[N-1], d.incMed[N-1]);
  const incLvl = totalIPI >= 6 ? 'crit' : totalIPI >= 3 ? 'warn' : 'ok';
  const incEl = document.getElementById('sp-inc-v');
  if (incEl) {
    incEl.textContent = '未結' + d.incOpen[N-1] + ' IPI:' + totalIPI;
    if (sessionType === 'real') {
      incEl.style.cursor = 'pointer';
      incEl.dataset.action = 'showIpiBreakdown';
    } else {
      incEl.style.cursor = 'default';
      incEl.removeAttribute('data-action');
    }
  }
  setRag('sp-inc', incLvl);
  const incMax = Math.max(...d.incOpen, ...d.incHigh.map((h,i)=>h+(d.incMed[i]||0)+(d.incLow[i]||0)), 3);
  drawSparkline('sc3', [
    {data:d.incOpen, color:'#e6edf3', w:1.5, unit:'件', label:'未結'},
  ], {yMin:0, yMax:incMax+1, yUnit:'件', noEndLabel:true, hover:true,
    stackedArea:[
      {data:d.incLow,  fillColor:'rgba(26,158,82,.2)'},
      {data:d.incMed,  fillColor:'rgba(212,144,0,.25)'},
      {data:d.incHigh, fillColor:'rgba(204,42,42,.3)'},
    ],
    hoverExtra:[
      {data:d.incHigh, color:'rgba(204,100,100,.9)', label:'高', unit:'件'},
      {data:d.incMed,  color:'rgba(212,170,0,.9)',   label:'中', unit:'件'},
      {data:d.incLow,  color:'rgba(26,158,82,.9)',   label:'低', unit:'件'},
    ]
  }, N, isExp('sp-inc'), t);

  // ④ 資源
  const ivLast = d.ivPct[N-1], oxLast = d.oxPct[N-1], toL = d.toPct[N-1];
  const minSupply = Math.min(ivLast, oxLast, toL);
  const resLvl = minSupply <= 20 ? 'crit' : minSupply <= 40 ? 'warn' : 'ok';
  const resEl = document.getElementById('sp-res-v');
  if (resEl) resEl.textContent = 'IV:' + ivLast + '% O₂:' + oxLast + '%';
  setRag('sp-res', resLvl);
  drawSparkline('sc4', [
    {data:d.ivPct,  color:'#D32F2F', w:1.5, dash:[3,2], unit:'%', label:'IV'},
    {data:d.oxPct,  color:'#F57F17', w:1.5, dash:[3,2], unit:'%', label:'O₂'},
    {data:d.toPct,  color:'#388E3C', w:1.5, dash:[3,2], unit:'%', label:'止血帶'},
  ], {yMin:0, yMax:100, yUnit:'%', noEndLabel:true, hover:true,
    thresholds:[{value:20,color:'rgba(204,42,42,.4)',label:'20%'}]}, N, isExp('sp-res'), t);

  // ⑤ 人力
  const sR = d.opsS[N-1], mR = d.opsM[N-1];
  const staffLvl = Math.max(sR, mR) > 8 ? 'crit' : Math.max(sR, mR) > 5 ? 'warn' : 'ok';
  const staffEl = document.getElementById('sp-staff-v');
  if (staffEl) staffEl.textContent = '收' + sR.toFixed(1) + ' 醫' + mR.toFixed(1);
  setRag('sp-staff', staffLvl);
  drawSparkline('sc5', [
    {data:d.opsS, color:'#1976D2', w:1.5, unit:'', label:'收容'},
    {data:d.opsM, color:'#7B1FA2', w:1.5, unit:'', label:'醫療'},
  ], {yMin:0, yMax:Math.max(...d.opsS, ...d.opsM, 8)+2, noEndLabel:true, hover:true,
    thresholds:[{value:8,color:'rgba(204,42,42,.4)',label:'8'}]}, N, isExp('sp-staff'), t);
}

// ══════════════════════════════════════════════════════════════
// Series 資料層（從 commander_dashboard.html 搬移）
// ══════════════════════════════════════════════════════════════

/** Sparkline 可視區間大小（顯示最近 N 筆） */
const VIEW_SIZE = 24;

/** 目前的 dashboard 資料快取（由外部呼叫 setDashData 更新） */
let _dashData = null;

/** 視窗右端索引（-1 代表對齊最新） */
let _viewEnd = -1;

/**
 * 更新 dashboard 資料並觸發 series 快取失效。
 * 由 cop.js 在 onData 收到資料後呼叫。
 */
export function setDashData(data) {
  _dashData = data;
}

/** 取得目前的 dashboard 原始資料 */
export function getDashData() {
  return _dashData;
}

/** 取得/設定視窗右端位置（供 sparkline 滾動控制用） */
export function getViewEnd() { return _viewEnd; }
export function setViewEnd(v) { _viewEnd = v; }

/**
 * 從 API 回傳的 dashboard 資料萃取 series 物件。
 * @param {Object} data - API /api/dashboard 回傳的完整 JSON
 * @returns {Object|null} series 物件，或 null（無快照時）
 */
export function getApiSeries(data) {
  const sh = data.shelter_history || [];
  const mh = data.medical_history || [];
  if (sh.length < 1 && mh.length < 1) return null;
  const N = Math.max(sh.length, mh.length);
  const pad = (arr, n) => { while(arr.length < n) arr.unshift(arr[0]||{}); return arr.slice(-n); };
  const s = pad([...sh].reverse(), N);
  const m = pad([...mh].reverse(), N);
  const ex = (snap) => (typeof snap.extra === 'string' ? JSON.parse(snap.extra) : snap.extra) || {};
  const times = s.map(snap => {
    const t = snap.snapshot_time || snap.t || '';
    return t ? _fmtLocalDT(t).slice(11, 16) : '?';
  });

  return {
    times, N,
    sPct: s.map(snap => snap.bed_total ? Math.round((snap.bed_used||0)/snap.bed_total*100) : 0),
    mPct: m.map(snap => snap.bed_total ? Math.round((snap.bed_used||0)/snap.bed_total*100) : 0),
    sBu: s.map(snap => snap.bed_used||0), sBt: s.map(snap => snap.bed_total||0),
    mBu: m.map(snap => snap.bed_used||0), mBt: m.map(snap => snap.bed_total||0),
    srcA: m.map(snap => ex(snap).src_a || 0),
    srcB: m.map(snap => ex(snap).src_b || 0),
    srcC: m.map(snap => ex(snap).src_c || 0),
    srtR: s.map(snap => { const srt = snap.srt || ex(snap).srt || {}; return srt.red || 0; }),
    srtY: s.map(snap => { const srt = snap.srt || ex(snap).srt || {}; return srt.yellow || 0; }),
    srtG: s.map(snap => { const srt = snap.srt || ex(snap).srt || {}; return srt.green || 0; }),
    medCR: m.map(snap => (snap.casualties_red ?? (snap.casualties||{}).red) || 0),
    medCY: m.map(snap => (snap.casualties_yellow ?? (snap.casualties||{}).yellow) || 0),
    medCG: m.map(snap => (snap.casualties_green ?? (snap.casualties||{}).green) || 0),
    medCB: m.map(snap => (snap.casualties_black ?? (snap.casualties||{}).black) || 0),
    pendS: s.map(snap => snap.pending_intake || 0),
    pendEvac: m.map(snap => snap.pending_evac || 0),
    incHigh: s.map((snap,i) => (ex(snap).incident_pressure?.high||0) + (ex(m[i]).incident_pressure?.high||0)),
    incMed:  s.map((snap,i) => (ex(snap).incident_pressure?.medium||0) + (ex(m[i]).incident_pressure?.medium||0)),
    incLow:  s.map((snap,i) => (ex(snap).incident_pressure?.low||0) + (ex(m[i]).incident_pressure?.low||0)),
    incOpen: s.map((snap,i) => (ex(snap).incident_pressure?.open_total||0) + (ex(m[i]).incident_pressure?.open_total||0)),
    incRes:  s.map((snap,i) => (ex(snap).incident_pressure?.resolved_30min||0) + (ex(m[i]).incident_pressure?.resolved_30min||0)),
    opsS: s.map(snap => ex(snap).staff_ratio || 0),
    opsM: m.map(snap => ex(snap).staff_ratio || 0),
    ivPct:  m.map(snap => { const sup=ex(snap).supplies||{}, mx=ex(snap).supplies_max||{}; return mx.iv ? Math.round((sup.iv||0)/mx.iv*100) : 100; }),
    oxPct:  m.map(snap => { const sup=ex(snap).supplies||{}, mx=ex(snap).supplies_max||{}; return mx.oxygen ? Math.round((sup.oxygen||0)/mx.oxygen*100) : 100; }),
    toPct:  m.map(snap => { const sup=ex(snap).supplies||{}, mx=ex(snap).supplies_max||{}; return mx.tourniquet ? Math.round((sup.tourniquet||0)/mx.tourniquet*100) : 100; }),
    rawS: s, rawM: m,
    rawIncS: s.map(snap => { const ip=ex(snap).incident_pressure||{}; return {h:ip.high||0,m:ip.medium||0,l:ip.low||0,open:ip.open_total||0,res:ip.resolved_30min||0}; }),
    rawIncM: m.map(snap => { const ip=ex(snap).incident_pressure||{}; return {h:ip.high||0,m:ip.medium||0,l:ip.low||0,open:ip.open_total||0,res:ip.resolved_30min||0}; }),
  };
}

/**
 * 將 series 切片到指定範圍
 * @param {Object} d - 完整 series 物件
 * @param {number} s - 起始索引（含）
 * @param {number} e - 結束索引（不含）
 */
export function sliceSeries(d, s, e) {
  const sl = arr => (arr||[]).slice(s, e);
  return {
    ...d, N: e - s, _totalN: d.N, _start: s, _end: e,
    times: sl(d.times),
    sPct: sl(d.sPct), mPct: sl(d.mPct),
    sBu: sl(d.sBu), sBt: sl(d.sBt), mBu: sl(d.mBu), mBt: sl(d.mBt),
    srcA: sl(d.srcA), srcB: sl(d.srcB), srcC: sl(d.srcC),
    srtR: sl(d.srtR), srtY: sl(d.srtY), srtG: sl(d.srtG),
    medCR: sl(d.medCR), medCY: sl(d.medCY), medCG: sl(d.medCG), medCB: sl(d.medCB),
    pendS: sl(d.pendS), pendEvac: sl(d.pendEvac),
    incHigh: sl(d.incHigh), incMed: sl(d.incMed), incLow: sl(d.incLow),
    incOpen: sl(d.incOpen), incRes: sl(d.incRes),
    opsS: sl(d.opsS), opsM: sl(d.opsM),
    ivPct: sl(d.ivPct), oxPct: sl(d.oxPct), toPct: sl(d.toPct),
    rawS: sl(d.rawS), rawM: sl(d.rawM),
    rawIncS: sl(d.rawIncS), rawIncM: sl(d.rawIncM),
  };
}

/**
 * 取目前可視區間的 series 物件（考慮 VIEW_SIZE 視窗）。
 * 使用模組內快取的 _dashData，需先呼叫 setDashData()。
 * @returns {Object|null}
 */
export function getSeries() {
  if (!_dashData) return null;
  const full = getApiSeries(_dashData);
  if (!full) return null;
  const total = full.N;
  if (total <= VIEW_SIZE) { _viewEnd = -1; return full; }
  const end = _viewEnd < 0 ? total : Math.min(_viewEnd, total);
  const start = Math.max(0, end - VIEW_SIZE);
  return sliceSeries(full, start, end);
}

// ══════════════════════════════════════════════════════════════
// showSlice（切面 Modal 內容建構）
// ══════════════════════════════════════════════════════════════

/**
 * @param {number} idx - 時間點索引
 * @param {Object} d   - 完整 series 物件
 */
export function buildSliceHtml(idx, d) {
  const t = d.times[idx];
  const sp = d.sPct[idx], mp = d.mPct[idx];
  const slc = v => v>=90?'var(--red)':v>=70?'var(--yellow)':'var(--green)';

  const sa=d.srcA[idx], sb=d.srcB[idx], sc=d.srcC[idx];
  const si = d.rawIncS[idx]||{h:0,m:0,l:0,open:0,res:0};
  const mi = d.rawIncM[idx]||{h:0,m:0,l:0,open:0,res:0};
  const iv = d.ivPct[idx], ox = d.oxPct[idx], to = d.toPct[idx];

  const supBar = (label, pct) => {
    const c = pct<=20?'var(--red)':pct<=40?'var(--yellow)':'var(--green)';
    return `<div class="slice-row"><span class="sk">${label}</span><span class="sv" style="color:${c}">${pct}%</span></div><div class="slice-bar"><div class="slice-fill" style="width:${pct}%;background:${c}"></div></div>`;
  };

  function segBar(segments, total) {
    if (!total) return '';
    let h = '<div style="display:flex;height:22px;border-radius:4px;overflow:hidden;background:var(--border);margin:6px 0 2px;">';
    segments.forEach(seg => {
      if (seg.count <= 0) return;
      const w = (seg.count / total * 100).toFixed(1);
      h += `<div style="width:${w}%;background:${seg.color};display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:700;color:#fff;min-width:18px;">${seg.count}</div>`;
    });
    h += '</div><div style="display:flex;gap:8px;margin-top:2px;">';
    segments.forEach(seg => {
      h += `<div style="font-size:9px;color:var(--text3)"><span style="color:${seg.color}">■</span> ${seg.label}${seg.count}</div>`;
    });
    h += '</div>';
    return h;
  }

  let html = `<div class="slice-time">${t} 時間點完整切面</div>`;
  html += '<div class="slice-grid">';

  // 收容組
  html += '<div class="slice-card"><div class="slice-card-title">收容組</div>';
  html += `<div style="display:flex;align-items:baseline;gap:6px;margin:4px 0 2px;">`;
  html += `<span style="font-size:28px;font-weight:800;">${d.sBu[idx]}</span>`;
  html += `<span style="font-size:12px;color:var(--text3)">/ ${d.sBt[idx]}人</span>`;
  html += `<span style="font-size:22px;font-weight:700;color:${slc(sp)};margin-left:auto">${sp}%</span></div>`;
  html += segBar([
    {count:d.srtR[idx],color:'#e74c3c',label:'R'},{count:d.srtY[idx],color:'#f1c40f',label:'Y'},{count:d.srtG[idx],color:'#2ecc71',label:'G'}
  ], d.sBt[idx]);
  html += `<div class="slice-row" style="margin-top:6px"><span class="sk">待入站</span><span class="sv">${d.pendS[idx]}人</span></div>`;
  html += `<div class="slice-row"><span class="sk">人均服務量</span><span class="sv">${d.opsS[idx].toFixed?d.opsS[idx].toFixed(1):d.opsS[idx]}</span></div>`;
  html += '</div>';

  // 醫療組
  html += '<div class="slice-card"><div class="slice-card-title">醫療組</div>';
  html += `<div style="display:flex;align-items:baseline;gap:6px;margin:4px 0 2px;">`;
  html += `<span style="font-size:28px;font-weight:800;">${d.mBu[idx]}</span>`;
  html += `<span style="font-size:12px;color:var(--text3)">/ ${d.mBt[idx]}床</span>`;
  html += `<span style="font-size:22px;font-weight:700;color:${slc(mp)};margin-left:auto">${mp}%</span></div>`;
  html += segBar([
    {count:d.medCB[idx],color:'#2c3e50',label:'黑'},{count:d.medCR[idx],color:'#e74c3c',label:'紅'},
    {count:d.medCY[idx],color:'#f1c40f',label:'黃'},{count:d.medCG[idx],color:'#2ecc71',label:'綠'}
  ], d.mBt[idx]);
  html += `<div class="slice-row" style="margin-top:6px"><span class="sk">待後送</span><span class="sv">${d.pendEvac[idx]}人</span></div>`;
  html += `<div class="slice-row"><span class="sk">人均服務量</span><span class="sv">${d.opsM[idx].toFixed?d.opsM[idx].toFixed(1):d.opsM[idx]}</span></div>`;
  html += '</div>';
  html += '</div>';

  // 來源
  html += '<div class="slice-card"><div class="slice-card-title">醫療在站傷患來源（'+t+'）</div>';
  html += '<div class="src-breakdown">';
  html += `<div class="src-chip" style="background:rgba(21,101,192,.12);border-color:#1565C0"><div class="sc-label" style="color:#1565C0">A 前進組</div><div class="sc-val" style="color:#1565C0">${sa}</div></div>`;
  html += `<div class="src-chip" style="background:rgba(230,81,0,.1);border-color:#E65100"><div class="sc-label" style="color:#E65100">B 收容轉送</div><div class="sc-val" style="color:#E65100">${sb}</div></div>`;
  html += `<div class="src-chip" style="background:rgba(46,125,50,.1);border-color:#2E7D32"><div class="sc-label" style="color:#2E7D32">C 自行抵達</div><div class="sc-val" style="color:#2E7D32">${sc}</div></div>`;
  html += '</div></div>';

  // 事件
  const sIPI = ipiCalc(si.h, si.m), mIPI = ipiCalc(mi.h, mi.m);
  const ic = v => v>=6?'var(--red)':v>=3?'var(--yellow)':'var(--green)';
  html += '<div class="slice-card"><div class="slice-card-title">事件壓力（最近 30 分）</div>';
  html += '<div style="display:flex;gap:8px;">';
  html += `<div style="flex:1;text-align:center;padding:8px;background:var(--surface2);border-radius:6px;">`;
  html += `<div style="font-size:9px;color:var(--text3)">收容 IPI</div>`;
  html += `<div style="font-size:22px;font-weight:700;color:${ic(sIPI)}">${sIPI}</div>`;
  html += `<div style="font-size:9px;color:var(--text3)">高${si.h} 中${si.m} 低${si.l}</div></div>`;
  html += `<div style="flex:1;text-align:center;padding:8px;background:var(--surface2);border-radius:6px;">`;
  html += `<div style="font-size:9px;color:var(--text3)">醫療 IPI</div>`;
  html += `<div style="font-size:22px;font-weight:700;color:${ic(mIPI)}">${mIPI}</div>`;
  html += `<div style="font-size:9px;color:var(--text3)">高${mi.h} 中${mi.m} 低${mi.l}</div></div>`;
  html += '</div>';
  html += `<div style="font-size:10px;color:var(--text2);margin-top:6px;">未結事件：${si.open+mi.open}　結案速率：${si.res+mi.res} 件/30min</div>`;
  html += '</div>';

  // 物資
  html += '<div class="slice-card"><div class="slice-card-title">物資狀況</div>';
  html += supBar('IV 輸液', iv) + supBar('氧氣瓶', ox) + supBar('止血帶', to);
  html += '</div>';

  return { title: '切面快照 · ' + t, body: html };
}
