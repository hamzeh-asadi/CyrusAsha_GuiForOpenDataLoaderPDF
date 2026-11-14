/* ==========================================================================
   CyrusAsha_GuiForOpenDataLoaderPDF - "Jobs" page
   Queue and history, job details (progress, per-file results, errors),
   live log and an output browser with previews for Markdown, JSON, HTML,
   PDF, text and images.
   ========================================================================== */
'use strict';

/** Filter and search state of the job list. */
const JV = { filter: localStorage.getItem('odl.jobFilter') || 'all', search: '' };

/**
 * Title of a job (its name or the first input).
 * The file name is wrapped in Unicode isolates (FSI … PDI) so that a
 * left-to-right name cannot reorder the surrounding Persian text.
 * @param {Object} job Job snapshot.
 * @returns {string} Title.
 */
function jobTitle(job) {
  if (job.name) return job.name;
  const first = job.inputs && job.inputs[0];
  if (!first) return job.id;
  const more = job.inputs.length - 1;
  return '\u2068' + first.label + '\u2069' + (more > 0 ? ' ' + t('jobs.more', { n: fmtNum(more) }) : '');
}

/**
 * Progress fraction of a running job.
 * @param {Object} job Job snapshot.
 * @returns {number|null} 0..1 or null if unknown.
 */
function jobProgress(job) {
  if (job.state === 'waiting' || job.state === 'queued') return null;
  if (!job.files_total) return null;
  const done = Math.max(0, job.files_started - 1) + 0.5;
  return Math.min(0.99, done / job.files_total);
}

/**
 * Duration of a job in seconds (live while running).
 * @param {Object} job Job snapshot.
 * @returns {number|null} Seconds.
 */
function jobDuration(job) {
  if (!job.started) return null;
  return (job.finished || (S.state ? S.state.time : Date.now() / 1000)) - job.started;
}

/** Common job actions. */
const JobActions = {
  /** @param {Object} job */
  async cancel(job) {
    if (job.state === 'running' && !(await confirmDialog(t('jobs.cancel_q'), jobTitle(job), { danger: true, ok: t('jobs.cancel') }))) return;
    await api.post('/api/jobs/' + job.id + '/cancel');
    refreshState();
  },
  /** @param {Object} job */
  async rerun(job) {
    const created = await api.post('/api/jobs/' + job.id + '/rerun');
    toast(t('convert.queued'), 'ok');
    go('jobs/' + created.id);
  },
  /** @param {Object} job @param {boolean} [stay] */
  async remove(job, stay) {
    const text = job.output_mode === 'job' ? t('jobs.delete_text_job') : t('jobs.delete_text_custom');
    if (!(await confirmDialog(t('jobs.delete_q'), text, { danger: true, ok: t('common.delete') }))) return;
    await api.del('/api/jobs/' + job.id);
    toast(t('jobs.deleted'), 'ok');
    if (!stay) go('jobs');
    refreshState();
  },
  /** @param {Object} job */
  async openFolder(job) {
    await api.post('/api/jobs/' + job.id + '/open', {});
  },
  /** @param {Object} job */
  zip(job) {
    location.href = '/api/jobs/' + job.id + '/zip';
  },
};

/**
 * Small badge for jobs whose right-to-left text was repaired.
 * @param {Object} job Job snapshot.
 * @returns {HTMLElement|null} Badge.
 */
function rtlBadge(job) {
  if (!job.rtl || !job.rtl.files) return null;
  return h('span.badge.ok', { title: t('jobs.rtl_title') }, t('jobs.rtl_badge'));
}

