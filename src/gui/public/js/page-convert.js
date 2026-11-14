/* ==========================================================================
   CyrusAsha_GuiForOpenDataLoaderPDF - "Convert" page
   Input list (drag & drop, browser upload, native picker, typed paths),
   presets, every CLI option, output folder, live command preview and the
   start button.
   ========================================================================== */
'use strict';

/** Default form used on first start (RAG preset: Markdown + JSON). */
const DEFAULT_FORM = { preset: 'rag', options: { format: ['markdown', 'json'] }, outputMode: 'job', outputDir: '', name: '', modified: false };

/** Persistent state of the convert page (survives navigation and language switches). */
const CV = {
  inputs: [],
  form: loadForm(),
  previewSeq: 0,
  previewError: null,
  command: '',
  uploadQueue: [],
  uploading: 0,
  showCommand: localStorage.getItem('odl.showCommand') === '1',
};

/**
 * Load the saved form from localStorage (passwords are never stored).
 * @returns {Object} Form state.
 */
function loadForm() {
  try {
    const saved = JSON.parse(localStorage.getItem('odl.form') || 'null');
    if (saved && saved.options) return Object.assign({}, DEFAULT_FORM, saved);
  } catch (e) { /* ignore broken storage */ }
  return JSON.parse(JSON.stringify(DEFAULT_FORM));
}

/** Save the form to localStorage (without the password). */
function saveForm() {
  const copy = JSON.parse(JSON.stringify(CV.form));
  delete copy.options.password;
  localStorage.setItem('odl.form', JSON.stringify(copy));
}

/**
 * Translated label of a CLI option (falls back to the option name).
 * @param {string} name Option name.
 * @returns {string} Label.
 */
function optLabel(name) {
  return hasKey('opt.' + name) ? t('opt.' + name) : name;
}

/**
 * Translated help text of an option (falls back to the CLI description).
 * @param {Object} spec Option specification.
 * @returns {string} Help text.
 */
function optHelp(spec) {
  return hasKey('opt.' + spec.name + '.help') ? t('opt.' + spec.name + '.help') : (spec.description || '');
}

/** Options handled by the GUI itself (also known before the option metadata is loaded). */
const GUI_ONLY_OPTIONS = ['rtl-fix'];

/**
 * Flag shown next to an option: `--name` for CLI options, a "GUI" marker for
 * options the GUI handles itself (they never reach the command line).
 * @param {Object} spec Option specification (or `{name}`).
 * @returns {string} Flag text.
 */
function optFlag(spec) {
  const meta = S.optionsMeta && (S.optionsMeta.spec || []).find((o) => o.name === spec.name);
  return (spec.gui || (meta && meta.gui) || GUI_ONLY_OPTIONS.includes(spec.name)) ? t('opt.gui_flag') : '--' + spec.name;
}

/**
 * Translated label of a choice value.
 * @param {string} name Option name.
 * @param {string} value Choice value.
 * @returns {string} Label.
 */
function choiceLabel(name, value) {
  const key = 'val.' + name + '.' + value;
  return hasKey(key) ? t(key) : (hasKey('val.' + value) ? t('val.' + value) : value);
}

/* ----------------------------------------------------------------- inputs */
/**
 * Generate a random id for upload batches and list items.
 * @param {string} prefix Prefix.
 * @returns {string} Id.
 */
function randomId(prefix) {
  return prefix + Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
}

/**
 * Add local paths (picked natively or typed) after asking the server about them.
 * @param {string[]} paths Absolute paths.
 */
async function addLocalPaths(paths) {
  if (!paths.length) return;
  const infos = await api.post('/api/inspect', { paths });
  let added = 0;
  for (const info of infos) {
    if (info.error && info.error !== 'no_pdfs') {
      toast(t('input.err.' + info.error, { path: info.path }), 'warn', 7000);
      continue;
    }
    if (info.error === 'no_pdfs') toast(t('input.err.no_pdfs', { path: info.path }), 'warn', 7000);
    if (CV.inputs.some((i) => i.path === info.path)) continue;
    CV.inputs.push(Object.assign({ key: randomId('i'), status: 'ready', progress: 1 }, info));
    added++;
  }
  if (added) toast(t('input.added', { n: fmtNum(added) }), 'ok');
  ConvertPage.renderInputs();
}

