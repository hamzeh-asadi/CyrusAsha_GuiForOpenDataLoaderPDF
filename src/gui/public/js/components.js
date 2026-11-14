/* ==========================================================================
   CyrusAsha_GuiForOpenDataLoaderPDF - reusable components
   Form fields, switches, selects, progress bars, state badges and a live
   log viewer that polls an endpoint incrementally ("lines since N").
   ========================================================================== */
'use strict';

/**
 * A labelled form field.
 * @param {string|Node} label Label text.
 * @param {Node} control Input element.
 * @param {Object} [opts] Options.
 * @param {string} [opts.help] Help text under the control.
 * @param {string} [opts.flag] CLI flag shown next to the label.
 * @param {boolean} [opts.wide] Span the whole grid row.
 * @returns {HTMLElement} The field wrapper.
 */
function field(label, control, opts) {
  opts = opts || {};
  return h('div.field', { class: opts.wide ? 'wide' : null },
    h('label', label, opts.flag ? h('span.flag', opts.flag) : null, opts.badge || null),
    control,
    opts.help ? h('div.help', opts.help) : null);
}

/**
 * A toggle switch (styled checkbox) with a label.
 * @param {boolean} checked Initial state.
 * @param {function(boolean):void} onChange Change handler.
 * @param {string|Node} [label] Label text.
 * @param {Object} [opts] `{disabled, title}`.
 * @returns {HTMLLabelElement} The switch.
 */
function switchInput(checked, onChange, label, opts) {
  opts = opts || {};
  const input = h('input', { type: 'checkbox', checked: !!checked, disabled: !!opts.disabled });
  input.addEventListener('change', () => onChange(input.checked));
  return h('label.switch', { title: opts.title || null }, input, label ? h('span', label) : null);
}

/**
 * A <select> element.
 * @param {Array<string|{value: string, label: string}>} options Choices.
 * @param {string} value Selected value.
 * @param {function(string):void} onChange Change handler.
 * @param {Object} [opts] `{disabled, cls}`.
 * @returns {HTMLSelectElement} The select.
 */
function selectInput(options, value, onChange, opts) {
  opts = opts || {};
  const sel = h('select', { disabled: !!opts.disabled, class: opts.cls || null },
    options.map((o) => {
      const item = typeof o === 'object' ? o : { value: o, label: o };
      return h('option', { value: item.value, selected: String(item.value) === String(value) }, item.label);
    }));
  sel.addEventListener('change', () => onChange(sel.value));
  return sel;
}

/**
 * A text input.
 * @param {string} value Initial value.
 * @param {function(string):void} onInput Called on every change.
 * @param {Object} [opts] `{type, placeholder, ltr, disabled, min, max, step}`.
 * @returns {HTMLInputElement} The input.
 */
function textInput(value, onInput, opts) {
  opts = opts || {};
  const input = h('input', {
    type: opts.type || 'text', value: value === undefined || value === null ? '' : value,
    placeholder: opts.placeholder || null, class: opts.ltr ? 'ltr' : null, disabled: !!opts.disabled,
    min: opts.min, max: opts.max, step: opts.step, spellcheck: 'false', autocomplete: 'off',
  });
  if (onInput) input.addEventListener('input', () => onInput(input.value));
  return input;
}

/**
 * A path input with a "Browse…" button that opens a native dialog.
 * @param {string} value Current path.
 * @param {function(string):void} onChange Called with the new path.
 * @param {"folder"|"json"|"exe"|"files"} kind Dialog type.
 * @param {Object} [opts] `{placeholder}`.
 * @returns {HTMLElement} Input group.
 */
function pathInput(value, onChange, kind, opts) {
  opts = opts || {};
  const input = textInput(value, onChange, { ltr: true, placeholder: opts.placeholder });
  const browse = button({
    label: t('common.browse'), icon: 'folder', cls: 'sm', disabled: !isLocal(),
    title: isLocal() ? null : t('common.local_only'),
    onClick: async () => {
      const paths = await pickNative(kind);
      if (paths.length) { input.value = paths[0]; onChange(paths[0]); }
    },
  });
  return h('div.input-group', input, browse);
}

/**
 * A progress bar.
 * @param {number|null} fraction 0..1, or null for an indeterminate bar.
 * @param {string} [cls] Extra classes ("ok", "err", "thin").
 * @returns {HTMLElement} The bar.
 */