const JobsPage = {
  /**
   * Build the list or the detail view (`#jobs/<id>`).
   * @param {HTMLElement} main Container.
   * @param {string[]} params Route parameters.
   */
  mount(main, params) {
    this.root = h('div.page');
    main.appendChild(this.root);
    this.detail = params[0] ? new JobDetail(params[0], this.root) : null;
    if (!this.detail) this.renderList();
  },

  /** @param {Object} state Application state. */
  update(state) {
    if (this.detail) this.detail.update(state);
    else this.renderRows();
  },

  /** Stop polling in the detail view. */
  unmount() {
    if (this.detail) this.detail.destroy();
    this.detail = null;
    this.root = null;
  },

  /** Build the list view. */
  renderList() {
    const filters = ['all', 'active', 'done', 'failed'];
    const seg = h('div.btn-group', filters.map((f) => h('button.btn.sm', {
      type: 'button', class: JV.filter === f ? 'active' : null,
      onclick: () => { JV.filter = f; localStorage.setItem('odl.jobFilter', f); this.renderList(); },
    }, t('jobs.filter.' + f))));
    const search = textInput(JV.search, (v) => { JV.search = v; this.renderRows(); }, { type: 'search', placeholder: t('jobs.search') });
    search.style.maxWidth = '260px';
    this.rowsBox = h('div.job-list');
    mount(this.root,
      h('div.page-head',
        h('div', h('h1', t('jobs.title')), h('p', t('jobs.subtitle'))),
        h('div.row',
          button({ label: t('nav.convert'), icon: 'plus', cls: 'primary', onClick: () => go('convert') }),
          button({ label: t('jobs.clear'), icon: 'trash', cls: 'danger', onClick: () => this.clearFinished() }))),
      h('div.spread.mb', seg, search),
      this.rowsBox);
    this.renderRows();
  },

  /** Render the job rows (called on every poll). */
  renderRows() {
    if (!this.rowsBox || !S.state) return;
    const q = JV.search.trim().toLowerCase();
    const groups = { active: ['queued', 'waiting', 'running'], done: ['success', 'partial'], failed: ['failed', 'canceled'] };
    const jobs = S.state.jobs.filter((j) => (JV.filter === 'all' || groups[JV.filter].includes(j.state)) &&
      (!q || jobTitle(j).toLowerCase().includes(q) || j.id.includes(q) || (j.inputs || []).some((i) => i.path.toLowerCase().includes(q))));
    if (!jobs.length) {
      mount(this.rowsBox, h('div.empty', icon('jobs', 'xl'), h('div', t('jobs.empty')),
        button({ label: t('nav.convert'), icon: 'plus', cls: 'primary mt', onClick: () => go('convert') })));
      return;
    }
    mount(this.rowsBox, jobs.map((job) => this.row(job)));
  },

  /**
   * One job row.
   * @param {Object} job Job snapshot.
   * @returns {HTMLElement} Row.
   */
  row(job) {
    const active = ['queued', 'waiting', 'running'].includes(job.state);
    const sub = [
      h('span', icon('clock', 'sm'), ' ', fmtAgo(job.created)),
      job.started ? h('span', fmtDuration(jobDuration(job))) : null,
      h('span', t('jobs.files', { n: fmtNum(job.files_total) })),
      job.pages_total ? h('span', t('jobs.pages', { n: fmtNum(job.pages_total) })) : null,
      h('span', listJoin((job.formats || []).map((f) => choiceLabel('format', f)))),
      job.hybrid ? h('span.badge.primary', t('jobs.hybrid')) : null,
      rtlBadge(job),
    ];
    let status;
    if (job.state === 'running') {
      status = h('div', { style: { minWidth: '160px' } }, h('div.small.ellipsis', { dir: 'auto' },
        job.phase === 'rtl' ? t('jobs.rtl_phase_short')
          : t('jobs.progress', { i: fmtNum(Math.max(1, job.files_started)), n: fmtNum(job.files_total) })),
      progressBar(jobProgress(job), 'thin'));
    } else if (job.state === 'waiting') {
      status = h('div.small.muted', t('jobs.waiting_hybrid'));
    } else if (!active) {
      status = h('div.small.muted', t('jobs.outputs', { n: fmtNum(job.outputs_count), size: fmtBytes(job.outputs_size) }));
    }
    return h('div.job-row', { onclick: () => go('jobs/' + job.id) },
      jobStateIcon(job.state),
      h('div', { style: { minWidth: 0 } },
        h('div.row.nowrap', h('span.title.ellipsis', { dir: 'auto' }, jobTitle(job)), jobStatePill(job.state)),
        h('div.sub', sub)),
      h('div.row.nowrap', status,
        h('div.actions',
          active ? button({ icon: 'stop', cls: 'sm danger', title: t('jobs.cancel'), onClick: () => JobActions.cancel(job) }) : null,
          !active && job.outputs_count && isLocal() ? button({ icon: 'folder', cls: 'sm', title: t('jobs.open_folder'), onClick: () => JobActions.openFolder(job) }) : null,
          !active ? button({ icon: 'rerun', cls: 'sm', title: t('jobs.rerun'), onClick: () => JobActions.rerun(job) }) : null,
          !active ? button({ icon: 'trash', cls: 'sm ghost', title: t('common.delete'), onClick: () => JobActions.remove(job, true) }) : null)));
  },

  /** Delete all finished jobs after confirmation. */
  async clearFinished() {
    if (!(await confirmDialog(t('jobs.clear_q'), t('jobs.clear_text'), { danger: true, ok: t('common.delete') }))) return;
    const res = await api.post('/api/jobs/clear', { states: ['success', 'partial', 'failed', 'canceled'] });
    toast(t('jobs.cleared', { n: fmtNum(res.removed) }), 'ok');
    refreshState();
  },
};

