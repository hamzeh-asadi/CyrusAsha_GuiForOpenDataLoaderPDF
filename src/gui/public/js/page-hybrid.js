/* ==========================================================================
   CyrusAsha_GuiForOpenDataLoaderPDF - "Hybrid server" page
   Start/stop/restart the local AI backend (opendataloader-pdf-hybrid),
   configure every server option, use an external server, and watch the log.
   ========================================================================== */
'use strict';

/** OCR engines offered in the engine field (free text is allowed too). */
const OCR_ENGINES = ['easyocr', 'rapidocr', 'tesseract', 'tesserocr', 'auto', 'ocrmac'];

/** Example OCR language codes per engine (Persian + English). */
const OCR_LANG_EXAMPLES = { easyocr: 'fa,en', tesseract: 'fas,eng', tesserocr: 'fas,eng', rapidocr: 'english,arabic', ocrmac: 'fa-IR,en-US', auto: '' };

/**
 * Pill for a hybrid server state.
 * @param {string} state Server state.
 * @returns {HTMLElement} Pill.
 */
function hybridStatePill(state) {
  const cls = { ready: 'ok', external: 'ok', starting: 'info pulse', stopping: 'warn pulse', crashed: 'err', stopped: '' }[state] || '';
  return h('span.pill', { class: cls }, h('span.dot'), t('hstate.' + (state || 'stopped')));
}

