/* ==========================================================================
   CyrusAsha_GuiForOpenDataLoaderPDF - shared utilities
   DOM builder, icons, formatting helpers, API client, toasts and dialogs.
   All functions are global (classic scripts, no build step needed).
   ========================================================================== */
'use strict';

/**
 * Query a single element.
 * @param {string} selector CSS selector.
 * @param {ParentNode} [root=document] Element to search in.
 * @returns {Element|null} The first match.
 */
function $(selector, root) {
  return (root || document).querySelector(selector);
}

/**
 * Create a DOM element (a tiny, XSS-safe alternative to innerHTML templates).
 * Strings in `children` become text nodes, so user data is never parsed as HTML.
 *
 * @param {string} tag Tag name, optionally with classes: "div.card.tight".
 * @param {Object} [props] Attributes/properties. `on*` keys add listeners,
 *   `class`, `style` (object or string), `dataset`, and boolean attributes are supported.
 * @param {...(Node|string|number|Array|null|false)} children Child nodes.
 * @returns {HTMLElement} The new element.
 */
function h(tag, props, ...children) {
  const parts = tag.split('.');
  const el = document.createElement(parts[0] || 'div');
  if (parts.length > 1) el.className = parts.slice(1).join(' ');
  if (props && (typeof props !== 'object' || props instanceof Node || Array.isArray(props))) {
    children.unshift(props);
    props = null;
  }
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key.startsWith('on') && typeof value === 'function') {
      el.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key === 'class') {
      el.className = (el.className ? el.className + ' ' : '') + value;
    } else if (key === 'style' && typeof value === 'object') {
      Object.assign(el.style, value);
    } else if (key === 'dataset') {
      Object.assign(el.dataset, value);
    } else if (key === 'value' || key === 'checked' || key === 'selected' || key === 'indeterminate') {
      el[key] = value;
    } else if (value === true) {
      el.setAttribute(key, '');
    } else {
      el.setAttribute(key, String(value));
    }
  }
  appendChildren(el, children);
  return el;
}

/**
 * Append children (nested arrays, strings, numbers, nodes; null/false ignored).
 * @param {Node} el Parent node.
 * @param {Array} children Children to append.
 */