/* ===================================================================== */
/** Detail view of one job. */
class JobDetail {
  /**
   * @param {string} id Job id.
   * @param {HTMLElement} root Container.
   */
  constructor(id, root) {
    this.id = id;
    this.root = root;
    this.tab = localStorage.getItem('odl.jobTab') || 'outputs';
    this.job = null;
    this.files = null;
    this.selected = null;
    this.logView = null;
    this.lastState = null;
    mount(root, h('div.boot', t('common.loading')));
    this.load();
  }

  /** Fetch the job details and render. */
  async load() {
    try {
      this.job = await api.get('/api/jobs/' + this.id);
    } catch (err) {
      mount(this.root, callout('err', errorText(err)), button({ label: t('jobs.back'), icon: 'back', cls: 'mt', iconCls: 'flip', onClick: () => go('jobs') }));
      return;
    }
    this.lastState = this.job.state;
    this.render();
  }

  /** Render the complete view. */
  render() {
    const job = this.job;
    this.headBox = h('div');
    this.progressBox = h('div');
    this.tabBox = h('div');
    this.body = h('div');
    mount(this.root,
      h('div.row.mb', button({ label: t('jobs.back'), icon: 'back', iconCls: 'flip', cls: 'ghost sm', onClick: () => go('jobs') })),
      this.headBox, this.progressBox, h('div.card.mt', this.tabBox, this.body));
    this.renderHead();
    this.renderProgress();
    this.renderTabs();
  }

  /**
   * Called on every state poll.
   * @param {Object} state Application state.
   */
  update(state) {
    if (!this.job) return;
    const snap = state.jobs.find((j) => j.id === this.id);
    if (!snap) return;
    const finishedNow = this.lastState !== snap.state && !['queued', 'waiting', 'running'].includes(snap.state);
    Object.assign(this.job, snap);
    this.lastState = snap.state;
    this.renderHead();
    this.renderProgress();
    if (finishedNow) {
      api.get('/api/jobs/' + this.id).then((full) => { this.job = full; this.files = null; this.renderHead(); this.renderTabs(); }).catch(() => {});
    }
  }

