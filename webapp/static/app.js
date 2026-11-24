'use strict';

const $ = (id) => document.getElementById(id);

const els = {
  dropzone: $('dropzone'), fileInput: $('fileInput'), pickBtn: $('pickBtn'), clearBtn: $('clearBtn'),
  dzEmpty: $('dzEmpty'), dzFile: $('dzFile'), preview: $('preview'), fileName: $('fileName'), fileSize: $('fileSize'),
  uploadError: $('uploadError'), optFps: $('optFps'), optDtype: $('optDtype'), optThreshold: $('optThreshold'),
  runBtn: $('runBtn'), runHint: $('runHint'), cardProgress: $('cardProgress'), phaseText: $('phaseText'),
  percentText: $('percentText'), barFill: $('barFill'), logBox: $('logBox'),
  resultStats: $('resultStats'), viewerFrame: $('viewerFrame'), openViewer: $('openViewer'),
  dlPly: $('dlPly'), dlPoses: $('dlPoses'), dlManifest: $('dlManifest'), dlBev: $('dlBev'),
  historyList: $('historyList'),
  plotSvg: $('plotSvg'), plotEmpty: $('plotEmpty'), plotScale: $('plotScale'),
  heroPts: $('heroPts'), heroLen: $('heroLen'), heroFrames: $('heroFrames'),
  tbSheet: $('tbSheet'), tbGrid: $('tbGrid'), tbSource: $('tbSource'), tbStatus: $('tbStatus'),
  footMeta: $('footMeta'),
};

let selectedFile = null;
let objectUrl = null;
let currentRunId = null;
let pollTimer = null;
let lastPoints = null;   // { positions, runId }

/* ══════════════════════════════════════════════════════════════════════
   轨迹平面图 —— 本页的签名元素
   把一次量测画成工程图纸：米制网格、尺寸线、总长、指北针、比例尺
   ══════════════════════════════════════════════════════════════════════ */
const PLOT = { w: 1000, h: 620, pad: { l: 92, r: 52, t: 48, b: 96 } };
const NS = 'http://www.w3.org/2000/svg';

function el(name, attrs, text) {
  const node = document.createElementNS(NS, name);
  for (const key in attrs) node.setAttribute(key, attrs[key]);
  if (text !== undefined) node.textContent = text;
  return node;
}

function niceStep(range, targetLines) {
  if (!(range > 0)) return 1;
  const raw = range / targetLines;
  const pow = Math.pow(10, Math.floor(Math.log10(raw)));
  const n = raw / pow;
  const mult = n < 1.5 ? 1 : n < 3.5 ? 2 : n < 7.5 ? 5 : 10;
  return mult * pow;
}

function pathLength(points) {
  let total = 0;
  for (let i = 1; i < points.length; i += 1) {
    total += Math.hypot(points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1]);
  }
  return total;
}

function clearPlot() {
  els.plotSvg.innerHTML = '';
  els.plotEmpty.hidden = false;
  els.plotScale.textContent = '';
  els.heroLen.textContent = '—';
  els.tbGrid.textContent = '—';
}