const HybridPage = {
  /**
   * Build the page.
   * @param {HTMLElement} main Container.
   */
  mount(main) {
    this.root = h('div.page');
    main.appendChild(this.root);
    this.cfg = null;
    this.storage = null;
    mount(this.root, h('div.boot', t('common.loading')));
    loadSettings(true).then(() => this.render()).catch(showError);
    this.loadStorage();
  },

  /** Load where the models are stored (shown in the status card). */
  async loadStorage() {
    try {
      this.storage = await api.get('/api/storage');
    } catch (err) {
      return;
    }
    if (this.statusBox) this.renderStatus();
    if (this.storage.pending && this.root) setTimeout(() => this.loadStorage(), 1500);
  },

  /** @param {Object} state Application state. */
  update(state) {
    if (this.statusBox) this.renderStatus();
  },

  /** Stop the log poller. */
  unmount() {
    if (this.logView) this.logView.stop();
    this.root = null;
    this.statusBox = null;
  },

  /** Render the page. */
  render() {
    if (!this.root) return;
    this.cfg = Object.assign({}, S.settings.hybrid_server);
    this.statusBox = h('div');
    this.cmdBox = h('div.cmd-preview');
    this.formBox = h('div');
    this.logView = new LogView('/api/hybrid/log', { cls: 'tall', fileName: 'hybrid-server', interval: 1000,
      active: () => S.state && ['starting', 'ready', 'stopping'].includes(S.state.hybrid.state) });
    mount(this.root,
      h('div.page-head', h('div', h('h1', t('hybrid.title')), h('p', t('hybrid.subtitle')))),
      h('div.stack',
        this.statusBox,
        h('div.card', h('div.card-head', h('h2', icon('settings'), t('hybrid.config')),
          h('div.row',
            button({ label: t('common.save'), icon: 'save', onClick: () => this.save(false) }),
            button({ label: t('hybrid.save_restart'), icon: 'refresh', cls: 'primary', onClick: () => this.save(true) }))),
        this.formBox,
        h('div.mt', h('div.field-label.mb', icon('terminal', 'sm'), t('hybrid.command')), this.cmdBox)),
        this.externalCard(),
        h('div.card', h('div.card-head', h('h2', icon('terminal'), t('hybrid.log'))), this.logView.el),
        callout('info', h('b', t('hybrid.req_title')), h('ul', { style: { margin: '4px 0 0', paddingInlineStart: '18px' } },
          [1, 2, 3, 4].map((i) => h('li', t('hybrid.req' + i)))))));
    this.renderStatus();
    this.renderForm();
  },

  /** Status card with start/stop buttons. */
  renderStatus() {
    const hy = (S.state && S.state.hybrid) || {};
    const env = (S.state && S.state.env) || {};
    const running = ['starting', 'ready', 'stopping'].includes(hy.state);
    const uptime = hy.started && running ? fmtDuration((S.state.time || Date.now() / 1000) - hy.started) : null;
    const notInstalled = !env.loading && !env.hybrid;
    mount(this.statusBox, h('div.card',
      h('div.spread',
        h('div.row.nowrap',
          h('div.state-icon', { class: hy.state === 'ready' || hy.state === 'external' ? 'ok' : hy.state === 'crashed' ? 'err' : running ? 'info' : 'muted' },
            icon(hy.state === 'starting' || hy.state === 'stopping' ? 'loader' : 'cpu', hy.state === 'starting' || hy.state === 'stopping' ? 'spin' : '')),
          h('div',
            h('div.row', h('b', t('hybrid.server')), hybridStatePill(hy.state)),
            h('div.small.muted.row',
              h('span.ltr', hy.url || ''),
              hy.pid ? h('span', 'PID ' + hy.pid) : null,
              uptime ? h('span', t('hybrid.uptime', { t: uptime })) : null,
              hy.exit_code !== null && hy.exit_code !== undefined && hy.state === 'crashed' ? h('span', t('hybrid.exit_code', { n: hy.exit_code })) : null))),
        h('div.row',
          running || hy.state === 'external'
            ? button({ label: t('hybrid.stop'), icon: 'stop', cls: 'danger', onClick: () => this.action('stop') })
            : button({ label: t('hybrid.start'), icon: 'play', cls: 'primary', disabled: notInstalled, onClick: () => this.action('start') }),
          running ? button({ label: t('hybrid.restart'), icon: 'refresh', onClick: () => this.action('restart') }) : null)),
      hy.state === 'starting' ? h('div.mt', progressBar(null), h('div.small.muted.mt', t('hybrid.starting_text'))) : null,
      hy.state === 'crashed' ? h('div.mt', callout('err', t('hybrid.crashed_text'))) : null,
      notInstalled ? h('div.mt', callout('warn', h('b', t('hybrid.not_installed')), h('p', t('hybrid.not_installed_text')),
        button({ label: t('hybrid.install'), icon: 'download', cls: 'primary sm', onClick: () => go('setup') }))) : null,
      h('div.row.mt',
        switchInput(!!hy.auto_start, async (v) => { await saveSettings({ hybrid_auto_start: v }); refreshState(); }, t('hybrid.autostart')),
        h('span.small.muted', t('hybrid.autostart_help'))),
      this.modelsLine()));
  },

  /**
   * "Models are stored in <folder> (size) — change" line.
   * @returns {HTMLElement|null} Line (null until the storage report is loaded).
   */
  modelsLine() {
    const models = this.storage && this.storage.models;
    if (!models) return null;
    return h('div.small.mt.install-location',
      icon('drive', 'sm'), ' ', h('b', t('hybrid.models_here')), ' ', h('span.ltr', models.path),
      models.size ? ' · ' + t('hybrid.models_size', { size: fmtBytes(models.size) }) : '', ' — ',
      h('a', { href: '#setup', onclick: (e) => { e.preventDefault(); go('setup'); } }, t('storage.change')),
      models.warnings.length ? h('div.tiny', { style: { color: 'var(--warning)' } }, t('storage.warn.' + models.warnings[0])) : null);
  },

  /**
   * Start, stop or restart the server.
   * @param {"start"|"stop"|"restart"} what Action.
   */
  async action(what) {
    const body = what === 'stop' ? {} : { config: this.cfg };
    await api.post('/api/hybrid/' + what, body);
    if (what !== 'stop') await loadSettings(true);
    refreshState();
  },

  /**
   * Save the configuration (optionally restarting the server).
   * @param {boolean} restart Restart afterwards (or start if stopped).
   */
  async save(restart) {
    await saveSettings({ hybrid_server: this.cfg });
    toast(t('common.saved'), 'ok');
    if (restart) {
      const hy = S.state && S.state.hybrid;
      await api.post('/api/hybrid/' + (hy && ['starting', 'ready'].includes(hy.state) ? 'restart' : 'start'), {});
      refreshState();
    }
  },

  /**
   * Change a configuration value and refresh the command preview.
   * @param {string} key Config key.
   * @param {*} value New value.
   * @param {boolean} [redraw] Rebuild the form (for dependent fields).
   */
  set(key, value, redraw) {
    this.cfg[key] = value;
    if (redraw) this.renderForm(); else this.preview();
  },

  /** Debounced command preview. */
  preview: debounce(async function () {
    try {
      const res = await api.post('/api/hybrid/preview', { config: HybridPage.cfg });
      mount(HybridPage.cmdBox, res.command);
      HybridPage.cmdBox.style.color = '';
    } catch (err) {
      mount(HybridPage.cmdBox, errorText(err));
      HybridPage.cmdBox.style.color = 'var(--danger)';
    }
  }, 250),

  /** Configuration form with every server option. */
  renderForm() {
    const c = this.cfg;
    const ocrOff = c.ocr_mode === 'off';
    const tess = c.ocr_engine === 'tesseract' || c.ocr_engine === 'tesserocr';
    const engineInput = textInput(c.ocr_engine, (v) => this.set('ocr_engine', v.trim(), false), { ltr: true, disabled: ocrOff });
    engineInput.setAttribute('list', 'ocr-engines');
    engineInput.addEventListener('change', () => this.renderForm());
    const example = OCR_LANG_EXAMPLES[c.ocr_engine];
    mount(this.formBox,
      h('datalist', { id: 'ocr-engines' }, OCR_ENGINES.map((e) => h('option', { value: e }))),
      section('hy-net', [icon('globe', 'sm'), t('hsec.network')], h('div.fields',
        field(t('hsrv.host'), textInput(c.host, (v) => this.set('host', v.trim()), { ltr: true }), { flag: '--host', help: t('hsrv.host.help') }),
        field(t('hsrv.port'), textInput(c.port, (v) => this.set('port', parseInt(v, 10) || 0), { type: 'number', ltr: true, min: 1, max: 65535 }), { flag: '--port' }),
        field(t('hsrv.log_level'), selectInput(['debug', 'info', 'warning', 'error'], c.log_level, (v) => this.set('log_level', v)), { flag: '--log-level' }),
        field(t('hsrv.device'), selectInput(['auto', 'cpu', 'cuda', 'mps', 'xpu'].map((d) => ({ value: d, label: t('val.device.' + d) })), c.device, (v) => this.set('device', v)), { flag: '--device', help: t('hsrv.device.help') }),
        field(t('hsrv.max_file_size'), textInput(c.max_file_size, (v) => this.set('max_file_size', parseInt(v, 10) || 0), { type: 'number', ltr: true, min: 0 }), { flag: '--max-file-size', help: t('hsrv.max_file_size.help') })), true),
      section('hy-ocr', [icon('eye', 'sm'), t('hsec.ocr')], h('div.fields',
        field(t('hsrv.ocr_mode'), selectInput(['default', 'force', 'off'].map((m) => ({ value: m, label: t('val.ocr_' + m) })), c.ocr_mode,
          (v) => this.set('ocr_mode', v, true)), { help: t('hsrv.ocr_mode.help'), flag: '--force-ocr / --no-ocr' }),
        field(t('hsrv.ocr_engine'), engineInput, { flag: '--ocr-engine', help: t('hsrv.ocr_engine.help') }),
        field(t('hsrv.ocr_lang'), textInput(c.ocr_lang, (v) => this.set('ocr_lang', v), { ltr: true, disabled: ocrOff, placeholder: example || '' }),
          { flag: '--ocr-lang', help: t('hsrv.ocr_lang.help') + (example ? ' ' + t('hsrv.ocr_lang.example', { ex: example }) : '') }),
        field(t('hsrv.psm'), textInput(c.psm, (v) => this.set('psm', v.trim()), { type: 'number', ltr: true, min: 0, max: 13, disabled: ocrOff || !tess }),
          { flag: '--psm', help: t('hsrv.psm.help') })), true),
      section('hy-enrich', [icon('wand', 'sm'), t('hsec.enrich')], h('div.fields',
        h('div.field', switchInput(c.enrich_formula, (v) => this.set('enrich_formula', v), h('b', t('hsrv.enrich_formula'))), h('div.help', t('hsrv.enrich_formula.help'))),
        h('div.field', switchInput(c.enrich_picture_description, (v) => this.set('enrich_picture_description', v, true), h('b', t('hsrv.enrich_picture_description'))), h('div.help', t('hsrv.enrich_picture_description.help'))),
        h('div.field', switchInput(c.heading_hierarchy, (v) => this.set('heading_hierarchy', v), h('b', t('hsrv.heading_hierarchy'))), h('div.help', t('hsrv.heading_hierarchy.help'))),
        field(t('hsrv.picture_area_threshold'), textInput(c.picture_area_threshold, (v) => this.set('picture_area_threshold', parseFloat(v) || 0),
          { type: 'number', ltr: true, min: 0, max: 1, step: 0.01, disabled: !c.enrich_picture_description }), { flag: '--picture-area-threshold', help: t('hsrv.picture_area_threshold.help') }),
        field(t('hsrv.picture_description_prompt'), textInput(c.picture_description_prompt, (v) => this.set('picture_description_prompt', v),
          { disabled: !c.enrich_picture_description }), { flag: '--picture-description-prompt', wide: true })),
      false),
      c.enrich_formula || c.enrich_picture_description ? h('div.mt', callout('info', t('hybrid.full_mode_hint'))) : null);
    this.preview();
  },

  /** Card for using an external hybrid server instead of the managed one. */
  externalCard() {
    let url = S.settings.hybrid_external_url || '';
    const input = textInput(url, (v) => { url = v.trim(); }, { ltr: true, placeholder: 'http://192.168.1.20:5002' });
    return h('div.card', h('div.card-head', h('h2', icon('link'), t('hybrid.external'))),
      h('p.muted.small', t('hybrid.external_text')),
      h('div.input-group', input,
        button({ label: t('hybrid.test'), icon: 'zap', onClick: async () => {
          if (!url) return;
          const res = await api.post('/api/hybrid/check', { url });
          toast(res.ok ? t('hybrid.test_ok') : t('hybrid.test_fail'), res.ok ? 'ok' : 'err');
        } }),
        button({ label: t('common.save'), icon: 'save', cls: 'primary', onClick: async () => {
          await saveSettings({ hybrid_external_url: url });
          toast(t('common.saved'), 'ok');
        } })));
  },
};

PAGES.hybrid = HybridPage;
