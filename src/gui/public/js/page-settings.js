/* ==========================================================================
   CyrusAsha_GuiForOpenDataLoaderPDF - "Settings" and "Help" pages
   Settings are saved automatically on change (validated by the server).
   ========================================================================== */
'use strict';

const SettingsPage = {
  /**
   * Build the page.
   * @param {HTMLElement} main Container.
   */
  mount(main) {
    this.root = h('div.page');
    main.appendChild(this.root);
    mount(this.root, h('div.boot', t('common.loading')));
    loadSettings(true).then(() => this.render()).catch(showError);
  },

  /** Nothing to update from the global state. */
  update() {},

  /** Forget references. */
  unmount() { this.root = null; },

  /**
   * Save one setting and show a short confirmation.
   * @param {string} key Setting name.
   * @param {*} value New value.
   */
  async set(key, value) {
    try {
      await saveSettings({ [key]: value });
      toast(t('common.saved'), 'ok', 1500);
    } catch (err) {
      showError(err);
    }
  },

  /** Render all setting groups. */
  render() {
    if (!this.root) return;
    const s = S.settings;
    const saveLater = debounce((key, value) => this.set(key, value), 700);
    mount(this.root,
      h('div.page-head', h('div', h('h1', t('settings.title')), h('p', t('settings.subtitle')))),
      h('div.stack',
        h('div.card', h('div.card-head', h('h2', icon('globe'), t('settings.general'))),
          h('div.fields',
            field(t('settings.language'), selectInput([{ value: 'fa', label: 'فارسی' }, { value: 'en', label: 'English' }], getLang(), (v) => setLang(v))),
            field(t('settings.theme'), selectInput(['auto', 'light', 'dark'].map((v) => ({ value: v, label: t('theme.' + v) })), getTheme(), (v) => setTheme(v))),
            field(t('settings.open_mode'), selectInput([{ value: 'app', label: t('settings.open_app') }, { value: 'browser', label: t('settings.open_browser') }],
              s.ui_open_mode, (v) => this.set('ui_open_mode', v)), { help: t('settings.open_mode_help') })),
          h('div.col.mt',
            switchInput(s.notify_on_finish, (v) => { this.set('notify_on_finish', v); if (v) requestNotifyPermission(); }, t('settings.notify')),
            switchInput(s.open_folder_on_finish, (v) => this.set('open_folder_on_finish', v), t('settings.open_folder')))),

        h('div.card', h('div.card-head', h('h2', icon('cpu'), t('settings.conversion'))),
          h('div.fields',
            field(t('settings.parallel'), textInput(s.max_parallel_jobs, (v) => saveLater('max_parallel_jobs', parseInt(v, 10) || 1), { type: 'number', min: 1, max: 8, ltr: true }),
              { help: t('settings.parallel_help') }),
            field(t('settings.jvm_memory'), textInput(s.jvm_max_memory_mb, (v) => saveLater('jvm_max_memory_mb', parseInt(v, 10) || 0), { type: 'number', min: 0, step: 512, ltr: true }),
              { help: t('settings.jvm_memory_help') }),
            field(t('settings.default_output'), pathInput(s.default_output_dir, (v) => saveLater('default_output_dir', v), 'folder', { placeholder: t('settings.default_output_ph') }),
              { help: t('settings.default_output_help'), wide: true })),
          h('div.col.mt', switchInput(s.delete_uploads_after_job, (v) => this.set('delete_uploads_after_job', v), t('settings.delete_uploads')))),

        h('div.card', h('div.card-head', h('h2', icon('link'), t('settings.network'))),
          h('p.small.muted', t('settings.network_text')),
          h('div.fields',
            field(t('settings.proxy'), textInput(s.proxy, (v) => saveLater('proxy', v.trim()), { ltr: true, placeholder: 'http://127.0.0.1:8080' }), { help: t('settings.proxy_help') }),
            field(t('settings.pip_index'), textInput(s.pip_index_url, (v) => saveLater('pip_index_url', v.trim()), { ltr: true, placeholder: 'https://pypi.org/simple' }), { flag: '--index-url' }),
            field(t('settings.pip_extra'), textInput(s.pip_extra_index_url, (v) => saveLater('pip_extra_index_url', v.trim()), { ltr: true }), { flag: '--extra-index-url' }),
            field(t('settings.pip_trusted'), textInput(s.pip_trusted_host, (v) => saveLater('pip_trusted_host', v.trim()), { ltr: true, placeholder: 'mirror.example.com' }), { flag: '--trusted-host' }),
            field(t('settings.java_url'), textInput(s.java_download_url, (v) => saveLater('java_download_url', v.trim()), { ltr: true, placeholder: 'https://…/OpenJDK21U-jre_x64_windows.zip' }),
              { help: t('settings.java_url_help'), wide: true }))),

        h('div.card', h('div.card-head', h('h2', icon('drive'), t('settings.storage'))),
          kvList(Object.entries(S.paths || {}).map(([k, v]) => [t('path.' + k), h('span.ltr.small', v)])),
          h('div.row.mt',
            button({ label: t('settings.change_locations'), icon: 'sliders', cls: 'sm primary', onClick: () => go('setup') }),
            isLocal() ? button({ label: t('setup.open_data'), icon: 'folder', cls: 'sm', onClick: () => api.post('/api/open', { target: 'data' }) }) : null,
            button({ label: t('settings.clear_uploads'), icon: 'trash', cls: 'sm', onClick: async () => {
              const res = await api.post('/api/uploads/clear');
              toast(t('settings.uploads_cleared', { n: fmtNum(res.removed) }), 'ok');
            } }),
            button({ label: t('jobs.clear'), icon: 'trash', cls: 'sm danger', onClick: () => JobsPage.clearFinished() }))),

        isLocal() && S.windows ? h('div.card', h('div.card-head', h('h2', icon('external'), t('settings.shortcut'))),
          h('p.small.muted', t('settings.shortcut_text')),
          button({ label: t('settings.shortcut_create'), icon: 'plus', onClick: async () => {
            await api.post('/api/shortcut');
            toast(t('settings.shortcut_done'), 'ok');
          } })) : null));
  },
};

