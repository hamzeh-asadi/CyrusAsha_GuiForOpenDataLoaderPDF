/* ==========================================================================
   CyrusAsha_GuiForOpenDataLoaderPDF - "Setup & updates" page
   One-click installation, install and models folders (with moving),
   Python/venv, Java (portable / winget / custom), opendataloader-pdf
   versions, hybrid extra, OCR engines, self-test and the live installer log.
   ========================================================================== */
'use strict';

const SetupPage = {
  /**
   * Build the page.
   * @param {HTMLElement} main Container.
   */
  mount(main) {
    this.root = h('div.page');
    main.appendChild(this.root);
    this.env = null;
    this.pythons = null;
    this.withHybrid = localStorage.getItem('odl.setupHybrid') === '1';
    this.python = '';
    this.version = '';
    this.extraArgs = '';
    this.taskId = null;
    this.logView = null;
    this.storage = null;
    this.storageTimer = null;
    this.render();
    this.loadEnv(false);
  },

  /**
   * React to state polls (task progress, task end).
   * @param {Object} state Application state.
   */
  update(state) {
    const task = state.setup;
    if (task && task.id !== this.taskId) {
      this.taskId = task.id;
      this.renderTask();
    } else if (this.taskBox) {
      this.renderTaskStatus();
    }
    if (task && this.lastTaskState === 'running' && task.state !== 'running') {
      this.loadEnv(true);
      S.optionsMeta = null; // the installed version may have changed
      const ok = task.state === 'success';
      toast(t(ok ? 'setup.task_ok' : 'setup.task_failed', { action: t('action.' + task.action) }), ok ? 'ok' : 'err', 6000);
    }
    this.lastTaskState = task ? task.state : null;
    this.updateButtons();
  },

  /** Stop the log poller. */
  unmount() {
    if (this.logView) this.logView.stop();
    clearTimeout(this.storageTimer);
    this.root = null;
    this.taskBox = null;
    this.storageBox = null;
  },

  /**
   * Load the full environment report.
   * @param {boolean} refresh Force re-detection.
   */
  async loadEnv(refresh) {
    this.loading = true;
    this.renderEnv();
    this.loadStorage(refresh);
    try {
      const [env, pythons] = await Promise.all([api.get('/api/env' + (refresh ? '?refresh=1' : '')), api.get('/api/pythons' + (refresh ? '?refresh=1' : ''))]);
      this.env = env;
      this.pythons = pythons;
      if (!this.python && pythons.length) {
        const configured = S.settings && S.settings.python_path;
        const preferred = pythons.find((p) => p.path === configured && p.supported) || pythons.find((p) => p.supported);
        this.python = preferred ? preferred.path : '';
      }
    } catch (err) {
      showError(err);
    }
    this.loading = false;
    if (this.root) this.renderEnv();
  },

  /** Skeleton of the page. */
  render() {
    this.heroBox = h('div');
    this.taskBox = h('div');
    this.storageBox = h('div');
    this.envBox = h('div');
    mount(this.root,
      h('div.page-head',
        h('div', h('h1', t('setup.title')), h('p', t('setup.subtitle'))),
        button({ label: t('setup.recheck'), icon: 'refresh', onClick: () => this.loadEnv(true) })),
      h('div.stack', this.heroBox, this.taskBox, this.storageBox, this.envBox));
    this.renderTask();
  },

  /* ------------------------------------------------------------ storage */
  /**
   * Load sizes and free space of the install and models folders. Sizes are
   * measured in the background, so the report is polled until it is complete.
   * @param {boolean} refresh Measure again.
   */
  async loadStorage(refresh) {
    clearTimeout(this.storageTimer);
    try {
      this.storage = await api.get('/api/storage' + (refresh ? '?refresh=1' : ''));
    } catch (err) {
      showError(err);
      return;
    }
    if (!this.root) return;
    this.renderStorage();
    if (this.env) mount(this.heroBox, this.hero(this.env));
    if (this.storage.pending) this.storageTimer = setTimeout(() => this.loadStorage(false), 1500);
  },

  /** Render the storage card in place. */
  renderStorage() {
    if (this.storageBox) mount(this.storageBox, this.storageCard());
    this.updateButtons();
  },

  /**
   * Install and models folders: paths, sizes, free space, warnings and actions.
   * @returns {HTMLElement} Card.
   */
  storageCard() {
    const st = this.storage;
    if (!st) return h('div.card', h('div.row', icon('loader', 'spin'), t('setup.checking')));
    const size = (info) => (info.size === null || info.size === undefined ? t('storage.measuring') : fmtBytes(info.size));
    const parts = (info, names) => (info.parts ? names.filter((n) => info.parts[n] > 0)
      .map((n) => t('storage.part.' + n) + ' ' + fmtBytes(info.parts[n])).join(' · ') : '');
    const changeButton = (kind) => {
      const btn = button({ label: t('storage.change'), icon: 'sliders', cls: 'sm', onClick: () => this.changeLocation(kind) });
      btn.dataset.setupAction = 'relocate';
      return btn;
    };
    const openButton = (target) => (isLocal() && st[target].exists
      ? button({ label: t('storage.open'), icon: 'folder', cls: 'sm ghost', onClick: () => api.post('/api/open', { target }) }) : null);
    const row = (kind, names, actions) => {
      const info = st[kind];
      const detail = parts(info, names);
      return h('div.storage-row',
        h('div.spread',
          h('div.row', h('b', t('storage.' + kind)), info.default ? h('span.badge', t('storage.default')) : h('span.badge.primary', t('storage.custom'))),
          h('div.row', actions)),
        h('div.cmd-preview.mt', { title: info.path }, info.path),
        h('div.small.muted.mt', t('storage.used', { size: size(info) }),
          info.free !== null && info.free !== undefined ? ' · ' + t('storage.free', { size: fmtBytes(info.free) }) : '',
          detail ? h('div.tiny', detail) : null),
        info.warnings.map((w) => h('div.mt', callout('warn', t('storage.warn.' + w)))));
    };
    const cache = st.install.parts ? st.install.parts.cache : 0;
    return h('div.card',
      h('div.card-head', h('h2', icon('drive'), t('storage.title')),
        button({ icon: 'refresh', cls: 'sm ghost', title: t('setup.recheck'), onClick: () => this.loadStorage(true) })),
      h('p.small.muted', t('storage.help')),
      row('install', ['venv', 'java', 'cache'], [changeButton('install'), openButton('install'),
        this.actionButton({ label: t('storage.clear_cache', { size: fmtBytes(cache || 0) }), icon: 'trash', cls: 'sm ghost', action: 'clear-cache',
          confirm: t('storage.clear_cache_q'), disabled: !cache })]),
      h('hr.sep'),
      row('models', ['huggingface', 'easyocr', 'docling', 'modelscope', 'torch', 'cache', 'other'], [changeButton('models'), openButton('models'),
        this.actionButton({ label: t('storage.clear_models'), icon: 'trash', cls: 'sm ghost', action: 'clear-models',
          confirm: t('storage.clear_models_q'), disabled: !st.models.size })]),
      st.windows && st.long_paths === false ? h('p.tiny.muted.mt', t('storage.long_paths_off')) : null);
  },

  /**
   * Dialog to choose a new install or models folder (move the files or start fresh).
   * @param {"install"|"models"} kind Folder to change.
   */
  async changeLocation(kind) {
    const st = this.storage || {};
    const info = st[kind] || {};
    let check = null;
    let mode = 'move';
    let deleteOld = false;
    const result = h('div.mt');
    const modeBox = h('div.mt');
    const moving = kind === 'install' ? (info.size || 0) + (st.models && st.models.default ? st.models.size || 0 : 0) : (info.size || 0);
    let apply = null;
    const render = () => {
      if (apply) apply.disabled = !check || !check.ok || check.same;
      if (!check) { mount(result, h('div.small.muted', icon('loader', 'spin'), ' ', t('storage.checking'))); mount(modeBox); return; }
      if (!check.ok) { mount(result, callout('err', t('err.location_' + check.error))); mount(modeBox); return; }
      const sep = check.path.includes('\\') ? '\\' : '/';
      const notes = [];
      if (check.same) notes.push(callout('info', t('storage.same')));
      if (check.conflicts.length) notes.push(callout(mode === 'move' ? 'warn' : 'info', t(mode === 'move' ? 'storage.conflicts_move' : 'storage.conflicts_fresh', { names: check.conflicts.join(', ') })));
      else if (check.exists && !check.empty && !check.same) {
        notes.push(callout('info', t('storage.not_empty'), h('div.mt', button({ label: t('storage.use_subfolder', { path: check.path + sep + 'OpenDataLoaderPDF' }), cls: 'sm',
          onClick: () => { input.value = check.path + sep + 'OpenDataLoaderPDF'; runCheck(); } }))));
      }
      for (const w of check.warnings) notes.push(callout('warn', t('storage.warn.' + w)));
      mount(result,
        h('div.small', h('b', t('storage.target')), ' ', h('span.ltr', check.path), check.default ? h('span.badge', { style: { marginInlineStart: '6px' } }, t('storage.default')) : null),
        check.free !== null ? h('div.small.muted', t('storage.free', { size: fmtBytes(check.free) })) : null,
        notes.map((n) => h('div.mt', n)));
      if (check.same) { mount(modeBox); return; }
      const radio = (value, label, help) => h('label.radio-row',
        h('input', { type: 'radio', name: 'storage-mode', checked: mode === value, onchange: () => { mode = value; render(); } }),
        h('span', h('b', label), h('div.small.muted', help)));
      mount(modeBox,
        radio('move', t('storage.mode_move'), moving ? t('storage.mode_move_help', { size: fmtBytes(moving) }) : t('storage.mode_move_empty')),
        radio('fresh', t('storage.mode_fresh'), t('storage.mode_fresh_help_' + kind)),
        mode === 'fresh' ? h('div.mt', switchInput(deleteOld, (v) => { deleteOld = v; }, t('storage.delete_old'))) : null);
    };
    const runCheck = debounce(async () => {
      check = null;
      render();
      try {
        check = await api.post('/api/storage/check', { kind, path: input.value });
      } catch (err) {
        check = { ok: false, error: 'bad_path' };
      }
      render();
    }, 350);
    const input = textInput(info.default ? '' : info.path || '', () => runCheck(), { ltr: true, placeholder: kind === 'install' ? 'D:\\ODL' : 'D:\\ODL\\models' });
    const browse = button({ label: t('common.browse'), icon: 'folder', cls: 'sm', disabled: !isLocal(), title: isLocal() ? null : t('common.local_only'),
      onClick: async () => {
        const paths = await pickNative('folder', t('storage.pick_' + kind));
        if (paths.length) { input.value = paths[0]; runCheck(); }
      } });
    const reset = button({ label: t('storage.reset_default'), cls: 'sm ghost', onClick: () => { input.value = ''; runCheck(); } });
    const body = h('div.storage-dialog',
      h('p.muted', t('storage.explain_' + kind)),
      h('label.small', h('b', t('storage.new_folder'))),
      h('div.input-group', input, browse, reset),
      h('div.tiny.muted', t('storage.empty_default')),
      result, modeBox);
    const done = dialog({
      title: t('storage.change_title_' + kind),
      body,
      buttons: [{ label: t('common.cancel'), value: null }, { label: t('storage.apply'), value: 'ok', cls: 'primary' }],
    });
    apply = $('#dialog .btn.primary');
    render();
    runCheck();
    setTimeout(() => input.focus(), 30);
    if ((await done) !== 'ok' || !check || !check.ok || check.same) return;
    try {
      await api.post('/api/storage/change', { kind, path: input.value.trim(), mode, delete_old: deleteOld });
      await refreshState();
      this.taskBox.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (err) {
      showError(err);
    }
  },

  /**
   * Start an installer action.
   * @param {string} action Action name.
   * @param {Object} [params] Parameters.
   * @param {string} [confirmText] Ask for confirmation first.
   */
  async run(action, params, confirmText) {
    if (confirmText && !(await confirmDialog(confirmText, null, { danger: /uninstall|reset|remove/.test(action), ok: t('common.continue') }))) return;
    await api.post('/api/setup/start', { action, params: params || {} });
    await refreshState();
    this.taskBox.scrollIntoView({ behavior: 'smooth', block: 'start' });
  },

  /** Disable action buttons while a task is running. */
  updateButtons() {
    const busy = !!(S.state && S.state.setup && S.state.setup.state === 'running');
    if (!this.root) return;
    this.root.querySelectorAll('[data-setup-action]').forEach((b) => { b.disabled = busy; });
  },

  /**
   * Button that starts an installer action.
   * @param {Object} opts Button options plus `action`, `params`, `confirm`.
   * @returns {HTMLButtonElement} Button.
   */
  actionButton(opts) {
    const btn = button(Object.assign({}, opts, { onClick: () => this.run(opts.action, typeof opts.params === 'function' ? opts.params() : opts.params, opts.confirm) }));
    btn.dataset.setupAction = opts.action;
    btn.disabled = !!(S.state && S.state.setup && S.state.setup.state === 'running') || !!opts.disabled;
    return btn;
  },

  /* --------------------------------------------------------------- task */
  /** Task panel (steps, progress, log). */
  renderTask() {
    if (!this.taskBox) return;
    const task = S.state && S.state.setup;
    if (this.logView) { this.logView.stop(); this.logView = null; }
    if (!task) { mount(this.taskBox); return; }
    this.taskStatus = h('div');
    this.logView = new LogView('/api/setup/log', { cls: '', fileName: 'setup-' + task.id, interval: 700,
      active: () => S.state && S.state.setup && S.state.setup.state === 'running' });
    this.taskCard = h('div.card',
      h('div.card-head', h('h2', icon('terminal'), t('setup.task'), ' — ', t('action.' + task.action))), this.taskStatus,
      h('div.mt', this.logView.el));
    mount(this.taskBox, this.taskCard);
    this.renderTaskStatus();
  },

  /** Update steps, progress and buttons of the task panel. */
  renderTaskStatus() {
    const task = S.state && S.state.setup;
    if (!task || !this.taskStatus) return;
    const stepIcon = { pending: 'clock', running: 'loader', done: 'check', skipped: 'check', failed: 'x', canceled: 'stop' };
    const dl = task.download;
    const running = task.state === 'running';
    mount(this.taskStatus,
      h('div.spread.mb',
        h('div.row', jobStatePill(task.state === 'success' ? 'success' : task.state === 'running' ? 'running' : task.state === 'canceled' ? 'canceled' : 'failed'),
          task.finished ? h('span.small.muted', fmtDuration(task.finished - task.started)) : h('span.small.muted', fmtDuration(S.state.time - task.started))),
        running ? button({ label: t('common.cancel'), icon: 'stop', cls: 'danger sm', onClick: () => api.post('/api/setup/cancel') }) : null),
      h('div.steps', task.steps.map((s) => h('div.step', { class: s.state },
        h('span.bullet', icon(stepIcon[s.state] || 'clock', 'sm' + (s.state === 'running' ? ' spin' : ''))),
        h('span', t('step.' + s.key)), h('span.detail.ltr', s.detail || (s.state === 'skipped' ? t('setup.skipped') : ''))))),
      dl ? h('div.mt', h('div.spread.small', h('span.ltr.ellipsis', dl.kind === 'copy' ? t('setup.copying', { name: dl.name }) : dl.name || t('setup.downloading')),
        h('span', dl.total ? fmtBytes(dl.done) + ' / ' + fmtBytes(dl.total) + ' (' + fmtNum(dl.done / dl.total * 100) + '%)' : fmtBytes(dl.done))),
      progressBar(dl.total ? dl.done / dl.total : null)) : (running ? h('div.mt', progressBar(null, 'thin')) : null),
      task.error ? h('div.mt', callout('err', h('b', hasKey('err.' + task.error) ? t('err.' + task.error) : t('err.generic', { code: task.error })),
        task.error_detail ? h('div.small.ltr', task.error_detail) : null, this.errorHint(task.error),
        (task.error === 'hybrid_preflight' || (task.error === 'pip_failed' && task.error_detail === 'hybrid')) && !running
          ? this.preflightActions() : null)) : null);
  },

  /**
   * Buttons offered when the hybrid pre-check failed: use an older Python or install anyway.
   * @returns {HTMLElement} Button row.
   */
  preflightActions() {
    const alt = this.olderPython();
    return h('div.row.mt',
      alt ? this.actionButton({ label: t('setup.use_python', { v: alt.version }), icon: 'python', cls: 'primary sm', action: 'full',
        params: () => ({ python: alt.path, hybrid: true, recreate: true }) }) : null,
      this.actionButton({ label: t('setup.install_anyway'), icon: 'download', cls: 'sm', action: 'hybrid',
        params: () => ({ force: true, extra_args: this.extraArgs }) }));
  },

  /**
   * Newest supported interpreter older than Python 3.15 (best wheel coverage), if any.
   * @returns {Object|null} Interpreter info.
   */
  olderPython() {
    return (this.pythons || []).filter((p) => p.supported && p.version_tuple[0] === 3 && p.version_tuple[1] < 15)
      .sort((a, b) => b.version_tuple[1] - a.version_tuple[1])[0] || null;
  },

  /**
   * Minor version of the interpreter that hosts (or will host) the environment.
   * @param {Object} env Environment report.
   * @returns {number} Minor version (0 if unknown).
   */
  targetMinor(env) {
    const version = env.venv.exists ? env.venv.python : ((this.pythons || []).find((p) => p.path === this.python) || {}).version;
    const parts = String(version || '').split('.');
    return parts[0] === '3' ? parseInt(parts[1], 10) || 0 : 0;
  },

  /**
   * Extra advice for some installer errors.
   * @param {string} code Error code.
   * @returns {Node|null} Hint.
   */
  errorHint(code) {
    const key = 'hint.' + code;
    return hasKey(key) ? h('p.small', t(key)) : null;
  },

  /* ---------------------------------------------------------------- env */
  /** Render hero card and component cards. */
  renderEnv() {
    if (!this.envBox) return;
    if (!this.env) {
      mount(this.heroBox, h('div.card', h('div.row', icon('loader', 'spin'), t('setup.checking'))));
      mount(this.envBox);
      return;
    }
    const env = this.env;
    mount(this.heroBox, this.hero(env));
    mount(this.envBox, h('div.grid-2',
      this.packageCard(env), this.javaCard(env), this.pythonCard(env), this.hybridCard(env), this.ocrCard(env), this.infoCard(env)),
    h('div.card.mt', h('div.card-head', h('h2', icon('alert'), t('setup.danger'))), h('p.small.muted', t('setup.danger_text')),
      h('div.row',
        this.actionButton({ label: t('setup.uninstall'), icon: 'trash', cls: 'danger', action: 'uninstall', confirm: t('setup.uninstall_q'), disabled: !env.package.installed }),
        this.actionButton({ label: t('setup.reset'), icon: 'trash', cls: 'danger', action: 'reset', confirm: t('setup.reset_q'), disabled: !env.venv.exists }),
        this.actionButton({ label: t('setup.java_remove'), icon: 'trash', cls: 'danger', action: 'java-remove', confirm: t('setup.java_remove_q') }))));
    this.updateButtons();
  },

  /**
   * Status overview and the one-click install.
   * @param {Object} env Environment report.
   * @returns {HTMLElement} Card.
   */
  hero(env) {
    const pkg = env.package;
    const java = env.java;
    const supported = (this.pythons || []).filter((p) => p.supported);
    const allGood = pkg.installed && java.ok;
    const check = (ok, ic, title, detail, warn) => h('div.check-row',
      h('div.state-icon', { class: ok ? 'ok' : warn ? 'warn' : 'err' }, icon(ok ? 'check' : warn ? 'alert' : 'x')),
      h('div.grow', h('b', title), h('div.small.muted', detail)));
    const pythonOk = env.venv.exists || supported.length > 0;
    const checks = h('div',
      check(pythonOk, 'python', t('setup.python'), env.venv.exists ? t('setup.venv_ready', { v: env.venv.python || '?' })
        : supported.length ? t('setup.python_found', { v: supported[0].version }) : t('setup.python_missing')),
      check(java.ok, 'java', 'Java', java.ok ? t('setup.java_ok', { v: java.selected.version, src: t('src.' + java.selected.source) }) : t('setup.java_missing_auto'), !java.ok),
      check(pkg.installed, 'package', 'opendataloader-pdf', pkg.installed
        ? (pkg.update_available ? t('setup.pkg_update', { v: pkg.version, latest: pkg.latest }) : t('setup.pkg_ok', { v: pkg.version }))
        : t('setup.pkg_missing', { latest: pkg.latest || '?' }), pkg.installed && pkg.update_available),
      check(pkg.hybrid, 'zap', t('setup.hybrid_extra'), pkg.hybrid ? t('setup.hybrid_ok', { v: pkg.packages.docling || '?' }) : t('setup.hybrid_optional'), true));

    const pythonSelect = this.pythonSelect();
    return h('div.card.hero',
      h('div.card-head', h('h2', icon(allGood ? 'check' : 'package'), allGood ? t('setup.ready_title') : t('setup.install_title'))),
      h('div.grid-2',
        checks,
        h('div.col',
          allGood ? h('p.muted', t('setup.ready_text')) : h('p.muted', t('setup.install_text')),
          !env.venv.exists ? field(t('setup.python_choose'), pythonSelect, { help: t('setup.python_choose_help') }) : null,
          h('div.field', switchInput(this.withHybrid, (v) => { this.withHybrid = v; localStorage.setItem('odl.setupHybrid', v ? '1' : '0'); this.renderEnv(); }, h('b', t('setup.with_hybrid'))),
            h('div.help', t('setup.with_hybrid_help'))),
          this.installLocationLine(env),
          h('div.row.mt',
            env.venv.exists
              ? this.actionButton({ label: allGood ? t('setup.repair') : t('setup.install_all'), icon: allGood ? 'refresh' : 'download', cls: allGood ? '' : 'primary lg',
                action: 'full', params: () => ({ hybrid: this.withHybrid, python: '' }) })
              : this.installButton(env, pythonOk),
            pkg.installed ? this.actionButton({ label: t('setup.selftest'), icon: 'check', action: 'verify' }) : null,
            pkg.update_available ? this.actionButton({ label: t('setup.update_to', { v: pkg.latest }), icon: 'download', cls: 'primary', action: 'update' }) : null),
          this.withHybrid && this.targetMinor(env) >= 15 ? callout('warn', t('setup.py315_hybrid', { alt: (this.olderPython() || {}).version || '3.13' })) : null,
          !pythonOk ? callout('err', h('b', t('setup.python_missing')), h('p', t('setup.python_missing_text'))) : null,
          env.pypi_error ? callout('warn', t('setup.offline'), h('div.small.ltr', env.pypi_error)) : null)));
  },

  /**
   * "Install everything" button of a fresh installation: opens the install dialog.
   * @param {Object} env Environment report.
   * @param {boolean} pythonOk A usable Python exists.
   * @returns {HTMLButtonElement} Button.
   */
  installButton(env, pythonOk) {
    const btn = button({ label: t('setup.install_all'), icon: 'download', cls: 'primary lg', onClick: () => this.confirmInstall(env) });
    btn.dataset.setupAction = 'full';
    btn.disabled = !pythonOk || !!(S.state && S.state.setup && S.state.setup.state === 'running');
    return btn;
  },

  /**
   * Install dialog: where opendataloader-pdf and the AI models go, the hybrid
   * option and the disk space needed - then start the full installation.
   * @param {Object} env Environment report.
   */
  async confirmInstall(env) {
    const st = this.storage || await api.get('/api/storage').catch(() => null) || {};
    const defaultInstall = st.data || env.paths.data;
    const sep = defaultInstall.includes('\\') ? '\\' : '/';
    const results = { install: h('div.small.mt'), models: h('div.small.mt') };
    const checks = { install: null, models: null };
    const estimate = h('div.mt');
    let hybrid = this.withHybrid;
    let apply = null;
    const GB = 1024 * 1024 * 1024;
    const inputs = {};
    const folderOf = (kind) => {
      const typed = inputs[kind].value.trim();
      if (typed) return typed;
      return kind === 'install' ? defaultInstall : folderOf('install') + sep + 'models';
    };
    const render = () => {
      inputs.models.placeholder = folderOf('install') + sep + 'models'; // the default models folder
      for (const kind of ['install', 'models']) {
        const check = checks[kind];
        if (!check) { mount(results[kind], h('span.muted', icon('loader', 'spin'), ' ', t('storage.checking'))); continue; }
        if (!check.ok) { mount(results[kind], callout('err', t('err.location_' + check.error))); continue; }
        mount(results[kind],
          h('div', h('span.muted', '→ '), h('span.ltr', folderOf(kind)), check.free !== null ? h('span.muted', ' · ' + t('storage.free', { size: fmtBytes(check.free) })) : null),
          check.warnings.map((w) => h('div.mt', callout('warn', t('storage.warn.' + w)))),
          check.conflicts.length && kind === 'install' ? h('div.mt', callout('info', t('storage.conflicts_fresh', { names: check.conflicts.join(', ') }))) : null);
      }
      const needInstall = (hybrid ? 6 : 0.4) * GB + (hybrid && !inputs.models.value.trim() ? 3 * GB : 0);
      const needModels = hybrid ? 3 * GB : 0;
      const low = [];
      if (checks.install && checks.install.ok && checks.install.free !== null && checks.install.free < needInstall) low.push([checks.install.free, needInstall]);
      if (hybrid && inputs.models.value.trim() && checks.models && checks.models.ok && checks.models.free !== null && checks.models.free < needModels) low.push([checks.models.free, needModels]);
      mount(estimate, callout(low.length ? 'warn' : 'info',
        h('div', t('setup.need_install', { size: fmtBytes(needInstall) })),
        hybrid ? h('div', t('setup.need_models', { size: fmtBytes(needModels) })) : null,
        low.map(([free, need]) => h('div', h('b', t('setup.space_low', { free: fmtBytes(free), need: fmtBytes(need) }))))));
      if (apply) apply.disabled = !checks.install || !checks.install.ok || !checks.models || !checks.models.ok;
    };
    const runCheck = debounce(async (kind) => {
      const kinds = kind === 'install' ? ['install', 'models'] : [kind];
      for (const k of kinds) checks[k] = null;
      render();
      for (const k of kinds) {
        try {
          // An empty models field means "<install folder>/models" of the folder typed above.
          checks[k] = await api.post('/api/storage/check', { kind: k, path: k === 'models' ? folderOf('models') : inputs[k].value });
        } catch (err) {
          checks[k] = { ok: false, error: 'bad_path' };
        }
      }
      render();
    }, 350);
    const row = (kind, placeholder) => {
      inputs[kind] = textInput(kind === 'install' ? (st.install && !st.install.default ? st.install.path : '') : (st.models && !st.models.default ? st.models.path : ''),
        () => runCheck(kind), { ltr: true, placeholder });
      return h('div.mt',
        h('label.small', h('b', t('storage.' + kind))),
        h('div.input-group', inputs[kind],
          button({ label: t('common.browse'), icon: 'folder', cls: 'sm', disabled: !isLocal(), title: isLocal() ? null : t('common.local_only'),
            onClick: async () => { const paths = await pickNative('folder', t('storage.pick_' + kind)); if (paths.length) { inputs[kind].value = paths[0]; runCheck(kind); } } }),
          button({ label: t('storage.reset_default'), cls: 'sm ghost', onClick: () => { inputs[kind].value = ''; runCheck(kind); } })),
        results[kind]);
    };
    const body = h('div.storage-dialog',
      h('p.muted', t('setup.confirm_text')),
      row('install', defaultInstall),
      row('models', defaultInstall + sep + 'models'),
      h('div.mt', switchInput(hybrid, (v) => { hybrid = v; render(); }, h('b', t('setup.with_hybrid'))), h('div.help', t('setup.with_hybrid_help'))),
      estimate);
    const done = dialog({
      title: t('setup.confirm_title'),
      body,
      buttons: [{ label: t('common.cancel'), value: null }, { label: t('setup.confirm_install'), value: 'ok', cls: 'primary' }],
    });
    apply = $('#dialog .btn.primary');
    render();
    runCheck('install');
    if ((await done) !== 'ok') return;
    this.withHybrid = hybrid;
    localStorage.setItem('odl.setupHybrid', hybrid ? '1' : '0');
    try {
      await api.post('/api/setup/start', { action: 'full', params: {
        hybrid, python: this.python, install_dir: inputs.install.value.trim(), models_dir: inputs.models.value.trim() } });
      await refreshState();
      this.taskBox.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (err) {
      showError(err);
    }
  },

  /**
   * "Installed in: <folder> (change)" line of the install card, so the folder
   * can be chosen before the first installation.
   * @param {Object} env Environment report.
   * @returns {HTMLElement} Line.
   */
  installLocationLine(env) {
    const st = this.storage;
    const path = st ? st.install.path : env.paths.install;
    const warnings = st ? st.install.warnings : [];
    const link = h('a', { href: '#', onclick: (e) => { e.preventDefault(); this.changeLocation('install'); } }, t('storage.change'));
    return h('div.small.install-location',
      icon('drive', 'sm'), ' ', h('b', t('storage.install_here')), ' ', h('span.ltr', path), ' — ', link,
      st && st.install.free !== null ? h('div.tiny.muted', t('storage.free', { size: fmtBytes(st.install.free) })) : null,
      warnings.length ? h('div.tiny', { style: { color: 'var(--warning)' } }, t('storage.warn.' + warnings[0])) : null);
  },

  /**
   * Interpreter dropdown.
   * @returns {HTMLSelectElement} Select.
   */
  pythonSelect() {
    const list = this.pythons || [];
    if (!list.length) return selectInput([{ value: '', label: t('setup.python_none') }], '', () => {});
    const sel = selectInput(list.map((p) => ({
      value: p.path,
      label: 'Python ' + p.version + ' (' + p.bits + '-bit)' + (p.supported ? '' : ' — ' + t('setup.too_old')) + ' — ' + p.path,
    })), this.python, (v) => { this.python = v; });
    sel.setAttribute('dir', 'ltr'); // paths read left-to-right even in the Persian UI
    return sel;
  },

  /**
   * opendataloader-pdf package card.
   * @param {Object} env Environment report.
   * @returns {HTMLElement} Card.
   */
  packageCard(env) {
    const pkg = env.package;
    return h('div.card',
      h('div.card-head', h('h2', icon('package'), 'opendataloader-pdf'),
        pkg.installed ? h('span.badge.ok', 'v' + pkg.version) : h('span.badge.err', t('setup.not_installed'))),
      kvList([
        [t('setup.installed'), pkg.version || '–'],
        [t('setup.latest_pypi'), pkg.latest || '–'],
        [t('setup.venv_python'), env.venv.python || '–'],
      ]),
      h('div.row.mt',
        this.actionButton({ label: pkg.installed ? (pkg.update_available ? t('setup.update_to', { v: pkg.latest }) : t('setup.reinstall_latest')) : t('setup.install'),
          icon: 'download', cls: pkg.update_available || !pkg.installed ? 'primary' : '', action: pkg.installed ? 'update' : 'install',
          params: () => ({ python: this.python }) }),
        pkg.installed ? this.actionButton({ label: t('setup.selftest'), icon: 'check', action: 'verify' }) : null),
      h('div.field.mt', h('label', t('setup.pin_version')),
        h('div.input-group', this.versionPicker(), this.actionButton({ label: t('setup.install_version'), icon: 'download', cls: 'sm', action: 'install',
          params: () => ({ version: this.version, python: this.python }) })),
        h('div.help', t('setup.pin_help'))),
      h('div.row.mt.small',
        h('a', { href: 'https://github.com/opendataloader-project/opendataloader-pdf', target: '_blank', rel: 'noopener' }, icon('external', 'sm'), ' GitHub'),
        h('a', { href: 'https://pypi.org/project/opendataloader-pdf/', target: '_blank', rel: 'noopener' }, icon('external', 'sm'), ' PyPI'),
        h('a', { href: 'https://opendataloader.org/docs', target: '_blank', rel: 'noopener' }, icon('external', 'sm'), ' Docs')));
  },

  /**
   * Version selector filled from PyPI (lazy).
   * @returns {HTMLElement} Select element.
   */
  versionPicker() {
    const sel = h('select', h('option', { value: '' }, t('setup.latest')));
    sel.addEventListener('change', () => { this.version = sel.value; });
    fetchVersions().then((versions) => {
      for (const v of versions) sel.appendChild(h('option', { value: v, selected: v === this.version }, v));
    });
    return sel;
  },

  /**
   * Java card.
   * @param {Object} env Environment report.
   * @returns {HTMLElement} Card.
   */
  javaCard(env) {
    const java = env.java;
    const sel = java.selected;
    const settings = S.settings || {};
    const portableVersion = settings.java_portable_version || 21;
    return h('div.card',
      h('div.card-head', h('h2', icon('java'), 'Java'),
        java.ok ? h('span.badge.ok', sel.version) : h('span.badge.err', t(java.error === 'java_too_old' ? 'setup.java_old' : 'setup.java_none'))),
      sel ? kvList([[t('setup.java_version'), h('span.ltr', sel.version + (sel.runtime ? ' — ' + sel.runtime : ''))], [t('setup.java_source'), t('src.' + sel.source)],
        [t('setup.java_path'), h('span.ltr.small', sel.path)]]) : callout('warn', t('setup.java_missing_text')),
      h('div.fields.mt',
        field(t('setup.java_mode'), selectInput(['auto', 'portable', 'system', 'custom'].map((m) => ({ value: m, label: t('jmode.' + m) })), settings.java_mode,
          async (v) => { await saveSettings({ java_mode: v }); this.loadEnv(true); }), { help: t('setup.java_mode_help') }),
        field(t('setup.java_portable_version'), selectInput([17, 21, 25].map((v) => ({ value: v, label: 'Temurin ' + v + ' LTS' })), portableVersion,
          async (v) => { await saveSettings({ java_portable_version: parseInt(v, 10) }); })),
        settings.java_mode === 'custom' ? field(t('setup.java_custom'), pathInput(settings.java_custom_path || '', debounce(async (v) => {
          await saveSettings({ java_custom_path: v }); this.loadEnv(true);
        }, 600), 'exe', { placeholder: 'C:\\Program Files\\Java\\jdk-21' }), { wide: true, help: t('setup.java_custom_help') }) : null),
      h('div.row.mt',
        this.actionButton({ label: t('setup.java_portable', { v: portableVersion }), icon: 'download', cls: java.ok ? '' : 'primary', action: 'java-portable',
          params: () => ({ java_version: (S.settings || {}).java_portable_version || 21 }) }),
        env.windows ? this.actionButton({ label: t('setup.java_winget'), icon: 'package', action: 'java-winget', disabled: !env.winget,
          params: () => ({ java_version: (S.settings || {}).java_portable_version || 21 }) }) : null),
      java.candidates && java.candidates.length > 1 ? h('details.mt', h('summary.small.muted', t('setup.java_candidates', { n: fmtNum(java.candidates.length) })),
        h('div.table-wrap.mt', h('table.table.compact', h('tbody', java.candidates.map((c) => h('tr',
          h('td', c.version || '–'), h('td', t('src.' + c.source)), h('td.ltr.small', c.path),
          h('td', c.ok ? h('span.badge.ok', 'OK') : h('span.badge.err', c.error === 'not_found' ? t('setup.not_found') : t('setup.java_old'))))))))) : null);
  },

  /**
   * Python & virtual environment card.
   * @param {Object} env Environment report.
   * @returns {HTMLElement} Card.
   */
  pythonCard(env) {
    const supported = (this.pythons || []).filter((p) => p.supported);
    const newest = supported[0];
    return h('div.card',
      h('div.card-head', h('h2', icon('python'), t('setup.python_venv')),
        env.venv.exists ? h('span.badge.ok', 'Python ' + (env.venv.python || '?')) : h('span.badge.warn', t('setup.no_venv'))),
      kvList([[t('setup.venv_path'), h('span.ltr.small', env.venv.path)], [t('setup.gui_python'), h('span.ltr.small', env.gui_python.version + ' — ' + env.gui_python.path)]]),
      field(t('setup.python_choose'), this.pythonSelect(), { help: t('setup.python_choose_help') }),
      newest && newest.version_tuple[1] >= 14 ? h('div.mt', callout('info', t('setup.py_new_hint', { v: newest.version }))) : null,
      h('div.row.mt',
        this.actionButton({ label: env.venv.exists ? t('setup.recreate_venv') : t('setup.create_venv'), icon: 'refresh', action: 'venv',
          params: () => ({ python: this.python }), confirm: env.venv.exists ? t('setup.recreate_q') : null }),
        button({ label: t('setup.rescan'), icon: 'refresh', cls: 'ghost', onClick: () => this.loadEnv(true) }),
        isLocal() ? button({ label: t('setup.open_venv'), icon: 'folder', cls: 'ghost', onClick: () => api.post('/api/open', { target: 'venv' }) }) : null));
  },

  /**
   * Hybrid extra card.
   * @param {Object} env Environment report.
   * @returns {HTMLElement} Card.
   */
  hybridCard(env) {
    const pkg = env.package;
    const p = pkg.packages || {};
    return h('div.card',
      h('div.card-head', h('h2', icon('zap'), t('setup.hybrid_extra')),
        pkg.hybrid ? h('span.badge.ok', t('setup.installed')) : h('span.badge', t('setup.not_installed'))),
      h('p.small.muted', t('setup.hybrid_text')),
      kvList([['docling', p.docling || '–'], ['torch', p.torch || '–'], ['easyocr', p.easyocr || '–'], ['fastapi / uvicorn', (p.fastapi || '–') + ' / ' + (p.uvicorn || '–')]]),
      h('div.field.mt', h('label', t('setup.extra_args')),
        textInput(this.extraArgs, (v) => { this.extraArgs = v; }, { ltr: true, placeholder: '--extra-index-url https://download.pytorch.org/whl/cu128' }),
        h('div.help', t('setup.extra_args_help'))),
      h('div.row.mt',
        this.actionButton({ label: pkg.hybrid ? t('setup.hybrid_reinstall') : t('setup.hybrid_install'), icon: 'download', cls: pkg.hybrid ? '' : 'primary',
          action: 'hybrid', params: () => ({ extra_args: this.extraArgs, python: this.python }), disabled: !pkg.installed })));
  },

  /**
   * OCR engines card.
   * @param {Object} env Environment report.
   * @returns {HTMLElement} Card.
   */
  ocrCard(env) {
    const p = env.package.packages || {};
    const row = (name, ok, detail, action) => h('div.check-row',
      h('div.state-icon', { class: ok ? 'ok' : 'muted' }, icon(ok ? 'check' : 'x')),
      h('div.grow', h('b', name), h('div.small.muted', detail)), action);
    return h('div.card',
      h('div.card-head', h('h2', icon('eye'), t('setup.ocr'))),
      h('p.small.muted', t('setup.ocr_text')),
      row('EasyOCR', !!p.easyocr, p.easyocr ? 'v' + p.easyocr + ' — ' + t('setup.ocr_easy') : t('setup.ocr_with_hybrid'), null),
      row('RapidOCR', !!p.rapidocr, p.rapidocr ? 'v' + p.rapidocr : t('setup.ocr_rapid'),
        this.actionButton({ label: t('setup.install'), icon: 'download', cls: 'sm', action: 'rapidocr', disabled: !env.package.hybrid })),
      row('Tesseract', !!env.tesseract, env.tesseract ? h('span.ltr', env.tesseract) : t('setup.ocr_tess'),
        env.windows ? this.actionButton({ label: t('setup.install_winget'), icon: 'package', cls: 'sm', action: 'tesseract', disabled: !env.winget }) : null),
      h('p.tiny.muted.mt', t('setup.ocr_lang_note')));
  },

  /**
   * System information card.
   * @param {Object} env Environment report.
   * @returns {HTMLElement} Card.
   */
  infoCard(env) {
    return h('div.card',
      h('div.card-head', h('h2', icon('info'), t('setup.system'))),
      kvList([
        [t('setup.os'), env.os],
        [t('setup.gui_python'), env.gui_python.version],
        ['Node.js', env.node || t('setup.not_found')],
        ['winget', env.windows ? (env.winget ? t('setup.available') : t('setup.not_found')) : '–'],
        [t('setup.data_dir'), h('span.ltr.small', env.paths.data)],
      ]),
      isLocal() ? h('div.row.mt',
        button({ label: t('setup.open_data'), icon: 'folder', cls: 'sm', onClick: () => api.post('/api/open', { target: 'data' }) }),
        button({ label: t('setup.open_logs'), icon: 'folder', cls: 'sm', onClick: () => api.post('/api/open', { target: 'logs' }) })) : null);
  },
};

/** Cached PyPI release list. */
let VERSION_CACHE = null;

/**
 * Release versions of opendataloader-pdf (newest first) from PyPI via the server.
 * @returns {Promise<string[]>} Versions.
 */
async function fetchVersions() {
  if (VERSION_CACHE) return VERSION_CACHE;
  try {
    const res = await api.get('/api/versions');
    VERSION_CACHE = res.versions || [];
  } catch (e) {
    VERSION_CACHE = [];
  }
  return VERSION_CACHE;
}

PAGES.setup = SetupPage;
