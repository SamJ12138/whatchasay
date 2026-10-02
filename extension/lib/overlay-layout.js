/**
 * overlay-layout.js: the layout math of the subtitle overlay (docs/overlay/README.md).
 *
 * fitLines(text, {maxLines, maxChars, cjk}): the rows a text is drawn on. Words wrap at
 * maxChars per row (CJK: characters); past maxLines rows the oldest words are dropped and
 * the first row starts with an ellipsis, so the newest words, the ones being spoken, are
 * always on screen.
 * fontPx(videoHeight, {scalePct, minPx, fallbackPx}): the font size from the video's
 * height, with a floor for small embeds and the px setting when no video is known.
 *
 * Pure: loaded by the content script and node tests.
 */
(function (root) {
  const CJK_LANGS = ['zh', 'ja', 'ko'];

  function isCJK(lang) {
    return !!lang && CJK_LANGS.includes(String(lang).toLowerCase().split(/[-_]/)[0]);
  }

  // split into units that may not be broken: words (Latin and the rest), characters (CJK)
  function units(text, cjk) {
    const t = String(text || '').replace(/\s+/g, ' ').trim();
    if (!t) return [];
    return cjk ? Array.from(t.replace(/ /g, '')) : t.split(' ');
  }

  // wrap units into rows of at most maxChars; a unit longer than a row is cut
  function wrap(items, maxChars, joiner) {
    const rows = [];
    let row = '';
    for (let u of items) {
      while (u.length > maxChars) {
        if (row) { rows.push(row); row = ''; }
        rows.push(u.slice(0, maxChars));
        u = u.slice(maxChars);
      }
      if (!u) continue;
      const next = row ? row + joiner + u : u;
      if (next.length <= maxChars) row = next;
      else { rows.push(row); row = u; }
    }
    if (row) rows.push(row);
    return rows;
  }

  function fitLines(text, opts = {}) {
    const maxLines = Math.max(1, opts.maxLines || 2);
    const maxChars = Math.max(4, opts.maxChars || 42);
    const cjk = !!opts.cjk;
    const joiner = cjk ? '' : ' ';
    const items = units(text, cjk);
    let rows = wrap(items, maxChars, joiner);
    if (rows.length <= maxLines) return { lines: rows, truncated: false };
    // too long: keep the newest units, as many as fit in maxLines rows with the ellipsis
    let lo = 0, hi = items.length;  // drop the first `lo` units
    while (lo < hi) {
      const mid = Math.floor((lo + hi) / 2);
      const r = wrap(['…' + items[mid]].concat(items.slice(mid + 1)), maxChars, joiner);
      if (r.length <= maxLines) hi = mid; else lo = mid + 1;
    }
    if (lo >= items.length) lo = items.length - 1;  // the last unit alone is wider than the rows
    rows = wrap(['…' + items[lo]].concat(items.slice(lo + 1)), maxChars, joiner);
    if (rows.length > maxLines) {
      rows = rows.slice(rows.length - maxLines);
      if (!rows[0].startsWith('…')) rows[0] = '…' + rows[0].slice(1);
    }
    return { lines: rows, truncated: true };
  }

  function fontPx(videoHeight, opts = {}) {
    const scale = (opts.scalePct == null ? 4.5 : opts.scalePct) / 100;
    const minPx = opts.minPx == null ? 14 : opts.minPx;
    if (!(videoHeight > 0)) return opts.fallbackPx == null ? Math.max(minPx, 20) : opts.fallbackPx;
    return Math.max(minPx, Math.round(videoHeight * scale * 10) / 10);
  }

  const api = { fitLines, fontPx, isCJK };
  root.STOverlayLayout = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : self);
