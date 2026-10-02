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

  // ---- placement (batch 2) ----

  /**
   * Where the subtitle block goes, in viewport coordinates.
   * input: {video: {x,y,w,h} | null, viewport: {w,h}, fullscreen, blockHeight, controls: rect | null,
   *         offset: {dx,dy} | null, gap}
   * Returns {mode, left, width, gap, top} (page: below the video's bottom edge, in the
   * page's space) or {mode, left, width, gap, bottom} (overlay: the block's bottom edge
   * over the video, a safe margin up, above a visible control bar; viewport: no video
   * known, 15% up the viewport as before). The dragged offset is added, then the block
   * is kept inside the viewport.
   */
  function placement(input) {
    const vp = input.viewport || { w: 0, h: 0 };
    const v = input.video;
    const gap = input.gap == null ? 6 : input.gap;
    const blockHeight = input.blockHeight || 0;
    const offset = input.offset || { dx: 0, dy: 0 };
    let out;
    if (!v || !(v.w > 0 && v.h > 0)) {
      const width = vp.w * 0.8;
      out = { mode: 'viewport', left: (vp.w - width) / 2, width, gap, bottom: vp.h * 0.85 };
    } else {
      const width = Math.round(v.w * 0.92);
      const left = v.x + (v.w - width) / 2;
      const fills = !!input.fullscreen || (v.w >= vp.w * 0.95 && v.h >= vp.h * 0.9);
      const roomBelow = v.y + v.h + gap + blockHeight <= vp.h;
      if (!fills && roomBelow) {
        out = { mode: 'page', left, width, gap, top: v.y + v.h + gap };
      } else {
        const margin = Math.max(8, Math.round(v.h * 0.02));
        let bottom = v.y + v.h - margin;
        const c = input.controls;
        if (c && c.h > 0 && c.y < bottom && c.y + c.h > bottom - blockHeight) bottom = c.y - gap;
        out = { mode: 'overlay', left, width, gap, bottom };
      }
    }
    out.left = Math.min(Math.max(0, out.left + (offset.dx || 0)), Math.max(0, vp.w - out.width));
    if (out.top != null) out.top = Math.min(Math.max(0, out.top + (offset.dy || 0)), Math.max(0, vp.h - blockHeight));
    if (out.bottom != null) out.bottom = Math.min(Math.max(blockHeight, out.bottom + (offset.dy || 0)), vp.h);
    return out;
  }

  /**
   * The site's control bar among the elements found over the video's bottom edge
   * (candidates: [{rect, opacity, isVideo}], the top-most first): visible, anchored at
   * the bottom of the video, wider than half of it and no taller than 30% of it. Returns
   * its rect, or null.
   */
  function pickControls(candidates, video) {
    if (!video) return null;
    const bottom = video.y + video.h;
    for (const c of candidates || []) {
      const r = c.rect;
      if (!r || c.isVideo || !(c.opacity > 0.05) || c.hidden) continue;
      if (Math.abs(r.y + r.h - bottom) > 6) continue;
      if (r.w < video.w * 0.5 || r.h < 16 || r.h > video.h * 0.3) continue;
      return { x: r.x, y: r.y, w: r.w, h: r.h };
    }
    return null;
  }

  // Per-site control bars: the selector, and the class on the player while the bar is
  // hidden (the generic rule finds bars by their place and visibility; these name them).
  const CONTROLS_OVERRIDES = [
    { host: /(^|\.)youtube\.com$/, selector: '.ytp-chrome-bottom', player: '.html5-video-player', hiddenClass: 'ytp-autohide' },
    { host: /^(127\.0\.0\.1|localhost)$/, selector: '#controls', player: '.player', hiddenClass: null },  // the browser harness's pages
  ];

  function controlsOverride(origin) {
    let host = '';
    try { host = new URL(origin).hostname; } catch (_) { return null; }
    return CONTROLS_OVERRIDES.find((o) => o.host.test(host)) || null;
  }

  const api = { fitLines, fontPx, isCJK, placement, pickControls, controlsOverride };
  root.STOverlayLayout = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : self);