  /** Header card with title, state and actions. */
  renderHead() {
    const job = this.job;
    const active = ['queued', 'waiting', 'running'].includes(job.state);
    mount(this.headBox, h('div.card',
      h('div.spread',
        h('div.row.nowrap', { style: { minWidth: 0 } }, jobStateIcon(job.state),
          h('div', { style: { minWidth: 0 } },
            h('h1.ellipsis', { dir: 'auto', style: { margin: 0 } }, jobTitle(job)),
            h('div.row.small.muted', jobStatePill(job.state), h('span.ltr', job.id), h('span', fmtTime(job.created)),
              job.started ? h('span', icon('clock', 'sm'), ' ', fmtDuration(jobDuration(job))) : null))),
        h('div.row',
          active ? button({ label: t('jobs.cancel'), icon: 'stop', cls: 'danger', onClick: () => JobActions.cancel(job) }) : null,
          isLocal() ? button({ label: t('jobs.open_folder'), icon: 'folder', onClick: () => JobActions.openFolder(job) }) : null,
          !active && job.outputs_count ? button({ label: t('jobs.zip'), icon: 'download', onClick: () => JobActions.zip(job) }) : null,
          !active ? button({ label: t('jobs.rerun'), icon: 'rerun', onClick: () => JobActions.rerun(job) }) : null,
          !active ? button({ icon: 'trash', cls: 'danger', title: t('common.delete'), onClick: () => JobActions.remove(job) }) : null)),
      this.errorBox(), this.rtlBox()));
  }

  /**
   * Note about the right-to-left repair of the outputs (Persian, Arabic, Hebrew).
   * @returns {Node|null} Callout.
   */
  rtlBox() {
    const info = this.job.rtl;
    if (!info || !info.files || ['queued', 'waiting', 'running'].includes(this.job.state)) return null;
    return h('div.mt', callout('ok', h('b', t('jobs.rtl_title')),
      h('p', { style: { margin: '4px 0 0' } }, t('jobs.rtl_text', {
        n: fmtNum(info.files), langs: listJoin((info.languages || []).map((l) => (hasKey('lang.' + l) ? t('lang.' + l) : l))),
        e: fmtNum(info.elements || 0), tables: fmtNum(info.tables || 0) }))));
  }

  /**
   * Error/partial information.
   * @returns {Node|null} Callout.
   */
  errorBox() {
    const job = this.job;
    if (job.state !== 'failed' && job.state !== 'partial') return null;
    const key = 'err.' + (job.error || 'cli_failed');
    const title = hasKey(key) ? t(key) : t('err.generic', { code: job.error });
    return h('div.mt', callout(job.state === 'partial' ? 'warn' : 'err',
      h('b', job.state === 'partial' ? t('jobs.partial_title') : title),
      job.state === 'partial' ? h('p', t('jobs.partial_text')) : null,
      job.error_detail ? h('div.small.ltr', job.error_detail) : null,
      (job.errors || []).length ? h('ul.small.ltr', { style: { margin: '6px 0 0', paddingInlineStart: '18px' } },
        job.errors.slice(-8).map((e) => h('li', e))) : null,
      job.error === 'hybrid_unavailable' ? button({ label: t('nav.hybrid'), icon: 'cpu', cls: 'sm mt', onClick: () => go('hybrid') }) : null,
      ['java_missing', 'java_too_old', 'package_missing'].includes(job.error) ? button({ label: t('nav.setup'), icon: 'package', cls: 'sm mt', onClick: () => go('setup') }) : null));
  }

  /** Progress card while the job is active. */
  renderProgress() {
    const job = this.job;
    if (!['queued', 'waiting', 'running'].includes(job.state)) { mount(this.progressBox); return; }
    let text;
    if (job.state === 'queued') text = t('jobs.queued_text');
    else if (job.state === 'waiting') text = t('jobs.waiting_hybrid_long');
    else if (job.phase === 'rtl') text = t('jobs.rtl_phase');
    else if (job.current_file) {
      text = t('jobs.processing', { i: fmtNum(Math.max(1, job.files_started)), n: fmtNum(job.files_total), file: job.current_file }) +
        (job.current_pages ? ' — ' + t('jobs.pages', { n: fmtNum(job.current_pages) }) : '');
    } else text = t('jobs.starting');
    mount(this.progressBox, h('div.card.mt',
      h('div.spread.mb', h('b', { dir: 'auto' }, text), h('span.muted.small',
        job.batches > 1 ? t('jobs.batch', { i: fmtNum(job.batch), n: fmtNum(job.batches) }) + ' · ' : '',
        t('jobs.warnings', { n: fmtNum(job.warnings) }))),
      progressBar(jobProgress(job))));
  }