/**
 * Collect files from a drop event. Must be called synchronously inside the
 * event handler because the DataTransfer is cleared afterwards.
 * @param {DataTransfer} dt Drop data.
 * @returns {{entries: Array, files: File[]}} Entries (for folder support) and plain files.
 */
function captureDrop(dt) {
  const entries = [];
  if (dt.items) {
    for (const item of dt.items) {
      if (item.kind !== 'file') continue;
      const entry = item.webkitGetAsEntry ? item.webkitGetAsEntry() : null;
      if (entry) entries.push(entry);
    }
  }
  return { entries, files: Array.from(dt.files || []) };
}

/**
 * Read a FileSystemFileEntry as File.
 * @param {FileSystemFileEntry} entry Entry.
 * @returns {Promise<File>} File.
 */
function entryFile(entry) {
  return new Promise((resolve, reject) => entry.file(resolve, reject));
}

/**
 * Recursively collect files below a dropped entry.
 * @param {FileSystemEntry} entry File or directory entry.
 * @param {string} prefix Relative folder path.
 * @param {Array<{file: File, rel: string}>} out Result list.
 */
async function walkEntry(entry, prefix, out) {
  if (entry.isFile) {
    out.push({ file: await entryFile(entry), rel: prefix + entry.name });
  } else if (entry.isDirectory) {
    const reader = entry.createReader();
    let batch;
    do {
      batch = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));
      for (const child of batch) await walkEntry(child, prefix + entry.name + '/', out);
    } while (batch.length);
  }
}

/**
 * Handle dropped files/folders: create list items and queue the uploads.
 * @param {{entries: Array, files: File[]}} drop Captured drop data.
 */
async function handleDrop(drop) {
  const groups = []; // {label, kind, files: [{file, rel}]}
  if (drop.entries.length) {
    for (const entry of drop.entries) {
      const files = [];
      await walkEntry(entry, '', files);
      groups.push({ label: entry.name, kind: entry.isDirectory ? 'folder' : 'file', files });
    }
  } else {
    for (const file of drop.files) groups.push({ label: file.name, kind: 'file', files: [{ file, rel: file.name }] });
  }
  queueUploadGroups(groups);
}

/**
 * Handle files chosen with the browser's file input.
 * @param {FileList} fileList Selected files.
 */
function handleFileInput(fileList) {
  queueUploadGroups(Array.from(fileList).map((file) => ({ label: file.name, kind: 'file', files: [{ file, rel: file.name }] })));
}

/**
 * Create input items for upload groups and start uploading.
 * @param {Array<{label: string, kind: string, files: Array}>} groups Groups.
 */
function queueUploadGroups(groups) {
  let skipped = 0;
  const batch = randomId('b');
  for (const group of groups) {
    const pdfs = group.files.filter((f) => /\.pdf$/i.test(f.file.name));
    skipped += group.files.length - pdfs.length;
    if (!pdfs.length) continue;
    const item = {
      key: randomId('i'), label: group.label, kind: group.kind, source: 'upload', status: 'uploading',
      path: '', pdfs: pdfs.length, size: pdfs.reduce((s, f) => s + f.file.size, 0), progress: 0, loaded: 0, pending: pdfs.length,
    };
    CV.inputs.push(item);
    for (const f of pdfs) CV.uploadQueue.push({ item, file: f.file, rel: f.rel, batch, loaded: 0 });
  }
  if (skipped) toast(t('input.skipped', { n: fmtNum(skipped) }), 'warn', 6000);
  ConvertPage.renderInputs();
  pumpUploads();
}

/** Start queued uploads (two at a time). */
function pumpUploads() {
  while (CV.uploading < 2 && CV.uploadQueue.length) {
    const job = CV.uploadQueue.shift();
    if (job.item.status === 'removed') continue;
    CV.uploading++;
    api.upload(job.file, job.batch, job.rel, (fraction) => {
      const previous = job.loaded;
      job.loaded = fraction * job.file.size;
      job.item.loaded += job.loaded - previous;
      job.item.progress = job.item.size ? job.item.loaded / job.item.size : 1;
      if (job.item.barEl) job.item.barEl.style.width = (job.item.progress * 100).toFixed(1) + '%';
    }).then((res) => {
      job.item.pending--;
      if (job.item.kind === 'file' || !job.item.path) job.item.path = res.root;
      if (job.item.pending <= 0 && job.item.status === 'uploading') {
        job.item.status = 'ready';
        job.item.progress = 1;
      }
    }).catch((err) => {
      job.item.status = 'error';
      job.item.error = errorText(err);
    }).finally(() => {
      CV.uploading--;
      ConvertPage.renderInputs();
      pumpUploads();
    });
  }
}

