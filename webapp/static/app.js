'use strict';

const $ = (id) => document.getElementById(id);

const els = {
  dropzone: $('dropzone'), fileInput: $('fileInput'), pickBtn: $('pickBtn'), clearBtn: $('clearBtn'),
  dzEmpty: $('dzEmpty'), dzFile: $('dzFile'), preview: $('preview'), fileName: $('fileName'), fileSize: $('fileSize'),
  uploadError: $('uploadError'), optFps: $('optFps'), optDtype: $('optDtype'), optThreshold: $('optThreshold'),
  runBtn: $('runBtn'), runHint: $('runHint'), cardProgress: $('cardProgress'), phaseText: $('phaseText'),
  percentText: $('percentText'), barFill: $('barFill'), logBox: $('logBox'), cardResult: $('cardResult'),
  resultStats: $('resultStats'), viewerFrame: $('viewerFrame'), openViewer: $('openViewer'), bevImage: $('bevImage'),
  dlPly: $('dlPly'), dlPoses: $('dlPoses'), dlManifest: $('dlManifest'), dlBev: $('dlBev'),
  historyList: $('historyList'), heroMeta: $('heroMeta'),
};

let selectedFile = null;
let objectUrl = null;
let currentRunId = null;
let pollTimer = null;

// ---------------------------------------------------------------- 上传区
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

// ---------------------------------------------------------------- 启动重建
els.runBtn.addEventListener('click', async () => {
  if (!selectedFile) return;
  els.runBtn.disabled = true;
  els.runHint.textContent = '上传中…';
  els.cardProgress.hidden = false;
  els.cardResult.hidden = true;
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
  }
});

// ---------------------------------------------------------------- 进度轮询
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
        els.runHint.textContent = state.state === 'done' ? '已完成' : '失败';
        if (state.state === 'done') showResult(state);
        refreshHistory();
      }
    } catch (error) {
      /* 轮询失败不打断流程 */
    }
  }, 900);
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

  const lines = state.logs || [];
  const joined = lines.join('\n');
  if (els.logBox.textContent.trim() !== joined.trim()) {
    els.logBox.textContent = joined ? `${joined}\n` : '';
    els.logBox.scrollTop = els.logBox.scrollHeight;
  }
  if (state.error) {
    if (!els.logBox.textContent.includes(state.error)) appendLog(`[失败] ${state.error}`);
  }
}

// ---------------------------------------------------------------- 结果
function showResult(state) {
  els.cardResult.hidden = false;
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

  const base = `/files/${currentRunId}`;
  const manifest = `${base}/point_cloud_chunks/manifest.json`;
  const trajectory = `${base}/point_cloud_chunks/trajectory.json`;
  const viewerUrl = `/viewer?manifest=${encodeURIComponent(manifest)}&trajectory=${encodeURIComponent(trajectory)}`;
  els.viewerFrame.src = viewerUrl;
  els.openViewer.href = viewerUrl;

  els.bevImage.src = `${base}/trajectory_bev.png`;
  els.dlPly.href = `${base}/reconstruction.ply`;
  els.dlPoses.href = `${base}/output/camera_poses.npy`;
  els.dlManifest.href = manifest;
  els.dlBev.href = `${base}/trajectory_bev.png`;

  els.cardResult.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ---------------------------------------------------------------- 历史
async function refreshHistory() {
  try {
    const response = await fetch('/api/runs');
    const payload = await response.json();
    const runs = payload.runs || [];
    if (!runs.length) {
      els.historyList.textContent = '还没有任务。';
      return;
    }
    const labels = { done: '已完成', running: '进行中', failed: '失败', uploaded: '待启动' };
    els.historyList.innerHTML = runs
      .map((run) => {
        const points = run.result && run.result.point_count;
        const detail = points ? `${points.toLocaleString('zh-CN')} 个点` : run.phase || '';
        return `<div class="hist-item">
          <span class="badge ${run.state}">${labels[run.state] || run.state}</span>
          <span class="grow">
            <span class="hist-name">${run.filename || run.run_id}</span>
            <span class="hist-meta">${detail}</span>
          </span>
          ${run.state === 'done' ? `<button class="ghost" data-open="${run.run_id}">查看</button>` : ''}
        </div>`;
      })
      .join('');
    els.historyList.querySelectorAll('[data-open]').forEach((button) => {
      button.addEventListener('click', () => {
        currentRunId = button.getAttribute('data-open');
        const base = `/files/${currentRunId}`;
        const manifest = `${base}/point_cloud_chunks/manifest.json`;
        const trajectory = `${base}/point_cloud_chunks/trajectory.json`;
        els.cardResult.hidden = false;
        els.viewerFrame.src = `/viewer?manifest=${encodeURIComponent(manifest)}&trajectory=${encodeURIComponent(trajectory)}`;
        els.openViewer.href = els.viewerFrame.src;
        els.bevImage.src = `${base}/trajectory_bev.png`;
        els.dlPly.href = `${base}/reconstruction.ply`;
        els.dlPoses.href = `${base}/output/camera_poses.npy`;
        els.dlManifest.href = manifest;
        els.dlBev.href = `${base}/trajectory_bev.png`;
        els.cardResult.scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
    });
  } catch (error) {
    /* 忽略 */
  }
}

// ---------------------------------------------------------------- 初始化
(async function init() {
  try {
    const response = await fetch('/api/config');
    const config = await response.json();
    const parts = [`GPU：${config.gpu || '未检测到 CUDA 设备'}`];
    parts.push(config.checkpoint_ready ? '权重：已就绪' : '权重：缺失（请放到 checkpoints/abot_recon.safetensors）');
    els.heroMeta.textContent = parts.join(' · ');
  } catch (error) {
    els.heroMeta.textContent = '无法读取本机信息';
  }
  refreshHistory();
})();
