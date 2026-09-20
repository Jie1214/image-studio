/* 图像工坊 · 批量压缩 —— 前端逻辑（无任何第三方依赖） */
(function () {
  'use strict';
  const $ = s => document.querySelector(s);
  const $$ = s => Array.from(document.querySelectorAll(s));

  const state = {
    files: [],          // {path,name,bytes,w,h,format,status?,result?}
    job: null,
    poll: null,
    cfg: null,
    cmp: { src: '', dst: '', name: '' },
  };

  /* ---------------- 基础 ---------------- */
  function fmtBytes(n) {
    n = Number(n) || 0;
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
    if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(2) + ' MB';
    return (n / 1024 / 1024 / 1024).toFixed(2) + ' GB';
  }
  function toast(msg, ms) {
    const t = $('#toast'); t.textContent = msg; t.hidden = false;
    clearTimeout(t._h); t._h = setTimeout(() => { t.hidden = true; }, ms || 2600);
  }
  async function api(path, opts) {
    const o = Object.assign({ headers: {} }, opts || {});
    if (o.body && !(o.body instanceof FormData)) {
      o.headers['Content-Type'] = 'application/json';
      o.body = JSON.stringify(o.body);
    }
    const r = await fetch(path, o);
    const ct = r.headers.get('Content-Type') || '';
    if (!ct.includes('application/json')) return r;
    const j = await r.json();
    if (!r.ok || j.ok === false) throw new Error(j.error || ('HTTP ' + r.status));
    return j;
  }
  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  /* ---------------- 参数收集 ---------------- */
  function settings() {
    return {
      format: $('#f-format').value,
      mode: $('#f-mode').value,
      quality: +$('#f-quality').value,
      target_kb: +$('#f-target').value,
      lossless: $('#f-lossless').checked,
      resize: $('#f-resize').value,
      long_edge: +$('#f-long').value,
      percent: +$('#f-percent').value,
      box_w: +$('#f-boxw').value,
      box_h: +$('#f-boxh').value,
      align: +$('#f-align').value,
      keep_metadata: $('#f-meta').checked,
      png_colors: +$('#f-pngcolors').value,
      skip_if_larger: $('#f-skip').checked,
      allow_shrink: $('#f-shrink').checked,
    };
  }
  function applySettings(s) {
    if (!s) return;
    $('#f-format').value = s.format || 'keep';
    $('#f-mode').value = s.mode || 'quality';
    $('#f-quality').value = s.quality || 78; $('#v-quality').textContent = $('#f-quality').value;
    $('#f-target').value = s.target_kb || 200;
    $('#f-lossless').checked = !!s.lossless;
    $('#f-resize').value = s.resize || 'none';
    $('#f-long').value = s.long_edge || 1920;
    $('#f-percent').value = s.percent || 100;
    $('#f-boxw').value = s.box_w || 1920;
    $('#f-boxh').value = s.box_h || 1080;
    $('#f-align').value = s.align || 0;
    $('#f-meta').checked = !!s.keep_metadata;
    $('#f-pngcolors').value = s.png_colors || 0;
    $('#f-skip').checked = s.skip_if_larger !== false;
    $('#f-shrink').checked = s.allow_shrink !== false;
    syncVisibility();
  }
  function syncVisibility() {
    const mode = $('#f-mode').value, rz = $('#f-resize').value, fmt = $('#f-format').value;
    $('#wrap-quality').hidden = mode !== 'quality';
    $('#wrap-target').hidden = mode !== 'target';
    $('#wrap-long').hidden = rz !== 'long';
    $('#wrap-percent').hidden = rz !== 'percent';
    $('#wrap-box').hidden = rz !== 'box';
    $('#f-pngcolors').disabled = !(fmt === 'PNG' || (fmt === 'keep'));
  }

  const PRESETS = {
    web: { format: 'WEBP', quality: 75, mode: 'quality', resize: 'long', long_edge: 1920, lossless: false },
    max: { format: 'WEBP', quality: 55, mode: 'quality', resize: 'long', long_edge: 1600, lossless: false },
    near: { format: 'keep', quality: 92, mode: 'quality', resize: 'none', lossless: false },
    keep: { format: 'keep', quality: 80, mode: 'quality', resize: 'none', lossless: false },
  };

  /* ---------------- 列表渲染 ---------------- */
  function renderTable() {
    const tb = $('#tbody');
    if (!state.files.length) {
      tb.innerHTML = '<tr><td colspan="8" class="empty">列表为空：先拖入图片，或填目录后点「扫描该目录」</td></tr>';
      $('#summary').textContent = '—';
      return;
    }
    tb.innerHTML = state.files.map((f, i) => {
      const r = f.result;
      const sizeCell = r && r.ok
        ? '<span class="mono">' + fmtBytes(r.src_bytes) + ' → <b class="good">' + fmtBytes(r.out_bytes) + '</b></span>'
        : '<span class="mono">' + fmtBytes(f.bytes) + '</span>';
      const saved = r && r.ok ? '<span class="' + (r.saved_pct >= 0 ? 'good' : 'bad') + '">' + (r.saved_pct >= 0 ? '−' : '+') + Math.abs(r.saved_pct).toFixed(1) + '%</span>' : '—';
      const dims = r && r.ok
        ? (r.src_w + '×' + r.src_h + (r.out_w !== r.src_w ? ' → ' + r.out_w + '×' + r.out_h : ''))
        : (f.w ? f.w + '×' + f.h : '—');
      const st = r
        ? (r.ok ? '<span class="tag">' + (r.passthrough ? '已保留原文件' : 'Q' + r.quality_used) + '</span>'
          + (r.saved_pct < 0 ? ' <span class="tag warn" title="压完更大了：这类图建议改回原格式，或把质量调低">变大了</span>' : '')
          : '<span class="bad">失败</span>')
        : '<span class="tag">待处理</span>';
      return '<tr data-i="' + i + '">'
        + '<td><img class="thumb" loading="lazy" src="/api/thumb?w=64&amp;path=' + encodeURIComponent(f.path) + '" alt=""></td>'
        + '<td><div>' + esc(f.name) + '</div><div class="hint mono">' + esc(f.dir || '') + '</div></td>'
        + '<td>' + sizeCell + '</td>'
        + '<td>' + saved + '</td>'
        + '<td class="mono">' + dims + '</td>'
        + '<td>' + esc((r && r.out_format) || f.format || '') + '</td>'
        + '<td>' + st + '</td>'
        + '<td><button class="btn sm" data-act="cmp">对比</button> <button class="btn sm" data-act="meta" title="读这张图的生成参数（模型/LoRA/提示词）">参数</button></td>'
        + '</tr>';
    }).join('');
    const bytes = state.files.reduce((a, f) => a + (f.bytes || 0), 0);
    $('#summary').innerHTML = '共 <b>' + state.files.length + '</b> 张 · 合计 <b>' + fmtBytes(bytes) + '</b>';
  }

  /* ---------------- 载入来源 ---------------- */
  function addFiles(list) {
    const known = new Set(state.files.map(f => f.path));
    let added = 0;
    list.forEach(f => {
      if (!f || !f.path || known.has(f.path)) return;
      known.add(f.path);
      state.files.push({ path: f.path, name: f.name || f.path.split(/[\\/]/).pop(), dir: f.dir || '', bytes: f.bytes || 0, w: f.w, h: f.h, format: f.format });
      added++;
    });
    renderTable();
    if (added) toast('已加入 ' + added + ' 张');
    return added;
  }

  async function doScan() {
    const dir = $('#scan-dir').value.trim();
    if (!dir) return toast('请先填写要扫描的目录');
    $('#job-hint').textContent = '正在扫描…';
    try {
      const j = await api('/api/scan', { method: 'POST', body: { dir: dir, recursive: $('#scan-rec').checked } });
      addFiles(j.files || []);
      $('#job-hint').textContent = '扫描完成：' + j.total + ' 张（' + fmtBytes(j.total_bytes) + '）' + (j.truncated ? ' · 已达上限' : '');
      rememberRoot(dir);
    } catch (e) {
      $('#job-hint').textContent = '扫描失败';
      toast('扫描失败：' + e.message, 4200);
    }
  }

  function rememberRoot(dir) {
    let roots = [];
    try { roots = JSON.parse(localStorage.getItem('imgstudio.roots') || '[]'); } catch (e) { }
    roots = [dir].concat(roots.filter(r => r !== dir)).slice(0, 6);
    localStorage.setItem('imgstudio.roots', JSON.stringify(roots));
    renderRoots();
  }
  function renderRoots() {
    let roots = [];
    try { roots = JSON.parse(localStorage.getItem('imgstudio.roots') || '[]'); } catch (e) { }
    $('#recent-roots').innerHTML = roots.length
      ? '<span class="hint">最近扫描：</span>' + roots.map(r => '<button data-dir="' + esc(r) + '">' + esc(r) + '</button>').join('')
      : '';
  }

  /* 上传（拖拽 / 选择文件），支持文件夹递归 */
  async function uploadFiles(fileList) {
    const files = Array.from(fileList || []).filter(f => /^image\//.test(f.type) || /\.(jpe?g|png|webp|avif|bmp|tiff?|gif|jfif)$/i.test(f.name));
    if (!files.length) return toast('没有可用的图片文件');
    if (files.length > 300) toast('文件较多（' + files.length + ' 张）：若这些图能直接访问，用「扫描该目录」不走上传会快很多', 5200);
    const chunk = 20;
    let added = 0;
    for (let i = 0; i < files.length; i += chunk) {
      const part = files.slice(i, i + chunk);
      const fd = new FormData();
      // 文件夹上传时带上相对路径（子目录同名文件不会被覆盖）
      part.forEach(f => fd.append('files', f, f.webkitRelativePath || f.name));
      $('#job-hint').textContent = '正在导入 ' + Math.min(i + chunk, files.length) + '/' + files.length + '…';
      try {
        const j = await api('/api/upload', { method: 'POST', body: fd });
        added += addFiles(j.files || []);
      } catch (e) {
        toast('导入失败：' + e.message, 4200);
      }
    }
    $('#job-hint').textContent = '导入完成：新增 ' + added + ' 张';
  }

  async function entriesFromDataTransfer(dt) {
    const out = [];
    const items = dt.items ? Array.from(dt.items) : [];
    const walkers = items.map(it => it.webkitGetAsEntry && it.webkitGetAsEntry()).filter(Boolean);
    if (!walkers.length) return Array.from(dt.files || []);
    async function walk(entry) {
      if (entry.isFile) {
        await new Promise(res => entry.file(f => { out.push(f); res(); }, res));
      } else if (entry.isDirectory) {
        const reader = entry.createReader();
        const all = await new Promise(res => {
          const acc = [];
          (function read() { reader.readEntries(es => { if (!es.length) return res(acc); acc.push.apply(acc, es); read(); }); })();
        });
        for (const e of all) await walk(e);
      }
    }
    for (const w of walkers) await walk(w);
    return out;
  }

  /* ---------------- 批量压缩 ---------------- */
  async function runJob() {
    if (!state.files.length) return toast('列表为空');
    $('#btn-run').disabled = true;
    try {
      const j = await api('/api/compress', {
        method: 'POST',
        body: { files: state.files.map(f => f.path), settings: settings(), workers: +$('#f-workers').value },
      });
      state.job = j.job;
      $('#btn-cancel').hidden = false;
      $('#btn-zip').hidden = true;
      $('#progress').hidden = false;
      $('#job-hint').textContent = '任务 ' + j.job.id + ' 进行中…';
      pollJob();
    } catch (e) {
      toast('启动失败：' + e.message, 4200);
      $('#btn-run').disabled = false;
    }
  }

  function pollJob() {
    clearInterval(state.poll);
    state.poll = setInterval(async () => {
      try {
        const j = await api('/api/job?id=' + state.job.id);
        const job = j.job;
        const byPath = new Map(job.results.concat(job.errors).map(r => [r.src, r]));
        state.files.forEach(f => { const r = byPath.get(f.path); if (r) f.result = r; });
        renderTable();
        const pct = job.total ? Math.round(100 * job.done / job.total) : 0;
        $('#bar').style.width = pct + '%';
        $('#job-hint').textContent = job.status === 'running'
          ? '进行中 ' + job.done + '/' + job.total + '（' + pct + '%）· ' + job.workers + ' 线程 · ' + job.elapsed + 's'
          : (job.status === 'done' ? '完成：' + job.results.length + ' 张成功'
            + (job.errors.length ? '，' + job.errors.length + ' 张失败' : '') + ' · 用时 ' + job.elapsed + 's'
            : '已停止');
        if (job.done || job.total) {
          $('#summary').innerHTML = '原 <b>' + fmtBytes(job.total_src_bytes) + '</b> → 压缩后 <b>' + fmtBytes(job.total_out_bytes)
            + '</b> · 总节省 <b class="good">' + (job.saved_pct >= 0 ? '−' : '+') + Math.abs(job.saved_pct).toFixed(1)
            + '%</b> · 输出：<span class="mono">' + esc(job.out_dir) + '</span>';
        }
        if (job.status !== 'running') {
          clearInterval(state.poll); state.poll = null;
          $('#btn-run').disabled = false;
          $('#btn-cancel').hidden = true;
          if (job.results.length) { $('#btn-zip').hidden = false; $('#btn-zip').href = '/api/zip?id=' + job.id; }
        }
      } catch (e) {
        clearInterval(state.poll); state.poll = null;
        $('#btn-run').disabled = false;
        toast('读取任务状态失败：' + e.message, 4200);
      }
    }, 600);
  }

  /* ---------------- 对比 ---------------- */
  function openCompare(f) {
    if (!f) return;
    const r = f.result || {};
    const dstPath = r.ok ? r.out : '';
    state.cmp = { src: f.path, dst: dstPath, name: f.name };
    $('#cmp-title').textContent = f.name || '对比';
    $('#cmp-src').src = '/api/file?path=' + encodeURIComponent(f.path);
    $('#cmp-src2').src = $('#cmp-src').src;
    $('#cmp-dst').src = dstPath ? ('/api/file?path=' + encodeURIComponent(dstPath)) : '';
    $('#cmp-dst2').src = $('#cmp-dst').src;
    $('#cmp-dl').href = dstPath ? ('/api/file?dl=1&path=' + encodeURIComponent(dstPath)) : $('#cmp-src').src;
    $('#cmp-info').innerHTML = r.ok
      ? '原图 <b>' + fmtBytes(r.src_bytes) + '</b>（' + r.src_w + '×' + r.src_h + ' ' + (r.src_format || f.format || '') + '）'
      + ' → 压缩后 <b>' + fmtBytes(r.out_bytes) + '</b>（' + r.out_w + '×' + r.out_h + ' ' + (r.out_format || '') + '）'
      + ' · 节省 <b class="' + (r.saved_pct >= 0 ? 'good' : 'bad') + '">' + (r.saved_pct >= 0 ? '−' : '+')
      + Math.abs(r.saved_pct).toFixed(1) + '%</b>'
      + ' · 质量 Q' + r.quality_used + (r.shrink && r.shrink < 1 ? '（另缩到 ' + Math.round(r.shrink * 100) + '%）' : '')
      + ' · 用时 ' + r.ms + 'ms' + (r.passthrough ? ' · <span class="warn">已保留原文件</span>' : '')
      : '还没压过这张：点「🚀 开始压缩」后这里会显示体积对比（原图 ' + fmtBytes(f.bytes) + '）';
    $('#cmp-modal').hidden = false;
    syncCmpMode();
    requestAnimationFrame(syncCmpWidth);
  }
  function closeCompare() { $('#cmp-modal').hidden = true; $('#cmp-dst').src = ''; $('#cmp-dst2').src = ''; }
  function syncCmpWidth() {
    const w = $('#cmp').clientWidth;
    $('#cmp').style.setProperty('--cmpw', w + 'px');
    setSlider(parseFloat($('#cmp-top').style.width) || 50);
  }
  function setSlider(pct) {
    pct = Math.max(2, Math.min(98, pct));
    $('#cmp-top').style.width = pct + '%';
    $('#cmp-handle').style.left = pct + '%';
  }
  function syncCmpMode() {
    const side = $('#cmp-mode-side').classList.contains('active');
    $('#cmp').hidden = side;
    $('#cmp-side').hidden = !side;
  }

  async function estimate() {
    if (!state.files.length) { $('#est-hint').textContent = '列表为空'; return; }
    const f = state.files[0];
    $('#est-hint').textContent = '估算中…（' + f.name + '）';
    try {
      const j = await api('/api/preview', { method: 'POST', body: { path: f.path, settings: settings() } });
      const r = j.result;
      f.result = r; renderTable();
      $('#est-hint').innerHTML = '估算（' + esc(f.name) + '）：<b>' + fmtBytes(r.src_bytes) + ' → ' + fmtBytes(r.out_bytes)
        + '</b>（' + (r.saved_pct >= 0 ? '−' : '+') + Math.abs(r.saved_pct).toFixed(1) + '%）· Q' + r.quality_used
        + (r.out_w !== r.src_w ? ' · ' + r.out_w + '×' + r.out_h : '');
    } catch (e) {
      $('#est-hint').textContent = '估算失败：' + e.message;
    }
  }
  let estTimer = null;
  function scheduleEstimate() {
    clearTimeout(estTimer);
    estTimer = setTimeout(estimate, 550);
  }

  /* ---------------- 读图参数（模型 / LoRA / 提示词）· 支持批量 ---------------- */
  const metaState = { files: [], last: null };

  function metaRowItem(f, i) {
    const m = f.meta;
    const thumb = '<img class="thumb" loading="lazy" src="/api/thumb?w=96&amp;path=' + encodeURIComponent(f.path) + '" alt="">';
    const size = (f.w && f.h) ? ((f.w || 0) + '×' + (f.h || 0)) : '—';
    if (f.busy) {
      return '<tr data-i="' + i + '"><td>' + thumb + '</td><td>' + esc(f.name) + '</td>'
        + '<td class="mono">' + size + '</td><td class="hint">解析中…</td></tr>';
    }
    if (!m) {
      return '<tr data-i="' + i + '"><td>' + thumb + '</td><td>' + esc(f.name) + '</td>'
        + '<td class="mono">' + size + '</td><td class="bad">未解析</td></tr>';
    }
    // 只要解析过就给「详情」按钮；没读到参数时右边只出图 + 占位文案
    const act = '<button class="btn sm" data-act="meta-detail">详情</button>';
    return '<tr data-i="' + i + '">'
      + '<td>' + thumb + '</td>'
      + '<td class="fname" title="' + esc(f.path) + '">' + esc(f.name) + '</td>'
      + '<td class="mono">' + size + '</td>'
      + '<td>' + act + '</td>'
      + '</tr>';
  }

  function renderMetaTable() {
    const tb = $('#meta-tbody');
    if (!metaState.files.length) {
      tb.innerHTML = '<tr><td colspan="4" class="empty">列表为空：拖入图片 / 选文件夹 / 填目录后点「读取该目录」</td></tr>';
    } else {
      tb.innerHTML = metaState.files.map((f, i) => metaRowItem(f, i)).join('');
    }
    const total = metaState.files.length;
    const done = metaState.files.filter(x => x.meta && !x.busy).length;
    const okN = metaState.files.filter(x => x.meta && x.meta.ok).length;
    const withModels = new Set();
    let loraN = 0;
    metaState.files.forEach(f => {
      if (!f.meta) return;
      (f.meta.models || []).forEach(m => withModels.add(m.name));
      loraN += (f.meta.loras || []).length;
    });
    $('#meta-summary').innerHTML = total
      ? ('共 <b>' + total + '</b> 张（已解析 <b>' + done + '</b>，其中 <b class="good">' + okN + '</b> 张带生成参数）'
        + ' · 涉及模型 <b>' + withModels.size + '</b> 个 · LoRA 引用 <b>' + loraN + '</b> 次')
      : '还没有图片';
    const canExport = okN > 0;
    const ex = $('#meta-export');
    ex.hidden = !canExport;
    if (canExport) {
      try {
        const md = metaState.files.filter(f => f.meta && f.meta.ok).map(f => metaReport(f.meta)).join('\n\n---\n\n');
        const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
        if (ex._url) URL.revokeObjectURL(ex._url);
        ex._url = URL.createObjectURL(blob);
        ex.href = ex._url;
        ex.download = '图片参数报告_' + okN + '张.md';
      } catch (e) { ex.hidden = true; }
    }
  }

  function showMetaProgress(done, total) {
    $('#meta-progress').hidden = false;
    $('#meta-bar').style.width = (total ? Math.round(100 * done / total) : 0) + '%';
  }
  function hideMetaProgress() {
    $('#meta-progress').hidden = true;
    $('#meta-bar').style.width = '0%';
  }

  async function parseMetaBatch(paths) {
    const added = [];
    (paths || []).forEach(p => {
      if (!p) return;
      const it = metaState.files.find(x => x.path === p);
      if (it) { if (!it.meta) { it.busy = true; added.push(p); } return; }
      metaState.files.push({ path: p, name: String(p).split(/[\\/]/).pop(), meta: null, busy: true });
      added.push(p);
    });
    renderMetaTable();
    if (!added.length) { toast('这些图已经在列表里了'); return 0; }
    const chunk = 8;
    let done = 0;
    showMetaProgress(0, added.length);
    for (let i = 0; i < added.length; i += chunk) {
      const part = added.slice(i, i + chunk);
      try {
        const j = await api('/api/meta', { method: 'POST', body: { paths: part } });
        (j.metas || []).forEach(m => {
          const it = metaState.files.find(x => x.path === (m.file && m.file.path));
          if (!it) return;
          it.meta = m; it.busy = false;
          it.name = (m.file && m.file.name) || it.name;
          it.w = m.file && m.file.w; it.h = m.file && m.file.h;
          it.bytes = m.file && m.file.bytes; it.format = m.file && m.file.format;
        });
      } catch (e) {
        part.forEach(p => {
          const it = metaState.files.find(x => x.path === p);
          if (!it) return;
          it.busy = false;
          it.meta = { ok: false, tool: '解析失败', file: { name: it.name, path: p }, notes: ['请求失败：' + e.message],
            models: [], loras: [], kinds: {}, sampler: {}, positive: '', negative: '', embeddings: [], node_census: {} };
        });
        toast('解析失败：' + e.message, 4200);
      }
      done += part.length;
      showMetaProgress(done, added.length);
      renderMetaTable();
    }
    hideMetaProgress();
    const okN = metaState.files.filter(x => x.meta && x.meta.ok).length;
    if (added.length) toast('解析完成：' + added.length + ' 张，其中 ' + okN + ' 张带生成参数', 3400);
    return added.length;
  }

  function bigInfo(it) {
    return [it.name, (it.w ? it.w + '×' + it.h : ''), fmtBytes(it.bytes || 0), it.format || '', it.dir || '']
      .filter(Boolean).join(' · ');
  }

  /* 详情左侧的大图：点图或按钮在「适应窗口 / 原始大小」之间切换 */
  function setBigImage(prefix, path, infoText) {
    const img = $('#' + prefix + '-big');
    if (!img) return;
    const wrap = img.parentElement, inf = $('#' + prefix + '-big-info');
    const op = $('#' + prefix + '-big-open'), fit = $('#' + prefix + '-big-fit');
    wrap.classList.remove('actual');
    if (fit) fit.textContent = '适应窗口';
    const url = '/api/file?path=' + encodeURIComponent(path);
    img.src = url;
    img.dataset.path = path;
    if (inf) inf.textContent = infoText || '';
    if (op) op.href = url + '&dl=1';
    const toggle = () => {
      wrap.classList.toggle('actual');
      if (fit) fit.textContent = wrap.classList.contains('actual') ? '缩小适应' : '适应窗口';
    };
    img.onclick = toggle;
    if (fit) fit.onclick = toggle;
  }

  function showMetaDetail(it) {
    if (!it || !it.meta) return;
    metaState.last = it;
    $('#meta-detail-title').textContent = '参数详情 · ' + it.name;
    $('#meta-detail-card').hidden = false;
    setBigImage('meta', it.path, bigInfo(it));
    renderMeta(it.meta);
  }

  async function copyAllPositive() {
    const items = metaState.files.filter(f => f.meta && f.meta.ok && f.meta.positive);
    if (!items.length) return toast('还没有解析出正向提示词');
    const text = items.map(f => '【' + f.name + '】\n' + f.meta.positive).join('\n\n');
    await copyText(text, '（' + items.length + ' 张的正向提示词）');
  }

  function metaReport(m) {
    const L = [];
    L.push('# 图片生成参数报告');
    L.push('');
    L.push('- 文件：' + m.file.name + '（' + m.file.w + '×' + m.file.h + '，' + fmtBytes(m.file.bytes) + '，' + (m.file.format || '') + '）');
    L.push('- 生成工具：' + m.tool + (m.meta_source ? '（元数据来源：' + m.meta_source + '）' : ''));
    if (m.models.length) {
      L.push('', '## 模型');
      m.models.forEach(x => L.push('- ' + x.kind + '：' + x.name));
    }
    if (m.loras.length) {
      L.push('', '## LoRA');
      m.loras.forEach(x => L.push('- ' + x.name + '（权重 model=' + (x.strength_model ?? '-') + ', clip=' + (x.strength_clip ?? '-') + '）'));
    }
    const kinds = Object.keys(m.kinds || {});
    if (kinds.length) {
      L.push('', '## 其他资源');
      kinds.forEach(k => L.push('- ' + k + '：' + (m.kinds[k] || []).join('、')));
    }
    if (m.embeddings && m.embeddings.length) L.push('', '## Textual Inversion', m.embeddings.map(e => '- ' + e).join('\n'));
    if (Object.keys(m.sampler || {}).length) {
      L.push('', '## 采样参数');
      Object.entries(m.sampler).forEach(([k, v]) => L.push('- ' + k + ': ' + v));
    }
    if (m.positive) L.push('', '## 正向提示词', '', '```', m.positive, '```');
    if (m.negative) L.push('', '## 负向提示词', '', '```', m.negative, '```');
    if (m.notes && m.notes.length) L.push('', '## 提示', m.notes.map(n => '- ' + n).join('\n'));
    return L.join('\n');
  }

  function renderMeta(m, targetSel, dlSel) {
    const tsel = targetSel || '#meta-panel', dsel = dlSel || '#meta-dl';
    state.meta = m;
    const f = m.file;
    const ok = m.ok;
    const kv = (o) => Object.entries(o || {}).filter(([, v]) => v !== null && v !== undefined && v !== '')
      .map(([k, v]) => '<span class="tag">' + esc(k) + ' <b>' + esc(v) + '</b></span>').join(' ');
    const models = (m.models || []).map(x => '<tr><td>' + esc(x.kind) + '</td><td class="mono">' + esc(x.name) + '</td></tr>').join('');
    const loras = (m.loras || []).map(x => '<tr><td class="mono">' + esc(x.name) + '</td><td>' + (x.strength_model ?? '-') + '</td><td>' + (x.strength_clip ?? '-') + '</td></tr>').join('');
    const shownKinds = new Set((m.models || []).map(x => x.kind));
    const kinds = Object.keys(m.kinds || {})
      .filter(k => !shownKinds.has(k) && !/^LoRA$/i.test(k))       // 模型表已列过的（含 VAE/ControlNet/放大模型）不重复
      .map(k => '<tr><td>' + esc(k) + '</td><td class="mono">' + esc((m.kinds[k] || []).join('、')) + '</td></tr>').join('');
    const census = Object.entries(m.node_census || {}).slice(0, 14)
      .map(([k, v]) => '<span class="tag">' + esc(k) + ' ×' + v + '</span>').join(' ');

    const ph = t => '<div class="hint ph">（' + esc(t) + '）</div>';
    $(tsel).hidden = false;
    $(tsel).innerHTML = ''
      + '<div class="meta-grid">'
      + '  <div>'
      + '    <div class="meta-file"><b>' + esc(f.name) + '</b> · ' + f.w + '×' + f.h + ' · ' + fmtBytes(f.bytes)
      + ' · ' + esc(f.format || '') + ' · <span class="' + (ok ? 'good' : 'warn') + '">' + esc(ok ? m.tool : '无生成参数') + '</span>'
      + (m.meta_source ? ' <span class="tag">来源：' + esc(m.meta_source) + '</span>' : '') + '</div>'
      + (models ? '    <div class="meta-sec"><h4>模型（' + m.models.length + '）</h4><table class="tbl mini">' + models + '</table></div>'
        : '    <div class="meta-sec"><h4>模型</h4>' + ph('没有读到模型信息') + '</div>')
      + (loras ? '    <div class="meta-sec"><h4>LoRA（' + m.loras.length + '）</h4><table class="tbl mini"><thead><tr><th>名称</th><th>model 权重</th><th>clip 权重</th></tr></thead>' + loras + '</table></div>' : '')
      + (kinds ? '    <div class="meta-sec"><h4>其他资源</h4><table class="tbl mini">' + kinds + '</table></div>' : '')
      + ((m.embeddings || []).length ? '    <div class="meta-sec"><h4>Embedding</h4><div>' + m.embeddings.map(e => '<span class="tag">' + esc(e) + '</span>').join(' ') + '</div></div>' : '')
      + (Object.keys(m.sampler || {}).length ? '    <div class="meta-sec"><h4>采样参数</h4><div class="meta-kv">' + kv(m.sampler) + '</div></div>'
        : '    <div class="meta-sec"><h4>采样参数</h4>' + ph('没有采样参数（seed / steps / cfg / sampler…）') + '</div>')
      + (m.total_nodes ? '    <div class="meta-sec"><h4>节点构成（共 ' + m.total_nodes + ' 个）</h4><div class="meta-kv">' + census + '</div></div>' : '')
      + (ok ? '' : '    <div class="meta-sec"><h4>说明</h4>' + ph('这张图里没有生成参数（ComfyUI / A1111 / EXIF 都没读到），右边只能看大图') + '</div>')
      + '  </div>'
      + '  <div>'
      + '    <div class="meta-sec"><h4>正向提示词 <button class="btn sm js-copy-pos">复制</button></h4><pre class="out">' + esc(m.positive || '（这张图没有正向提示词）') + '</pre></div>'
      + '    <div class="meta-sec"><h4>负向提示词 <button class="btn sm js-copy-neg">复制</button></h4><pre class="out">' + esc(m.negative || '（没有负向提示词）') + '</pre></div>'
      + '    ' + ((m.notes || []).length ? '<div class="meta-sec"><h4>提示</h4><ul class="notes">' + m.notes.map(n => '<li>' + esc(n) + '</li>').join('') + '</ul></div>' : '')
      + '    <details class="meta-raw"><summary>原始元数据（点开可复制给其它工具）</summary>'
      + '      <div class="hint">容器里带的键：' + esc((m.meta_keys || []).join('、') || '无') + '</div>'
      + '      <pre class="out">' + esc(m.raw && m.raw.prompt ? m.raw.prompt : (m.raw && m.raw.parameters ? m.raw.parameters : '（无）')) + '</pre>'
      + '    </details>'
      + '  </div>'
      + '</div>';
    const panel = $(tsel);
    const cp = panel.querySelector('.js-copy-pos');
    if (cp && m.positive) cp.onclick = () => copyText(m.positive, '正向提示词');
    const cn = panel.querySelector('.js-copy-neg');
    if (cn && m.negative) cn.onclick = () => copyText(m.negative, '负向提示词');
    const dl = dsel ? $(dsel) : null;
    if (!dl) return;
    try {
      const blob = new Blob([metaReport(m)], { type: 'text/markdown;charset=utf-8' });
      if (dl._url) URL.revokeObjectURL(dl._url);
      dl._url = URL.createObjectURL(blob);
      dl.href = dl._url;
      dl.download = (f.name.replace(/\.[^.]+$/, '') || 'image') + '_参数报告.md';
      dl.hidden = false;
    } catch (e) { dl.hidden = true; }
  }

  async function copyText(t, what) {
    try {
      await navigator.clipboard.writeText(t);
      toast('已复制' + what + '（' + t.length + ' 字符）');
    } catch (e) {
      const ta = document.createElement('textarea');
      ta.value = t; document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); toast('已复制' + what); } catch (e2) { toast('复制失败，请手动选中'); }
      ta.remove();
    }
  }

  async function readMeta(path, scroll) {
    if (!path) return;
    const it = metaState.files.find(x => x.path === path);
    if (it && it.meta && it.meta.ok) { showMetaDetail(it); if (scroll) $('#meta-detail-card').scrollIntoView({ behavior: 'smooth', block: 'start' }); return; }
    await parseMetaBatch([path]);
    const got = metaState.files.find(x => x.path === path);
    if (got && got.meta) { showMetaDetail(got); if (scroll) $('#meta-detail-card').scrollIntoView({ behavior: 'smooth', block: 'start' }); }
  }

  async function uploadAndReadMeta(fileList) {
    const files = Array.from(fileList || []).filter(f => /^image\//.test(f.type) || /\.(jpe?g|png|webp|avif|bmp|tiff?|gif|jfif)$/i.test(f.name));
    if (!files.length) return toast('没有可用的图片文件');
    const chunk = 20;
    const paths = [];
    for (let i = 0; i < files.length; i += chunk) {
      const part = files.slice(i, i + chunk);
      const fd = new FormData();
      part.forEach(f => fd.append('files', f, f.webkitRelativePath || f.name));
      $('#meta-summary').textContent = '正在导入 ' + Math.min(i + chunk, files.length) + '/' + files.length + '…';
      try {
        const j = await api('/api/upload', { method: 'POST', body: fd });
        (j.files || []).forEach(f => paths.push(f.path));
      } catch (e) { toast('导入失败：' + e.message, 4200); }
    }
    await parseMetaBatch(paths);
  }

  /* ---------------- 设置 ---------------- */
  async function openSettings() {
    const j = await api('/api/config');
    state.cfg = j.config;
    $('#s-roots').value = (j.config.input_roots || []).join('\n');
    $('#s-out').value = j.config.output_dir || '';
    $('#s-workers').value = j.config.workers || 4;
    $('#s-port').value = j.config.port || 8720;
    $('#s-exts').value = (j.config.exts || []).join(',');
    $('#s-note').textContent = '项目目录：' + j.project + '　当前输出：' + j.output_dir;
    $('#set-modal').hidden = false;
  }
  async function saveSettings() {
    try {
      const cfg = {
        input_roots: $('#s-roots').value.split('\n').map(s => s.trim()).filter(Boolean),
        output_dir: $('#s-out').value.trim(),
        workers: +$('#s-workers').value || 4,
        port: +$('#s-port').value || 8720,
        exts: $('#s-exts').value.split(',').map(s => s.trim().toLowerCase()).filter(Boolean),
      };
      const j = await api('/api/config', { method: 'POST', body: cfg });
      state.cfg = j.config;
      $('#f-workers').value = j.config.workers || 4;
      toast('设置已保存' + (String(j.config.port) !== location.port ? '（端口改动需重启服务）' : ''));
      $('#set-modal').hidden = true;
      const st = await api('/api/stats');
      $('#hdr-stat').textContent = '输出目录 ' + st.output_dir + ' · 已产出 ' + st.output_files + ' 个文件 · ' + st.workers + ' 线程';
    } catch (e) {
      toast('保存失败：' + e.message, 4200);
    }
  }

  /* ---------------- 顶栏 tab 切换 ---------------- */
  const VIEWS = ['compress', 'meta', 'classify'];
  function switchView(v) {
    const target = VIEWS.includes(v) ? v : 'compress';
    VIEWS.forEach(k => { const el = $('#view-' + k); if (el) el.hidden = (k !== target); });
    $$('#tabs .tab').forEach(b => b.classList.toggle('active', b.dataset.view === target));
    try { localStorage.setItem('imgstudio.view', target); } catch (e) { }
    state.view = target;
  }

  /* ---------------- 一键分类（第三个 tab） ---------------- */
  const clsState = { items: [], root: '', out: '', running: false };

  function clsNames() {
    return {
      A: ($('#cls-name-a').value.trim() || '有ComfyUI信息'),
      B: ($('#cls-name-b').value.trim() || '其他'),
      C: ($('#cls-name-c').value.trim() || '无生成信息'),
    };
  }
  function clsBadge(b) {
    const n = clsNames()[b] || b;
    return '<span class="badge ' + String(b).toLowerCase() + '">' + esc(n) + '</span>';
  }
  function clsStamp() {
    const d = new Date(), p = n => String(n).padStart(2, '0');
    return d.getFullYear() + p(d.getMonth() + 1) + p(d.getDate()) + '-' + p(d.getHours()) + p(d.getMinutes()) + p(d.getSeconds());
  }
  function renderClsTable() {
    const tb = $('#cls-tbody');
    if (!clsState.items.length) {
      tb.innerHTML = '<tr><td colspan="6" class="empty">先填目录后点「扫描并预览」</td></tr>';
      return;
    }
    tb.innerHTML = clsState.items.map((it, i) => {
      const thumb = '<img class="thumb" loading="lazy" alt="" src="/api/thumb?w=48&path=' + encodeURIComponent(it.path) + '">';
      return '<tr data-i="' + i + '">'
        + '<td>' + thumb + '</td>'
        + '<td class="fname" title="' + esc(it.dir || '') + '">' + esc(it.rel || it.name) + '</td>'
        + '<td>' + clsBadge(it.bucket) + '</td>'
        + '<td>' + esc(it.tool || '—') + (it.source ? ' <span class="hint">' + esc(it.source) + '</span>' : '') + '</td>'
        + '<td>' + fmtBytes(it.bytes || 0) + '</td>'
        + '<td>' + (it.w ? it.w + '×' + it.h : '—') + '</td>'
        + '</tr>';
    }).join('');
  }
  function clsSummary() {
    const c = {}, b = {};
    clsState.items.forEach(it => { c[it.bucket] = (c[it.bucket] || 0) + 1; b[it.bucket] = (b[it.bucket] || 0) + (it.bytes || 0); });
    const names = clsNames();
    $('#cls-summary').innerHTML = clsState.items.length
      ? ('共 <b>' + clsState.items.length + '</b> 张：' + ['A', 'B', 'C'].filter(k => c[k])
        .map(k => '<b>' + esc(names[k]) + '</b> ' + c[k] + ' 张（' + fmtBytes(b[k]) + '）').join(' · '))
      : '没有可分类的图片';
  }
  function makeClsCsv() {
    const names = clsNames();
    const head = '文件名,相对路径,判定,归入文件夹,工具,来源,宽,高,体积字节';
    const q = v => '"' + String(v ?? '').replace(/"/g, '""') + '"';
    const rows = clsState.items.map(it => [it.name, it.rel, it.tool || '', names[it.bucket] || it.bucket,
      it.source || '', it.w, it.h, it.bytes].map(q).join(','));
    const blob = new Blob(['\ufeff' + head + '\n' + rows.join('\n')], { type: 'text/csv;charset=utf-8' });
    const a = $('#cls-export');
    if (a._url) URL.revokeObjectURL(a._url);
    a._url = URL.createObjectURL(blob);
    a.href = a._url;
    a.download = '分类清单_' + clsStamp() + '.csv';
  }
  async function ensureDirAllowed(dir) {
    try {
      const j = await api('/api/allow_dir', { method: 'POST', body: { dir: dir } });
      if (j.added) toast('已把该目录加入「可扫描目录」：' + dir, 4000);
      return !!(j && j.ok);
    } catch (e) { return false; }
  }

  async function clsScan() {
    const dir = $('#cls-dir').value.trim();
    if (!dir) return toast('先填要分类的目录');
    try { localStorage.setItem('imgstudio.clsdir', dir); } catch (e) { }
    $('#btn-cls-scan').disabled = true;
    $('#cls-summary').textContent = '正在扫描并逐张判定…（图多时稍等）';
    try {
      await ensureDirAllowed(dir);
      const j = await api('/api/classify/scan', {
        method: 'POST', body: {
          dir: dir, recursive: $('#cls-rec').checked, rule: $('#cls-rule').value,
          out_dir: $('#cls-out').value.trim() || undefined,
        }
      });
      if (!j.ok) throw new Error(j.error || '扫描失败');
      clsState.items = j.items || [];
      clsState.root = j.root;
      renderClsTable(); clsSummary();
      $('#btn-cls-run').disabled = !clsState.items.length;
      $('#cls-export').hidden = !clsState.items.length;
      if (clsState.items.length) makeClsCsv();
      toast('扫描完成：' + clsState.items.length + ' 张');
    } catch (e) {
      $('#cls-summary').textContent = '扫描失败';
      toast('扫描失败：' + e.message, 5000);
    } finally { $('#btn-cls-scan').disabled = false; }
  }
  async function clsRun() {
    if (clsState.running || !clsState.items.length) return;
    const mode = $('#cls-mode').value, names = clsNames();
    const outDir = $('#cls-out').value.trim() || ('output/分类_' + clsStamp());
    if (mode === 'move') {
      const ok = window.confirm('移动模式：' + clsState.items.length + ' 张图会从原位置被移走（不是删除，但不会留在原处）。\n\n'
        + '建议先用「复制」确认分类正确。确定要移动吗？');
      if (!ok) return;
    }
    clsState.running = true;
    $('#btn-cls-run').disabled = true;
    $('#cls-progress').hidden = false;
    $('#cls-bar').style.width = '15%';
    try {
      const j = await api('/api/classify/run', {
        method: 'POST', body: {
          items: clsState.items.map(it => ({ path: it.path, bucket: it.bucket })),
          root: clsState.root, out_dir: outDir, names: names, mode: mode,
          conflict: $('#cls-conflict').value, preserve_tree: $('#cls-tree').checked, confirm: true,
        }
      });
      if (!j.ok) throw new Error(j.error || '分类失败');
      clsState.out = j.out_dir;
      $('#cls-bar').style.width = '100%';
      const s = j.stat || {};
      toast('分类完成：' + (s.copied ? '复制 ' + s.copied + ' 张' : '移动 ' + s.moved + ' 张')
        + (s.skipped ? '，跳过 ' + s.skipped : '') + (s.failed ? '，失败 ' + s.failed : ''), 6000);
      $('#cls-summary').innerHTML = '✅ 已输出到 <span class="mono">' + esc(j.out_dir) + '</span>：'
        + ['A', 'B', 'C'].filter(k => clsState.items.some(i => i.bucket === k))
          .map(k => '<b>' + esc(names[k]) + '</b> ' + clsState.items.filter(i => i.bucket === k).length + ' 张').join(' · ');
    } catch (e) {
      toast('分类失败：' + e.message, 6000);
    } finally {
      clsState.running = false;
      $('#btn-cls-run').disabled = false;
      setTimeout(() => { $('#cls-progress').hidden = true; $('#cls-bar').style.width = '0'; }, 1200);
    }
  }
  async function clsDetail(i) {
    const it = clsState.items[i];
    if (!it) return;
    $('#cls-detail-card').hidden = false;
    $('#cls-detail-title').textContent = '条目详情 · ' + it.name;
    setBigImage('cls', it.path, bigInfo(it));
    $('#cls-detail').innerHTML = '<div class="hint" style="padding:8px">正在读完整参数…</div>';
    try {
      const m = await api('/api/meta', { method: 'POST', body: { path: it.path } });
      renderMeta(m.meta || m, '#cls-detail', null);
    } catch (e) {
      $('#cls-detail').innerHTML = '<div class="hint">读取失败：' + esc(e.message) + '</div>';
    }
  }

  /* ---------------- 事件绑定 ---------------- */
  function bind() {
    // 拖拽
    const drop = $('#drop');
    ['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('hot'); }));
    ['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('hot'); }));
    drop.addEventListener('drop', async e => {
      const files = await entriesFromDataTransfer(e.dataTransfer);
      uploadFiles(files);
    });
    ['dragover', 'drop'].forEach(ev => window.addEventListener(ev, e => e.preventDefault()));

    $('#btn-pick').onclick = () => $('#file-input').click();
    $('#file-input').onchange = e => uploadFiles(e.target.files);
    // 「选择文件夹」：webkitdirectory 能一次拿到整个文件夹（含子文件夹）里的所有文件
    $('#btn-pickdir').onclick = () => $('#dir-input').click();
    $('#dir-input').onchange = e => {
      const n = (e.target.files || []).length;
      if (!n) return toast('这个文件夹里没找到图片');
      toast('已选中 ' + n + ' 个文件，开始导入…');
      uploadFiles(e.target.files);
    };
    $('#btn-scan').onclick = doScan;
    /* ---- 读图参数 tab ---- */
    const md = $('#metadrop');
    ['dragenter', 'dragover'].forEach(ev => md.addEventListener(ev, e => { e.preventDefault(); md.classList.add('hot'); }));
    ['dragleave', 'drop'].forEach(ev => md.addEventListener(ev, e => { e.preventDefault(); md.classList.remove('hot'); }));
    md.addEventListener('drop', async e => {
      const files = await entriesFromDataTransfer(e.dataTransfer);
      if (files && files.length) uploadAndReadMeta(files);
    });
    $('#btn-meta-pick').onclick = () => $('#meta-input').click();
    $('#meta-input').onchange = e => uploadAndReadMeta(e.target.files);
    $('#btn-meta-pickdir').onclick = () => $('#meta-dir-input').click();
    $('#meta-dir-input').onchange = e => {
      const n = (e.target.files || []).length;
      if (!n) return toast('这个文件夹里没找到图片');
      toast('已选中 ' + n + ' 个文件，开始读取…');
      uploadAndReadMeta(e.target.files);
    };
    $('#btn-meta-scan').onclick = async () => {
      let dir = $('#meta-scan-dir').value.trim();
      if (!dir) {
        // 空输入别"点了没反应"：先尝试用上次读过的目录，再不行就把光标和提示摆到眼前
        const last = metaState.lastDir || (() => { try { return localStorage.getItem('is_meta_dir') || ''; } catch (e) { return ''; } })();
        if (!last) {
          const inp = $('#meta-scan-dir');
          inp.focus();
          inp.classList.add('need');
          setTimeout(() => inp.classList.remove('need'), 1800);
          return toast('先在输入框里填一个目录路径（浏览器选文件夹拿不到绝对路径），再点「读取该目录」', 4600);
        }
        dir = last;
        $('#meta-scan-dir').value = dir;
        toast('输入框为空，改用上次读过的目录：' + dir, 3200);
      }
      $('#meta-summary').textContent = '正在扫描 ' + dir + ' …';
      try {
        await ensureDirAllowed(dir);       // 用户手写的目录自动进白名单，免得读取时被挡
        const j = await api('/api/scan', { method: 'POST', body: { dir: dir, recursive: $('#meta-scan-rec').checked } });
        if (j && j.ok === false) {          // 目录不存在 / 被拒绝：别报成「没有图片」，并把失效的记忆清掉
          toast('读取失败：' + (j.error || '未知错误'), 4600);
          $('#meta-summary').textContent = '读取失败：' + (j.error || '');
          if (String(j.error || '').indexOf('不存在') >= 0) {
            try { localStorage.removeItem('is_meta_dir'); } catch (e) {}
            metaState.lastDir = '';
          }
          return;
        }
        metaState.lastDir = dir;
        try { localStorage.setItem('is_meta_dir', dir); } catch (e) {}
        if (!j.total) return toast('这个目录里没有图片');
        await parseMetaBatch((j.files || []).map(f => f.path));
      } catch (e) { toast('扫描失败：' + e.message, 4200); $('#meta-summary').textContent = '扫描失败'; }
    };
    $('#meta-scan-dir').addEventListener('keydown', e => { if (e.key === 'Enter') $('#btn-meta-scan').click(); });
    $('#meta-clear').onclick = () => {
      metaState.files = [];
      renderMetaTable();
      $('#meta-detail-close').click();
      toast('已清空');
    };
    $('#meta-copy-pos-all').onclick = copyAllPositive;
    $('#meta-detail-close').onclick = () => {
      $('#meta-detail-title').textContent = '参数详情';
      $('#meta-panel').innerHTML = '<div class="hint" style="padding:12px">左边点一行 → 这里显示模型 / LoRA / 提示词 / 采样参数</div>';
      const img = $('#meta-big');
      img.removeAttribute('src');
      img.onclick = null;
      $('#meta-big-info').textContent = '左边点一行 → 这里看大图';
      $('#meta-big-open').removeAttribute('href');
      $('#meta-dl').hidden = true;
    };
    /* ---- 一键分类 tab ---- */
    $('#cls-rule').onchange = () => { $('#cls-wrap-c').hidden = ($('#cls-rule').value !== 'three'); };
    ['#cls-name-a', '#cls-name-b', '#cls-name-c'].forEach(s => {
      $(s).oninput = () => { if (clsState.items.length) { renderClsTable(); clsSummary(); makeClsCsv(); } };
    });
    $('#btn-cls-scan').onclick = clsScan;
    $('#cls-dir').addEventListener('keydown', e => { if (e.key === 'Enter') clsScan(); });
    $('#btn-cls-run').onclick = clsRun;
    $('#btn-cls-open').onclick = async () => {
      const dir = clsState.out || $('#cls-out').value.trim() || '';
      if (!dir) return toast('还没有输出目录');
      try { await api('/api/reveal', { method: 'POST', body: { path: dir } }); toast('已打开输出目录'); }
      catch (e) { toast('打开失败：' + e.message); }
    };
    $('#cls-tbody').onclick = e => {
      const tr = e.target.closest('tr');
      if (!tr || tr.dataset.i === undefined) return;
      clsDetail(+tr.dataset.i);
    };
    $('#cls-detail-close').onclick = () => {
      $('#cls-detail-title').textContent = '条目详情';
      $('#cls-detail').innerHTML = '<div class="hint" style="padding:12px">左边点一行 → 这里看判定依据和完整参数</div>';
      const img = $('#cls-big');
      img.removeAttribute('src');
      img.onclick = null;
      $('#cls-big-info').textContent = '左边点一行 → 这里看大图';
      $('#cls-big-open').removeAttribute('href');
    };
    $('#meta-tbody').onclick = e => {
      const tr = e.target.closest('tr'); if (!tr) return;
      const it = metaState.files[+tr.dataset.i]; if (!it) return;
      if (it.meta) showMetaDetail(it);
    };
    $('#meta-detail-card').addEventListener('dblclick', e => { if (e.detail === 2 && e.target.closest('img')) $('#meta-big-fit').click(); });
    // 顶栏 tab
    $$('#tabs .tab').forEach(b => { b.onclick = () => switchView(b.dataset.view); });
    renderMetaTable();
    try {                                   // 上次读过的目录填回去，「读取该目录」随时有东西可执行
      const last = localStorage.getItem('is_meta_dir');
      if (last && !$('#meta-scan-dir').value) { $('#meta-scan-dir').value = last; metaState.lastDir = last; }
    } catch (e) {}
    $('#btn-clear').onclick = () => { state.files = []; renderTable(); $('#progress').hidden = true; $('#bar').style.width = '0'; $('#job-hint').textContent = '已清空'; };
    $('#recent-roots').onclick = e => { const b = e.target.closest('button'); if (b) { $('#scan-dir').value = b.dataset.dir; doScan(); } };
    $('#scan-dir').addEventListener('keydown', e => { if (e.key === 'Enter') doScan(); });

    // 参数联动
    $('#f-quality').oninput = e => { $('#v-quality').textContent = e.target.value; scheduleEstimate(); };
    ['#f-format', '#f-mode', '#f-target', '#f-resize', '#f-long', '#f-percent', '#f-boxw', '#f-boxh',
      '#f-align', '#f-pngcolors', '#f-lossless', '#f-meta', '#f-skip', '#f-shrink'].forEach(sel => {
        const el = $(sel);
        if (el) { el.onchange = () => { syncVisibility(); scheduleEstimate(); }; }
      });
    $$('#presets .segb').forEach(b => {
      b.onclick = () => {
        $$('#presets .segb').forEach(x => x.classList.remove('active'));
        b.classList.add('active');
        const p = PRESETS[b.dataset.preset];
        applySettings(Object.assign(settings(), p));
        scheduleEstimate();
      };
    });

    // 结果表
    $('#tbody').onclick = e => {
      const tr = e.target.closest('tr'); if (!tr) return;
      const f = state.files[+tr.dataset.i]; if (!f) return;
      const act = e.target.dataset.act;
      if (act === 'meta') { switchView('meta'); readMeta(f.path, true); return; }
      if (act === 'cmp' || e.target.classList.contains('thumb')) {
        openCompare(f);
      }
    };

    // 批量
    $('#btn-run').onclick = runJob;
    $('#btn-cancel').onclick = async () => {
      try {
        if (state.job) await api('/api/cancel', { method: 'POST', body: { id: state.job.id } });
        toast('已请求停止（手头这几张跑完即停）');
      } catch (e) { toast('取消失败：' + e.message, 4200); }
    };
    $('#btn-open').onclick = async () => {
      try { const j = await api('/api/reveal', { method: 'POST', body: { path: state.job ? state.job.out_dir : '' } }); toast('已打开：' + j.opened, 3200); }
      catch (e) { toast('打开失败：' + e.message, 4200); }
    };

    // 对比弹窗
    $('#cmp-close').onclick = closeCompare;
    $('#cmp-modal').onclick = e => { if (e.target.id === 'cmp-modal') closeCompare(); };
    $('#cmp-mode-slider').onclick = () => { $('#cmp-mode-slider').classList.add('active'); $('#cmp-mode-side').classList.remove('active'); syncCmpMode(); requestAnimationFrame(syncCmpWidth); };
    $('#cmp-mode-side').onclick = () => { $('#cmp-mode-side').classList.add('active'); $('#cmp-mode-slider').classList.remove('active'); syncCmpMode(); };
    let dragging = false;
    const startDrag = () => dragging = true;
    const endDrag = () => dragging = false;
    $('#cmp-handle').addEventListener('mousedown', startDrag);
    $('#cmp').addEventListener('mousedown', e => { if (e.target.id === 'cmp-src' || e.target.id === 'cmp') { startDrag(); move(e); } });
    window.addEventListener('mouseup', endDrag);
    function move(e) {
      if (!dragging) return;
      const r = $('#cmp').getBoundingClientRect();
      setSlider(((e.clientX - r.left) / r.width) * 100);
    }
    window.addEventListener('mousemove', move);
    window.addEventListener('resize', () => { if (!$('#cmp-modal').hidden) syncCmpWidth(); });

    // 设置弹窗
    $('#btn-settings').onclick = openSettings;
    $('#set-close').onclick = () => $('#set-modal').hidden = true;
    $('#set-modal').onclick = e => { if (e.target.id === 'set-modal') $('#set-modal').hidden = true; };
    $('#set-save').onclick = saveSettings;
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape') { closeCompare(); $('#set-modal').hidden = true; }
    });
  }

  /* ---------------- 启动 ---------------- */
  (async function init() {
    bind();
    renderRoots();
    try { switchView(localStorage.getItem('imgstudio.view') || 'compress'); } catch (e) { switchView('compress'); }
    try { $('#cls-dir').value = localStorage.getItem('imgstudio.clsdir') || ''; } catch (e) { }
    try {
      const j = await api('/api/config');
      state.cfg = j.config;
      applySettings(j.config.defaults);
      $('#f-workers').value = j.config.workers || 4;
      applySettings(Object.assign({}, j.config.defaults));   // 再同步一次（含 md 覆盖）
      const st = await api('/api/stats');
      $('#hdr-stat').textContent = '输出目录 ' + st.output_dir + ' · 已产出 ' + st.output_files + ' 个文件 · ' + st.workers + ' 线程';
      if ((j.config.input_roots || []).length) $('#scan-dir').value = j.config.input_roots[0];
      if (state.files.length) scheduleEstimate();
    } catch (e) {
      toast('初始化失败：' + e.message, 5000);
    }
  })();

  // 便于调试
  window.__imgstudio = { state, settings, renderTable, doScan, runJob, estimate };
})();