function drawPlot(points, meta) {
  const svg = els.plotSvg;
  svg.innerHTML = '';
  if (!points || points.length < 2) { clearPlot(); return; }

  const areaW = PLOT.w - PLOT.pad.l - PLOT.pad.r;
  const areaH = PLOT.h - PLOT.pad.t - PLOT.pad.b;

  const xs = points.map((p) => p[0]);
  const zs = points.map((p) => p[1]);
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minZ = Math.min(...zs), maxZ = Math.max(...zs);
  const spanX = Math.max(maxX - minX, 1e-6);
  const spanZ = Math.max(maxZ - minZ, 1e-6);

  // 等比例投影，保持图面不被拉伸
  const scale = Math.min(areaW / spanX, areaH / spanZ);
  const drawW = spanX * scale;
  const drawH = spanZ * scale;
  const ox = PLOT.pad.l + (areaW - drawW) / 2;
  const oy = PLOT.pad.t + (areaH - drawH) / 2;
  const X = (v) => ox + (v - minX) * scale;
  const Y = (v) => oy + drawH - (v - minZ) * scale;   // Z 向上为正

  const step = niceStep(Math.max(spanX, spanZ), 6);

  // 图框与四角定位标记
  svg.appendChild(el('rect', {
    x: ox, y: oy, width: drawW, height: drawH,
    fill: 'none', stroke: 'var(--rule)', 'stroke-width': 1.4,
  }));
  const corner = 13;
  [[ox, oy, 1, 1], [ox + drawW, oy, -1, 1], [ox, oy + drawH, 1, -1], [ox + drawW, oy + drawH, -1, -1]]
    .forEach(([cx, cy, sx, sy]) => {
      svg.appendChild(el('path', {
        class: 'corner',
        d: `M${cx + sx * corner} ${cy}L${cx} ${cy}L${cx} ${cy + sy * corner}`,
      }));
    });

  // 米制网格（对齐到绝对 0 点，像真实坐标网）
  const firstX = Math.floor(minX / step) * step;
  for (let v = firstX; v <= maxX + step * 0.5; v += step) {
    const x = X(v);
    svg.appendChild(el('line', {
      class: 'grid-line', x1: x, y1: oy, x2: x, y2: oy + drawH,
    }));
    svg.appendChild(el('text', {
      class: 'grid-text', x, y: oy + drawH + 20, 'text-anchor': 'middle',
    }, String(Math.round(v))));
  }
  const firstZ = Math.floor(minZ / step) * step;
  for (let v = firstZ; v <= maxZ + step * 0.5; v += step) {
    const y = Y(v);
    svg.appendChild(el('line', {
      class: 'grid-line', x1: ox, y1: y, x2: ox + drawW, y2: y,
    }));
    svg.appendChild(el('text', {
      class: 'grid-text', x: ox - 14, y: y + 5, 'text-anchor': 'end',
    }, String(Math.round(v))));
  }

  // 轨迹线
  const d = points.map((p, i) => `${i ? 'L' : 'M'}${X(p[0]).toFixed(2)} ${Y(p[1]).toFixed(2)}`).join(' ');
  svg.appendChild(el('path', { class: 'trail-ghost', d }));
  const trail = el('path', { class: 'trail', d });
  svg.appendChild(trail);

  // 起止标记
  const first = points[0];
  const last = points[points.length - 1];
  svg.appendChild(el('circle', { class: 'mark-start', cx: X(first[0]), cy: Y(first[1]), r: 5 }));
  svg.appendChild(el('circle', { class: 'mark-end', cx: X(last[0]), cy: Y(last[1]), r: 6.5 }));
  svg.appendChild(el('text', {
    class: 'mark-label', x: X(first[0]) + 11, y: Y(first[1]) + 5,
  }, '起'));
  svg.appendChild(el('text', {
    class: 'mark-label', x: X(last[0]) + 11, y: Y(last[1]) + 5, 'text-anchor': 'start',
  }, '止'));

  // 尺寸线：X 向总尺寸
  const dimY = oy + drawH + 46;
  svg.appendChild(el('line', { class: 'dim-line', x1: ox, y1: dimY, x2: ox + drawW, y2: dimY }));
  [[ox, 1], [ox + drawW, -1]].forEach(([hx, dir]) => {
    svg.appendChild(el('path', {
      class: 'dim-head',
      d: `M${hx} ${dimY}L${hx + dir * 9} ${dimY - 4.5}L${hx + dir * 9} ${dimY + 4.5}Z`,
    }));
  });
  svg.appendChild(el('text', {
    class: 'dim-text', x: ox + drawW / 2, y: dimY - 9, 'text-anchor': 'middle',
  }, `X ${spanX.toFixed(1)} m`));

  // 尺寸线：Z 向总尺寸
  const dimX = ox - 54;
  svg.appendChild(el('line', { class: 'dim-line', x1: dimX, y1: oy, x2: dimX, y2: oy + drawH }));
  [[oy, 1], [oy + drawH, -1]].forEach(([hy, dir]) => {
    svg.appendChild(el('path', {
      class: 'dim-head',
      d: `M${dimX} ${hy}L${dimX - 4.5} ${hy + dir * 9}L${dimX + 4.5} ${hy + dir * 9}Z`,
    }));
  });
  const zLabel = el('text', {
    class: 'dim-text', x: dimX - 10, y: oy + drawH / 2, 'text-anchor': 'middle',
    transform: `rotate(-90 ${dimX - 10} ${oy + drawH / 2})`,
  }, `Z ${spanZ.toFixed(1)} m`);
  svg.appendChild(zLabel);

  // 轨迹总长标注
  const length = pathLength(points);
  svg.appendChild(el('text', {
    class: 'note-text', x: ox + 12, y: oy + 26,
  }, `轨迹长 ${length.toFixed(1)} m`));

  // 指北针
  const nx = PLOT.w - PLOT.pad.r + 18;
  const ny = PLOT.pad.t + 34;
  svg.appendChild(el('path', {
    class: 'north',
    d: `M${nx} ${ny - 20}L${nx - 8} ${ny + 6}L${nx} ${ny + 1}L${nx + 8} ${ny + 6}Z`,
  }));
  svg.appendChild(el('text', { class: 'north-text', x: nx, y: ny + 24, 'text-anchor': 'middle' }, 'N'));

  // 比例尺
  const barLen = 2 * step * scale;
  const bx = ox + 14;
  const by = oy + drawH - 16;
  svg.appendChild(el('rect', { class: 'bar-block', x: bx, y: by, width: barLen / 2, height: 6 }));
  svg.appendChild(el('rect', { class: 'bar-outline', x: bx, y: by, width: barLen, height: 6 }));
  svg.appendChild(el('text', { class: 'grid-text', x: bx, y: by + 22, 'text-anchor': 'middle' }, '0'));
  svg.appendChild(el('text', { class: 'grid-text', x: bx + barLen, y: by + 22, 'text-anchor': 'middle' },
    String(Math.round(2 * step))));

  // 收尾：状态文字与图纸信息
  els.plotEmpty.hidden = true;
  els.plotScale.textContent = `网格 1 格 = ${step} m`;
  els.heroLen.textContent = `${length.toFixed(1)} m`;
  els.tbGrid.textContent = `${step} m`;
  if (meta) {
    els.tbSheet.textContent = meta.runId ? `AB-${String(meta.runId).slice(0, 6).toUpperCase()}` : '—';
    els.tbSource.textContent = meta.filename || '—';
  }

  // 入场：把轨迹"画"出来
  if (!window.matchMedia('(prefers-reduced-motion: reduce)').matches && typeof trail.getTotalLength === 'function') {
    const lengthPx = trail.getTotalLength();
    trail.style.strokeDasharray = `${lengthPx}`;
    trail.style.strokeDashoffset = `${lengthPx}`;
    trail.getBoundingClientRect();
    trail.style.transition = 'stroke-dashoffset 1.4s cubic-bezier(.22,.61,.36,1)';
    trail.style.strokeDashoffset = '0';
  }
}

