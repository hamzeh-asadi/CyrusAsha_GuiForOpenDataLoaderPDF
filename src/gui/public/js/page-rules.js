/* ==========================================================================
   CyrusAsha_GuiForOpenDataLoaderPDF - "Sanitize rules" page
   Create, import, edit, test and export rule files for --sanitize-rules
   (regular expressions that replace sensitive data in the output).
   ========================================================================== */
'use strict';

const RulesPage = {
  /**
   * Build the page.
   * @param {HTMLElement} main Container.
   */
  mount(main) {
    this.root = h('div.page');
    main.appendChild(this.root);
    this.current = null; // {name, rules: [...], dirty}
    this.raw = false;
    this.render();
    this.reload();
  },

  /** Nothing to update from the global state. */
  update() {},

  /** Forget references. */
  unmount() { this.root = null; },

  /** Reload the list of rule files. */
  async reload() {
    await loadRules(true);
    if (!this.root) return;
    if (!this.current && S.rules.length) await this.open(S.rules[0].name);
    this.renderList();
  },

  /** Page skeleton. */
  render() {
    this.listBox = h('div');
    this.editorBox = h('div');
    this.importInput = h('input', { type: 'file', accept: '.json,application/json', hidden: true });
    this.importInput.addEventListener('change', () => { if (this.importInput.files[0]) this.importFile(this.importInput.files[0]); this.importInput.value = ''; });
    mount(this.root,
      h('div.page-head', h('div', h('h1', t('rules.title')), h('p', t('rules.subtitle')))),
      h('div', { style: { display: 'grid', gridTemplateColumns: 'minmax(220px, 280px) 1fr', gap: '16px', alignItems: 'start' } },
        h('div.card.tight', h('div.card-head', h('h3', { style: { margin: 0 } }, t('rules.files')),
          h('div.row', button({ icon: 'plus', cls: 'sm', title: t('rules.new'), onClick: () => this.create() }),
            button({ icon: 'upload', cls: 'sm', title: t('rules.import'), onClick: () => this.importInput.click() }))),
        this.listBox, h('div.tiny.muted.mt', t('rules.drop_hint'))),
        this.editorBox),
      this.importInput);
    this.renderEditor();
  },

  /** Rule file list. */
  renderList() {
    if (!this.listBox) return;
    const rules = S.rules || [];
    mount(this.listBox, rules.length ? h('div.col', { style: { gap: '2px' } }, rules.map((r) => h('div.tree-item', {
      class: this.current && this.current.name === r.name ? 'active' : null, onclick: () => this.open(r.name).catch(showError),
    }, icon('shield', 'sm'), h('span.grow.ellipsis.ltr', r.name), r.valid ? h('span.badge', fmtNum(r.count)) : h('span.badge.err', '!'))))
      : h('div.muted.small', t('rules.none_yet')));
  },

  /**
   * Open a rule file in the editor.
   * @param {string} name File name without extension.
   */
  async open(name) {
    if (this.current && this.current.dirty && !(await confirmDialog(t('rules.discard_q')))) return;
    const res = await api.get('/api/rules/' + encodeURIComponent(name));
    this.current = { name, rules: (res.data && res.data.rules) || [], dirty: false, isNew: false };
    this.renderList();
    this.renderEditor();
  },

  /** Start a new, empty rule file. */
  async create() {
    const name = await promptDialog(t('rules.new_name'), 'my-rules', 'my-rules');
    if (!name) return;
    if (!/^[A-Za-z0-9_-]{1,60}$/.test(name)) { toast(t('err.bad_rules_name'), 'err'); return; }
    this.current = { name, rules: [{ id: 'email', pattern: '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}', replacement: 'email@example.com' }], dirty: true, isNew: true };
    this.renderEditor();
  },

  /**
   * Import a JSON file (from the file input or a drop).
   * @param {File} file JSON file.
   */
  async importFile(file) {
    try {
      const data = JSON.parse(await file.text());
      const name = file.name.replace(/\.json$/i, '').replace(/[^A-Za-z0-9_-]/g, '-').slice(0, 60) || 'imported';
      const saved = await api.put('/api/rules/' + encodeURIComponent(name), data);
      toast(t('rules.imported', { name: saved.name, n: fmtNum(saved.count) }), 'ok');
      this.current = null;
      await loadRules(true);
      await this.open(saved.name);
    } catch (err) {
      showError(err instanceof SyntaxError ? new ApiFailure('bad_json', err.message) : err);
    }
  },

  /** Editor for the current rule file. */
  renderEditor() {
    if (!this.editorBox) return;
    const cur = this.current;
    if (!cur) {
      mount(this.editorBox, h('div.card', h('div.empty', icon('shield', 'xl'), h('div', t('rules.pick')),
        button({ label: t('rules.new'), icon: 'plus', cls: 'primary mt', onClick: () => this.create() }))));
      return;
    }
    const markDirty = () => { cur.dirty = true; if (this.dirtyBadge) this.dirtyBadge.hidden = false; };
    this.dirtyBadge = h('span.badge.warn', { hidden: !cur.dirty }, t('rules.unsaved'));
    let content;
    if (this.raw) {
      const area = h('textarea.ltr', { rows: 18, spellcheck: 'false' }, JSON.stringify({ rules: cur.rules }, null, 2));
      area.addEventListener('input', () => {
        markDirty();
        try { cur.rules = JSON.parse(area.value).rules || []; area.classList.remove('invalid'); } catch (e) { area.classList.add('invalid'); }
      });
      content = area;
    } else {
      content = h('div.table-wrap', h('table.table.compact',
        h('thead', h('tr', h('th', t('rules.col.id')), h('th', t('rules.col.pattern')), h('th', t('rules.col.replacement')), h('th', ''))),
        h('tbody', cur.rules.map((rule, index) => h('tr',
          h('td', { style: { width: '150px' } }, textInput(rule.id, (v) => { rule.id = v; markDirty(); }, { ltr: true })),
          h('td', textInput(rule.pattern, (v) => { rule.pattern = v; markDirty(); }, { ltr: true })),
          h('td', { style: { width: '200px' } }, textInput(rule.replacement, (v) => { rule.replacement = v; markDirty(); }, { ltr: true })),
          h('td', { style: { width: '40px' } }, button({ icon: 'x', cls: 'sm ghost', title: t('common.remove'),
            onClick: () => { cur.rules.splice(index, 1); markDirty(); this.renderEditor(); } })))))));
    }
    const sample = h('textarea', { rows: 4, dir: 'auto' }, t('rules.sample'));
    const result = h('div');
    mount(this.editorBox, h('div.stack',
      h('div.card',
        h('div.card-head',
          h('h2', icon('shield'), h('span.ltr', cur.name + '.json'), this.dirtyBadge),
          h('div.row',
            h('div.btn-group',
              h('button.btn.sm', { type: 'button', class: this.raw ? null : 'active', onclick: () => { this.raw = false; this.renderEditor(); } }, t('rules.table')),
              h('button.btn.sm', { type: 'button', class: this.raw ? 'active' : null, onclick: () => { this.raw = true; this.renderEditor(); } }, 'JSON')),
            button({ label: t('common.save'), icon: 'save', cls: 'primary sm', onClick: () => this.save() }),
            !cur.isNew ? h('a.btn.sm', { href: '/api/rules/' + encodeURIComponent(cur.name) + '/download', title: t('common.download') }, icon('download')) : null,
            !cur.isNew ? button({ icon: 'trash', cls: 'sm danger', title: t('common.delete'), onClick: () => this.remove() }) : null)),
        content,
        !this.raw ? h('div.row.mt', button({ label: t('rules.add'), icon: 'plus', cls: 'sm', onClick: () => {
          cur.rules.push({ id: 'rule-' + (cur.rules.length + 1), pattern: '', replacement: '' }); markDirty(); this.renderEditor();
        } }), h('span.small.muted', t('rules.limits'))) : null),
      h('div.card',
        h('div.card-head', h('h2', icon('eye'), t('rules.test'))),
        h('p.small.muted', t('rules.test_text')),
        sample,
        h('div.row.mt', button({ label: t('rules.run_test'), icon: 'play', onClick: async () => {
          const res = await api.post('/api/rules/test', { data: { rules: cur.rules }, text: sample.value });
          mount(result, h('div.mt',
            h('div.row.mb', Object.keys(res.hits).length ? Object.entries(res.hits).map(([id, n]) => h('span.badge.primary', id + ' × ' + fmtNum(n))) : h('span.muted.small', t('rules.no_hits'))),
            h('pre.cmd-preview', { dir: 'auto' }, res.result)));
        } })),
        result),
      callout('info', t('rules.howto'))));
  },

  /** Validate and save the current rule file. */
  async save() {
    const cur = this.current;
    const saved = await api.put('/api/rules/' + encodeURIComponent(cur.name), { rules: cur.rules });
    cur.dirty = false;
    cur.isNew = false;
    toast(t('rules.saved', { n: fmtNum(saved.count) }), 'ok');
    await loadRules(true);
    this.renderList();
    this.renderEditor();
  },

  /** Delete the current rule file. */
  async remove() {
    const cur = this.current;
    if (!(await confirmDialog(t('rules.delete_q'), cur.name + '.json', { danger: true, ok: t('common.delete') }))) return;
    await api.del('/api/rules/' + encodeURIComponent(cur.name));
    this.current = null;
    await loadRules(true);
    if (S.rules.length) await this.open(S.rules[0].name);
    this.renderList();
    this.renderEditor();
  },
};

PAGES.rules = RulesPage;