  /** Tabs: outputs, per-file results, log, details. */
  renderTabs() {
    const tabs = [['outputs', 'folder'], ['files', 'convert'], ['log', 'terminal'], ['details', 'info']];
    mount(this.tabBox, h('div.tabs', tabs.map(([key, ic]) => h('button.tab', {
      type: 'button', class: this.tab === key ? 'active' : null,
      onclick: () => { this.tab = key; localStorage.setItem('odl.jobTab', key); this.renderTabs(); },
    }, icon(ic, 'sm'), t('jobtab.' + key),
    key === 'outputs' && this.job.outputs_count ? h('span.badge', fmtNum(this.job.outputs_count)) : null))));
    if (this.logView && this.tab !== 'log') { this.logView.stop(); this.logView = null; }
    if (this.tab === 'outputs') this.renderOutputs();
    else if (this.tab === 'files') this.renderFiles();
    else if (this.tab === 'log') this.renderLog();
    else this.renderDetails();
  }

  /** Output browser: file tree + preview. */
  async renderOutputs() {
    const job = this.job;
    if (['queued', 'waiting'].includes(job.state)) { mount(this.body, h('div.empty', t('jobs.no_outputs_yet'))); return; }
    if (!this.files || job.state === 'running') {
      try { this.files = (await api.get('/api/jobs/' + this.id + '/files')).files; } catch (err) { mount(this.body, callout('err', errorText(err))); return; }
    }
    if (this.tab !== 'outputs') return;
    if (!this.files.length) { mount(this.body, h('div.empty', icon('folder', 'xl'), h('div', t('jobs.no_outputs')))); return; }
    this.viewer = h('div.viewer');
    const tree = h('div.tree');
    const groups = {};
    for (const f of this.files) {
      const dir = f.path.includes('/') ? f.path.slice(0, f.path.lastIndexOf('/')) : '';
      (groups[dir] = groups[dir] || []).push(f);
    }
    for (const dir of Object.keys(groups).sort()) {
      if (dir) tree.appendChild(h('div.tree-group', icon('folder', 'sm'), ' ', dir, ' (' + fmtNum(groups[dir].length) + ')'));
      const list = groups[dir];
      const shown = dir && list.length > 12 && !this.expanded ? list.slice(0, 12) : list;
      for (const f of shown) {
        const item = h('div.tree-item', { class: this.selected === f.path ? 'active' : null, title: f.path,
          onclick: () => { this.selected = f.path; tree.querySelectorAll('.tree-item').forEach((n) => n.classList.remove('active')); item.classList.add('active'); this.preview(f); } },
        h('span.ext', { class: f.ext }, f.ext || '?'), h('span.ellipsis.grow', { dir: 'auto' }, basename(f.path)), h('span.tiny.muted', fmtBytes(f.size)));
        tree.appendChild(item);
      }
      if (shown.length < list.length) {
        tree.appendChild(h('button.link-btn.small', { type: 'button', style: { padding: '4px 10px' },
          onclick: () => { this.expanded = true; this.renderOutputs(); } }, t('jobs.show_all', { n: fmtNum(list.length) })));
      }
    }
    mount(this.body, h('div.browser', tree, this.viewer));
    const preferred = this.files.find((f) => f.path === this.selected) ||
      this.files.find((f) => f.ext === 'md') || this.files.find((f) => f.ext === 'html') || this.files.find((f) => f.ext === 'json') || this.files[0];
    this.selected = preferred.path;
    const active = Array.from(tree.querySelectorAll('.tree-item')).find((n) => n.title === preferred.path);
    if (active) active.classList.add('active');
    this.preview(preferred);
  }

