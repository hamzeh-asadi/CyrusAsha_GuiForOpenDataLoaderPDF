/* ==========================================================================
   CyrusAsha_GuiForOpenDataLoaderPDF - application shell
   Router, state polling, header & navigation, global drag & drop,
   language/theme switching, notifications and keyboard shortcuts.
   ========================================================================== */
'use strict';

/** Product identity shown in the header, footer and window title. */
const BRAND = {
  product: 'CyrusAsha_GuiForOpenDataLoaderPDF',
  version: '1.0.0',
  tagline: 'Code . Intelligence . Order',
  logo: 'brand/CyrusAsha_GuiForOpenDataLoaderPDF.png',
  logoFull: 'brand/logo-full.png',
  /** Developer and project links (same as the CyrusAsha_UniversalRTL control panel). */
  links: [
    { label: 'GitHub', icon: 'github', href: 'https://github.com/hamzeh-asadi' },
    { label: 'Telegram', icon: 'telegram', href: 'https://t.me/Asadi_hamzeh' },
    { label: 'opendataloader-pdf', icon: 'external', href: 'https://github.com/opendataloader-project/opendataloader-pdf' },
  ],
};

/** Navigation entries: [route, icon]. */
const NAV = [['convert', 'convert'], ['jobs', 'jobs'], ['hybrid', 'cpu'], ['setup', 'package'], ['rules', 'shield'], ['settings', 'settings'], ['help', 'help']];

/* ---------------------------------------------------------------- loaders */
/**
 * Load the settings (cached unless `force`).
 * @param {boolean} [force] Reload from the server.
 * @returns {Promise<Object>} Settings.
 */
async function loadSettings(force) {
  if (!S.settings || force) S.settings = await api.get('/api/settings');
  return S.settings;
}

/**
 * Save settings and update the cache.
 * @param {Object} patch Changed keys.
 * @returns {Promise<Object>} All settings.
 */
async function saveSettings(patch) {
  S.settings = await api.post('/api/settings', patch);
  return S.settings;
}

/**
 * Load the CLI option metadata.
 * @param {boolean} [force] Reload.
 * @returns {Promise<Object>} Metadata.
 */
async function loadOptionsMeta(force) {
  if (!S.optionsMeta || force) S.optionsMeta = await api.get('/api/options');
  return S.optionsMeta;
}

/**
 * Load presets.
 * @param {boolean} [force] Reload.
 * @returns {Promise<Object[]>} Presets.
 */
async function loadPresets(force) {
  if (!S.presets || force) S.presets = await api.get('/api/presets');
  return S.presets;
}

/**
 * Load the list of sanitization rule files.
 * @param {boolean} [force] Reload.
 * @returns {Promise<Object[]>} Rule files.
 */
async function loadRules(force) {
  if (!S.rules || force) S.rules = await api.get('/api/rules');
  return S.rules;
}

/* ----------------------------------------------------------------- router */
/**
 * Navigate to a route such as "jobs/<id>".
 * @param {string} route Route without "#".
 */
function go(route) {
  if (location.hash === '#' + route) renderRoute();
  else location.hash = route;
}

/** Render the page for the current hash. */
function renderRoute() {
  const parts = (location.hash.slice(1) || '').split('/').filter(Boolean);
  let page = parts[0];
  if (!page || !PAGES[page]) {
    const env = S.state && S.state.env;
    page = env && !env.loading && !env.installed ? 'setup' : 'convert';
  }
  if (S.current && S.current.unmount) {
    try { S.current.unmount(); } catch (e) { console.error(e); }
  }
  S.page = page;
  S.current = PAGES[page];
  const view = $('#view');
  mount(view);
  $('#main').scrollTop = 0;
  S.current.mount(view, parts.slice(1));
  if (S.state && S.current.update) S.current.update(S.state);
  renderNav();
  renderFooter();
  document.title = t('nav.' + page) + ' — ' + BRAND.product + '-' + BRAND.version;
}

/* ---------------------------------------------------------------- polling */
/**
 * True while something is running (poll faster).
 * @param {Object} st State.
 * @returns {boolean} Busy.
 */
