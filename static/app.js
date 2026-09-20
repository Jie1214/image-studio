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
        + '<td><button class="btn sm" data-act="cmp">对比</button></td>'
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
      if (e.target.dataset.act === 'cmp' || e.target.classList.contains('thumb')) {
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