function progressBar(fraction, cls) {
  const indeterminate = fraction === null || fraction === undefined || isNaN(fraction);
  return h('div.progress', { class: (cls || '') + (indeterminate ? ' indeterminate' : '') },
    h('div', { style: { width: indeterminate ? '35%' : Math.max(0, Math.min(100, fraction * 100)).toFixed(1) + '%' } }));
}

/** Visual style for every job state: [pill class, icon]. */
const JOB_STATE_STYLE = {
  queued: ['', 'clock'], waiting: ['info', 'loader'], running: ['primary', 'loader'], success: ['ok', 'check'],
  partial: ['warn', 'alert'], failed: ['err', 'x'], canceled: ['', 'stop'],
};

/**
 * A pill showing a job state.
 * @param {string} state Job state.
 * @returns {HTMLElement} The pill.
 */
function jobStatePill(state) {
  const style = JOB_STATE_STYLE[state] || ['', 'info'];
  const active = state === 'running' || state === 'waiting';
  return h('span.pill', { class: style[0] + (active ? ' pulse' : '') }, h('span.dot'), t('state.' + state));
}

/**
 * A square icon box for a job state.
 * @param {string} state Job state.
 * @returns {HTMLElement} The icon box.
 */
function jobStateIcon(state) {
  const style = JOB_STATE_STYLE[state] || ['', 'info'];
  const map = { '': 'muted', primary: 'info', info: 'info', ok: 'ok', warn: 'warn', err: 'err' };
  const spin = state === 'running' || state === 'waiting';
  return h('div.state-icon', { class: map[style[0]] }, icon(style[1], spin ? 'spin' : ''));
}

/**
 * A callout box.
 * @param {"info"|"warn"|"err"|"ok"} kind Style.
 * @param {...*} content Content nodes.
 * @returns {HTMLElement} The callout.
 */
function callout(kind, ...content) {
  const icons = { info: 'info', warn: 'alert', err: 'alert', ok: 'check' };
  return h('div.callout.' + kind, icon(icons[kind] || 'info'), h('div.grow', content));
}

/**
 * Collapsible section (<details>) that remembers its open state.
 * @param {string} id Storage key.
 * @param {string|Node} title Summary content.
 * @param {Node|Array} body Content.
 * @param {boolean} [openDefault] Initially open when nothing is stored.
 * @returns {HTMLDetailsElement} The section.
 */
function section(id, title, body, openDefault) {
  const key = 'odl.sec.' + id;
  const stored = localStorage.getItem(key);
  const el = h('details.section', { open: stored === null ? !!openDefault : stored === '1' },
    h('summary', title), h('div.section-body', body));
  el.addEventListener('toggle', () => localStorage.setItem(key, el.open ? '1' : '0'));
  return el;
}

/**
 * Classify a log line for colouring.
 * @param {string} line Log line.
 * @returns {string} CSS class.
 */