function isBusy(st) {
  if (!st) return false;
  const counts = st.job_counts || {};
  return !!(counts.running || counts.waiting || counts.queued || (st.setup && st.setup.state === 'running') ||
    ['starting', 'stopping'].includes(st.hybrid && st.hybrid.state) || (st.env && st.env.loading));
}

/** Poll /api/state forever with an adaptive interval. */
async function pollLoop() {
  await refreshState();
  const delay = document.hidden ? 5000 : isBusy(S.state) ? 800 : 2500;
  setTimeout(pollLoop, delay);
}

/**
 * Fetch the state once and dispatch it.
 * @returns {Promise<void>}
 */
async function refreshState() {
  try {
    const st = await api.get('/api/state');
    const wasOffline = !S.online;
    S.online = true;
    handleState(st);
    if (wasOffline) toast(t('app.reconnected'), 'ok');
  } catch (err) {
    if (S.online) { S.online = false; renderHeader(); }
  }
}

/**
 * Process a new state: notifications, header, page update.
 * @param {Object} st New state.
 */
function handleState(st) {
  const prev = S.state;
  if (prev) notifyFinished(prev, st);
  const firstState = !prev;
  S.state = st;
  if (prev && prev.env && st.env && prev.env.version !== st.env.version) S.optionsMeta = null;
  renderHeader();
  renderNav();
  if (firstState && !location.hash) renderRoute();
  else if (S.current && S.current.update) {
    try { S.current.update(st); } catch (e) { console.error(e); }
  }
}

/**
 * Show toasts / desktop notifications for jobs that just finished.
 * @param {Object} prev Previous state.
 * @param {Object} st New state.
 */
function notifyFinished(prev, st) {
  const before = {};
  for (const j of prev.jobs) before[j.id] = j.state;
  for (const job of st.jobs) {
    const old = before[job.id];
    if (!old || !['queued', 'waiting', 'running'].includes(old) || ['queued', 'waiting', 'running'].includes(job.state)) continue;
    const kind = job.state === 'success' ? 'ok' : job.state === 'partial' ? 'warn' : job.state === 'canceled' ? 'info' : 'err';
    const text = t('notify.job_' + job.state, { name: jobTitle(job), n: fmtNum(job.outputs_count) });
    const viewing = location.hash === '#jobs/' + job.id;
    toast(text, kind, 8000, viewing ? null : { label: t('common.view'), onClick: () => go('jobs/' + job.id) });
    if (S.settings && S.settings.notify_on_finish && document.hidden && 'Notification' in window && Notification.permission === 'granted') {
      try { new Notification(BRAND.product, { body: text, icon: BRAND.logo }); } catch (e) { /* ignore */ }
    }
  }
}

/** Ask for permission to show desktop notifications (needs a user gesture). */
function requestNotifyPermission() {
  if ('Notification' in window && Notification.permission === 'default' && (!S.settings || S.settings.notify_on_finish)) {
    try { Notification.requestPermission(); } catch (e) { /* ignore */ }
  }
}