  /**
   * URL of an output file.
   * @param {string} rel Relative path.
   * @returns {string} URL.
   */
  fileUrl(rel) {
    return '/files/' + this.id + '/' + rel.split('/').map(encodeURIComponent).join('/');
  }

  /**
   * Show a file in the preview pane.
   * @param {Object} f File entry `{path, ext, size}`.
   */
  async preview(f) {
    const url = this.fileUrl(f.path);
    const body = h('div.viewer-body');
    const modeBtns = h('span');
    mount(this.viewer,
      h('div.viewer-bar',
        h('span.ext.badge', f.ext), h('b.ellipsis.grow', { dir: 'auto' }, f.path), modeBtns,
        h('span.small.muted', fmtBytes(f.size)),
        isLocal() && ['pdf', 'md', 'json', 'html', 'txt', 'png', 'jpeg', 'jpg'].includes(f.ext)
          ? button({ icon: 'external', cls: 'sm ghost', title: t('jobs.open_app'), onClick: () => api.post('/api/jobs/' + this.id + '/open', { path: f.path, mode: 'file' }) }) : null,
        isLocal() ? button({ icon: 'folder', cls: 'sm ghost', title: t('jobs.reveal'), onClick: () => api.post('/api/jobs/' + this.id + '/open', { path: f.path, mode: 'reveal' }) }) : null,
        h('a.btn.sm.ghost.icon-only', { href: url + '?download=1', title: t('common.download') }, icon('download')),
        h('a.btn.sm.ghost.icon-only', { href: url, target: '_blank', rel: 'noopener', title: t('jobs.new_tab') }, icon('external'))),
      body);
    const MAX_TEXT = 3 * 1024 * 1024;
    try {
      if (['png', 'jpg', 'jpeg', 'gif', 'webp'].includes(f.ext)) {
        mount(body, h('img.preview', { src: url, alt: f.path }));
      } else if (f.ext === 'pdf') {
        mount(body, h('iframe', { src: url, title: f.path }));
      } else if (f.ext === 'html' || f.ext === 'htm') {
        const frame = () => h('iframe', { src: url, sandbox: 'allow-popups allow-popups-to-escape-sandbox', title: f.path });
        this.modeSwitch(modeBtns, body, [['view', frame], ['raw', async () => this.rawText(url, f)]]);
      } else if (f.ext === 'md') {
        const render = async () => {
          const text = await (await fetch(url)).text();
          const dir = f.path.includes('/') ? f.path.slice(0, f.path.lastIndexOf('/') + 1) : '';
          const base = location.origin + this.fileUrl(dir || '_').replace(/_$/, '');
          const resolve = (u) => { try { return new URL(u, base).pathname; } catch (e) { return '#'; } };
          return h('div.md', Markdown.render(text.slice(0, MAX_TEXT), resolve));
        };
        this.modeSwitch(modeBtns, body, [['view', render], ['raw', async () => this.rawText(url, f)]]);
      } else if (f.ext === 'json') {
        this.modeSwitch(modeBtns, body, [['summary', async () => this.jsonSummary(url, f)], ['raw', async () => this.rawJson(url, f)]]);
      } else if (['txt', 'csv', 'log', 'xml'].includes(f.ext) || f.size < 200000) {
        mount(body, await this.rawText(url, f));
      } else {
        mount(body, h('div.viewer-placeholder', t('jobs.no_preview')));
      }
    } catch (err) {
      mount(body, h('div.viewer-placeholder', errorText(err)));
    }
  }