function appendChildren(el, children) {
  for (const child of children) {
    if (child === null || child === undefined || child === false || child === true) continue;
    if (Array.isArray(child)) appendChildren(el, child);
    else el.appendChild(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

/**
 * Replace all children of an element.
 * @param {Element} el Target element.
 * @param {...*} children New children.
 * @returns {Element} The element.
 */
function mount(el, ...children) {
  el.textContent = '';
  appendChildren(el, children);
  return el;
}

/* ------------------------------------------------------------------ icons */
/** Inner SVG markup of the stroke icons (24×24, Feather style, MIT). */
const ICONS = {
  convert: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/>',
  jobs: '<line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/>',
  cpu: '<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><line x1="9" y1="1" x2="9" y2="4"/><line x1="15" y1="1" x2="15" y2="4"/><line x1="9" y1="20" x2="9" y2="23"/><line x1="15" y1="20" x2="15" y2="23"/><line x1="20" y1="9" x2="23" y2="9"/><line x1="20" y1="14" x2="23" y2="14"/><line x1="1" y1="9" x2="4" y2="9"/><line x1="1" y1="14" x2="4" y2="14"/>',
  package: '<line x1="16.5" y1="9.4" x2="7.5" y2="4.21"/><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/>',
  shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
  help: '<circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
  folder: '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>',
  file: '<path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/>',
  trash: '<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/>',
  play: '<polygon points="6 3 20 12 6 21 6 3"/>',
  stop: '<rect x="5" y="5" width="14" height="14" rx="2"/>',
  refresh: '<polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>',
  external: '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
  upload: '<polyline points="16 16 12 12 8 16"/><line x1="12" y1="12" x2="12" y2="21"/><path d="M20.39 18.39A5 5 0 0 0 18 9h-1.26A8 8 0 1 0 3 16.3"/>',
  x: '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
  check: '<polyline points="20 6 9 17 4 12"/>',
  alert: '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
  info: '<circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>',
  copy: '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
  eye: '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>',
  moon: '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>',
  sun: '<circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>',
  auto: '<circle cx="12" cy="12" r="9"/><path d="M12 3v18a9 9 0 0 0 0-18z" fill="currentColor"/>',
  globe: '<circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>',
  power: '<path d="M18.36 6.64a9 9 0 1 1-12.73 0"/><line x1="12" y1="2" x2="12" y2="12"/>',
  rerun: '<polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/>',
  terminal: '<polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/>',
  plus: '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
  save: '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/>',
  image: '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/>',
  code: '<polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>',
  back: '<line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/>',
  down: '<line x1="12" y1="5" x2="12" y2="19"/><polyline points="19 12 12 19 5 12"/>',
  zap: '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
  clock: '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
  drive: '<line x1="22" y1="12" x2="2" y2="12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/><line x1="6" y1="16" x2="6.01" y2="16"/><line x1="10" y1="16" x2="10.01" y2="16"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
  sliders: '<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/>',
  coffee: '<path d="M18 8h1a4 4 0 0 1 0 8h-1"/><path d="M2 8h16v9a4 4 0 0 1-4 4H6a4 4 0 0 1-4-4V8z"/><line x1="6" y1="1" x2="6" y2="4"/><line x1="10" y1="1" x2="10" y2="4"/><line x1="14" y1="1" x2="14" y2="4"/>',
  loader: '<line x1="12" y1="2" x2="12" y2="6"/><line x1="12" y1="18" x2="12" y2="22"/><line x1="4.93" y1="4.93" x2="7.76" y2="7.76"/><line x1="16.24" y1="16.24" x2="19.07" y2="19.07"/><line x1="2" y1="12" x2="6" y2="12"/><line x1="18" y1="12" x2="22" y2="12"/><line x1="4.93" y1="19.07" x2="7.76" y2="16.24"/><line x1="16.24" y1="7.76" x2="19.07" y2="4.93"/>',
  java: '<path d="M8 17c2 1 6 1 8 0"/><path d="M7 20c3 1.5 7 1.5 10 0"/><path d="M12 3c2 2-2 3 0 6"/><path d="M15 5c1.5 1.5-1.5 2.5 0 4.5"/><path d="M5 13h12a3 3 0 0 1 0 6"/>',
  python: '<path d="M12 2c-3 0-5 1-5 3v3h5v1H5c-2 0-3 2-3 4s1 4 3 4h2v-3c0-2 2-3 4-3h4c1.5 0 3-1 3-3V5c0-2-2-3-6-3z"/><circle cx="9.5" cy="5" r=".8" fill="currentColor"/><path d="M12 22c3 0 5-1 5-3v-3h-5v-1h7c2 0 3-2 3-4"/>',
  github: '<path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22"/>',
  telegram: '<path d="M22 2L11 13"/><path d="M22 2l-7 20-4-9-9-4 20-7z"/>',
  wand: '<path d="M15 4V2"/><path d="M15 16v-2"/><path d="M8 9h2"/><path d="M20 9h2"/><path d="M17.8 11.8L19 13"/><path d="M15 9h.01"/><path d="M17.8 6.2L19 5"/><path d="M3 21l9-9"/><path d="M12.2 6.2L11 5"/>',
};

/**
 * Create an SVG icon element.
 * @param {string} name Key of ICONS.
 * @param {string} [cls] Extra classes ("sm", "lg", "xl", "spin", "flip").
 * @returns {SVGElement} The icon.
 */
function icon(name, cls) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('class', 'icon' + (cls ? ' ' + cls : ''));
  svg.setAttribute('aria-hidden', 'true');
  svg.innerHTML = ICONS[name] || ICONS.info; // static, trusted markup
  return svg;
}

/**
 * Create a button with an optional icon.
 * @param {Object} opts Options.
 * @param {string} [opts.label] Text.
 * @param {string} [opts.icon] Icon name.
 * @param {string} [opts.cls] Classes such as "primary sm".
 * @param {Function} [opts.onClick] Click handler (async handlers show a busy state).
 * @param {string} [opts.title] Tooltip.
 * @param {boolean} [opts.disabled] Disabled state.
 * @returns {HTMLButtonElement} The button.
 */
function button(opts) {
  const btn = h('button.btn', {
    type: 'button',
    class: (opts.cls || '') + (opts.label ? '' : ' icon-only'),
    title: opts.title || null,
    'aria-label': opts.title || null,
    disabled: !!opts.disabled,
  }, opts.icon ? icon(opts.icon, opts.iconCls) : null, opts.label ? h('span', opts.label) : null);
  if (opts.onClick) {
    btn.addEventListener('click', async (event) => {
      event.stopPropagation();
      if (btn.dataset.busy) return;
      const result = opts.onClick(event);
      if (result && typeof result.then === 'function') {
        btn.dataset.busy = '1';
        btn.disabled = true;
        try { await result; } catch (err) { showError(err); } finally {
          delete btn.dataset.busy;
          if (btn.isConnected) btn.disabled = !!opts.disabled;
        }
      }
    });
  }
  return btn;
}

/* ------------------------------------------------------------- formatting */
/**
 * Current locale for Intl formatting (Persian uses the Jalali calendar).
 * @returns {string} BCP-47 locale.
 */
function locale() {
  return getLang() === 'fa' ? 'fa-IR' : 'en-US';
}

/**
 * Format a number with locale digits.
 * @param {number} n Number.
 * @param {number} [digits=0] Maximum fraction digits.
 * @returns {string} Formatted number.
 */
function fmtNum(n, digits) {
  if (n === null || n === undefined || isNaN(n)) return '–';
  return Number(n).toLocaleString(locale(), { maximumFractionDigits: digits || 0 });
}

/**
 * Format a byte count ("2.4 MB").
 * @param {number} bytes Size in bytes.
 * @returns {string} Human readable size.
 */
function fmtBytes(bytes) {
  if (!bytes) return fmtNum(0) + ' B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  let n = bytes;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return fmtNum(n, n < 10 && i > 0 ? 1 : 0) + ' ' + units[i];
}

/**
 * Format a duration in seconds ("1 min 05 s").
 * @param {number} seconds Duration.
 * @returns {string} Human readable duration.
 */
function fmtDuration(seconds) {
  if (seconds === null || seconds === undefined || isNaN(seconds)) return '–';
  seconds = Math.max(0, Math.round(seconds));
  const hrs = Math.floor(seconds / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  const secs = seconds % 60;
  const parts = [];
  if (hrs) parts.push(fmtNum(hrs) + ' ' + t('unit.h'));
  if (mins) parts.push(fmtNum(mins) + ' ' + t('unit.m'));
  if (!hrs) parts.push(fmtNum(secs) + ' ' + t('unit.s'));
  return parts.join(' ');
}

/**
 * Format a UNIX timestamp as local date and time.
 * @param {number} ts Seconds since epoch.
 * @param {boolean} [timeOnly] Only show the time.
 * @returns {string} Formatted date.
 */
function fmtTime(ts, timeOnly) {
  if (!ts) return '–';
  const opts = timeOnly ? { timeStyle: 'short' } : { dateStyle: 'medium', timeStyle: 'short' };
  try { return new Intl.DateTimeFormat(locale(), opts).format(new Date(ts * 1000)); } catch (e) { return new Date(ts * 1000).toLocaleString(); }
}

/**
 * Relative time ("3 minutes ago").
 * @param {number} ts Seconds since epoch.
 * @returns {string} Relative time text.
 */
function fmtAgo(ts) {
  if (!ts) return '–';
  const diff = ts - Date.now() / 1000;
  const abs = Math.abs(diff);
  const rtf = new Intl.RelativeTimeFormat(locale(), { numeric: 'auto' });
  if (abs < 60) return rtf.format(Math.round(diff), 'second');
  if (abs < 3600) return rtf.format(Math.round(diff / 60), 'minute');
  if (abs < 86400) return rtf.format(Math.round(diff / 3600), 'hour');
  return fmtTime(ts);
}

/**
 * Join list items with the separator of the current language ("، " or ", ").
 * @param {string[]} items Items.
 * @returns {string} Joined text.
 */
function listJoin(items) {
  return items.join(getLang() === 'fa' ? '، ' : ', ');
}

/**
 * Format a PDF date ("D:20190111014532Z" or "D:20251010112501+09'00'") as local date/time.
 * @param {string} value PDF date string.
 * @returns {string} Formatted date, or the original text if it cannot be parsed.
 */
function fmtPdfDate(value) {
  const m = /^D:(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?(Z|[+-]\d{2}'?\d{2}'?)?/.exec(String(value || ''));
  if (!m) return String(value);
  let iso = m[1] + '-' + (m[2] || '01') + '-' + (m[3] || '01') + 'T' + (m[4] || '00') + ':' + (m[5] || '00') + ':' + (m[6] || '00');
  if (m[7]) iso += m[7] === 'Z' ? 'Z' : m[7].replace(/'/g, '').replace(/^([+-]\d{2})(\d{2})$/, '$1:$2');
  const date = new Date(iso);
  return isNaN(date) ? String(value) : fmtTime(date.getTime() / 1000);
}

/**
 * Return a file's base name for both "/" and "\" separators.
 * @param {string} path Path.
 * @returns {string} Last path component.
 */
function basename(path) {
  const parts = String(path || '').replace(/[\\/]+$/, '').split(/[\\/]/);
  return parts[parts.length - 1] || path;
}

/**
 * Debounce a function.
 * @param {Function} fn Function to call.
 * @param {number} ms Delay in milliseconds.
 * @returns {Function} Debounced function.
 */
function debounce(fn, ms) {
  let timer = null;
  return function (...args) {
    clearTimeout(timer);
    timer = setTimeout(() => fn.apply(this, args), ms);
  };
}

/**
 * Copy text to the clipboard and show a confirmation toast.
 * @param {string} text Text to copy.
 */
async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (e) {
    const area = h('textarea', { style: { position: 'fixed', opacity: '0' } }, text);
    document.body.appendChild(area);
    area.select();
    document.execCommand('copy');
    area.remove();
  }
  toast(t('common.copied'), 'ok');
}

/**
 * True when the UI is opened on the same computer as the server.
 * Native file dialogs and "open folder" only make sense locally.
 * @returns {boolean} Local access.
 */
function isLocal() {
  return ['localhost', '127.0.0.1', '[::1]', '::1'].includes(location.hostname);
}

/* -------------------------------------------------------------------- API */
/** Custom header required by the server for every state-changing request (CSRF protection). */
const CSRF_HEADER = 'X-CyrusAsha-ODL';

/** Error thrown by the API helpers; `code` is translated via `err.<code>`. */
class ApiFailure extends Error {
  /**
   * @param {string} code Machine readable error code.
   * @param {string} detail Technical detail.
   * @param {number} status HTTP status.
   */
  constructor(code, detail, status) {
    super(code + (detail ? ': ' + detail : ''));
    this.code = code;
    this.detail = detail || '';
    this.status = status || 0;
  }
}

/**
 * Perform a JSON request against the local API.
 * @param {string} method HTTP method.
 * @param {string} path URL path (relative, e.g. "/api/state").
 * @param {*} [body] JSON body.
 * @returns {Promise<*>} Parsed response.
 */
async function request(method, path, body) {
  const opts = { method, headers: {} };
  if (method !== 'GET') opts.headers[CSRF_HEADER] = '1';
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, opts);
  } catch (e) {
    throw new ApiFailure('offline', String(e.message || e), 0);
  }
  let data = null;
  const text = await res.text();
  try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
  if (!res.ok) {
    throw new ApiFailure((data && data.error) || 'http_' + res.status, (data && data.detail) || text.slice(0, 300), res.status);
  }
  return data;
}

/** Thin wrappers around `request`. */
const api = {
  /** @param {string} path @returns {Promise<*>} */
  get: (path) => request('GET', path),
  /** @param {string} path @param {*} [body] @returns {Promise<*>} */
  post: (path, body) => request('POST', path, body === undefined ? {} : body),
  /** @param {string} path @param {*} body @returns {Promise<*>} */
  put: (path, body) => request('PUT', path, body),
  /** @param {string} path @returns {Promise<*>} */
  del: (path) => request('DELETE', path),

  /**
   * Upload one file as raw PUT body with progress events.
   * @param {File} file File object from a drop or file input.
   * @param {string} batch Batch id grouping one drop.
   * @param {string} relPath Relative path (folder structure is kept).
   * @param {function(number):void} onProgress Receives 0..1.
   * @returns {Promise<Object>} Server response `{path, size, root}`.
   */
  upload(file, batch, relPath, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('PUT', '/api/upload?batch=' + encodeURIComponent(batch) + '&path=' + encodeURIComponent(relPath));
      xhr.setRequestHeader(CSRF_HEADER, '1');
      xhr.upload.onprogress = (e) => { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
      xhr.onload = () => {
        let data = null;
        try { data = JSON.parse(xhr.responseText); } catch (e) { /* ignore */ }
        if (xhr.status >= 200 && xhr.status < 300) resolve(data);
        else reject(new ApiFailure((data && data.error) || 'http_' + xhr.status, (data && data.detail) || '', xhr.status));
      };
      xhr.onerror = () => reject(new ApiFailure('offline', '', 0));
      xhr.send(file);
    });
  },
};

/**
 * Translate an error object to a readable message.
 * @param {*} err Error (ApiFailure or anything else).
 * @returns {string} Message in the current language.
 */
function errorText(err) {
  if (err instanceof ApiFailure) {
    const key = 'err.' + err.code;
    const base = hasKey(key) ? t(key) : t('err.generic', { code: err.code });
    return err.detail ? base + ' — ' + err.detail : base;
  }
  return String((err && err.message) || err);
}

/**
 * Show an error toast.
 * @param {*} err Error to display.
 */
function showError(err) {
  console.error(err);
  toast(errorText(err), 'err', 9000);
}

/* ----------------------------------------------------------------- toasts */
/**
 * Show a short notification in the corner.
 * @param {string|Node} message Content.
 * @param {"ok"|"err"|"warn"|"info"} [kind="info"] Style.
 * @param {number} [ms=4000] Display time.
 * @param {{label: string, onClick: Function}} [action] Optional action button.
 */
function toast(message, kind, ms, action) {
  kind = kind || 'info';
  const icons = { ok: 'check', err: 'alert', warn: 'alert', info: 'info' };
  const box = h('div.toast.' + kind, icon(icons[kind]), h('div.grow', message),
    action ? button({ label: action.label, cls: 'sm', onClick: () => { action.onClick(); box.remove(); } }) : null,
    button({ icon: 'x', cls: 'ghost sm', title: t('common.close'), onClick: () => box.remove() }));
  $('#toasts').appendChild(box);
  setTimeout(() => box.remove(), ms || 4000);
}

/* ---------------------------------------------------------------- dialogs */
/**
 * Show a modal dialog and resolve with the clicked button's value.
 * @param {Object} opts Options.
 * @param {string} opts.title Title.
 * @param {string|Node} [opts.body] Content.
 * @param {Array<{label: string, value: *, cls?: string}>} opts.buttons Buttons.
 * @returns {Promise<*>} Value of the clicked button (undefined on Esc).
 */
function dialog(opts) {
  const dlg = $('#dialog');
  return new Promise((resolve) => {
    const finish = (value) => { dlg.close(); resolve(value); };
    mount(dlg,
      h('div.dialog-body', h('h3', opts.title), opts.body ? h('div', opts.body) : null),
      h('div.dialog-actions', opts.buttons.map((b) => button({ label: b.label, cls: b.cls || '', onClick: () => finish(b.value) }))));
    dlg.onclose = () => resolve(undefined);
    dlg.showModal();
    const primary = dlg.querySelector('.btn.primary, .btn.solid-danger');
    if (primary) primary.focus();
  });
}

/**
 * Ask for confirmation.
 * @param {string} title Question.
 * @param {string} [text] Explanation.
 * @param {{danger?: boolean, ok?: string}} [opts] Options.
 * @returns {Promise<boolean>} True if confirmed.
 */
async function confirmDialog(title, text, opts) {
  opts = opts || {};
  const value = await dialog({
    title,
    body: text ? h('p.muted', text) : null,
    buttons: [
      { label: t('common.cancel'), value: false },
      { label: opts.ok || t('common.ok'), value: true, cls: opts.danger ? 'solid-danger' : 'primary' },
    ],
  });
  return value === true;
}

/**
 * Ask for a line of text.
 * @param {string} title Prompt title.
 * @param {string} [value] Initial value.
 * @param {string} [placeholder] Placeholder.
 * @returns {Promise<string|null>} Entered text or null if cancelled.
 */
function promptDialog(title, value, placeholder) {
  const input = h('input', { type: 'text', value: value || '', placeholder: placeholder || '' });
  const dlg = $('#dialog');
  const done = dialog({
    title,
    body: input,
    buttons: [{ label: t('common.cancel'), value: null }, { label: t('common.ok'), value: 'ok', cls: 'primary' }],
  }).then((v) => (v === 'ok' ? input.value.trim() : null));
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); dlg.querySelector('.btn.primary').click(); }
  });
  setTimeout(() => input.focus(), 30);
  return done;
}

/**
 * Show a native file/folder dialog on the server computer.
 * @param {"files"|"folder"|"json"|"exe"} kind Dialog type.
 * @param {string} [title] Dialog title.
 * @returns {Promise<string[]>} Selected absolute paths (empty if cancelled).
 */
async function pickNative(kind, title) {
  try {
    const res = await api.post('/api/pick', { kind, title: title || '' });
    return res.paths || [];
  } catch (err) {
    if (err.code === 'picker_failed' && /no_tk|tkinter/i.test(err.detail)) toast(t('err.no_tk'), 'warn', 8000);
    else showError(err);
    return [];
  }
}