function logLineClass(line) {
  if (/^\$ /.test(line)) return 'cmd';
  if (/^==> /.test(line)) return 'head';
  if (/^\[GUI\]/.test(line)) return 'gui';
  if (/SEVERE|ERROR|Error|Traceback|Exception|failed|FAILED/.test(line)) return 'err';
  if (/WARNING|WARN|Warning/.test(line)) return 'warn';
  if (/^\s+\.\.\. \(repeated/.test(line) || /^\(exit code/.test(line)) return 'dim';
  return '';
}

/**
 * Live log viewer. Polls `url?since=N` and appends new lines.
 * Auto-scrolls while the view is scrolled to the bottom.
 */
class LogView {
  /**
   * @param {string} url Endpoint returning `{lines, next}`.
   * @param {Object} [opts] Options.
   * @param {string} [opts.cls] Extra console classes ("tall", "short").
   * @param {string} [opts.title] Title shown in the toolbar.
   * @param {number} [opts.interval=1000] Poll interval in ms.
   * @param {function():boolean} [opts.active] Return false to slow polling down.
   */
  constructor(url, opts) {
    this.url = url;
    this.opts = opts || {};
    this.since = 0;
    this.lines = [];
    this.filter = '';
    this.stopped = false;
    this.follow = true;
    this.consoleEl = h('div.console', { class: this.opts.cls || '', tabindex: '0' });
    this.consoleEl.addEventListener('scroll', () => {
      const el = this.consoleEl;
      this.follow = el.scrollTop + el.clientHeight >= el.scrollHeight - 8;
    });
    const filterInput = textInput('', (v) => { this.filter = v.toLowerCase(); this.redraw(); },
      { type: 'search', placeholder: t('log.filter') });
    filterInput.style.maxWidth = '220px';
    filterInput.style.height = '28px';
    this.el = h('div.logview',
      h('div.console-bar',
        this.opts.title ? h('b', this.opts.title) : null,
        h('span.grow'),
        filterInput,
        button({ icon: 'copy', cls: 'sm ghost', title: t('log.copy'), onClick: () => copyText(this.lines.join('\n')) }),
        button({ icon: 'download', cls: 'sm ghost', title: t('log.save'), onClick: () => this.save() }),
        button({ icon: 'down', cls: 'sm ghost', title: t('log.bottom'),
          onClick: () => { this.follow = true; this.scroll(); } })),
      this.consoleEl);
    this.tick();
  }

  /** Fetch new lines and schedule the next poll (stops when detached). */
  async tick() {
    if (this.stopped) return;
    if (!this.el.isConnected && this.since > 0) { this.stopped = true; return; }
    try {
      for (let round = 0; round < 20; round++) { // drain large backlogs quickly
        const data = await api.get(this.url + (this.url.includes('?') ? '&' : '?') + 'since=' + this.since);
        if (data.next < this.since) this.reset(); // log was restarted
        if (data.lines && data.lines.length) this.append(data.lines);
        this.since = data.next;
        if (!data.lines || data.lines.length < 3000) break;
      }
    } catch (err) { /* keep polling; the header shows connection problems */ }
    const active = this.opts.active ? this.opts.active() : true;
    const delay = document.hidden ? 4000 : (active ? (this.opts.interval || 1000) : 3000);
    setTimeout(() => this.tick(), delay);
  }

  /**
   * Append lines to the view.
   * @param {string[]} lines New lines.
   */
  append(lines) {
    const frag = document.createDocumentFragment();
    for (const line of lines) {
      this.lines.push(line);
      if (this.filter && !line.toLowerCase().includes(this.filter)) continue;
      frag.appendChild(this.lineEl(line));
    }
    if (this.lines.length > 20000) { this.lines.splice(0, this.lines.length - 15000); this.redraw(); return; }
    this.consoleEl.appendChild(frag);
    this.scroll();
  }

  /**
   * Create the element for one line.
   * @param {string} line Log line.
   * @returns {HTMLElement} Line element.
   */
  lineEl(line) {
    return h('div.ln', { class: logLineClass(line) }, line || '\u00a0');
  }

  /** Re-render all lines (after filtering). */
  redraw() {
    const frag = document.createDocumentFragment();
    for (const line of this.lines) {
      if (this.filter && !line.toLowerCase().includes(this.filter)) continue;
      frag.appendChild(this.lineEl(line));
    }
    mount(this.consoleEl, frag);
    this.scroll();
  }

  /** Forget everything (used when the server restarts a log). */
  reset() {
    this.lines = [];
    this.since = 0;
    mount(this.consoleEl);
  }

  /** Scroll to the bottom if following. */
  scroll() {
    if (this.follow) this.consoleEl.scrollTop = this.consoleEl.scrollHeight;
  }

  /** Save the log as a text file. */
  save() {
    const blob = new Blob([this.lines.join('\n')], { type: 'text/plain;charset=utf-8' });
    const link = h('a', { href: URL.createObjectURL(blob), download: (this.opts.fileName || 'log') + '.txt' });
    document.body.appendChild(link);
    link.click();
    setTimeout(() => { URL.revokeObjectURL(link.href); link.remove(); }, 1000);
  }

  /** Stop polling. */
  stop() {
    this.stopped = true;
  }
}

/**
 * A small "label: value" definition list.
 * @param {Array<[string, *]>} rows Pairs of label and value (null values are skipped).
 * @returns {HTMLElement} The list.
 */
function kvList(rows) {
  return h('dl.kv', rows.filter((r) => r && r[1] !== null && r[1] !== undefined && r[1] !== '')
    .map(([k, v]) => [h('dt', k), h('dd', { dir: 'auto' }, v)]));
}

/** Registry of pages: `{mount(main, params), update(state), unmount()}`. */
const PAGES = {};

/** Global application state shared by all pages (filled by app.js). */
const S = { state: null, settings: null, optionsMeta: null, presets: null, rules: null, online: true };