/* 读取某次任务的相机轨迹并绘图 */
async function loadTrajectory(runId, meta) {
  try {
    const response = await fetch(`/files/${runId}/point_cloud_chunks/trajectory.json`, { cache: 'no-store' });
    if (!response.ok) { clearPlot(); return; }
    const data = await response.json();
    const raw = data.positions || [];
    const points = raw.map((p) => [Number(p[0]), Number(p[2])]);   // 俯视平面取 X / Z
    lastPoints = { points, runId };
    drawPlot(points, Object.assign({ runId }, meta || {}));
  } catch (error) {
    clearPlot();
  }
}

/* ── 上传区 ───────────────────────────────────────────────────────── */
els.pickBtn.addEventListener('click', () => els.fileInput.click());
els.dropzone.addEventListener('click', (event) => {
  if (event.target === els.clearBtn) return;
  if (!selectedFile) els.fileInput.click();
});
els.fileInput.addEventListener('change', () => {
  if (els.fileInput.files && els.fileInput.files[0]) handleFile(els.fileInput.files[0]);
});
els.clearBtn.addEventListener('click', (event) => {
  event.stopPropagation();
  resetSelection();
});

['dragenter', 'dragover'].forEach((name) =>
  els.dropzone.addEventListener(name, (event) => {
    event.preventDefault();
    els.dropzone.classList.add('drag');
  })
);
['dragleave', 'drop'].forEach((name) =>
  els.dropzone.addEventListener(name, (event) => {
    event.preventDefault();
    els.dropzone.classList.remove('drag');
  })
);
els.dropzone.addEventListener('drop', (event) => {
  const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
  if (file) handleFile(file);
});