/* ------------------------------------------------------------------- page */
const ConvertPage = {
  /**
   * Build the page.
   * @param {HTMLElement} main Container.
   */
  mount(main) {
    this.root = h('div.page');
    main.appendChild(this.root);
    this.fileInput = h('input', { type: 'file', accept: '.pdf,application/pdf', multiple: true, hidden: true });
    this.fileInput.addEventListener('change', () => { handleFileInput(this.fileInput.files); this.fileInput.value = ''; });
    mount(this.root, h('div.boot', t('common.loading')));
    this.load();
  },

  /** Load metadata (options, presets, rules, settings) and render. */
  async load() {
    try {
      await Promise.all([loadOptionsMeta(), loadPresets(), loadRules(), loadSettings()]);
    } catch (err) {
      showError(err);
    }
    if (!this.root || !this.root.isConnected) return;
    this.render();
  },

  /** Render the complete page. */
  render() {
    this.banner = h('div');
    this.inputsBox = h('div');
    this.optionsBox = h('div');
    this.outputBox = h('div');
    this.actionBar = h('div.action-bar');
    mount(this.root,
      h('div.page-head', h('div', h('h1', t('convert.title')), h('p', t('convert.subtitle')))),
      this.banner,
      h('div.stack',
        h('div.card', h('div.card-head', h('h2', icon('file'), t('convert.inputs'))), this.dropzone(), this.inputsBox),
        h('div.card', h('div.card-head', h('h2', icon('sliders'), t('convert.options'))), this.optionsBox),
        h('div.card', h('div.card-head', h('h2', icon('folder'), t('convert.output'))), this.outputBox)),
      this.actionBar,
      this.fileInput);
    this.renderInputs();
    this.renderOptions();
    this.renderOutput();
    this.renderBanner();
    this.renderActions();
    this.schedulePreview();
  },

  /**
   * Called on every state poll.
   * @param {Object} state Application state.
   */
  update(state) {
    if (!this.actionBar) return;
    this.renderBanner();
    this.renderActions();
    if (this.hybridStatusEl) mount(this.hybridStatusEl, this.hybridStatus());
  },

  /** Release references when leaving the page. */
  unmount() {
    this.root = null;
    this.actionBar = null;
  },

  /** Readiness warnings (not installed, Java missing, installer running). */
  renderBanner() {
    if (!this.banner) return;
    const env = (S.state && S.state.env) || {};
    let content = null;
    if (env.loading) {
      content = callout('info', t('convert.checking'));
    } else if (!env.installed) {
      content = callout('warn', h('b', t('convert.not_installed')), h('p', t('convert.not_installed_text')),
        button({ label: t('convert.go_setup'), icon: 'package', cls: 'primary sm', onClick: () => go('setup') }));
    } else if (!env.java_ok) {
      content = callout('err', h('b', t('convert.no_java')), h('p', t('convert.no_java_text')),
        button({ label: t('convert.go_setup'), icon: 'package', cls: 'primary sm', onClick: () => go('setup') }));
    } else if (S.state.setup && S.state.setup.state === 'running') {
      content = callout('info', t('convert.setup_running'));
    }
    mount(this.banner, content ? h('div.mb', content) : null);
  },

  /** The drag & drop area with the add buttons. */
  dropzone() {
    const zone = h('div.dropzone',
      icon('upload', 'xl'),
      h('h3', t('drop.title')),
      h('div.muted', t('drop.subtitle')),
      h('div.row',
        button({ label: t('drop.browse'), icon: 'upload', cls: 'primary', onClick: () => this.fileInput.click() }),
        isLocal() ? button({ label: t('drop.pick_files'), icon: 'file', onClick: async () => addLocalPaths(await pickNative('files', t('drop.pick_files'))) }) : null,
        isLocal() ? button({ label: t('drop.pick_folder'), icon: 'folder', onClick: async () => addLocalPaths(await pickNative('folder', t('drop.pick_folder'))) }) : null),
      h('div.row.mt', this.pathAdder()),
      isLocal() ? h('div.tiny.muted.mt', t('drop.tip')) : null);
    zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('over'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('over'));
    zone.addEventListener('drop', () => zone.classList.remove('over')); // the global handler does the work
    return zone;
  },

  /** Small "type a path" input. */
  pathAdder() {
    const input = textInput('', null, { ltr: true, placeholder: t('drop.path_placeholder') });
    input.style.maxWidth = '460px';
    const add = async () => {
      const value = input.value.trim();
      if (!value) return;
      await addLocalPaths(value.split(/\r?\n|;/).map((s) => s.trim()).filter(Boolean));
      input.value = '';
    };
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); add().catch(showError); } });
    return h('div.input-group', { style: { width: 'min(560px, 100%)' } }, input, button({ label: t('drop.add_path'), icon: 'plus', onClick: add }));
  },

  /** Render the list of inputs. */
  renderInputs() {
    if (!this.inputsBox || !this.inputsBox.isConnected) return;
    if (!CV.inputs.length) { mount(this.inputsBox); this.renderActions(); this.schedulePreview(); return; }
    const totalPdfs = CV.inputs.reduce((s, i) => s + (i.pdfs || 0), 0);
    const totalSize = CV.inputs.reduce((s, i) => s + (i.size || 0), 0);
    mount(this.inputsBox, h('div.file-list.mt',
      CV.inputs.map((item) => this.inputRow(item)),
      h('div.list-foot',
        h('span', t('input.summary', { inputs: fmtNum(CV.inputs.length), pdfs: fmtNum(totalPdfs), size: fmtBytes(totalSize) })),
        button({ label: t('input.clear'), icon: 'trash', cls: 'sm ghost', onClick: () => {
          CV.inputs.forEach((i) => { i.status = 'removed'; });
          CV.inputs = [];
          this.renderInputs();
        } }))));
    this.renderActions();
    this.schedulePreview();
  },

  /**
   * One row of the input list.
   * @param {Object} item Input item.
   * @returns {HTMLElement} Row.
   */
  inputRow(item) {
    const bar = progressBar(item.progress, 'thin' + (item.status === 'error' ? ' err' : ''));
    item.barEl = bar.firstChild;
    const badges = [
      h('span.badge', { class: item.source === 'upload' ? 'info' : '' }, t(item.source === 'upload' ? 'input.uploaded' : 'input.local')),
      item.kind === 'folder' ? h('span.badge', t('input.pdf_count', { n: fmtNum(item.pdfs) })) : null,
      h('span.badge', fmtBytes(item.size)),
    ];
    return h('div.file-item',
      h('div.icon-box', { class: item.kind === 'folder' ? 'folder' : '' }, icon(item.kind === 'folder' ? 'folder' : 'file')),
      h('div.meta',
        h('div.row.nowrap', h('span.name.ellipsis', { dir: 'auto' }, item.label), badges),
        item.path ? h('div.path.ellipsis.ltr', item.path) : null,
        item.status === 'uploading' ? bar : null,
        item.status === 'error' ? h('div.small', { style: { color: 'var(--danger)' } }, item.error) : null),
      item.status === 'uploading' ? h('span.small.muted', t('input.uploading')) : null,
      button({ icon: 'x', cls: 'ghost sm', title: t('common.remove'), onClick: () => {
        item.status = 'removed';
        CV.inputs = CV.inputs.filter((i) => i !== item);
        this.renderInputs();
      } }));
  },

  /* ------------------------------------------------------------- options */
  /** Render the preset bar and the option sections. */
  renderOptions() {
    if (!this.optionsBox) return;
    const meta = S.optionsMeta;
    if (!meta) { mount(this.optionsBox, h('div.muted', t('common.loading'))); return; }
    const presets = S.presets || [];
    const builtin = presets.filter((p) => p.builtin);
    const user = presets.filter((p) => !p.builtin);
    const presetName = (p) => (p.name && (p.name[getLang()] || p.name.en)) || p.id;
    const presetSelect = h('select', { style: { maxWidth: '360px' } },
      h('option', { value: '', selected: !CV.form.preset }, t('preset.none')),
      h('optgroup', { label: t('preset.builtin') }, builtin.map((p) => h('option', { value: p.id, selected: p.id === CV.form.preset }, presetName(p)))),
      user.length ? h('optgroup', { label: t('preset.user') }, user.map((p) => h('option', { value: p.id, selected: p.id === CV.form.preset }, presetName(p)))) : null);
    presetSelect.addEventListener('change', () => this.applyPreset(presetSelect.value));
    const current = presets.find((p) => p.id === CV.form.preset);

    const groups = {};
    this.countBadges = {};
    for (const spec of meta.spec.concat(meta.extra)) (groups[spec.group] = groups[spec.group] || []).push(spec);
    const sections = meta.groups.filter((g) => groups[g] && groups[g].length).map((g) => this.optionSection(g, groups[g], meta));

    mount(this.optionsBox,
      h('div.row.mb',
        h('b', t('preset.label')), presetSelect,
        this.modifiedBadge = h('span.badge.warn', { hidden: !(CV.form.modified && current) }, t('preset.modified')),
        h('span.grow'),
        current ? button({ label: t('preset.reset'), icon: 'rerun', cls: 'sm', onClick: () => this.applyPreset(current.id) }) : null,
        button({ label: t('preset.save'), icon: 'save', cls: 'sm', onClick: () => this.savePreset() }),
        current && !current.builtin ? button({ label: t('common.delete'), icon: 'trash', cls: 'sm danger', onClick: () => this.deletePreset(current) }) : null),
      current && current.server ? this.presetServerHint(current) : null,
      meta.version ? null : h('div.mb', callout('info', t('opt.no_meta'))),
      sections);
  },

  /**
   * One collapsible group of options.
   * @param {string} group Group key.
   * @param {Object[]} specs Options in the group.
   * @param {Object} meta Option metadata (for unsupported options).
   * @returns {HTMLElement} Section.
   */
  optionSection(group, specs, meta) {
    const count = specs.filter((s) => this.isSet(s)).length;
    const badge = h('span.badge.primary.count-badge', { hidden: !count }, fmtNum(count));
    this.countBadges[group] = { el: badge, specs };
    const titleIcons = { output: 'file', text: 'convert', layout: 'jobs', images: 'image', separators: 'code', safety: 'shield', hybrid: 'zap', performance: 'cpu', other: 'sliders' };
    const body = [];
    if (group === 'hybrid') body.push(this.hybridIntro());
    const disabledHybrid = group === 'hybrid' && (CV.form.options.hybrid || 'off') === 'off';
    body.push(h('div.fields', specs.map((spec) => this.optionField(spec, meta.unsupported.includes(spec.name),
      disabledHybrid && spec.name !== 'hybrid'))));
    return section('opt-' + group,
      [icon(titleIcons[group] || 'sliders', 'sm'), t('group.' + group), badge],
      body, group === 'output');
  },

  /**
   * True if an option differs from its default.
   * @param {Object} spec Option specification.
   * @returns {boolean} Set by the user.
   */
  isSet(spec) {
    const v = CV.form.options[spec.name];
    if (v === undefined || v === null || v === '' || v === false) return false;
    if (Array.isArray(v)) return spec.name === 'format' ? true : v.length > 0;
    if (spec.default !== undefined && String(v) === String(spec.default)) return false;
    return true;
  },

  /**
   * Field for one option.
   * @param {Object} spec Option specification.
   * @param {boolean} unsupported Not available in the installed version.
   * @param {boolean} disabled Disabled because of another option.
   * @returns {HTMLElement} Field.
   */
  optionField(spec, unsupported, disabled) {
    const name = spec.name;
    const value = CV.form.options[name];
    const set = (v) => this.setOption(spec, v);
    const flag = optFlag(spec);
    const off = unsupported || disabled;
    const badge = unsupported ? h('span.badge.warn', t('opt.unsupported')) : null;
    const help = optHelp(spec);
    let control;
    switch (spec.type) {
      case 'bool':
        return h('div.field', { class: name === 'format' ? 'wide' : null },
          h('div.row', switchInput(!!value, set, h('span', h('b', optLabel(name))), { disabled: off }), h('span.flag.field', flag), badge),
          help ? h('div.help', help) : null);
      case 'choice':
        control = selectInput(spec.choices.map((c) => ({ value: c, label: choiceLabel(name, c) })),
          value === undefined ? spec.default : value, set, { disabled: off });
        break;
      case 'multi': {
        const selected = Array.isArray(value) ? value : (value ? String(value).split(',') : (spec.default || []));
        control = h('div.chips', spec.choices.map((c) => {
          const input = h('input', { type: 'checkbox', checked: selected.includes(c), disabled: off });
          input.addEventListener('change', () => {
            const next = spec.choices.filter((x) => (x === c ? input.checked : selected.includes(x)));
            selected.splice(0, selected.length, ...next);
            set(next);
          });
          return h('label.chip', input, h('span', choiceLabel(name, c)));
        }));
        return h('div.field.wide', h('label', optLabel(name), h('span.flag', flag), badge), control, help ? h('div.help', help) : null);
      }
      case 'int':
      case 'number':
        control = textInput(value === undefined ? '' : value, set,
          { type: 'number', placeholder: spec.placeholder, ltr: true, disabled: off, min: spec.min, max: spec.max, step: spec.type === 'int' ? 1 : 'any' });
        break;
      case 'password': {
        const input = textInput(value || '', set, { type: 'password', ltr: true, disabled: off });
        control = h('div.input-group', input, button({ icon: 'eye', cls: 'sm', title: t('common.show'),
          onClick: () => { input.type = input.type === 'password' ? 'text' : 'password'; } }));
        break;
      }
      case 'path':
        control = pathInput(value || '', set, 'folder');
        break;
      case 'rules': {
        const rules = S.rules || [];
        const known = rules.map((r) => 'rules:' + r.name);
        const isCustom = value && !known.includes(value);
        const pathBox = h('div', { hidden: !isCustom }, pathInput(isCustom ? value : '', set, 'json', { placeholder: 'C:\\rules.json' }));
        const sel = selectInput([{ value: '', label: t('rules.none') }]
          .concat(rules.filter((r) => r.valid).map((r) => ({ value: 'rules:' + r.name, label: r.name + ' (' + fmtNum(r.count) + ')' })))
          .concat([{ value: '__custom', label: t('rules.custom_file') }]),
        isCustom ? '__custom' : (value || ''), (v) => {
          pathBox.hidden = v !== '__custom';
          if (v !== '__custom') set(v);
        }, { disabled: off });
        control = h('div.col', h('div.input-group', sel, button({ icon: 'shield', cls: 'sm', title: t('nav.rules'), onClick: () => go('rules') })), pathBox);
        break;
      }
      default:
        control = textInput(value === undefined ? '' : value, set, { placeholder: spec.placeholder, ltr: true, disabled: off });
    }
    return field(optLabel(name), control, { help, flag, badge });
  },

  /**
   * Store an option value and refresh dependent UI.
   * @param {Object} spec Option specification.
   * @param {*} value New value.
   */
  setOption(spec, value) {
    const options = CV.form.options;
    const empty = value === '' || value === null || value === undefined || value === false || (Array.isArray(value) && !value.length && spec.name !== 'format');
    if (empty || (spec.default !== undefined && !Array.isArray(value) && String(value) === String(spec.default))) delete options[spec.name];
    else options[spec.name] = value;
    CV.form.modified = true;
    saveForm();
    if (spec.name === 'hybrid') this.renderOptions();
    else this.refreshCounts();
    this.schedulePreview();
    this.renderActions();
  },

  /** Update the "n options set" badges and the "modified" marker in place (keeps focus and scroll position). */
  refreshCounts() {
    for (const entry of Object.values(this.countBadges || {})) {
      const count = entry.specs.filter((s) => this.isSet(s)).length;
      entry.el.textContent = fmtNum(count);
      entry.el.hidden = !count;
    }
    if (this.modifiedBadge) this.modifiedBadge.hidden = !(CV.form.modified && CV.form.preset);
  },

  /** Explanation at the top of the hybrid section. */
  hybridIntro() {
    this.hybridStatusEl = h('div');
    mount(this.hybridStatusEl, this.hybridStatus());
    return h('div.mb', this.hybridStatusEl);
  },

  /**
   * Status of the hybrid backend for the current options.
   * @returns {Node} Callout.
   */
  hybridStatus() {
    const options = CV.form.options;
    if ((options.hybrid || 'off') === 'off') return callout('info', t('hybrid.intro_off'));
    const external = options['hybrid-url'] || (S.settings && S.settings.hybrid_external_url);
    if (external) return callout('info', t('hybrid.uses_external', { url: external }));
    const env = (S.state && S.state.env) || {};
    const hy = (S.state && S.state.hybrid) || {};
    if (!env.hybrid) {
      return callout('warn', h('b', t('hybrid.not_installed')), h('p', t('hybrid.not_installed_text')),
        button({ label: t('hybrid.install'), icon: 'download', cls: 'sm primary', onClick: () => go('setup') }));
    }
    return callout(hy.state === 'ready' || hy.state === 'external' ? 'ok' : 'info',
      h('div.status-line', h('b', t('hybrid.managed')), h('span.ltr', hy.url || ''), hybridStatePill(hy.state)),
      h('div.small', hy.auto_start ? t('hybrid.autostart_on') : t('hybrid.autostart_off')),
      button({ label: t('nav.hybrid'), icon: 'cpu', cls: 'sm mt', onClick: () => go('hybrid') }));
  },

  /**
   * Hint for presets that need special hybrid-server options.
   * @param {Object} preset Preset with a `server` object.
   * @returns {Node} Callout.
   */
  presetServerHint(preset) {
    const current = (S.settings && S.settings.hybrid_server) || {};
    const differs = Object.keys(preset.server).filter((k) => current[k] !== preset.server[k]);
    if (!differs.length) return null;
    const valueText = (k, v) => (typeof v === 'boolean' ? t(v ? 'common.on' : 'common.off') : k === 'ocr_mode' ? t('val.ocr_' + v) : String(v));
    const names = differs.map((k) => t('hsrv.' + k) + ' = ' + valueText(k, preset.server[k])).join(getLang() === 'fa' ? '، ' : ', ');
    return h('div.mb', callout('info', h('div', t('preset.server_hint', { list: names })),
      button({ label: t('preset.apply_server'), icon: 'check', cls: 'sm mt', onClick: async () => {
        await saveSettings({ hybrid_server: preset.server });
        const hy = S.state && S.state.hybrid;
        if (hy && (hy.state === 'ready' || hy.state === 'starting')) {
          if (await confirmDialog(t('hybrid.restart_q'), t('hybrid.restart_q_text'))) await api.post('/api/hybrid/restart', {});
        }
        toast(t('common.saved'), 'ok');
        this.renderOptions();
      } })));
  },

  /**
   * Replace the options with a preset.
   * @param {string} id Preset id ('' = no preset).
   */
  applyPreset(id) {
    const preset = (S.presets || []).find((p) => p.id === id);
    const password = CV.form.options.password;
    CV.form.preset = id;
    CV.form.options = preset ? JSON.parse(JSON.stringify(preset.options)) : { format: ['json'] };
    if (password) CV.form.options.password = password;
    CV.form.modified = false;
    saveForm();
    this.renderOptions();
    this.schedulePreview();
    this.renderActions();
  },

  /** Save the current options as a user preset. */
  async savePreset() {
    const name = await promptDialog(t('preset.save_title'), '', t('preset.name_placeholder'));
    if (!name) return;
    const preset = await api.post('/api/presets', { name, options: CV.form.options });
    await loadPresets(true);
    CV.form.preset = preset.id;
    CV.form.modified = false;
    saveForm();
    this.renderOptions();
    toast(t('preset.saved'), 'ok');
  },

  /**
   * Delete a user preset.
   * @param {Object} preset Preset.
   */
  async deletePreset(preset) {
    if (!(await confirmDialog(t('preset.delete_q'), preset.name.en, { danger: true, ok: t('common.delete') }))) return;
    await api.del('/api/presets/' + encodeURIComponent(preset.id));
    await loadPresets(true);
    CV.form.preset = '';
    saveForm();
    this.renderOptions();
  },

  /* -------------------------------------------------------------- output */
  /** Output folder and job name. */
  renderOutput() {
    if (!this.outputBox) return;
    const form = CV.form;
    if (!form.outputDir && S.settings && S.settings.default_output_dir) form.outputDir = S.settings.default_output_dir;
    const radio = (value, title, text) => {
      const input = h('input', { type: 'radio', name: 'outmode', value, checked: form.outputMode === value });
      input.addEventListener('change', () => { form.outputMode = value; saveForm(); this.renderOutput(); this.schedulePreview(); this.renderActions(); });
      return h('label.radio-card', input, h('div', h('b', title), h('div.small.muted', text)));
    };
    mount(this.outputBox,
      h('div.radio-cards', radio('job', t('output.job'), t('output.job_text')), radio('custom', t('output.custom'), t('output.custom_text'))),
      form.outputMode === 'custom'
        ? h('div.mt', field(t('output.folder'), pathInput(form.outputDir, (v) => { form.outputDir = v; saveForm(); this.schedulePreview(); this.renderActions(); }, 'folder',
          { placeholder: 'D:\\Output' }), { help: t('output.folder_help') }))
        : null,
      h('div.fields.mt', field(t('output.name'), textInput(form.name, (v) => { form.name = v; saveForm(); }, { placeholder: t('output.name_placeholder') }))));
  },

  /* ------------------------------------------------------------- actions */
  /** Debounced request for the command preview. */
  schedulePreview: debounce(function () { ConvertPage.updatePreview(); }, 250),

  /** Ask the server for the command line (also validates the options). */
  async updatePreview() {
    const seq = ++CV.previewSeq;
    try {
      const res = await api.post('/api/jobs/preview', this.payload());
      if (seq !== CV.previewSeq) return;
      CV.command = res.command;
      CV.previewError = null;
    } catch (err) {
      if (seq !== CV.previewSeq) return;
      CV.previewError = err;
    }
    this.renderActions();
  },

  /**
   * Build the job request from the form.
   * @returns {Object} Payload for /api/jobs.
   */
  payload() {
    return {
      inputs: CV.inputs.filter((i) => i.status === 'ready').map((i) => ({ path: i.path, source: i.source })),
      options: CV.form.options,
      output_mode: CV.form.outputMode === 'custom' ? 'custom' : 'job',
      output_dir: CV.form.outputMode === 'custom' ? CV.form.outputDir : '',
      name: CV.form.name,
      preset: CV.form.preset,
    };
  },

  /**
   * Reason why the conversion cannot start yet (null = ready).
   * @returns {string|null} Translated reason.
   */
  blockReason() {
    const env = (S.state && S.state.env) || {};
    if (!CV.inputs.length) return t('convert.need_input');
    if (CV.inputs.some((i) => i.status === 'uploading')) return t('convert.wait_upload');
    if (CV.inputs.some((i) => i.status === 'error')) return t('convert.upload_error');
    if (env.loading) return t('convert.checking');
    if (!env.installed) return t('convert.not_installed');
    if (!env.java_ok) return t('convert.no_java');
    if (!(CV.form.options.format || []).length) return t('convert.need_format');
    if (CV.form.outputMode === 'custom' && !CV.form.outputDir) return t('convert.need_folder');
    if (CV.previewError) return errorText(CV.previewError);
    return null;
  },

  /** Render the sticky action bar. */
  renderActions() {
    if (!this.actionBar || !this.actionBar.isConnected) return;
    const reason = this.blockReason();
    const pdfs = CV.inputs.reduce((s, i) => s + (i.pdfs || 0), 0);
    const formats = listJoin((CV.form.options.format || []).map((f) => choiceLabel('format', f)));
    const startBtn = button({ label: t('convert.start'), icon: 'play', cls: 'primary lg', disabled: !!reason, onClick: () => this.start() });
    startBtn.id = 'start-button';
    mount(this.actionBar,
      h('div.grow',
        h('div', h('b', pdfs ? t('convert.ready_summary', { n: fmtNum(pdfs) }) : t('convert.no_files')), formats ? h('span.muted', ' → ' + formats) : null),
        reason ? h('div.small', { style: { color: 'var(--warning)' } }, reason) : h('div.small.muted', t('convert.shortcut'))),
      button({ label: t('convert.command'), icon: 'terminal', cls: 'ghost sm', onClick: () => {
        CV.showCommand = !CV.showCommand;
        localStorage.setItem('odl.showCommand', CV.showCommand ? '1' : '0');
        this.renderActions();
      } }),
      startBtn,
      CV.showCommand ? h('div', { style: { flexBasis: '100%' } },
        h('div.row.nowrap', h('div.cmd-preview.grow', CV.command || '…'),
          button({ icon: 'copy', cls: 'sm', title: t('log.copy'), onClick: () => copyText(CV.command) }))) : null);
  },

  /** Create the job and open its page. */
  async start() {
    if (this.blockReason()) return;
    requestNotifyPermission();
    const job = await api.post('/api/jobs', this.payload());
    CV.inputs = [];
    toast(t('convert.queued'), 'ok');
    go('jobs/' + job.id);
  },
};

PAGES.convert = ConvertPage;