  /**
   * Small segmented control switching between preview modes.
   * @param {HTMLElement} holder Where the buttons go.
   * @param {HTMLElement} body Preview body.
   * @param {Array<[string, Function]>} modes [key, async render()] pairs.
   */
  modeSwitch(holder, body, modes) {
    let current = localStorage.getItem('odl.previewMode.' + modes.map((m) => m[0]).join()) || modes[0][0];
    if (!modes.some((m) => m[0] === current)) current = modes[0][0];
    const draw = async () => {
      mount(holder, h('div.btn-group', modes.map(([key]) => h('button.btn.sm', {
        type: 'button', class: key === current ? 'active' : null,
        onclick: () => { current = key; localStorage.setItem('odl.previewMode.' + modes.map((m) => m[0]).join(), key); draw(); },
      }, t('preview.' + key)))));
      mount(body, h('div.viewer-placeholder', t('common.loading')));
      try { mount(body, await modes.find((m) => m[0] === current)[1]()); } catch (err) { mount(body, h('div.viewer-placeholder', errorText(err))); }
    };
    draw();
  }

  /**
   * Plain text view (first 3 MB).
   * @param {string} url File URL.
   * @param {Object} f File entry.
   * @returns {Promise<Node>} Element.
   */
  async rawText(url, f) {
    const text = await (await fetch(url)).text();
    const limit = 3 * 1024 * 1024;
    return h('div', text.length > limit ? h('div.callout.warn', t('jobs.truncated')) : null, h('pre.raw', text.slice(0, limit)));
  }