/* ---------------------------------------------------------- header & nav */
/** Render the top bar (brand, status pills, language/theme/exit buttons). */
function renderHeader() {
  const st = S.state;
  const env = (st && st.env) || {};
  const pills = [];
  if (!S.online) {
    pills.push(h('span.pill.err', h('span.dot'), t('app.offline')));
  } else if (st) {
    if (env.loading) pills.push(h('span.pill.pulse', h('span.dot'), t('app.checking')));
    else {
      pills.push(h('button.pill', { class: env.java_ok ? 'ok' : 'err', onclick: () => go('setup'), title: t('nav.setup') },
        h('span.dot'), 'Java ' + (env.java_version || '✗')));
      pills.push(h('button.pill', { class: !env.installed ? 'err' : env.update_available ? 'warn' : 'ok', onclick: () => go('setup'),
        title: env.update_available ? t('setup.update_to', { v: env.latest }) : 'opendataloader-pdf' },
      h('span.dot'), env.installed ? 'ODL ' + env.version + (env.update_available ? ' ↑' : '') : t('app.not_installed')));
    }
    const hy = st.hybrid || {};
    pills.push(h('button.pill', { class: { ready: 'ok', external: 'ok', starting: 'info pulse', stopping: 'warn pulse', crashed: 'err' }[hy.state] || '',
      onclick: () => go('hybrid') }, h('span.dot'), t('app.hybrid') + ': ' + t('hstate.' + (hy.state || 'stopped'))));
    const running = (st.job_counts.running || 0) + (st.job_counts.waiting || 0);
    if (running) pills.push(h('button.pill.primary.pulse', { onclick: () => go('jobs') }, h('span.dot'), t('app.running', { n: fmtNum(running) })));
    if (st.setup && st.setup.state === 'running') pills.push(h('button.pill.info.pulse', { onclick: () => go('setup') }, h('span.dot'), t('app.installing')));
  }
  const themeIcon = { auto: 'auto', light: 'sun', dark: 'moon' }[getTheme()];
  const langSeg = h('div.seg', { role: 'group', 'aria-label': t('app.language') },
    [['fa', 'فارسی'], ['en', 'English']].map(([code, label]) => h('button', {
      type: 'button', class: getLang() === code ? 'active' : null, lang: code,
      onclick: () => { if (getLang() !== code) setLang(code); },
    }, label)));
  mount($('#topbar'),
    h('a.brand', { href: '#convert', title: BRAND.product + '-' + BRAND.version },
      h('img.brand-logo', { src: BRAND.logo, alt: 'CyrusAsha' }),
      h('div.brand-text',
        h('div.brand-title', h('span.name', BRAND.product), h('span.ver', BRAND.version)),
        h('div.brand-sub', t('app.subtitle')))),
    h('div.top-status', pills),
    h('div.top-actions',
      langSeg,
      button({ icon: themeIcon, cls: 'ghost', title: t('app.theme') + ': ' + t('theme.' + getTheme()), onClick: cycleTheme }),
      button({ icon: 'power', cls: 'ghost', title: t('app.exit'), onClick: shutdownApp })));
}

/** Render the sidebar navigation. */
function renderNav() {
  const st = S.state;
  const active = st ? (st.job_counts.running || 0) + (st.job_counts.waiting || 0) + (st.job_counts.queued || 0) : 0;
  mount($('#nav'),
    NAV.map(([key, ic], index) => [
      index === 3 || index === 5 ? h('div.nav-sep') : null,
      h('a.nav-item', { href: '#' + key, class: S.page === key ? 'active' : null, title: t('nav.' + key) + ' (Alt+' + (index + 1) + ')' },
        icon(ic), h('span.label', t('nav.' + key)),
        key === 'jobs' && active ? h('span.count', fmtNum(active)) : null,
        key === 'setup' && st && st.env && !st.env.loading && (!st.env.installed || !st.env.java_ok) ? h('span.count', { style: { background: 'var(--danger)' } }, '!') : null),
    ]),
    h('div.nav-foot', 'CyrusAsha · v' + ((st && st.version) || BRAND.version)));
}

/** Render the footer with the developer and project links. */
function renderFooter() {
  mount($('#footer'), h('div.site-footer-inner',
    h('div.footer-brand', h('img', { src: BRAND.logo, alt: '' }), h('span', t('footer.title')),
      h('span.tagline', BRAND.tagline)),
    h('nav.footer-links', { 'aria-label': t('footer.title') }, BRAND.links.map((link) =>
      h('a.footer-link', { href: link.href, target: '_blank', rel: 'noopener noreferrer' }, icon(link.icon), h('span', link.label))))));
}

/* ------------------------------------------------------- language & theme */
/**
 * Switch the UI language and re-render everything.
 * @param {"fa"|"en"} lang Language.
 */
function setLang(lang) {
  localStorage.setItem('odl.lang', lang);
  document.documentElement.lang = lang;
  document.documentElement.dir = lang === 'fa' ? 'rtl' : 'ltr';
  saveSettings({ language: lang }).catch(() => {});
  renderHeader();
  renderRoute();
}

/**
 * Current theme setting.
 * @returns {"auto"|"light"|"dark"} Theme.
 */
function getTheme() {
  return localStorage.getItem('odl.theme') || 'auto';
}

/**
 * Apply and store a theme.
 * @param {"auto"|"light"|"dark"} theme Theme.
 */
function setTheme(theme) {
  localStorage.setItem('odl.theme', theme);
  document.documentElement.setAttribute('data-theme', theme);
  saveSettings({ theme }).catch(() => {});
  renderHeader();
}