/* ===================================================================== */
const HelpPage = {
  /**
   * Build the help page.
   * @param {HTMLElement} main Container.
   */
  mount(main) {
    this.root = h('div.page');
    main.appendChild(this.root);
    const env = (S.state && S.state.env) || {};
    const modeRows = [
      ['help.mode.simple', 'help.mode.simple_how'], ['help.mode.tables', 'help.mode.tables_how'], ['help.mode.scanned', 'help.mode.scanned_how'],
      ['help.mode.formula', 'help.mode.formula_how'], ['help.mode.charts', 'help.mode.charts_how'], ['help.mode.tagged', 'help.mode.tagged_how'],
      ['help.mode.sensitive', 'help.mode.sensitive_how'], ['help.mode.persian', 'help.mode.persian_how'],
    ];
    this.logView = new LogView('/api/logs/app', { cls: 'short', fileName: 'CyrusAsha_GuiForOpenDataLoaderPDF-app', interval: 3000, active: () => false });
    mount(this.root,
      h('div.page-head', h('div', h('h1', t('help.title')), h('p', t('help.subtitle')))),
      h('div.stack',
        h('div.card', h('div.card-head', h('h2', icon('play'), t('help.quick'))),
          h('ol', { style: { margin: 0, paddingInlineStart: '20px' } }, [1, 2, 3, 4, 5].map((i) => h('li', t('help.quick' + i))))),
        h('div.card', h('div.card-head', h('h2', icon('sliders'), t('help.modes'))),
          h('div.table-wrap', h('table.table.help-table', h('thead', h('tr', h('th', t('help.col.need')), h('th', t('help.col.how')))),
            h('tbody', modeRows.map(([a, b]) => h('tr', h('td', t(a)), h('td', t(b)))))))),
        h('div.grid-2',
          h('div.card', h('div.card-head', h('h2', icon('file'), t('help.formats'))),
            h('ul', { style: { margin: 0, paddingInlineStart: '20px' } }, ['json', 'markdown', 'html', 'text', 'pdf', 'tagged-pdf'].map((f) => h('li', h('b', choiceLabel('format', f)), ' — ', t('help.fmt.' + f))))),
          h('div.card', h('div.card-head', h('h2', icon('terminal'), t('help.shortcuts'))),
            h('ul.kbd-list', { style: { margin: 0, paddingInlineStart: '20px' } },
              h('li', h('kbd', 'Ctrl'), ' + ', h('kbd', 'Enter'), ' — ', t('help.kb.start')),
              h('li', h('kbd', 'Ctrl'), ' + ', h('kbd', 'O'), ' — ', t('help.kb.open')),
              h('li', h('kbd', 'Alt'), ' + ', h('kbd', '1…7'), ' — ', t('help.kb.pages')),
              h('li', t('help.kb.drop'))))),
        h('div.card', h('div.card-head', h('h2', icon('alert'), t('help.faq'))),
          [1, 2, 3, 4, 5, 6].map((i) => h('details.section', h('summary', t('help.faq' + i + '.q')), h('div.section-body', h('p', t('help.faq' + i + '.a')))))),
        h('div.card', h('div.card-head', h('h2', icon('info'), t('help.about'))),
          h('img.about-logo', { src: BRAND.logoFull, alt: 'CyrusAsha' }),
          h('div.about-tagline', BRAND.tagline),
          h('p.muted', { style: { textAlign: 'center' } }, t('help.about_text')),
          this.aboutBox = h('div'),
          h('div.row.mt',
            BRAND.links.slice(0, 2).map((link) => h('a.btn.sm', { href: link.href, target: '_blank', rel: 'noopener' }, icon(link.icon, 'sm'), link.label)),
            h('a.btn.sm', { href: 'https://github.com/opendataloader-project/opendataloader-pdf', target: '_blank', rel: 'noopener' }, icon('external', 'sm'), 'opendataloader-pdf'),
            h('a.btn.sm', { href: 'https://opendataloader.org/docs', target: '_blank', rel: 'noopener' }, icon('external', 'sm'), t('help.docs')),
            h('a.btn.sm', { href: 'https://pypi.org/project/opendataloader-pdf/', target: '_blank', rel: 'noopener' }, icon('external', 'sm'), 'PyPI'))),
        h('div.card', h('div.card-head', h('h2', icon('terminal'), t('help.applog'))), this.logView.el)));
    this.aboutKey = null;
    this.renderAbout(env);
  },

  /**
   * Refresh the version list when the environment report arrives or changes.
   * @param {Object} state Application state.
   */
  update(state) {
    const env = (state && state.env) || {};
    const key = [env.version, env.java_version, env.loading].join('|');
    if (key !== this.aboutKey) this.renderAbout(env);
  },

  /**
   * Versions of the GUI, opendataloader-pdf and Java.
   * @param {Object} env Brief environment report from the state.
   */
  renderAbout(env) {
    if (!this.aboutBox) return;
    this.aboutKey = [env.version, env.java_version, env.loading].join('|');
    const pending = !S.state || env.loading;
    mount(this.aboutBox, kvList([
      [BRAND.product, (S.state && S.state.version) || BRAND.version],
      ['opendataloader-pdf', pending ? t('common.loading') : (env.version || t('setup.not_installed'))],
      ['Java', pending ? t('common.loading') : (env.java_version || '–')],
      [t('help.license'), 'MIT © CyrusAsha · opendataloader-pdf: Apache-2.0'],
    ]));
  },

  /** Stop the log poller. */
  unmount() {
    if (this.logView) this.logView.stop();
    this.root = null;
    this.aboutBox = null;
    this.aboutKey = null;
  },
};

PAGES.settings = SettingsPage;
PAGES.help = HelpPage;