function formatBytes(bytes) {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) { value /= 1024; index += 1; }
  return `${value.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function handleFile(file) {
  if (!file.type.startsWith('video/') && !/\.(mp4|mov|mkv|avi|webm|m4v)$/i.test(file.name)) {
    showUploadError('这看起来不是视频文件，请选择 mp4 / mov / mkv / avi 等格式。');
    return;
  }
  showUploadError('');
  selectedFile = file;
  if (objectUrl) URL.revokeObjectURL(objectUrl);
  objectUrl = URL.createObjectURL(file);
  els.preview.src = objectUrl;
  els.fileName.textContent = file.name;
  els.fileSize.textContent = formatBytes(file.size);
  els.dzEmpty.hidden = true;
  els.dzFile.hidden = false;
  els.runBtn.disabled = false;
  els.runHint.textContent = '点击「开始重建」';
}

function resetSelection() {
  selectedFile = null;
  if (objectUrl) { URL.revokeObjectURL(objectUrl); objectUrl = null; }
  els.fileInput.value = '';
  els.preview.removeAttribute('src');
  els.dzEmpty.hidden = false;
  els.dzFile.hidden = true;
  els.runBtn.disabled = true;
  els.runHint.textContent = '请先上传视频';
}

function showUploadError(message) {
  els.uploadError.textContent = message;
  els.uploadError.hidden = !message;
}

/* ── 启动重建 ─────────────────────────────────────────────────────── */
els.runBtn.addEventListener('click', async () => {
  if (!selectedFile) return;
  els.runBtn.disabled = true;
  els.runHint.textContent = '上传中…';
  els.cardProgress.hidden = false;
  els.resultStats.innerHTML = '';
  setStatus('上传视频', 'running');
  setProgress('上传视频', 0);
  els.logBox.textContent = '';

  try {
    const form = new FormData();
    form.append('video', selectedFile);
    const response = await fetch('/api/upload', { method: 'POST', body: form });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || '上传失败');

    currentRunId = payload.run_id;
    els.runHint.textContent = '已上传，正在排队…';

    const startResponse = await fetch('/api/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        run_id: currentRunId,
        fps: Number(els.optFps.value),
        amp_dtype: els.optDtype.value,
        confidence_threshold: Number(els.optThreshold.value),
      }),
    });
    const startPayload = await startResponse.json();
    if (!startResponse.ok) throw new Error(startPayload.error || '启动失败');

    els.runHint.textContent = '重建中，请勿关闭页面';
    startPolling();
    refreshHistory();
  } catch (error) {
    els.runBtn.disabled = false;
    els.runHint.textContent = '出错了';
    appendLog(`[错误] ${error.message}`);
    setStatus('出错', 'failed');
  }
});

/* ── 进度轮询 ─────────────────────────────────────────────────────── */
function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    if (!currentRunId) return;
    try {
      const response = await fetch(`/api/status/${currentRunId}`);
      if (!response.ok) return;
      const state = await response.json();
      renderState(state);
      if (state.state === 'done' || state.state === 'failed') {
        clearInterval(pollTimer);
        pollTimer = null;
        els.runBtn.disabled = false;
        els.runHint.textContent = state.state === 'done' ? '重建完成' : '重建失败';
        if (state.state === 'done') showResult(state);
        refreshHistory();
      }
    } catch (error) {
      /* 轮询失败不打断流程 */
    }
  }, 900);
}

function setStatus(text, state) {
  els.tbStatus.textContent = text;
  if (state) els.tbStatus.setAttribute('data-state', state);
}

function setProgress(phase, ratio) {
  els.phaseText.textContent = phase;
  const percent = Math.max(0, Math.min(100, Math.round(ratio * 100)));
  els.percentText.textContent = `${percent}%`;
  els.barFill.style.width = `${percent}%`;
}

function appendLog(line) {
  els.logBox.textContent += `${line}\n`;
  els.logBox.scrollTop = els.logBox.scrollHeight;
}

function renderState(state) {
  const phaseIndex = state.phase_index || 0;
  const phaseTotal = state.phase_total || 1;
  const done = state.progress_done || 0;
  const total = state.progress_total || 0;
  let ratio = phaseIndex / phaseTotal;
  if (total > 0) ratio = Math.min(1, (phaseIndex + done / total) / phaseTotal);
  setProgress(state.phase || '', ratio);
  setStatus(state.phase || '', state.state === 'failed' ? 'failed' : 'running');

  const lines = state.logs || [];
  const joined = lines.join('\n');
  if (els.logBox.textContent.trim() !== joined.trim()) {
    els.logBox.textContent = joined ? `${joined}\n` : '';
    els.logBox.scrollTop = els.logBox.scrollHeight;
  }
  if (state.error && !els.logBox.textContent.includes(state.error)) {
    appendLog(`[失败] ${state.error}`);
  }
}

/* ── 结果 ─────────────────────────────────────────────────────────── */
function showResult(state) {
  const result = state.result || {};
  const stats = [
    ['点数', (result.point_count || 0).toLocaleString('zh-CN')],
    ['帧数', result.frames || 0],
    ['分块', result.chunk_count || 0],
    ['耗时', state.duration ? `${state.duration} 秒` : '—'],
  ];
  els.resultStats.innerHTML = stats
    .map(([label, value]) => `<div class="stat"><b>${value}</b><span>${label}</span></div>`)
    .join('');
  els.heroPts.textContent = (result.point_count || 0).toLocaleString('zh-CN');
  els.heroFrames.textContent = result.frames || 0;
  setStatus('完成', 'done');

  attachResultFiles(currentRunId, state.filename);
  loadTrajectory(currentRunId, { runId: currentRunId, filename: state.filename });
}

function attachResultFiles(runId, filename) {
  const base = `/files/${runId}`;
  const manifest = `${base}/point_cloud_chunks/manifest.json`;
  const trajectory = `${base}/point_cloud_chunks/trajectory.json`;
  const viewerUrl = `/viewer?manifest=${encodeURIComponent(manifest)}&trajectory=${encodeURIComponent(trajectory)}`;
  els.viewerFrame.src = viewerUrl;
  els.openViewer.href = viewerUrl;
  els.dlPly.href = `${base}/reconstruction.ply`;
  els.dlPoses.href = `${base}/output/camera_poses.npy`;
  els.dlManifest.href = manifest;
  els.dlBev.href = `${base}/trajectory_bev.png`;
  if (filename) els.tbSource.textContent = filename;
}

/* 打开一次已完成的量测：填成果区 + 载入轨迹平面图 */
async function openRun(runId, filename) {
  currentRunId = runId;
  try {
    const response = await fetch(`/api/status/${runId}`, { cache: 'no-store' });
    if (response.ok) {
      const state = await response.json();
      if (state.state === 'done') { showResult(state); return; }
    }
  } catch (error) {
    /* 落到下面的兜底分支 */
  }
  attachResultFiles(runId, filename);
  loadTrajectory(runId, { runId, filename });
}

/* ── 历史 ─────────────────────────────────────────────────────────── */
async function refreshHistory() {
  try {
    const response = await fetch('/api/runs');
    const payload = await response.json();
    const runs = payload.runs || [];
    if (!runs.length) {
      els.historyList.innerHTML = '<div class="hist-item"><span class="grow hist-meta">还没有量测记录。</span></div>';
      return;
    }
    const labels = { done: '已完成', running: '进行中', failed: '失败', uploaded: '待启动' };
    els.historyList.innerHTML = runs
      .map((run) => {
        const points = run.result && run.result.point_count;
        const detail = points ? `${points.toLocaleString('zh-CN')} 点 · ${run.run_id.slice(0, 6)}` : (run.phase || '');
        return `<div class="hist-item">
          <span class="badge ${run.state}">${labels[run.state] || run.state}</span>
          <span class="grow">
            <span class="hist-name">${run.filename || run.run_id}</span>
            <span class="hist-meta">${detail}</span>
          </span>
          ${run.state === 'done' ? `<button class="ghost" data-open="${run.run_id}" data-name="${run.filename || ''}">查看</button>` : ''}
        </div>`;
      })
      .join('');
    els.historyList.querySelectorAll('[data-open]').forEach((button) => {
      button.addEventListener('click', () => {
        const runId = button.getAttribute('data-open');
        const filename = button.getAttribute('data-name');
        openRun(runId, filename);
        document.querySelector('.viewport').scrollIntoView({ behavior: 'smooth', block: 'center' });
      });
    });

    // 首屏没有选中任务时，自动打开最近一次成功的量测，让轨迹平面图直接有内容
    if (!currentRunId) {
      const latest = runs.find((run) => run.state === 'done');
      if (latest) openRun(latest.run_id, latest.filename);
    }
  } catch (error) {
    /* 忽略 */
  }
}

/* ── 初始化 ───────────────────────────────────────────────────────── */
(async function init() {
  clearPlot();
  try {
    const response = await fetch('/api/config');
    const config = await response.json();
    const parts = [];
    parts.push(`GPU ${config.gpu || '未检测到 CUDA 设备'}`);
    parts.push(config.checkpoint_ready ? '权重已就绪' : '权重缺失');
    els.footMeta.textContent = parts.join(' · ');
  } catch (error) {
    els.footMeta.textContent = '无法读取本机信息';
  }
  refreshHistory();
})();