/** Cycle auto → light → dark. */
function cycleTheme() {
  const order = ['auto', 'light', 'dark'];
  setTheme(order[(order.indexOf(getTheme()) + 1) % order.length]);
}

/* ---------------------------------------------------------------- exit */
/** Stop the application after confirmation. */
async function shutdownApp() {
  const counts = (S.state && S.state.job_counts) || {};
  const busy = (counts.running || 0) + (counts.queued || 0) + (counts.waiting || 0);
  const ok = await confirmDialog(t('app.exit_q'), busy ? t('app.exit_busy', { n: fmtNum(busy) }) : t('app.exit_text'), { danger: busy > 0, ok: t('app.exit') });
  if (!ok) return;
  await api.post('/api/shutdown').catch(() => {});
  S.online = false;
  mount(document.body, h('div.empty', { style: { paddingTop: '16vh' } }, h('img', { src: BRAND.logoFull, width: 180, height: 180, alt: 'CyrusAsha' }),
    h('h2', t('app.closed')), h('p.muted', t('app.closed_text'))));
  setTimeout(() => window.close(), 400);
}

/* ------------------------------------------------------------ drag & drop */
/** Global drag & drop: PDFs/folders go to the convert page, JSON to rules. */
function setupDragDrop() {
  let depth = 0;
  const overlay = $('#drop-overlay');
  const hasFiles = (e) => e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files');
  window.addEventListener('dragenter', (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    depth++;
    mount($('#drop-overlay-text'), S.page === 'rules' ? t('drop.overlay_rules') : t('drop.overlay'));
    overlay.hidden = false;
  });
  window.addEventListener('dragover', (e) => { if (hasFiles(e)) { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; } });
  window.addEventListener('dragleave', (e) => {
    if (!hasFiles(e)) return;
    depth = Math.max(0, depth - 1);
    if (!depth) overlay.hidden = true;
  });
  window.addEventListener('drop', (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    depth = 0;
    overlay.hidden = true;
    const files = Array.from(e.dataTransfer.files || []);
    if (S.page === 'rules' && files.length && files.every((f) => /\.json$/i.test(f.name))) {
      files.forEach((f) => RulesPage.importFile(f));
      return;
    }
    const drop = captureDrop(e.dataTransfer); // must happen synchronously
    if (S.page !== 'convert') go('convert');
    handleDrop(drop).catch(showError);
  });
}

/* ------------------------------------------------------------- keyboard */
/** Keyboard shortcuts: Ctrl+Enter start, Ctrl+O open files, Alt+1..7 pages. */
function setupKeyboard() {
  window.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter' && S.page === 'convert') {
      e.preventDefault();
      ConvertPage.start().catch(showError);
    } else if ((e.ctrlKey || e.metaKey) && (e.key === 'o' || e.key === 'O')) {
      e.preventDefault();
      if (S.page !== 'convert') go('convert');
      setTimeout(() => ConvertPage.fileInput && ConvertPage.fileInput.click(), 50);
    } else if (e.altKey && !e.ctrlKey && /^[1-7]$/.test(e.key)) {
      e.preventDefault();
      go(NAV[parseInt(e.key, 10) - 1][0]);
    }
  });
}

/* ------------------------------------------------------------------ init */
/** Start the application. */
async function init() {
  setupDragDrop();
  setupKeyboard();
  window.addEventListener('hashchange', renderRoute);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshState(); });
  renderHeader();
  renderNav();
  renderFooter();
  try {
    const info = await api.get('/api/info');
    S.paths = info.paths;
    S.windows = info.windows;
    const settings = await loadSettings();
    // The server remembers the language/theme; adopt them if this browser has no own choice yet.
    if (!localStorage.getItem('odl.lang') && settings.language && settings.language !== getLang()) {
      localStorage.setItem('odl.lang', settings.language);
      document.documentElement.lang = settings.language;
      document.documentElement.dir = settings.language === 'fa' ? 'rtl' : 'ltr';
    }
    if (!localStorage.getItem('odl.theme') && settings.theme) {
      localStorage.setItem('odl.theme', settings.theme);
      document.documentElement.setAttribute('data-theme', settings.theme);
    }
  } catch (err) {
    showError(err);
  }
  if (location.hash) renderRoute();
  pollLoop();
}

init();