  /**
   * Syntax-coloured JSON (first 1.5 MB).
   * @param {string} url File URL.
   * @param {Object} f File entry.
   * @returns {Promise<Node>} Element.
   */
  async rawJson(url, f) {
    let text = await (await fetch(url)).text();
    const limit = 1.5 * 1024 * 1024;
    if (text.length > limit) return this.rawText(url, f);
    try { text = JSON.stringify(JSON.parse(text), null, 2); } catch (e) { /* show as is */ }
    const html = Markdown.esc(text).replace(/("(?:\\.|[^"\\])*")(\s*:)?|\b(true|false)\b|\bnull\b|-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b/g,
      (m, str, colon, bool) => {
        if (str) return colon ? '<span class="json-key">' + str + '</span>' + colon : '<span class="json-str">' + str + '</span>';
        if (bool) return '<span class="json-bool">' + m + '</span>';
        if (m === 'null') return '<span class="json-null">null</span>';
        return '<span class="json-num">' + m + '</span>';
      });
    const pre = h('pre.raw');
    pre.innerHTML = html; // built from escaped text only
    return pre;
  }

  /**
   * Summary of an opendataloader JSON file (metadata + element counts).
   * @param {string} url File URL.
   * @param {Object} f File entry.
   * @returns {Promise<Node>} Element.
   */
  async jsonSummary(url, f) {
    if (f.size > 40 * 1024 * 1024) return h('div.viewer-placeholder', t('jobs.too_big'));
    const data = await (await fetch(url)).json();
    const counts = {};
    const pages = new Set();
    const walk = (node) => {
      if (Array.isArray(node)) { node.forEach(walk); return; }
      if (!node || typeof node !== 'object') return;
      if (node.type) counts[node.type] = (counts[node.type] || 0) + 1;
      if (node['page number']) pages.add(node['page number']);
      for (const k of Object.keys(node)) if (typeof node[k] === 'object') walk(node[k]);
    };
    walk(data.kids || data);
    const meta = ['file name', 'number of pages', 'author', 'title', 'creation date', 'modification date']
      .filter((k) => data[k] !== undefined && data[k] !== null && data[k] !== '' && data[k] !== 'null')
      .map((k) => [t('json.' + k.replace(/ /g, '_')), /date/.test(k) ? fmtPdfDate(data[k]) : String(data[k])]);
    const sorted = Object.entries(counts).sort((a, b) => b[1] - a[1]);
    return h('div',
      h('div', { style: { padding: '14px 14px 0' } }, kvList(meta)),
      h('div.stat-grid', sorted.map(([type, n]) => h('div.stat', h('b', fmtNum(n)), h('span', hasKey('el.' + type) ? t('el.' + type) : type)))),
      h('div.small.muted', { style: { padding: '0 14px 14px' } }, t('json.hint')));
  }

  /** Per-file results table. */
  renderFiles() {
    const results = this.job.file_results || [];
    if (!results.length) { mount(this.body, h('div.empty', t('jobs.no_results'))); return; }
    mount(this.body, h('div.table-wrap', h('table.table',
      h('thead', h('tr', h('th', '#'), h('th', t('jobs.col.file')), h('th', t('jobs.col.pages')), h('th', t('jobs.col.outputs')), h('th', t('jobs.col.status')))),
      h('tbody', results.map((r, i) => h('tr',
        h('td.muted', fmtNum(i + 1)),
        h('td', { dir: 'auto' }, r.name),
        h('td', r.pages ? fmtNum(r.pages) : '–'),
        h('td', fmtNum(r.outputs)),
        h('td', r.ok === null ? h('span.badge.info', t('state.running')) : r.ok ? h('span.badge.ok', t('jobs.ok'))
          : h('span.badge.err', r.error === 'password' ? t('jobs.err_password') : t('jobs.failed')),
        r.rtl && r.rtl.files ? h('span.badge.primary', { title: t('jobs.rtl_file_hint', { e: fmtNum(r.rtl.elements), tables: fmtNum(r.rtl.tables) }), style: { marginInlineStart: '6px' } },
          t('jobs.rtl_badge')) : null)))))));
  }

  /** Live log. */
  renderLog() {
    if (!this.logView) {
      this.logView = new LogView('/api/jobs/' + this.id + '/log', {
        cls: 'tall', fileName: 'job-' + this.id, interval: 800,
        active: () => this.job && ['queued', 'waiting', 'running'].includes(this.job.state),
      });
    }
    mount(this.body, this.logView.el);
    this.logView.scroll();
  }

  /** Inputs, options, command. */
  renderDetails() {
    const job = this.job;
    const options = Object.entries(job.options || {});
    mount(this.body, h('div.stack',
      h('div', h('h3', t('jobs.inputs')), h('div.file-list', (job.inputs || []).map((i) => h('div.file-item',
        h('div.icon-box', { class: i.kind === 'folder' ? 'folder' : '' }, icon(i.kind === 'folder' ? 'folder' : 'file')),
        h('div.meta', h('div.name', { dir: 'auto' }, i.label), h('div.path.ltr', i.path)),
        h('span.badge', t('input.pdf_count', { n: fmtNum(i.pdfs) })), h('span.badge', fmtBytes(i.size)))))),
      h('div', h('h3', t('jobs.output_dir')), h('div.row.nowrap', h('div.cmd-preview.grow', job.output_dir),
        button({ icon: 'copy', cls: 'sm', title: t('log.copy'), onClick: () => copyText(job.output_dir) }))),
      h('div', h('h3', t('jobs.options')), options.length ? h('div.table-wrap', h('table.table.compact',
        h('tbody', options.map(([k, v]) => h('tr', h('td', optLabel(k), ' ', h('span.flag.tiny.muted.ltr', optFlag({ name: k }))),
          h('td.ltr', Array.isArray(v) ? v.join(', ') : String(v))))))) : h('div.muted', t('jobs.default_options'))),
      job.command ? h('div', h('h3', t('jobs.command')), h('div.row.nowrap', h('div.cmd-preview.grow', job.command),
        button({ icon: 'copy', cls: 'sm', title: t('log.copy'), onClick: () => copyText(job.command) }))) : null,
      job.hybrid_url ? kvList([[t('jobs.hybrid_url'), h('span.ltr', job.hybrid_url)]]) : null));
  }

  /** Stop polling. */
  destroy() {
    if (this.logView) this.logView.stop();
  }
}

PAGES.jobs = JobsPage;
