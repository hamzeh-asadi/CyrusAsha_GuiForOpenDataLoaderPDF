/* ==========================================================================
   CyrusAsha_GuiForOpenDataLoaderPDF - minimal Markdown renderer + HTML sanitizer
   Renders the Markdown produced by opendataloader-pdf (headings, paragraphs,
   lists, GFM tables, images, links, code, emphasis, HTML tables when
   --markdown-with-html is used). The result is always passed through a
   whitelist sanitizer, so content from untrusted PDFs cannot run scripts.
   ========================================================================== */
'use strict';

const Markdown = (function () {
  /** Allowed tags and their allowed attributes. */
  const ALLOWED = {
    h1: [], h2: [], h3: [], h4: [], h5: [], h6: [], p: [], br: [], hr: [], ul: [], ol: ['start'], li: [],
    blockquote: [], pre: [], code: [], em: [], strong: [], b: [], i: [], u: [], s: [], del: [], sup: [], sub: [],
    a: ['href', 'title'], img: ['src', 'alt', 'title', 'width', 'height'], span: [], div: [], figure: [], figcaption: [],
    table: [], thead: [], tbody: [], tfoot: [], tr: [], th: ['colspan', 'rowspan', 'align'], td: ['colspan', 'rowspan', 'align'], caption: [],
  };
  /**
   * Elements whose text follows its own direction (Persian/English mixed
   * content). Tables and lists too: a Persian table starts on the right.
   */
  const AUTO_DIR = new Set(['P', 'LI', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'TD', 'TH', 'BLOCKQUOTE', 'FIGCAPTION', 'CAPTION',
    'TABLE', 'UL', 'OL']);

  /**
   * Escape text for safe insertion into HTML.
   * @param {string} s Raw text.
   * @returns {string} Escaped text.
   */
  function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  /**
   * Render inline Markdown (code, images, links, emphasis).
   * @param {string} text One line/paragraph of Markdown.
   * @returns {string} HTML.
   */
  function inline(text) {
    // Finished HTML snippets are parked as placeholders so later steps cannot touch them.
    const holds = [];
    const hold = (html) => { holds.push(html); return '\u0000' + (holds.length - 1) + '\u0000'; };
    let s = text.replace(/`([^`]+)`/g, (m, c) => hold('<code>' + esc(c) + '</code>'));
    // Images first: ![alt](<path with spaces.png> "title") or ![alt](path.png).
    // (Must run before the HTML-tag step, otherwise "<doc_images/x.png>" looks like a tag.)
    s = s.replace(/!\[([^\]]*)\]\(\s*(?:<([^>]+)>|([^\s)]+))(?:\s+"([^"]*)")?\s*\)/g, (m, alt, a, b, title) =>
      hold('<img alt="' + esc(alt) + '" src="' + esc(a || b) + '"' + (title ? ' title="' + esc(title) + '"' : '') + '>'));
    // Links: [text](<url>) or [text](url).
    s = s.replace(/\[([^\]]+)\]\(\s*(?:<([^>]+)>|([^\s)]+))(?:\s+"([^"]*)")?\s*\)/g, (m, label, a, b) =>
      hold('<a href="' + esc(a || b) + '">' + emphasis(esc(label)) + '</a>'));
    // Inline HTML tags are kept (the sanitizer decides later); all other text is escaped.
    s = s.replace(/<\/?[a-zA-Z][^<>]*>/g, (m) => hold(m));
    s = emphasis(esc(s));
    for (let round = 0; round < 3 && s.includes('\u0000'); round++) {
      s = s.replace(/\u0000(\d+)\u0000/g, (m, i) => holds[+i]);
    }
    return s;
  }

  /**
   * Apply bold/italic/strike-through to already escaped text.
   * @param {string} s Escaped text.
   * @returns {string} HTML.
   */
  function emphasis(s) {
    s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>').replace(/__([^_]+)__/g, '<strong>$1</strong>');
    s = s.replace(/(^|[^*])\*([^*\s][^*]*)\*/g, '$1<em>$2</em>');
    return s.replace(/~~([^~]+)~~/g, '<del>$1</del>');
  }

  /**
   * Split a GFM table row into cells.
   * @param {string} line Table row.
   * @returns {string[]} Cell contents.
   */
  function cells(line) {
    let s = line.trim();
    if (s.startsWith('|')) s = s.slice(1);
    if (s.endsWith('|') && !s.endsWith('\\|')) s = s.slice(0, -1);
    return s.split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, '|'));
  }

  /**
   * Convert Markdown to (unsanitized) HTML.
   * @param {string} src Markdown text.
   * @returns {string} HTML.
   */
  function toHtml(src) {
    const lines = String(src).replace(/\r\n?/g, '\n').split('\n');
    const out = [];
    let para = [];
    const listStack = [];

    const flushPara = () => {
      if (para.length) { out.push('<p>' + inline(para.join(' ')) + '</p>'); para = []; }
    };
    const closeLists = (indent) => {
      while (listStack.length && (indent === undefined || listStack[listStack.length - 1].indent > indent)) {
        out.push('</li></' + listStack.pop().type + '>');
      }
    };

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      // fenced code
      const fence = line.match(/^\s*(```|~~~)(.*)$/);
      if (fence) {
        flushPara(); closeLists();
        const body = [];
        i++;
        while (i < lines.length && !lines[i].trim().startsWith(fence[1])) body.push(lines[i++]);
        out.push('<pre><code>' + esc(body.join('\n')) + '</code></pre>');
        continue;
      }
      if (!line.trim()) { flushPara(); continue; }
      // raw HTML block (e.g. <table> from --markdown-with-html)
      if (/^\s*<(table|div|figure|p|ul|ol|h[1-6]|blockquote|pre|hr|br|img)\b/i.test(line)) {
        flushPara(); closeLists();
        const block = [line];
        while (i + 1 < lines.length && lines[i + 1].trim()) block.push(lines[++i]);
        out.push(block.join('\n'));
        continue;
      }
      const heading = line.match(/^(#{1,6})\s+(.*?)\s*#*\s*$/);
      if (heading) {
        flushPara(); closeLists();
        out.push('<h' + heading[1].length + '>' + inline(heading[2]) + '</h' + heading[1].length + '>');
        continue;
      }
      if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { flushPara(); closeLists(); out.push('<hr>'); continue; }
      // table: header + separator row
      if (line.includes('|') && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(lines[i + 1])) {
        flushPara(); closeLists();
        const head = cells(line);
        const aligns = cells(lines[i + 1]).map((c) => (c.startsWith(':') && c.endsWith(':') ? 'center' : c.endsWith(':') ? 'right' : ''));
        i += 2;
        const rows = [];
        while (i < lines.length && lines[i].includes('|') && lines[i].trim()) rows.push(cells(lines[i++]));
        i--;
        const cell = (tag, c, k) => '<' + tag + (aligns[k] ? ' align="' + aligns[k] + '"' : '') + '>' + inline(c) + '</' + tag + '>';
        out.push('<table><thead><tr>' + head.map((c, k) => cell('th', c, k)).join('') + '</tr></thead><tbody>' +
          rows.map((r) => '<tr>' + r.map((c, k) => cell('td', c, k)).join('') + '</tr>').join('') + '</tbody></table>');
        continue;
      }
      const quote = line.match(/^\s*>\s?(.*)$/);
      if (quote) { flushPara(); closeLists(); out.push('<blockquote>' + inline(quote[1]) + '</blockquote>'); continue; }
      const item = line.match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
      if (item) {
        flushPara();
        const indent = item[1].replace(/\t/g, '    ').length;
        const type = /\d/.test(item[2]) ? 'ol' : 'ul';
        const top = listStack[listStack.length - 1];
        if (!top || indent > top.indent) {
          listStack.push({ indent, type });
          out.push('<' + type + (type === 'ol' && parseInt(item[2], 10) > 1 ? ' start="' + parseInt(item[2], 10) + '"' : '') + '><li>');
        } else {
          closeLists(indent);
          const current = listStack[listStack.length - 1];
          if (current && current.type !== type && current.indent === indent) {
            out.push('</li></' + listStack.pop().type + '>');
            listStack.push({ indent, type });
            out.push('<' + type + '><li>');
          } else {
            out.push('</li><li>');
          }
        }
        out.push(inline(item[3]));
        continue;
      }
      if (listStack.length && /^\s{2,}\S/.test(line)) { out.push(' ' + inline(line.trim())); continue; }
      closeLists();
      para.push(line.trim().replace(/\s{2,}$/, '') + (/\s{2,}$/.test(line) ? '<br>' : ''));
    }
    flushPara();
    closeLists();
    return out.join('\n');
  }

  /**
   * Remove everything that is not whitelisted and rewrite relative URLs.
   * @param {string} html Untrusted HTML.
   * @param {function(string):string} resolveUrl Maps relative URLs to served URLs.
   * @returns {DocumentFragment} Safe DOM fragment.
   */
  function sanitize(html, resolveUrl) {
    const doc = new DOMParser().parseFromString('<body>' + html + '</body>', 'text/html');
    const frag = document.createDocumentFragment();

    const walk = (node, parent) => {
      for (const child of Array.from(node.childNodes)) {
        if (child.nodeType === Node.TEXT_NODE) {
          parent.appendChild(document.createTextNode(child.textContent));
        } else if (child.nodeType === Node.ELEMENT_NODE) {
          const tag = child.tagName.toLowerCase();
          if (!(tag in ALLOWED)) {
            if (['script', 'style', 'iframe', 'object', 'embed', 'template', 'noscript', 'svg', 'math'].includes(tag)) continue;
            walk(child, parent); // drop the tag, keep its text
            continue;
          }
          const el = document.createElement(tag);
          for (const attr of ALLOWED[tag]) {
            let value = child.getAttribute(attr);
            if (value === null) continue;
            if (attr === 'href') {
              if (/^(https?:|mailto:)/i.test(value)) { el.setAttribute('target', '_blank'); el.setAttribute('rel', 'noopener noreferrer'); }
              else if (!value.startsWith('#')) value = resolveUrl(value);
              if (/^\s*(javascript|data|vbscript):/i.test(value)) continue;
            }
            if (attr === 'src') {
              if (/^data:image\/(png|jpe?g|gif|webp);/i.test(value)) { /* embedded image */ }
              else if (/^[a-z][a-z0-9+.-]*:/i.test(value) || value.startsWith('//')) continue; // no remote/odd schemes
              else value = resolveUrl(value);
            }
            el.setAttribute(attr, value);
          }
          if (AUTO_DIR.has(child.tagName)) el.setAttribute('dir', 'auto');
          if (tag === 'img') el.setAttribute('loading', 'lazy');
          parent.appendChild(el);
          walk(child, el);
        }
      }
    };
    walk(doc.body, frag);
    return frag;
  }

  /**
   * Render Markdown into a safe DOM fragment.
   * @param {string} src Markdown text.
   * @param {function(string):string} resolveUrl Maps relative URLs (images/links).
   * @returns {DocumentFragment} Rendered content.
   */
  function render(src, resolveUrl) {
    return sanitize(toHtml(src), resolveUrl || ((u) => u));
  }

  return { render, sanitize, toHtml, esc };
})();
